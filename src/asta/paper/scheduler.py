"""Step scheduling and session bootstrap (M2-B). DB-only; the network history provider is injected.

STEP jobs are created strictly in time order and idempotently (job_key = session + bar open time).
A bar is scheduled as soon as every enabled venue/symbol has all of its 1-minute candles; if some
are still missing after a grace period the step is scheduled anyway and the worker records the
missing markets as DATA_GAP (entries blocked) — time never stalls on a dead feed, and nothing is
ever interpolated."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from asta.core.timeutil import utcnow
from asta.db.paper_models import DurableJob, MarketObservation, PaperSession
from asta.paper import jobs, ops, store
from asta.paper.bars import CANDLE_1M, bucket_start
from asta.paper.config import PaperSessionConfig
from asta.paper.states import SessionState
from asta.paper.store import bar_to_json
from asta.research.data.models import Bar

HistoryProvider = Callable[[str, str, int, int], Sequence[Bar]]  # venue, symbol, tf_s, limit
ACTIVE_STATES = (
    SessionState.RUNNING.value,
    SessionState.PAUSED.value,
    SessionState.RISK_HALTED.value,
)


def _bar_ready(db: Session, cfg: PaperSessionConfig, ts: datetime) -> bool:
    tf = cfg.timeframe.seconds
    need = tf // 60
    rows = db.execute(
        select(MarketObservation.venue, MarketObservation.symbol, func.count())
        .where(
            MarketObservation.kind == CANDLE_1M,
            MarketObservation.complete.is_(True),
            MarketObservation.event_ts >= ts,
            MarketObservation.event_ts < ts + timedelta(seconds=tf),
            MarketObservation.venue.in_(cfg.enabled_venues),
        )
        .group_by(MarketObservation.venue, MarketObservation.symbol)
    ).all()
    have = {(v, s): n for v, s, n in rows}
    return all(have.get((v, s), 0) >= need for v in cfg.enabled_venues for s in cfg.symbols)


def schedule_steps(
    db: Session, now: datetime | None = None, grace_s: int = 150, max_per_call: int = 500
) -> int:
    """Enqueue due STEP jobs for every active session. Returns the number of new jobs."""
    now = now or utcnow()
    created = 0
    for sess in db.execute(
        select(PaperSession).where(PaperSession.state.in_(ACTIVE_STATES))
    ).scalars():
        if sess.last_step_ts is None:
            continue
        cfg = store.load_config(db, sess.id)
        tf = timedelta(seconds=cfg.timeframe.seconds)
        ts = sess.last_step_ts + tf
        n = 0
        while ts + tf <= now and n < max_per_call:
            if not _bar_ready(db, cfg, ts) and now < ts + tf + timedelta(seconds=grace_s):
                break  # still waiting for the bar's minutes; keep strict time order
            if jobs.enqueue(
                db,
                f"STEP:{sess.id}:{ts.isoformat()}",
                jobs.KIND_STEP,
                sess.id,
                {"step_ts": ts.isoformat(), "warm": False},
            ):
                created += 1
            ts += tf
            n += 1
    return created


def bootstrap_session(
    db: Session,
    sid: uuid.UUID,
    history: Mapping[str, Sequence[Bar]],
    now: datetime | None = None,
) -> int:
    """Create the warm-up STEP jobs from REST history (closed session-timeframe bars). Returns the
    number of warm steps. Fewer bars than ``warmup_bars`` is refused (indicators would be wrong)."""
    now = now or utcnow()
    cfg = store.load_config(db, sid)
    tf = timedelta(seconds=cfg.timeframe.seconds)
    last_closed = bucket_start(now, cfg.timeframe.seconds) - tf  # open time of last closed bar
    per: dict[str, dict[datetime, Bar]] = {
        k: {b.open_time: b for b in bs if b.open_time <= last_closed} for k, bs in history.items()
    }
    expected = {f"{v}:{s}" for v in cfg.enabled_venues for s in cfg.symbols}
    if not expected <= set(per) or any(not per[k] for k in expected):
        raise ValueError("history missing for some enabled markets")
    timeline = sorted({t for k in expected for t in per[k]})[-cfg.warmup_bars :]
    if len(timeline) < cfg.warmup_bars:
        raise ValueError(f"only {len(timeline)} history bars for a warm-up of {cfg.warmup_bars}")
    for ts in timeline:
        bars = {k: bar_to_json(per[k][ts]) for k in sorted(expected) if ts in per[k]}
        jobs.enqueue(
            db,
            f"STEP:{sid}:{ts.isoformat()}",
            jobs.KIND_STEP,
            sid,
            {"step_ts": ts.isoformat(), "warm": True, "bars": bars},
        )
    return len(timeline)


def advance_initializing(
    db: Session, provider: HistoryProvider, now: datetime | None = None
) -> list[uuid.UUID]:
    """INITIALIZING sessions: bootstrap warm-up (once), then move to READY when it is applied."""
    now = now or utcnow()
    ready: list[uuid.UUID] = []
    for sess in db.execute(
        select(PaperSession).where(PaperSession.state == SessionState.INITIALIZING.value)
    ).scalars():
        has_jobs = db.execute(
            select(func.count()).select_from(DurableJob).where(DurableJob.session_id == sess.id)
        ).scalar_one()
        if not has_jobs:
            cfg = store.load_config(db, sess.id)
            try:
                tf = cfg.timeframe.seconds
                hist: dict[str, Sequence[Bar]] = {
                    f"{v}:{s}": provider(v, s, tf, cfg.warmup_bars + 5)
                    for v in cfg.enabled_venues
                    for s in cfg.symbols
                }
                bootstrap_session(db, sess.id, hist, now)
            except Exception as exc:  # noqa: BLE001 - recorded, retried on the next pass
                ops.open_incident(
                    db,
                    ops.UNEXPECTED_ERROR,
                    ops.SEV_WARN,
                    f"warm-up bootstrap failed: {type(exc).__name__}: {exc}",
                    f"bootstrap:{sess.id}",
                    sess.id,
                )
            continue
        pending = db.execute(
            select(func.count())
            .select_from(DurableJob)
            .where(
                DurableJob.session_id == sess.id,
                DurableJob.kind == jobs.KIND_STEP,
                DurableJob.status.in_((jobs.PENDING, jobs.LEASED)),
            )
        ).scalar_one()
        dead = db.execute(
            select(func.count())
            .select_from(DurableJob)
            .where(DurableJob.session_id == sess.id, DurableJob.status == jobs.DEAD)
        ).scalar_one()
        if dead:
            store.transition(db, sess.id, SessionState.FAILED, "system", "warm-up job failed")
        elif pending == 0 and sess.last_step_ts is not None:
            store.transition(db, sess.id, SessionState.READY, "system", "warm-up applied")
            ops.resolve_incident(db, f"bootstrap:{sess.id}")
            ready.append(sess.id)
    return ready


__all__ = ["HistoryProvider", "advance_initializing", "bootstrap_session", "schedule_steps"]
_ = Any
