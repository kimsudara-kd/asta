"""FastAPI app. M0 routes are read-only status + auth. There is NO order, transfer or live route."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from asta import __version__
from asta.api.deps import COOKIE, Db, require
from asta.api.paper import register_paper_routes
from asta.api.research import ResearchRuntime, register_research_routes
from asta.auth import service as auth
from asta.auth.rbac import Permission
from asta.core.environment import Environment
from asta.core.events import Venue
from asta.core.settings import Settings
from asta.core.timeutil import ClockMonitor, utcnow
from asta.db.audit_store import append_audit, recent_audit, verify_audit_chain
from asta.db.models import Incident
from asta.exchanges.binance.adapter import BinancePublicAdapter
from asta.exchanges.common.base import ALLOWLIST_SYMBOLS, BINANCE_ALLOWLIST
from asta.exchanges.okx.adapter import OkxPublicAdapter
from asta.market_data.fixture_feed import run_fixture_feed
from asta.market_data.service import IngestSupervisor
from asta.market_data.state import FreshnessMonitor, MarketState
from asta.research.data.store import DatasetFileStore
from asta.research_jobs import ResearchWorker
from asta.risk.gateway import (
    Decision,
    ExecutionGateway,
    OrderRequest,
    is_execution_enabled,
    is_live_trading_enabled,
)
from asta.risk.policy import RiskPolicy, RiskSnapshot, evaluate

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class MfaBody(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class AppContext:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        state: MarketState | None = None,
        clock: ClockMonitor | None = None,
    ) -> None:
        self.settings = settings
        self.sessions = session_factory
        self.state = state or MarketState(FreshnessMonitor(settings.stale_after))
        self.clock = clock or ClockMonitor(settings.max_clock_drift)
        self.supervisor = IngestSupervisor(self.state, self.clock)
        self.policy = RiskPolicy()
        self.gateway = ExecutionGateway()
        self.started_at = utcnow()
        self.research = ResearchRuntime(
            DatasetFileStore(Path(settings.data_dir)), Path(settings.import_dir)
        )
        self.fixture_venues: set[Venue] = set()
        self._bg: list[asyncio.Task[None]] = []


def create_app(
    ctx: AppContext,
    start_ingest: bool = True,
    fixture_venues: set[Venue] | None = None,
    start_research_worker: bool = True,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        fixtures = fixture_venues or set()
        ctx.fixture_venues = fixtures
        if start_ingest:
            if Venue.BINANCE not in fixtures:
                ctx.supervisor.start(BinancePublicAdapter(BINANCE_ALLOWLIST), ALLOWLIST_SYMBOLS)
            if Venue.OKX not in fixtures:
                ctx.supervisor.start(OkxPublicAdapter(), ALLOWLIST_SYMBOLS)
        for v in fixtures:
            ctx._bg.append(asyncio.create_task(run_fixture_feed(ctx.state, v)))
        if start_research_worker:
            ctx.research.worker = ResearchWorker(
                ctx.sessions, ctx.research.store, ctx.settings.worker_queue_size
            )
            ctx.research.worker.start()
        try:
            yield
        finally:
            if ctx.research.worker is not None:
                ctx.research.worker.stop()
            await ctx.supervisor.stop()
            for t in ctx._bg:
                t.cancel()

    app = FastAPI(
        title="ASTA", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None
    )

    @app.middleware("http")
    async def headers(request: Request, call_next: Callable[..., Any]) -> Response:
        response: Response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    app.state.ctx = ctx

    status_user = require(Permission.VIEW_STATUS)
    audit_user = require(Permission.VIEW_AUDIT)

    # ---------------- public liveness ----------------
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readiness")
    def readiness(db: Db) -> dict[str, Any]:
        db.execute(text("SELECT 1"))
        return {"status": "ready", "database": "ok"}

    # ---------------- auth ----------------
    @app.post("/v1/auth/login")
    def login(body: LoginBody, response: Response, db: Db) -> dict[str, Any]:
        try:
            res = auth.login(
                db,
                body.username,
                body.password,
                timedelta(minutes=ctx.settings.session_ttl_minutes),
            )
        except auth.AuthError:
            raise HTTPException(401, "invalid credentials") from None
        response.set_cookie(
            COOKIE,
            res.token,
            httponly=True,
            samesite="strict",
            secure=ctx.settings.cookie_secure,
            max_age=ctx.settings.session_ttl_minutes * 60,
            path="/",
        )
        return {"mfa_required": res.needs_mfa}

    @app.post("/v1/auth/mfa")
    def mfa(
        body: MfaBody, db: Db, token: Annotated[str | None, Cookie(alias=COOKIE)] = None
    ) -> dict[str, str]:
        if not token:
            raise HTTPException(401, "not authenticated")
        try:
            auth.verify_mfa(db, token, body.code)
        except auth.AuthError:
            raise HTTPException(401, "invalid code") from None
        return {"status": "ok"}

    @app.get("/v1/auth/me")
    def me(p: Annotated[auth.Principal, Depends(require(None))]) -> dict[str, str]:
        return {"username": p.username, "role": p.role, "csrf_token": p.csrf_token}

    @app.post("/v1/auth/logout")
    def logout(
        response: Response,
        db: Db,
        p: Annotated[auth.Principal, Depends(require(None))],
        token: Annotated[str | None, Cookie(alias=COOKIE)] = None,
        x_csrf_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        if not auth.csrf_ok(p.csrf_token, x_csrf_token):
            raise HTTPException(403, "csrf")
        if token:
            auth.logout(db, token)
        response.delete_cookie(COOKIE, path="/")
        return {"status": "ok"}

    # ---------------- read-only status ----------------
    def _venue_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for venue in Venue:
            drift = ctx.clock.reading(venue.value)
            err = ctx.supervisor.venue_errors.get(venue)
            quote_keys = [s for s in ALLOWLIST_SYMBOLS if (venue, s) in ctx.state.quotes]
            fresh = [s for s in ALLOWLIST_SYMBOLS if not ctx.state.freshness.is_stale(venue, s)]
            if venue in ctx.fixture_venues:
                state = "FIXTURE"
            elif err:
                state = "UNREACHABLE"
            elif fresh:
                state = "LIVE"
            else:
                state = "STALE" if quote_keys else "CONNECTING"
            rows.append(
                {
                    "venue": venue.value,
                    "state": state,
                    "error": err,
                    "clock_offset_ms": int(drift.offset.total_seconds() * 1000) if drift else None,
                    # a fixture venue has no clock to measure: report "not applicable", not "bad"
                    "clock_ok": None
                    if venue in ctx.fixture_venues
                    else ctx.clock.is_drift_ok(venue.value),
                    "reconnects": ctx.state.reconnects.get(venue, 0),
                    "gap_open": ctx.state.gap_open.get(venue, False),
                }
            )
        return rows

    def _risk() -> dict[str, Any]:
        venues = [v for v in Venue if v not in ctx.fixture_venues]
        data_fresh = bool(venues) and all(
            not ctx.state.freshness.is_stale(v, s) for v in venues for s in ALLOWLIST_SYMBOLS
        )
        clock_ok = bool(venues) and all(ctx.clock.is_drift_ok(v.value) for v in venues)
        snap = RiskSnapshot(
            equity=ctx.policy.starting_equity,
            start_of_day_equity=ctx.policy.starting_equity,
            peak_equity=ctx.policy.starting_equity,
            invested_by_asset={},
            cash=ctx.policy.starting_equity,
            data_fresh=data_fresh,
            clock_ok=clock_ok,
            reconciled=True,  # nothing to reconcile in M0 (no orders exist)
        )
        ev = evaluate(ctx.policy, snap)
        decision = ctx.gateway.authorize(
            OrderRequest("PAPER", "BINANCE", "none", "BTC-USDT", "BUY", ctx.policy.starting_equity)
        )
        return {
            "halt_new_buys": ev.halt_new_buys,
            "halt_reasons": list(ev.reasons),
            "gateway_probe": {
                "decision": decision.decision.value,
                "reasons": list(decision.reason_codes),
            },
            "execution_enabled": is_execution_enabled(),
            "live_trading_enabled": is_live_trading_enabled(),
            "policy": {k: str(v) for k, v in asdict(ctx.policy).items()},
        }

    @app.get("/v1/overview")
    def overview(_: Annotated[auth.Principal, Depends(status_user)]) -> dict[str, Any]:
        risk = _risk()
        assert risk["gateway_probe"]["decision"] == Decision.DENY.value
        return {
            "environment": Environment.PAPER.value,
            "banner": "M0 — NO TRADING. Public market data only. No real funds.",
            "equity": {
                "simulated_start": str(ctx.policy.starting_equity),
                "pnl": "0",
                "fee_drag": "0",
            },
            "venues": _venue_rows(),
            "heartbeat_utc": utcnow().isoformat(),
            "uptime_seconds": int((utcnow() - ctx.started_at).total_seconds()),
            "risk": risk,
            "events_accepted": ctx.state.events_accepted,
            "duplicates_dropped": ctx.state.duplicates_dropped,
        }

    @app.get("/v1/venues")
    def venues(_: Annotated[auth.Principal, Depends(status_user)]) -> list[dict[str, Any]]:
        return _venue_rows()

    @app.get("/v1/markets")
    def markets(_: Annotated[auth.Principal, Depends(status_user)]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for venue in Venue:
            for sym in ALLOWLIST_SYMBOLS:
                q = ctx.state.quotes.get((venue, sym))
                st = ctx.state.status(venue, sym)
                out.append(
                    {
                        "venue": venue.value,
                        "symbol": sym,
                        "bid": str(q.bid_price) if q else None,
                        "ask": str(q.ask_price) if q else None,
                        "spread_bps": f"{q.spread_bps:.2f}" if q else None,
                        "last_ingested": st.last_ingested.isoformat() if st.last_ingested else None,
                        "stale": st.stale,
                        "source": st.source,
                    }
                )
        return out

    @app.get("/v1/risk")
    def risk(_: Annotated[auth.Principal, Depends(status_user)]) -> dict[str, Any]:
        return _risk()

    @app.get("/v1/incidents")
    def incidents(
        db: Db, _: Annotated[auth.Principal, Depends(status_user)]
    ) -> list[dict[str, Any]]:
        rows = db.query(Incident).order_by(Incident.opened_at.desc()).limit(100).all()
        return [
            {
                "severity": r.severity,
                "kind": r.kind,
                "detail": r.detail,
                "opened_at": r.opened_at.isoformat(),
            }
            for r in rows
        ]

    @app.get("/v1/audit")
    def audit(
        db: Db, p: Annotated[auth.Principal, Depends(audit_user)], limit: int = 100
    ) -> dict[str, Any]:
        limit = max(1, min(limit, 500))
        rows = recent_audit(db, limit)
        ok, bad = verify_audit_chain(db)
        append_audit(db, p.username, "audit.view", {"limit": limit})
        db.commit()
        return {
            "chain_ok": ok,
            "first_bad_index": bad,
            "events": [
                {
                    "id": r.id,
                    "at": r.at.isoformat(),
                    "actor": r.actor,
                    "action": r.action,
                    "payload": r.payload,
                }
                for r in rows
            ],
        }

    register_research_routes(app, ctx)
    register_paper_routes(app, ctx)
    return app
