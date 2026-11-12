"""Read models for the paper-trading API/UI (M2-D). Pure DB reads; every number is derived from the
durable rows. Decimals are returned as plain strings."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from asta.core.money import dec_str
from asta.core.timeutil import utcnow
from asta.db.models import Incident
from asta.db.paper_models import (
    MarketDataGap,
    MarketFeedConnection,
    OperationalAlert,
    PaperBalance,
    PaperEquitySnapshot,
    PaperOrder,
    PaperRiskEvent,
    PaperSession,
    PaperStepInput,
    StrategySignal,
)
from asta.paper import jobs, ops, store
from asta.paper.config import LABEL

ZERO = Decimal(0)
LABELS = [LABEL, "SIMULATED", "ESTIMATED FILLS", "NOT LIVE TRADING"]


def _d(x: Decimal | None) -> str | None:
    return None if x is None else dec_str(x)


def latest_snapshot(db: Session, sid: uuid.UUID) -> PaperEquitySnapshot | None:
    return db.execute(
        select(PaperEquitySnapshot)
        .where(PaperEquitySnapshot.session_id == sid)
        .order_by(PaperEquitySnapshot.step_seq.desc())
        .limit(1)
    ).scalar_one_or_none()


def session_json(db: Session, s: PaperSession) -> dict[str, Any]:
    snap = latest_snapshot(db, s.id)
    cfg = store.load_config(db, s.id)
    return {
        "id": str(s.id),
        "name": s.name,
        "state": s.state,
        "state_reason": s.state_reason,
        "created_at": s.created_at.isoformat(),
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "stopped_at": s.stopped_at.isoformat() if s.stopped_at else None,
        "last_step_ts": s.last_step_ts.isoformat() if s.last_step_ts else None,
        "last_step_seq": s.last_step_seq,
        "last_reconciled_at": s.last_reconciled_at.isoformat() if s.last_reconciled_at else None,
        "last_reconcile_ok": s.last_reconcile_ok,
        "config_hash": s.config_hash,
        "equity": _d(snap.equity) if snap else _d(cfg.total_equity),
        "starting_equity": _d(cfg.total_equity),
        "timeframe": cfg.timeframe.value,
        "enabled_venues": list(cfg.enabled_venues),
        "idle_cash_venues": sorted(set(cfg.wallets) - set(cfg.enabled_venues)),
        "wallets": {k: _d(v) for k, v in cfg.wallets.items()},
        "strategies": [x.to_json() for x in cfg.strategies],
        "labels": LABELS,
    }


# ------------------------------------------------------------------ portfolio
def portfolio(db: Session, sid: uuid.UUID) -> dict[str, Any]:
    cfg = store.load_config(db, sid)
    bals = db.execute(select(PaperBalance).where(PaperBalance.session_id == sid)).scalars().all()
    realized = {
        a: Decimal(r)
        for a, r in db.execute(
            text(
                "select account, coalesce(sum(realized_pnl),0) from paper_fills "
                "where session_id=:s group by account"
            ),
            {"s": str(sid)},
        )
    }
    accounts: dict[str, dict[str, Any]] = {}
    for b in bals:
        a = accounts.setdefault(
            b.account,
            {"cash": ZERO, "reserved_cash": ZERO, "assets": [], "value": ZERO, "cost": ZERO},
        )
        if b.asset == cfg.quote_asset:
            a["cash"] = b.balance
            a["reserved_cash"] = b.reserved
        elif b.balance or b.reserved:
            mark = b.mark
            value = b.balance * mark if mark is not None else ZERO
            a["assets"].append(
                {
                    "asset": b.asset,
                    "quantity": _d(b.balance),
                    "reserved": _d(b.reserved),
                    "cost_basis": _d(b.cost_total),
                    "mark": _d(mark),
                    "value": _d(value),
                    "unrealized_pnl": _d(value - b.cost_total),
                }
            )
            a["value"] += value
            a["cost"] += b.cost_total
    out_accounts = {}
    by_venue: dict[str, Decimal] = {}
    by_asset: dict[str, Decimal] = {}
    total_cash = ZERO
    for key in sorted(accounts):
        a = accounts[key]
        venue = key.split(":")[0]
        equity = a["cash"] + a["value"]
        out_accounts[key] = {
            "venue": venue,
            "strategy": key.split(":", 1)[1],
            "cash_available": _d(a["cash"] - a["reserved_cash"]),
            "cash_reserved": _d(a["reserved_cash"]),
            "cash_total": _d(a["cash"]),
            "assets": a["assets"],
            "unrealized_pnl": _d(a["value"] - a["cost"]),
            "realized_pnl": _d(realized.get(key, ZERO)),
            "equity": _d(equity),
        }
        by_venue[venue] = by_venue.get(venue, ZERO) + a["value"]
        for it in a["assets"]:
            by_asset[it["asset"]] = by_asset.get(it["asset"], ZERO) + Decimal(it["value"] or "0")
        total_cash += a["cash"]
    idle = sum((v for k, v in cfg.wallets.items() if k not in cfg.enabled_venues), ZERO)
    total_equity = idle + total_cash + sum(by_asset.values(), ZERO)
    return {
        "labels": LABELS,
        "accounts": out_accounts,
        "idle_wallet_cash": _d(idle),
        "totals": {
            "equity": _d(total_equity),
            "cash": _d(total_cash + idle),
            "inventory_value": _d(sum(by_asset.values(), ZERO)),
            "realized_pnl": _d(sum(realized.values(), ZERO)),
            "unrealized_pnl": _d(
                sum((Decimal(a["unrealized_pnl"]) for a in out_accounts.values()), ZERO)
            ),
        },
        "exposure_by_venue": {k: _d(v) for k, v in sorted(by_venue.items())},
        "exposure_by_asset": {k: _d(v) for k, v in sorted(by_asset.items())},
    }


# ------------------------------------------------------------------ orders / signals
def orders(
    db: Session, sid: uuid.UUID, account: str | None, status: str | None, limit: int
) -> list[dict[str, Any]]:
    q = select(PaperOrder).where(PaperOrder.session_id == sid)
    if account:
        q = q.where(PaperOrder.account == account)
    if status:
        q = q.where(PaperOrder.status == status)
    rows = db.execute(q.order_by(PaperOrder.id.desc()).limit(limit)).scalars()
    fills: dict[tuple[str, int], tuple[Decimal, Decimal, Decimal]] = {}
    for r in db.execute(
        text(
            "select account, order_id, sum(fee_amount), sum(slippage_cost), sum(notional) "
            "from paper_fills where session_id=:s group by account, order_id"
        ),
        {"s": str(sid)},
    ):
        fills[(r[0], r[1])] = (Decimal(r[2]), Decimal(r[3]), Decimal(r[4]))
    out = []
    for o in rows:
        fee, slip, notional = fills.get((o.account, o.order_id), (ZERO, ZERO, ZERO))
        out.append(
            {
                "account": o.account,
                "order_id": o.order_id,
                "venue": o.venue,
                "symbol": o.symbol,
                "side": o.side,
                "type": o.order_type,
                "quantity": _d(o.quantity),
                "filled_qty": _d(o.filled_qty),
                "limit_price": _d(o.limit_price),
                "status": o.status,
                "reason": o.reason,
                "tag": o.tag,
                "created_ts": o.created_ts.isoformat(),
                "updated_ts": o.updated_ts.isoformat(),
                "estimated_fee": _d(fee),
                "estimated_slippage": _d(slip),
                "filled_notional": _d(notional),
                "labels": ["SIMULATED", "ESTIMATED FILLS"],
            }
        )
    return out


def signals(db: Session, sid: uuid.UUID, limit: int, accepted: bool | None) -> list[dict[str, Any]]:
    q = select(StrategySignal).where(StrategySignal.session_id == sid)
    if accepted is not None:
        q = q.where(StrategySignal.accepted.is_(accepted))
    rows = db.execute(q.order_by(StrategySignal.id.desc()).limit(limit)).scalars()
    return [
        {
            "account": r.account,
            "signal_id": r.signal_id,
            "step_ts": r.step_ts.isoformat(),
            "strategy": r.strategy_id,
            "strategy_version": r.strategy_version,
            "venue": r.venue,
            "symbol": r.symbol,
            "side": r.side,
            "type": r.order_type,
            "tag": r.tag,
            "accepted": r.accepted,
            "reason": r.reason,
        }
        for r in rows
    ]


# ------------------------------------------------------------------ risk
def risk_view(db: Session, sid: uuid.UUID) -> dict[str, Any]:
    cfg = store.load_config(db, sid)
    lim = cfg.risk_limits
    snaps = db.execute(
        select(PaperEquitySnapshot.ts, PaperEquitySnapshot.equity, PaperEquitySnapshot.exposure)
        .where(PaperEquitySnapshot.session_id == sid)
        .order_by(PaperEquitySnapshot.step_seq)
    ).all()
    equity = snaps[-1][1] if snaps else lim.starting_equity
    exposure = snaps[-1][2] if snaps else ZERO
    peak = max([lim.starting_equity] + [s[1] for s in snaps])
    today = utcnow().date()
    day_open = lim.starting_equity
    for ts, eq, _ in snaps:
        if ts.date() < today:
            day_open = eq
    events = (
        db.execute(
            select(PaperRiskEvent)
            .where(PaperRiskEvent.session_id == sid, PaperRiskEvent.kind != "ORDER_REJECTED")
            .order_by(PaperRiskEvent.seq)
        )
        .scalars()
        .all()
    )
    daily = False
    drawdown = False
    for e in events:
        if e.kind == "DAILY_LOSS_TRIP":
            daily = True
        elif e.kind == "DAILY_REARM":
            daily = False
        elif e.kind == "DRAWDOWN_HALT":
            drawdown = True
    rejects: dict[str, int] = dict(
        db.execute(
            select(StrategySignal.reason, func.count())
            .where(StrategySignal.session_id == sid, StrategySignal.accepted.is_(False))
            .group_by(StrategySignal.reason)
        ).all()  # type: ignore[arg-type]
    )
    last = db.execute(
        select(PaperStepInput)
        .where(PaperStepInput.session_id == sid)
        .order_by(PaperStepInput.step_seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    markets = {}
    for v in cfg.enabled_venues:
        for s in cfg.symbols:
            key = f"{v}:{s}"
            markets[key] = (
                "NO_DATA"
                if last is None
                else last.blocked.get(key, "OK" if key in last.bars else "DATA_GAP")
            )
    return {
        "labels": LABELS,
        "policy": lim.to_json(),
        "daily_auto_reset": False,
        "current": {
            "equity": _d(equity),
            "starting_equity": _d(lim.starting_equity),
            "peak_equity": _d(peak),
            "drawdown_pct": _d((peak - equity) / peak if peak else ZERO),
            "daily_pnl_pct": _d((equity - day_open) / day_open if day_open else ZERO),
            "invested_exposure": _d(exposure),
            "invested_exposure_pct": _d(exposure / lim.starting_equity),
        },
        "halts": {"daily_loss_tripped": daily, "drawdown_halted": drawdown},
        "events": [
            {"seq": e.seq, "ts": e.ts.isoformat(), "kind": e.kind, "detail": e.detail}
            for e in events[-50:]
        ],
        "rejections_by_reason": {k: int(v) for k, v in rejects.items()},
        "market_health": markets,
        "open_incidents": [ops.incident_json(i) for i in ops.open_incidents(db, sid)],
    }


# ------------------------------------------------------------------ performance
def performance(db: Session, sid: uuid.UUID, max_points: int = 600) -> dict[str, Any]:
    cfg = store.load_config(db, sid)
    snaps = (
        db.execute(
            select(PaperEquitySnapshot)
            .where(PaperEquitySnapshot.session_id == sid)
            .order_by(PaperEquitySnapshot.step_seq)
        )
        .scalars()
        .all()
    )
    peak = ZERO
    pts: list[dict[str, Any]] = []
    for s in snaps:
        peak = max(peak, s.equity)
        pts.append(
            {
                "ts": s.ts.isoformat(),
                "equity": _d(s.equity),
                "drawdown": _d((peak - s.equity) / peak if peak else ZERO),
                "constrained_hold": s.benchmarks.get("BENCH:hold_constrained"),
                "unconstrained_hold": s.benchmarks.get("BENCH:hold_unconstrained"),
                "all_cash": s.benchmarks.get("BENCH:all_cash"),
            }
        )
    if len(pts) > max_points:
        stride = len(pts) / max_points
        pts = [pts[int(i * stride)] for i in range(max_points)] + [pts[-1]]
    fees = db.execute(
        text(
            "select account, side, coalesce(sum(case when fee_asset=:q then fee_amount "
            "else fee_amount*price end),0), coalesce(sum(slippage_cost),0), count(*), "
            "coalesce(sum(notional),0), coalesce(sum(realized_pnl),0) "
            "from paper_fills where session_id=:s group by account, side"
        ),
        {"s": str(sid), "q": cfg.quote_asset},
    ).all()
    by_strategy: dict[str, dict[str, Decimal]] = {}
    for acct, _side, fee, slip, n, notional, realized in fees:
        st = acct.split(":", 1)[1]
        d = by_strategy.setdefault(
            st, {"fees": ZERO, "slippage": ZERO, "fills": ZERO, "notional": ZERO, "realized": ZERO}
        )
        d["fees"] += Decimal(fee)
        d["slippage"] += Decimal(slip)
        d["fills"] += Decimal(n)
        d["notional"] += Decimal(notional)
        d["realized"] += Decimal(realized)
    strat_equity: dict[str, Decimal] = {}
    if snaps:
        for key, rec in snaps[-1].accounts.items():
            st = key.split(":", 1)[1]
            strat_equity[st] = strat_equity.get(st, ZERO) + Decimal(rec["equity"])
    start_by: dict[str, Decimal] = {}
    for a in cfg.accounts():
        if a.counts_toward_portfolio:
            start_by[a.strategy_id] = start_by.get(a.strategy_id, ZERO) + a.starting_cash
    strategies = []
    for st, eq in sorted(strat_equity.items()):
        d = by_strategy.get(st, {})
        strategies.append(
            {
                "strategy": st,
                "starting_cash": _d(start_by.get(st, ZERO)),
                "equity": _d(eq),
                "net_pnl": _d(eq - start_by.get(st, ZERO)),
                "fills": int(d.get("fills", ZERO)),
                "fees": _d(d.get("fees", ZERO)),
                "estimated_slippage": _d(d.get("slippage", ZERO)),
                "realized_pnl": _d(d.get("realized", ZERO)),
                "deployment_candidate": False,
            }
        )
    steps = db.execute(
        select(PaperStepInput.blocked, PaperStepInput.bars).where(
            PaperStepInput.session_id == sid, PaperStepInput.warm.is_(False)
        )
    ).all()
    coverage = {}
    for v in cfg.enabled_venues:
        keys = [f"{v}:{s}" for s in cfg.symbols]
        ok = sum(1 for b, bars in steps if all(k in bars and k not in b for k in keys))
        coverage[v] = {"steps": len(steps), "complete_and_fresh": ok}
    return {
        "labels": LABELS,
        "points": pts,
        "strategies": strategies,
        "data_coverage": coverage,
        "equity": _d(snaps[-1].equity) if snaps else None,
        "net_pnl": _d(snaps[-1].equity - cfg.total_equity) if snaps else "0",
        "note": "Simulated, estimated fills, virtual funds. A short observation is not evidence of a profitable edge.",
    }


# ------------------------------------------------------------------ feeds / health
def feeds_status(db: Session) -> dict[str, Any]:
    now = utcnow()
    feeds = []
    for f in db.execute(
        select(MarketFeedConnection).order_by(MarketFeedConnection.venue)
    ).scalars():
        age = (now - f.last_event_at).total_seconds() if f.last_event_at else None
        feeds.append(
            {
                "venue": f.venue,
                "state": f.state,
                "source": f.source,
                "reason": f.reason,
                "reconnects": f.reconnects,
                "since": f.since.isoformat(),
                "last_event_at": f.last_event_at.isoformat() if f.last_event_at else None,
                "last_event_age_s": age,
                "clock_offset_ms": f.clock_offset_ms,
                "connection_id": f.connection_id,
            }
        )
    gaps = db.execute(
        select(MarketDataGap).order_by(MarketDataGap.detected_at.desc()).limit(50)
    ).scalars()
    return {
        "labels": [
            "PUBLIC MARKET DATA ONLY",
            "NO AUTHENTICATED ACCESS",
            "NO ORDERS TO ANY EXCHANGE",
        ],
        "feeds": feeds,
        "gaps": [
            {
                "venue": g.venue,
                "symbol": g.symbol,
                "start": g.gap_start.isoformat(),
                "end": g.gap_end.isoformat(),
                "missing": g.missing,
                "status": g.status,
                "detail": g.detail,
            }
            for g in gaps
        ],
    }


def health(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    workers = jobs.workers_alive(db, 30, "paper-worker", now)
    q = jobs.queue_stats(db, now)
    sessions = db.execute(select(PaperSession)).scalars().all()
    active = [s for s in sessions if s.state in ("RUNNING", "PAUSED", "RISK_HALTED", "RECOVERING")]
    return {
        "labels": LABELS,
        "database": "ok",
        "worker_online": bool(workers),
        "workers": [
            {"id": w.worker_id, "age_s": (now - w.heartbeat_at).total_seconds(), "info": w.info}
            for w in workers
        ],
        "queue": q,
        "active_sessions": [
            {
                "id": str(s.id),
                "name": s.name,
                "state": s.state,
                "last_step_ts": s.last_step_ts.isoformat() if s.last_step_ts else None,
                "last_reconcile_ok": s.last_reconcile_ok,
            }
            for s in active
        ],
        "open_incidents": len(ops.open_incidents(db)),
        "unacknowledged_alerts": int(
            db.execute(
                select(func.count())
                .select_from(OperationalAlert)
                .where(OperationalAlert.acknowledged_at.is_(None))
            ).scalar_one()
        ),
        "feeds": feeds_status(db)["feeds"],
    }


def alerts(db: Session, sid: uuid.UUID | None, limit: int = 100) -> list[dict[str, Any]]:
    q = select(OperationalAlert).order_by(OperationalAlert.id.desc()).limit(limit)
    if sid is not None:
        q = q.where((OperationalAlert.session_id == sid) | (OperationalAlert.session_id.is_(None)))
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "severity": a.severity,
            "message": a.message,
            "created_at": a.created_at.isoformat(),
            "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
            "acknowledged_by": a.acknowledged_by,
        }
        for a in db.execute(q).scalars()
    ]


def incidents(db: Session, sid: uuid.UUID, limit: int = 200) -> list[dict[str, Any]]:
    rows = db.execute(
        select(Incident)
        .where((Incident.session_id == sid) | (Incident.session_id.is_(None)))
        .order_by(Incident.opened_at.desc())
        .limit(limit)
    ).scalars()
    return [ops.incident_json(i) for i in rows]


_ = (timedelta, UTC)
