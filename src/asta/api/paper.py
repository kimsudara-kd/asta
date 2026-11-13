"""Paper-trading API (M2-D). Every endpoint reads or changes LOCAL simulated state only; there is no
exchange client, no credential and no order submission anywhere in this module.

State changes require RBAC (CONTROL_PAPER) + CSRF + Idempotency-Key and are audited."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from asta.api.deps import Db, require, require_write
from asta.auth import service as auth
from asta.auth.rbac import Permission
from asta.core.environment import Environment
from asta.core.timeutil import utcnow
from asta.db import research_store as rs
from asta.db.audit_store import append_audit
from asta.db.models import MarketRulesVersion
from asta.db.paper_models import (
    DailyPaperReport,
    MarketFeedConnection,
    PaperSession,
    PaperStepInput,
)
from asta.paper import ops, reports, store, views
from asta.paper.config import ConfigError, PaperSessionConfig, StrategySpec
from asta.paper.states import TERMINAL, IllegalTransition, SessionState
from asta.paper.worker import KIND_ACTION, request_action
from asta.research.data.profiles import DEFAULT_FEES, RuleProfile
from asta.research.strategies.registry import REGISTRY
from asta.research.types import Timeframe

KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
TOTAL_VIRTUAL = Decimal(1000)
SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
ACTIVE_STRATEGIES = ("regime_grid", "trend_pullback", "sma_cross")

_view = require(Permission.VIEW_PAPER)
_ctl = require_write(Permission.CONTROL_PAPER)
Viewer = Annotated[auth.Principal, Depends(_view)]
Controller = Annotated[auth.Principal, Depends(_ctl)]


StrategyName = Literal["regime_grid", "trend_pullback", "sma_cross"]


def _default_strategies() -> list[StrategyName]:
    return ["regime_grid", "trend_pullback", "sma_cross"]


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[\w .:+()-]+$")
    binance_wallet: Decimal = Field(default=Decimal(500), ge=0, le=1000)
    okx_wallet: Decimal = Field(default=Decimal(500), ge=0, le=1000)
    strategies: list[StrategyName] = Field(
        default_factory=_default_strategies, min_length=1, max_length=3
    )
    timeframe: Literal["5m", "15m", "1h"] = "1h"

    @model_validator(mode="after")
    def _sum(self) -> CreateBody:
        if self.binance_wallet + self.okx_wallet != TOTAL_VIRTUAL:
            raise ValueError("the two virtual wallets must add up to exactly 1000 USDT")
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("duplicate strategy")
        return self


class StopBody(BaseModel):
    reason: str = Field(default="operator stop", max_length=200)


class ReportBody(BaseModel):
    session_id: uuid.UUID
    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


def _rules_from_db(db: Session, venue: str) -> tuple[RuleProfile, ...] | None:
    out = []
    for sym in SYMBOLS:
        r = db.execute(
            select(MarketRulesVersion)
            .where(
                MarketRulesVersion.venue == venue,
                MarketRulesVersion.symbol == sym,
                MarketRulesVersion.source == "LIVE",
                MarketRulesVersion.trading.is_(True),
            )
            .order_by(MarketRulesVersion.retrieved_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if r is None:
            return None
        out.append(
            RuleProfile(
                f"{venue}:{sym}:{r.retrieved_at.date().isoformat()}",
                venue,
                sym,
                r.tick_size,
                r.step_size,
                r.min_qty,
                r.min_notional if r.min_notional is not None else Decimal(5),
                "LIVE public exchange rules snapshot (current rules; unverified for history)",
                r.retrieved_at,
                False,
                "stored by the paper service from the public exchange-info endpoint",
            )
        )
    return tuple(out)


def verified_venues(db: Session, stale_after_s: int = 180) -> tuple[list[str], dict[str, str]]:
    """Venues whose feed is CONNECTED right now (events fresh) and whose rules are stored; the
    rest are disabled WITH a reason and keep their virtual cash uninvested."""
    now = utcnow()
    ok: list[str] = []
    why: dict[str, str] = {}
    for v in ("BINANCE", "OKX"):
        f = db.get(MarketFeedConnection, v)
        if f is None:
            why[v] = "no market-data feed status recorded (paper service not running?)"
        elif f.source != "LIVE" or f.state != "CONNECTED":
            why[v] = f"feed {f.state}: {f.reason or 'not connected'}"
        elif f.last_event_at is None or (now - f.last_event_at) > timedelta(seconds=stale_after_s):
            why[v] = "feed has no fresh events"
        elif _rules_from_db(db, v) is None:
            why[v] = "public trading rules not stored yet"
        else:
            ok.append(v)
    return ok, why


def build_config(db: Session, body: CreateBody) -> PaperSessionConfig:
    enabled, _ = verified_venues(db)
    if not enabled:
        raise HTTPException(
            409, "no venue has a verified live public market-data feed; start the paper service"
        )
    rules = {v: r for v in enabled if (r := _rules_from_db(db, v)) is not None}
    fees = {v: DEFAULT_FEES[v] for v in enabled}
    return PaperSessionConfig(
        name=body.name,
        wallets={"BINANCE": body.binance_wallet, "OKX": body.okx_wallet},
        enabled_venues=tuple(v for v in ("BINANCE", "OKX") if v in enabled),
        strategies=tuple(StrategySpec(s) for s in body.strategies),
        rules=rules,
        fees=fees,
        symbols=SYMBOLS,
        timeframe=Timeframe(body.timeframe),
    )


def register_paper_routes(app: FastAPI, ctx: Any) -> None:
    def idem(value: str | None) -> str:
        if not value or not KEY_RE.match(value):
            raise HTTPException(400, "Idempotency-Key header (8-128 chars) is required")
        return value

    def replay(db: Db, key: str, user: str, endpoint: str, body: Any) -> JSONResponse | str:
        rhash = rs.request_hash(body)
        try:
            hit = rs.idempotent_lookup(db, key, user, endpoint, rhash)
        except rs.IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from None
        if hit is not None:
            return JSONResponse(hit[1], status_code=hit[0], headers={"Idempotent-Replay": "true"})
        return rhash

    def get(db: Db, sid: uuid.UUID) -> PaperSession:
        try:
            return store.get_session(db, sid)
        except store.SessionNotFound:
            raise HTTPException(404, "paper session not found") from None

    def go(db: Db, sid: uuid.UUID, to: SessionState, who: str, reason: str) -> PaperSession:
        try:
            return store.transition(db, sid, to, who, reason)
        except IllegalTransition as exc:
            raise HTTPException(409, str(exc)) from None

    # ------------------------------------------------------------------ reads
    @app.get("/v1/paper/sessions")
    def list_sessions(db: Db, _: Viewer) -> dict[str, Any]:
        rows = db.execute(select(PaperSession).order_by(PaperSession.created_at.desc())).scalars()
        return {"labels": views.LABELS, "sessions": [views.session_json(db, s) for s in rows]}

    @app.get("/v1/paper/sessions/{sid}")
    def session(sid: uuid.UUID, db: Db, _: Viewer) -> dict[str, Any]:
        return views.session_json(db, get(db, sid))

    @app.get("/v1/paper/sessions/{sid}/portfolio")
    def portfolio(sid: uuid.UUID, db: Db, _: Viewer) -> dict[str, Any]:
        get(db, sid)
        return views.portfolio(db, sid)

    @app.get("/v1/paper/sessions/{sid}/orders")
    def orders(
        sid: uuid.UUID,
        db: Db,
        _: Viewer,
        account: str | None = Query(default=None, max_length=64),
        status: str | None = Query(default=None, max_length=20),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        get(db, sid)
        return {"labels": views.LABELS, "orders": views.orders(db, sid, account, status, limit)}

    @app.get("/v1/paper/sessions/{sid}/signals")
    def signals(
        sid: uuid.UUID,
        db: Db,
        _: Viewer,
        accepted: bool | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        get(db, sid)
        return {"labels": views.LABELS, "signals": views.signals(db, sid, limit, accepted)}

    @app.get("/v1/paper/sessions/{sid}/risk")
    def risk(sid: uuid.UUID, db: Db, _: Viewer) -> dict[str, Any]:
        get(db, sid)
        return views.risk_view(db, sid)

    @app.get("/v1/paper/sessions/{sid}/performance")
    def performance(
        sid: uuid.UUID, db: Db, _: Viewer, max_points: int = Query(default=600, ge=10, le=5000)
    ) -> dict[str, Any]:
        get(db, sid)
        return views.performance(db, sid, max_points)

    @app.get("/v1/paper/sessions/{sid}/incidents")
    def incidents(sid: uuid.UUID, db: Db, _: Viewer) -> dict[str, Any]:
        get(db, sid)
        return {
            "labels": views.LABELS,
            "incidents": views.incidents(db, sid),
            "alerts": views.alerts(db, sid),
        }

    @app.get("/v1/market-feeds/status")
    def feeds(db: Db, _: Viewer) -> dict[str, Any]:
        return views.feeds_status(db)

    @app.get("/v1/paper/health")
    def health(db: Db, _: Viewer) -> dict[str, Any]:
        return views.health(db)

    @app.get("/v1/reports/daily")
    def daily(
        db: Db,
        _: Viewer,
        session_id: uuid.UUID | None = None,
        day: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> dict[str, Any]:
        q = select(DailyPaperReport).order_by(
            DailyPaperReport.report_date.desc(), DailyPaperReport.revision.desc()
        )
        if session_id:
            q = q.where(DailyPaperReport.session_id == session_id)
        if day:
            d = datetime.fromisoformat(day).replace(tzinfo=UTC)
            q = q.where(DailyPaperReport.report_date == d)
        seen: set[tuple[uuid.UUID, datetime]] = set()
        rows = []
        started = {
            sid: st
            for sid, st in db.execute(
                select(PaperStepInput.session_id, func.min(PaperStepInput.step_ts))
                .where(PaperStepInput.warm.is_(False))
                .group_by(PaperStepInput.session_id)
            )
        }  # first LIVE step per session
        for r in db.execute(q).scalars():  # highest revision per (session, day)
            st = started.get(r.session_id)
            if st is None or r.report_date + timedelta(days=1) <= st:
                continue  # invalidated: the day ended before the session existed
            if (r.session_id, r.report_date) not in seen:
                seen.add((r.session_id, r.report_date))
                rows.append(r)
        rows = rows[:60]
        return {
            "labels": reports.LABELS,
            "reports": [
                {
                    "session_id": str(r.session_id),
                    "date": r.report_date.date().isoformat(),
                    "revision": r.revision,
                    "content_hash": r.content_hash,
                    "reproducible": reports.verify_report(db, r),
                    "body": r.body,
                }
                for r in rows
            ],
        }

    # ------------------------------------------------------------------ controls
    @app.post("/v1/paper/sessions", status_code=201)
    def create(
        body: CreateBody,
        db: Db,
        p: Controller,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        key = idem(idempotency_key)
        ep = "POST /v1/paper/sessions"
        hit = replay(db, key, p.user_id, ep, body.model_dump(mode="json"))
        if isinstance(hit, JSONResponse):
            return hit
        cfg = build_config(db, body)
        try:
            s = store.create_session(db, cfg, p.username)
        except ConfigError as exc:
            raise HTTPException(422, str(exc)) from None
        go(db, s.id, SessionState.INITIALIZING, p.username, "warm-up requested")
        append_audit(
            db,
            p.username,
            "paper.session.create",
            {
                "session": str(s.id),
                "config_hash": s.config_hash,
                "venues": list(cfg.enabled_venues),
            },
            Environment.PAPER,
        )
        out = views.session_json(db, s)
        rs.idempotent_store(db, key, p.user_id, ep, hit, 201, out)
        db.commit()
        return out

    def control(
        name: str,
        sid: uuid.UUID,
        db: Db,
        p: auth.Principal,
        key_header: str | None,
        to: SessionState,
        allowed_from: tuple[SessionState, ...],
        reason: str,
        pre: Any = None,
    ) -> Any:
        key = idem(key_header)
        ep = f"POST /v1/paper/sessions/{{id}}/{name}"
        hit = replay(db, key, p.user_id, ep, {"id": str(sid), "reason": reason})
        if isinstance(hit, JSONResponse):
            return hit
        s = get(db, sid)
        if SessionState(s.state) not in allowed_from:
            raise HTTPException(409, f"cannot {name} a session in state {s.state}")
        if pre is not None:
            pre(db, s)
        go(db, sid, to, p.username, reason)
        if to in TERMINAL:
            from asta.paper import jobs

            jobs.cancel_session_jobs(db, sid)
        append_audit(
            db,
            p.username,
            f"paper.session.{name}",
            {"session": str(sid), "reason": reason},
            Environment.PAPER,
        )
        out = views.session_json(db, get(db, sid))
        rs.idempotent_store(db, key, p.user_id, ep, hit, 200, out)
        db.commit()
        return out

    @app.post("/v1/paper/sessions/{sid}/start")
    def start(
        sid: uuid.UUID,
        db: Db,
        p: Controller,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        return control(
            "start",
            sid,
            db,
            p,
            idempotency_key,
            SessionState.RUNNING,
            (SessionState.READY,),
            "operator start",
        )

    @app.post("/v1/paper/sessions/{sid}/pause")
    def pause(
        sid: uuid.UUID,
        db: Db,
        p: Controller,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        return control(
            "pause",
            sid,
            db,
            p,
            idempotency_key,
            SessionState.PAUSED,
            (SessionState.RUNNING,),
            "operator pause (no new strategy decisions)",
        )

    def validate_resume(db: Session, s: PaperSession) -> None:
        problems: list[str] = []
        bad = store.reconcile(db, s.id)
        store.record_reconcile(db, s.id, not bad)
        if bad:
            problems.append("reconciliation failed: " + bad[0].detail)
        crit = [i for i in ops.open_incidents(db, s.id) if i.severity == ops.SEV_CRIT]
        if crit:
            problems.append(f"{len(crit)} unresolved critical incident(s)")
        enabled, why = verified_venues(db)
        cfg = store.load_config(db, s.id)
        lost = [v for v in cfg.enabled_venues if v not in enabled]
        if lost:
            problems.append(
                "market data not fresh for " + ", ".join(f"{v} ({why.get(v)})" for v in lost)
            )
        if problems:
            db.rollback()
            raise HTTPException(409, "resume refused: " + "; ".join(problems))

    @app.post("/v1/paper/sessions/{sid}/resume")
    def resume(
        sid: uuid.UUID,
        db: Db,
        p: Controller,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        return control(
            "resume",
            sid,
            db,
            p,
            idempotency_key,
            SessionState.RUNNING,
            (SessionState.PAUSED,),
            "operator resume (validated)",
            validate_resume,
        )

    @app.post("/v1/paper/sessions/{sid}/stop")
    def stop(
        sid: uuid.UUID,
        body: StopBody,
        db: Db,
        p: Controller,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        return control(
            "stop",
            sid,
            db,
            p,
            idempotency_key,
            SessionState.STOPPED,
            tuple(s for s in SessionState if s not in TERMINAL),
            body.reason,
        )

    @app.post("/v1/paper/sessions/{sid}/rearm")
    def rearm(
        sid: uuid.UUID,
        db: Db,
        p: Controller,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        """Release a latched DAILY-loss halt (later UTC day only). Never releases a drawdown halt."""
        key = idem(idempotency_key)
        ep = "POST /v1/paper/sessions/{id}/rearm"
        hit = replay(db, key, p.user_id, ep, {"id": str(sid)})
        if isinstance(hit, JSONResponse):
            return hit
        s = get(db, sid)
        if SessionState(s.state) != SessionState.RISK_HALTED:
            raise HTTPException(409, "session is not risk-halted")
        risk = views.risk_view(db, sid)
        if risk["halts"]["drawdown_halted"]:
            raise HTTPException(409, "a drawdown halt is latched for the whole session; stop it")
        trip = [e for e in risk["events"] if e["kind"] == "DAILY_LOSS_TRIP"]
        if not trip or datetime.fromisoformat(trip[-1]["ts"]).date() >= utcnow().date():
            raise HTTPException(409, "the daily halt can only be re-armed on a later UTC day")
        request_action(
            db, sid, {"action": "REARM_DAILY", "ts": utcnow().isoformat()}, f"ACTION:{sid}:{key}"
        )
        go(db, sid, SessionState.PAUSED, p.username, "operator re-armed the daily breaker")
        append_audit(
            db, p.username, "paper.session.rearm", {"session": str(sid)}, Environment.PAPER
        )
        out = views.session_json(db, get(db, sid))
        rs.idempotent_store(db, key, p.user_id, ep, hit, 200, out)
        db.commit()
        return out

    @app.post("/v1/paper/alerts/{alert_id}/ack")
    def ack(
        alert_id: int,
        db: Db,
        p: Controller,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        idem(idempotency_key)
        ok = ops.acknowledge_alert(db, alert_id, p.username)
        append_audit(db, p.username, "paper.alert.ack", {"alert": alert_id}, Environment.PAPER)
        db.commit()
        return {"acknowledged": ok}

    @app.post("/v1/reports/daily", status_code=201)
    def make_report(
        body: ReportBody,
        db: Db,
        p: Controller,
        idempotency_key: Annotated[str | None, Header()] = None,
    ) -> Any:
        idem(idempotency_key)
        get(db, body.session_id)
        day = datetime.fromisoformat(body.day).replace(tzinfo=UTC)
        if day + timedelta(days=1) > utcnow():
            raise HTTPException(409, "the reporting day is not complete yet")
        if not reports.window_is_covered(db, body.session_id, day):
            raise HTTPException(409, "the session has not processed every step of that day yet")
        bad = store.reconcile(db, body.session_id)
        if bad:
            raise HTTPException(409, "report refused: reconciliation failed: " + bad[0].detail)
        row, created = reports.store_report(db, body.session_id, day)
        append_audit(
            db,
            p.username,
            "paper.report.create",
            {"session": str(body.session_id), "day": body.day, "created": created},
            Environment.PAPER,
        )
        db.commit()
        return {"created": created, "content_hash": row.content_hash, "body": row.body}


_ = (KIND_ACTION, REGISTRY)
