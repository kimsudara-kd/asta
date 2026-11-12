"""Durable paper worker (M2-B/C): leases STEP / RECONCILE / DAILY_REPORT jobs and applies them.

It never touches the network: it reads stored observations and writes simulated state. Every step
is ONE transaction (job completion + posting), so a crash at any point leaves either the whole step
or nothing. The in-memory runtime is a cache: after ANY error it is dropped and rebuilt by verified
replay from the durable inputs.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from asta.core.timeutil import utcnow
from asta.db.paper_models import DurableJob, MarketFeedConnection
from asta.paper import jobs, ops, store
from asta.paper.bars import load_bar
from asta.paper.config import PaperSessionConfig
from asta.paper.runtime import PaperRuntime
from asta.paper.states import SessionState
from asta.research.data.models import Bar

log = logging.getLogger("asta.paper.worker")

KIND_ACTION = "ACTION"  # operator actions, consumed by the next STEP of the session (not leased)
OBSERVING_OR_RUNNING = frozenset(
    {SessionState.RUNNING, SessionState.PAUSED, SessionState.RISK_HALTED}
)


def max_decision_lag(cfg: PaperSessionConfig) -> timedelta:
    return timedelta(seconds=max(cfg.stale_after_seconds * 3, 300))


def plan_inputs(
    db: Session, cfg: PaperSessionConfig, step_ts: datetime, now: datetime
) -> tuple[dict[str, Bar], dict[str, str]]:
    """Closed bars available for [step_ts, step_ts+tf) and the symbols blocked from NEW entries."""
    tf = cfg.timeframe.seconds
    bars: dict[str, Bar] = {}
    blocked: dict[str, str] = {}
    for venue in cfg.enabled_venues:
        feed = db.get(MarketFeedConnection, venue)
        feed_ok = (
            feed is not None
            and feed.state == "CONNECTED"
            and feed.last_event_at is not None
            and (now - feed.last_event_at) <= timedelta(seconds=cfg.stale_after_seconds)
        )
        late = (now - (step_ts + timedelta(seconds=tf))) > max_decision_lag(cfg)
        for sym in cfg.symbols:
            key = f"{venue}:{sym}"
            bar = load_bar(db, venue, sym, step_ts, tf)
            if bar is None:
                blocked[key] = "DATA_GAP"
                continue
            bars[key] = bar
            if not feed_ok and not late:
                blocked[key] = "STALE_DATA"
    return bars, blocked


class StepWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        worker_id: str | None = None,
        lease_seconds: int = 60,
        clock: Callable[[], datetime] = utcnow,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.factory = factory
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.lease_seconds = lease_seconds
        self.clock = clock
        self.fault = fault or (lambda point: None)  # deterministic fault-injection hook
        self.runtimes: dict[uuid.UUID, PaperRuntime] = {}
        self.processed = 0
        self.stopping = False

    # ------------------------------------------------------------------ main entry
    def run_once(self) -> bool:
        """Claim and process ONE job. Returns False when nothing was runnable."""
        with self.factory() as db:
            job = jobs.claim(db, self.worker_id, lease_seconds=self.lease_seconds, now=self.clock())
            db.commit()
            if job is None:
                return False
            job_id, kind, sid, payload = job.id, job.kind, job.session_id, dict(job.payload)
        try:
            with self.factory() as db:
                self.fault("before_handle")
                if kind == jobs.KIND_STEP:
                    self._handle_step(db, job_id, sid, payload)
                elif kind == jobs.KIND_RECONCILE:
                    self._handle_reconcile(db, job_id, sid)
                elif kind == jobs.KIND_REPORT:
                    from asta.paper import reports

                    reports.handle_report_job(db, sid, payload)
                else:
                    raise jobs.JobError(f"unknown job kind {kind}", permanent=True)
                if not jobs.complete(db, job_id, self.worker_id, self.clock()):
                    raise jobs.JobError("lease lost before completion")
                self.fault("before_commit")
                db.commit()
            self.processed += 1
        except store.StepAlreadyPosted:
            # redelivery of a step that is already durable: the effect exists, so just finish
            with self.factory() as db:
                jobs.complete(db, job_id, self.worker_id, self.clock())
                db.commit()
            self.runtimes.pop(sid, None) if sid else None
        except Exception as exc:
            if sid:
                self.runtimes.pop(sid, None)  # cache may be ahead of the DB: rebuild by replay
            permanent = isinstance(exc, jobs.JobError) and exc.permanent
            if isinstance(exc, store.ReplayMismatch):
                permanent = True
            with self.factory() as db:
                status = jobs.fail(
                    db,
                    job_id,
                    self.worker_id,
                    f"{type(exc).__name__}: {exc}",
                    permanent,
                    self.clock(),
                )
                if status == jobs.DEAD:
                    ops.open_incident(
                        db,
                        ops.UNEXPECTED_ERROR,
                        ops.SEV_CRIT,
                        f"job {job_id} ({kind}) dead-lettered: {type(exc).__name__}: {exc}"[:1500],
                        f"job-dead:{job_id}",
                        sid,
                    )
                db.commit()
            log.warning("job %s failed: %s", job_id, exc)
        return True

    def drain(self, max_jobs: int = 10_000) -> int:
        n = 0
        while n < max_jobs and not self.stopping and self.run_once():
            n += 1
        return n

    # ------------------------------------------------------------------ handlers
    def _runtime(self, db: Session, sid: uuid.UUID) -> PaperRuntime:
        sess = store.get_session(db, sid)
        rt = self.runtimes.get(sid)
        if rt is not None and rt.step_seq == sess.last_step_seq:
            return rt
        rt, rep = store.rebuild_runtime(db, sid)  # verified replay; raises ReplayMismatch
        self.runtimes[sid] = rt
        if rep.steps:
            ops.open_incident(
                db,
                ops.RECOVERY,
                ops.SEV_INFO,
                f"runtime restored by verified replay of {rep.steps} steps (digest {rep.last_digest[:12]})",
                f"recovery:{sid}:{rep.last_step_seq}",
                sid,
            )
        return rt

    def _pending_actions(self, db: Session, sid: uuid.UUID, now: datetime) -> list[DurableJob]:
        return list(
            db.execute(
                select(DurableJob)
                .where(
                    DurableJob.session_id == sid,
                    DurableJob.kind == KIND_ACTION,
                    DurableJob.status == jobs.PENDING,
                    DurableJob.run_after <= now,
                )
                .order_by(DurableJob.id)
                .with_for_update()
            ).scalars()
        )

    def _handle_step(
        self, db: Session, job_id: int, sid: uuid.UUID | None, payload: dict[str, Any]
    ) -> None:
        assert sid is not None
        now = self.clock()
        sess = store.get_session(db, sid, lock=True)
        state = SessionState(sess.state)
        step_ts = datetime.fromisoformat(payload["step_ts"])
        warm = bool(payload.get("warm", False))
        if state not in OBSERVING_OR_RUNNING and not (
            warm and state in (SessionState.INITIALIZING, SessionState.READY)
        ):
            return  # session stopped/failed/created: nothing to apply (job completes as a no-op)
        if sess.last_step_ts is not None and step_ts <= sess.last_step_ts:
            return  # already durable (idempotent redelivery)
        rt = self._runtime(db, sid)
        cfg = rt.cfg
        if "bars" in payload:  # warm-up bars supplied by the bootstrap (REST history)
            bars = {k: store.bar_from_json(v) for k, v in payload["bars"].items()}
            blocked: dict[str, str] = dict(payload.get("blocked", {}))
        else:
            bars, blocked = plan_inputs(db, cfg, step_ts, now)
        late = (now - (step_ts + timedelta(seconds=cfg.timeframe.seconds))) > max_decision_lag(cfg)
        # warm-up steps consult strategies like any step (their orders are rejected as WARMUP);
        # a paused/late step consults them muted so indicator state never falls behind the market
        active = warm or (state in (SessionState.RUNNING, SessionState.RISK_HALTED) and not late)
        actions: list[dict[str, Any]] = []
        for a in self._pending_actions(db, sid, now):
            act = dict(a.payload)
            err = store.apply_action(rt, act) if not warm else "NOT_APPLICABLE_DURING_WARMUP"
            actions.append({**act, "result": err})
            a.status, a.finished_at = jobs.DONE, now
        k = sess.last_step_seq + 1
        self.fault("before_step")
        rows = rt.step(k, step_ts, bars, blocked, active, warm)
        self.fault("after_step_before_persist")
        store.persist_step(db, sid, k, step_ts, warm, bars, blocked, active, actions, rows)
        self.fault("after_persist")
        # ---- reconciliation after every transaction that moved money, and every N steps
        if rows.fills or k % 12 == 0:
            self._reconcile_or_fail(db, sid)
        # ---- risk state -> session state + incidents
        kinds = {e["kind"] for e in rows.risk_events}
        if "DAILY_LOSS_TRIP" in kinds:
            ops.open_incident(
                db,
                ops.DAILY_LOSS_LIMIT,
                ops.SEV_CRIT,
                "daily loss limit reached; new entries halted",
                f"daily-loss:{sid}:{step_ts.date()}",
                sid,
            )
        if "DRAWDOWN_HALT" in kinds:
            ops.open_incident(
                db,
                ops.MAX_DRAWDOWN,
                ops.SEV_CRIT,
                "maximum drawdown reached; halted for the session",
                f"drawdown:{sid}",
                sid,
            )
        cur = SessionState(store.get_session(db, sid).state)
        if rt.halted() and cur == SessionState.RUNNING:
            store.transition(
                db,
                sid,
                SessionState.RISK_HALTED,
                "system",
                "risk halt: " + ",".join(sorted(kinds)) if kinds else "risk halt",
            )
        # stale / gap incidents
        for key, why in blocked.items():
            venue, sym = key.split(":", 1)
            kind = ops.PRICE_STALE if why == "STALE_DATA" else ops.DATA_GAP
            ops.open_incident(
                db,
                kind,
                ops.SEV_WARN,
                f"{key}: new entries blocked ({why}) at {step_ts.isoformat()}",
                f"{kind}:{sid}:{key}",
                sid,
                venue,
                sym,
            )
        for key in bars:
            if key not in blocked:
                for kind in (ops.PRICE_STALE, ops.DATA_GAP):
                    ops.resolve_incident(db, f"{kind}:{sid}:{key}")

    def _reconcile_or_fail(self, db: Session, sid: uuid.UUID) -> None:
        bad = store.reconcile(db, sid)
        store.record_reconcile(db, sid, not bad)
        if bad:
            detail = "; ".join(f"{d.where}: {d.detail}" for d in bad[:5])
            ops.open_incident(
                db, ops.RECONCILIATION_MISMATCH, ops.SEV_CRIT, detail, f"reconcile:{sid}", sid
            )
            sess = store.get_session(db, sid)
            if SessionState(sess.state) not in (SessionState.FAILED, SessionState.STOPPED):
                store.transition(
                    db,
                    sid,
                    SessionState.FAILED,
                    "system",
                    "reconciliation mismatch: " + detail[:300],
                )
            db.commit()  # keep the incident + FAILED state even though the step is discarded
            raise jobs.JobError(
                "reconciliation mismatch; session suspended (fail-closed)", permanent=True
            )

    def _handle_reconcile(self, db: Session, job_id: int, sid: uuid.UUID | None) -> None:
        assert sid is not None
        self._reconcile_or_fail(db, sid)

    # ------------------------------------------------------------------ liveness
    def beat(self) -> None:
        with self.factory() as db:
            jobs.worker_beat(
                db, self.worker_id, "paper-worker", {"processed": self.processed}, self.clock()
            )
            db.commit()


def request_action(db: Session, sid: uuid.UUID, action: dict[str, Any], key: str) -> bool:
    """Queue an operator action; it is applied (and recorded) by the session's next STEP."""
    return jobs.enqueue(db, key, KIND_ACTION, sid, action)


__all__ = ["KIND_ACTION", "StepWorker", "plan_inputs", "request_action", "update"]
