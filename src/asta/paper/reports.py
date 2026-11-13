"""Daily CEO paper-trading report (M2-E). One immutable report per completed UTC day, built ONLY from
durable session data (equity snapshots, fills, ledger, signals, step inputs, incidents) so it can be
recomputed bit-for-bit. No generated commentary: every sentence is a deterministic template over
recorded numbers, and nothing here can alter a financial record.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from asta.core.money import dec_str
from asta.db.models import Incident
from asta.db.paper_models import (
    DailyPaperReport,
    MarketDataGap,
    MarketObservation,
    PaperEquitySnapshot,
    PaperFill,
    PaperOrderEvent,
    PaperRiskEvent,
    PaperStepInput,
    StrategySignal,
)
from asta.paper import store
from asta.paper.jobs import JobError
from asta.research.runner import canonical

ZERO = Decimal(0)
REPORT_VERSION = "paper-report-1"
LABELS = ["PAPER TRADING — VIRTUAL FUNDS ONLY", "SIMULATED", "ESTIMATED FILLS", "NOT LIVE TRADING"]


def day_start(d: datetime) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _s(x: Decimal | None) -> str | None:
    return None if x is None else dec_str(x)


def build_report(
    db: Session, session_id: uuid.UUID, day: datetime, until: datetime | None = None
) -> dict[str, Any]:
    """Pure function of durable rows for the UTC window [day, day+1). ``until`` produces a
    clearly-flagged PARTIAL preview of an unfinished day (never stored, never a CEO report)."""
    w0 = day_start(day)
    w1 = until if until is not None else w0 + timedelta(days=1)
    cfg = store.load_config(db, session_id)
    start_eq = cfg.total_equity

    snaps = list(
        db.execute(
            select(PaperEquitySnapshot)
            .where(PaperEquitySnapshot.session_id == session_id)
            .order_by(PaperEquitySnapshot.step_seq)
        ).scalars()
    )
    before = [s for s in snaps if s.ts <= w0]
    inwin = [s for s in snaps if w0 < s.ts <= w1]
    opening = before[-1] if before else None
    closing = inwin[-1] if inwin else None
    open_eq = opening.equity if opening else start_eq
    close_eq = closing.equity if closing else open_eq

    # drawdown inside the window and since session start (peak to trough on snapshots)
    def max_dd(series: list[Decimal]) -> Decimal:
        peak, dd = ZERO, ZERO
        for v in series:
            peak = max(peak, v)
            if peak > 0:
                dd = max(dd, (peak - v) / peak)
        return dd

    win_series = ([open_eq] if opening else []) + [s.equity for s in inwin]
    all_series = [start_eq] + [s.equity for s in snaps if s.ts <= w1]

    fills = list(
        db.execute(
            select(PaperFill)
            .where(PaperFill.session_id == session_id, PaperFill.ts >= w0, PaperFill.ts < w1)
            .order_by(PaperFill.id)
        ).scalars()
    )
    realized = sum((f.realized_pnl for f in fills), ZERO)
    slippage = sum((f.slippage_cost for f in fills), ZERO)
    fees = sum(
        (f.fee_amount if f.fee_asset == cfg.quote_asset else f.fee_amount * f.price for f in fills),
        ZERO,
    )
    net = close_eq - open_eq
    sells = [f for f in fills if f.side == "SELL"]
    wins = [f for f in sells if f.realized_pnl > 0]
    losses = [f for f in sells if f.realized_pnl < 0]

    # balances at the window end, re-derived from ledger events (reproducible for any past day)
    inv: dict[str, dict[str, str]] = {}
    for acct, asset, amt in db.execute(
        text(
            "select account, asset, sum(amount) from paper_ledger_entries "
            "where session_id=:s and ts < :t and kind not in ('RESERVE','RELEASE') "
            "group by account, asset order by account, asset"
        ),
        {"s": str(session_id), "t": w1},
    ):
        inv.setdefault(acct, {})[asset] = dec_str(Decimal(amt))

    # venue / strategy contribution from the snapshots' per-account equity
    def acct_eq(snap: PaperEquitySnapshot | None, key: str, default: Decimal) -> Decimal:
        if snap is None or key not in snap.accounts:
            return default
        return Decimal(snap.accounts[key]["equity"])

    contrib: dict[str, dict[str, str]] = {}
    by_strategy: dict[str, Decimal] = {}
    by_venue: dict[str, Decimal] = {}
    for a in cfg.accounts():
        if not a.counts_toward_portfolio:
            continue
        o = acct_eq(opening, a.key, a.starting_cash)
        c = acct_eq(closing, a.key, o)
        d = c - o
        by_strategy[a.strategy_id] = by_strategy.get(a.strategy_id, ZERO) + d
        by_venue[a.venue] = by_venue.get(a.venue, ZERO) + d
        contrib[a.key] = {"open": dec_str(o), "close": dec_str(c), "pnl": dec_str(d)}

    def bench(snap: PaperEquitySnapshot | None, name: str, default: Decimal) -> Decimal:
        if snap is None or name not in snap.benchmarks:
            return default
        return Decimal(snap.benchmarks[name])

    bench_rows = {}
    for name in ("BENCH:all_cash", "BENCH:hold_constrained", "BENCH:hold_unconstrained"):
        o = bench(opening, name, start_eq)
        c = bench(closing, name, o)
        bench_rows[name] = {"open": dec_str(o), "close": dec_str(c), "pnl": dec_str(c - o)}

    n_signals = db.execute(
        select(func.count())
        .select_from(StrategySignal)
        .where(
            StrategySignal.session_id == session_id,
            StrategySignal.step_ts >= w0,
            StrategySignal.step_ts < w1,
        )
    ).scalar_one()
    n_rejected = db.execute(
        select(func.count())
        .select_from(StrategySignal)
        .where(
            StrategySignal.session_id == session_id,
            StrategySignal.step_ts >= w0,
            StrategySignal.step_ts < w1,
            StrategySignal.accepted.is_(False),
        )
    ).scalar_one()
    ev_counts: dict[str, int] = dict(
        db.execute(
            select(PaperOrderEvent.status, func.count())
            .where(
                PaperOrderEvent.session_id == session_id,
                PaperOrderEvent.ts >= w0,
                PaperOrderEvent.ts < w1,
            )
            .group_by(PaperOrderEvent.status)
        ).all()  # type: ignore[arg-type]
    )
    risk_events = list(
        db.execute(
            select(PaperRiskEvent)
            .where(
                PaperRiskEvent.session_id == session_id,
                PaperRiskEvent.ts >= w0,
                PaperRiskEvent.ts < w1,
                PaperRiskEvent.kind != "ORDER_REJECTED",
            )
            .order_by(PaperRiskEvent.seq)
        ).scalars()
    )

    # operations
    steps = list(
        db.execute(
            select(PaperStepInput)
            .where(
                PaperStepInput.session_id == session_id,
                PaperStepInput.step_ts >= w0,
                PaperStepInput.step_ts < w1,
                PaperStepInput.warm.is_(False),
            )
            .order_by(PaperStepInput.step_seq)
        ).scalars()
    )
    tf = cfg.timeframe.seconds
    expected_steps = max(1, int((w1 - w0).total_seconds() // tf))
    feed_avail: dict[str, str] = {}
    for v in cfg.enabled_venues:
        keys = [f"{v}:{s}" for s in cfg.symbols]
        ok = sum(1 for st in steps if all(k in st.bars and k not in st.blocked for k in keys))
        feed_avail[v] = dec_str(Decimal(ok) / Decimal(expected_steps))
    gaps = list(
        db.execute(
            select(MarketDataGap).where(
                MarketDataGap.gap_start < w1,
                MarketDataGap.gap_end >= w0,
                MarketDataGap.detected_at < w1,
            )
        ).scalars()
    )
    incidents = list(
        db.execute(
            select(Incident)
            .where(Incident.opened_at >= w0, Incident.opened_at < w1)
            .where((Incident.session_id == session_id) | (Incident.session_id.is_(None)))
        ).scalars()
    )
    unresolved = list(
        db.execute(
            select(Incident).where(
                # AS OF the window end: reproducible no matter when the report is recomputed
                Incident.opened_at < w1,
                (Incident.resolved_at.is_(None)) | (Incident.resolved_at >= w1),
                (Incident.session_id == session_id) | (Incident.session_id.is_(None)),
            )
        ).scalars()
    )
    src: dict[str, int] = dict(
        db.execute(
            select(MarketObservation.source, func.count())
            .where(
                MarketObservation.kind == "CANDLE_1M",
                MarketObservation.event_ts >= w0,
                MarketObservation.event_ts < w1,
                MarketObservation.ingested_ts < w1,
                MarketObservation.venue.in_(cfg.enabled_venues),
            )
            .group_by(MarketObservation.source)
        ).all()  # type: ignore[arg-type]
    )
    breached = any(e.kind in ("DAILY_LOSS_TRIP", "DRAWDOWN_HALT") for e in risk_events)

    body: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "labels": LABELS,
        "session_id": str(session_id),
        "config_hash": store.get_session(db, session_id).config_hash,
        "window": {
            "start": w0.isoformat(),
            "end": w1.isoformat(),
            "timezone": "UTC",
            "partial": until is not None,
        },
        "kind": "OBSERVED_PAPER_SIMULATION",
        "financial": {
            "opening_equity": dec_str(open_eq),
            "closing_equity": dec_str(close_eq),
            "net_pnl": dec_str(net),
            "realized_pnl": dec_str(realized),
            "unrealized_pnl_movement": dec_str(net - realized),
            "fees": dec_str(fees),
            "estimated_slippage": dec_str(slippage),
            "return_pct": dec_str(net / open_eq) if open_eq else None,
            "max_drawdown_window": dec_str(max_dd(win_series)),
            "max_drawdown_since_start": dec_str(max_dd(all_series)),
            "cash": dec_str(closing.cash) if closing else None,
            "exposure": dec_str(closing.exposure) if closing else None,
            "inventory_by_account": inv,
            "venue_contribution": {k: dec_str(v) for k, v in sorted(by_venue.items())},
            "account_equity": contrib,
        },
        "trading": {
            "signals_generated": n_signals,
            "signals_rejected": n_rejected,
            "orders_created": int(ev_counts.get("CREATED", 0)),
            "orders_filled": int(ev_counts.get("FILLED", 0)),
            "partial_fills": int(ev_counts.get("PARTIALLY_FILLED", 0)),
            "orders_cancelled": int(ev_counts.get("CANCELLED", 0)),
            "fills": len(fills),
            "closed_sells": len(sells),
            "win_rate": dec_str(Decimal(len(wins)) / Decimal(len(sells))) if sells else "N/A",
            "average_gain": dec_str(sum((f.realized_pnl for f in wins), ZERO) / len(wins))
            if wins
            else "N/A",
            "average_loss": dec_str(sum((f.realized_pnl for f in losses), ZERO) / len(losses))
            if losses
            else "N/A",
            "strategy_pnl": {k: dec_str(v) for k, v in sorted(by_strategy.items())},
            "benchmarks": bench_rows,
            "strategies_are_deployment_candidates": False,
        },
        "operations": {
            "steps_processed": len(steps),
            "steps_expected": expected_steps,
            "uptime_fraction": dec_str(Decimal(len(steps)) / Decimal(expected_steps)),
            "feed_availability": feed_avail,
            "data_gaps": [
                {
                    "venue": g.venue,
                    "symbol": g.symbol,
                    "start": g.gap_start.isoformat(),
                    "end": g.gap_end.isoformat(),
                    "missing": g.missing,
                    "status": g.status if g.closed_at is not None and g.closed_at < w1 else "OPEN",
                }
                for g in gaps
            ],
            "worker_restarts_recoveries": sum(1 for i in incidents if i.kind == "RECOVERY"),
            "reconciliation_failures": sum(
                1 for i in incidents if i.kind == "RECONCILIATION_MISMATCH"
            ),
            "risk_incidents": [
                {"kind": e.kind, "ts": e.ts.isoformat(), "detail": e.detail} for e in risk_events
            ],
            "unresolved_alerts": [
                {"kind": i.kind, "severity": i.severity, "detail": i.detail[:200]}
                for i in unresolved
            ],
        },
        "research_conclusion": {
            "profitable_period": net > 0,
            "risk_compliant_period": not breached,
            "market_data": (
                "STORED_PUBLIC_OBSERVATIONS"
                if sum(src.values())
                else "NO_STORED_OBSERVATIONS (fixture/test input)"
            ),
            "observation_sources": {k: int(v) for k, v in src.items()},
            "outcome_type": "OBSERVED_PAPER_OUTCOME (not a hypothetical scenario)",
            "statement": (
                f"Simulated net P&L {net:.8f} USDT on {open_eq:.8f} opening equity; "
                f"risk limits {'were breached' if breached else 'were not breached'}. "
                "This is a short paper observation, not evidence of a profitable edge."
            ),
            "ai_commentary": None,
        },
    }
    return body


def report_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def latest_report(db: Session, session_id: uuid.UUID, day: datetime) -> DailyPaperReport | None:
    return db.execute(
        select(DailyPaperReport)
        .where(
            DailyPaperReport.session_id == session_id,
            DailyPaperReport.report_date == day_start(day),
        )
        .order_by(DailyPaperReport.revision.desc())
        .limit(1)
    ).scalar_one_or_none()


def window_is_covered(db: Session, session_id: uuid.UUID, day: datetime) -> bool:
    """The session has processed every step of the day (cursor reached the window end)."""
    sess = store.get_session(db, session_id)
    if sess.last_step_ts is None:
        return False
    cfg = store.load_config(db, session_id)
    return sess.last_step_ts + timedelta(seconds=cfg.timeframe.seconds) >= day_start(
        day
    ) + timedelta(days=1)


def store_report(
    db: Session, session_id: uuid.UUID, day: datetime, supersede: bool = False
) -> tuple[DailyPaperReport, bool]:
    """Persist one immutable report per day. Returns (latest row, created).

    ``supersede=True`` (explicit correction) appends a NEW revision, only if the latest revision is
    not reproducible from the durable data; the old row is never modified or removed."""
    w0 = day_start(day)
    cur = latest_report(db, session_id, w0)
    if cur is not None:
        if not supersede or verify_report(db, cur):
            return cur, False
        rev = cur.revision + 1
    else:
        rev = 0
    body = build_report(db, session_id, w0)
    row = DailyPaperReport(
        session_id=session_id,
        report_date=w0,
        window_end=w0 + timedelta(days=1),
        body=body,
        content_hash=report_hash(body),
        revision=rev,
    )
    db.add(row)
    db.flush()
    return row, True


def verify_report(db: Session, row: DailyPaperReport) -> bool:
    """Recompute from the durable rows: the stored report must match (reproducibility check)."""
    return report_hash(build_report(db, row.session_id, row.report_date)) == row.content_hash


def handle_report_job(db: Session, sid: uuid.UUID | None, payload: dict[str, Any]) -> None:
    assert sid is not None
    day = datetime.fromisoformat(payload["day"])
    sess = store.get_session(db, sid)
    if sess.started_at is None or day_start(day) + timedelta(days=1) <= sess.started_at:
        return  # the session did not exist on that day: no report (never invent an empty one)
    if not window_is_covered(db, sid, day):
        # steps of the day are still being posted: a report now would be incomplete and, being
        # immutable, wrong forever -> retry later (found live: 7 of 8 steps in a first report)
        raise JobError("reporting window not fully processed yet")
    # the day must be COMPLETE and the ledger must reconcile before it is reported
    if day_start(day) + timedelta(days=1) > datetime.now(UTC):
        raise ValueError("reporting window is not complete yet")
    bad = store.reconcile(db, sid)
    if bad:
        raise ValueError("report refused: reconciliation failed: " + bad[0].detail)
    store_report(db, sid, day)
