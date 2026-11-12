"""Durable PostgreSQL work queue (M2-B). No Redis/Kafka: one host, one database, a handful of
jobs per hour per session. Semantics are AT-LEAST-ONCE delivery; financial effects are
effectively-once because job handlers post through ``paper.store.persist_step`` (compare-and-set
cursor + unique keys), never because the queue promises exactly-once.

* idempotent creation: unique ``job_key``; re-enqueue is a no-op.
* leasing: ``FOR UPDATE SKIP LOCKED`` so two workers can never hold the same job; a lease that
  expires (crashed/hung worker) makes the job claimable again.
* STEP jobs of one session are strictly serialised in creation order.
* bounded retries with exponential backoff; TRANSIENT failures retry, PERMANENT ones (and
  exhausted retries) become terminal DEAD (dead-letter) rows that stay visible.
* cancellation of a session's pending work; worker heartbeats for liveness monitoring.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from asta.core.timeutil import utcnow
from asta.db.paper_models import DurableJob, WorkerLease

KIND_STEP = "STEP"
KIND_RECONCILE = "RECONCILE"
KIND_REPORT = "DAILY_REPORT"
KINDS = (KIND_STEP, KIND_RECONCILE, KIND_REPORT)

PENDING, LEASED, DONE, DEAD, CANCELLED = "PENDING", "LEASED", "DONE", "DEAD", "CANCELLED"
TRANSIENT, PERMANENT = "TRANSIENT", "PERMANENT"

BACKOFF_BASE_S = 5
BACKOFF_CAP_S = 300


def backoff_seconds(attempts: int) -> int:
    return int(min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** max(0, attempts - 1))))


class JobError(Exception):
    """Raised by handlers. ``permanent`` failures are never retried."""

    def __init__(self, message: str, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


def enqueue(
    db: Session,
    key: str,
    kind: str,
    session_id: uuid.UUID | None,
    payload: dict[str, Any],
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> bool:
    """Idempotent creation. Returns True when a new job row was created."""
    stmt = (
        pg_insert(DurableJob)
        .values(
            job_key=key,
            kind=kind,
            session_id=session_id,
            payload=payload,
            status=PENDING,
            attempts=0,
            max_attempts=max_attempts,
            run_after=run_after or utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["job_key"])
        .returning(DurableJob.id)
    )
    return db.execute(stmt).first() is not None


_CLAIM_SQL = text(
    """
    SELECT j.id FROM durable_jobs j
    WHERE j.kind = ANY(:kinds)
      AND (
            (j.status = 'PENDING' AND j.run_after <= :now)
         OR (j.status = 'LEASED' AND j.lease_expires_at < :now)
      )
      AND NOT EXISTS (
            SELECT 1 FROM durable_jobs e
            WHERE e.kind = 'STEP' AND j.kind = 'STEP'
              AND e.session_id IS NOT DISTINCT FROM j.session_id
              AND e.id < j.id AND e.status IN ('PENDING', 'LEASED')
      )
    ORDER BY j.id
    FOR UPDATE OF j SKIP LOCKED
    LIMIT 1
    """
)


def claim(
    db: Session,
    worker_id: str,
    kinds: tuple[str, ...] = KINDS,
    lease_seconds: int = 60,
    now: datetime | None = None,
) -> DurableJob | None:
    """Lease one runnable job, or None. A job whose retry budget is already spent is dead-lettered
    instead of being handed out again (poison-pill protection)."""
    now = now or utcnow()
    row = db.execute(_CLAIM_SQL, {"kinds": list(kinds), "now": now}).first()
    if row is None:
        return None
    job = db.get(DurableJob, row[0], with_for_update=True)
    assert job is not None
    if job.status == LEASED:  # expired lease: the previous owner is presumed dead
        job.last_error = f"lease expired (owner {job.lease_owner})"
    if job.attempts >= job.max_attempts:
        job.status = DEAD
        job.failure_class = job.failure_class or TRANSIENT
        job.last_error = (job.last_error or "") + " | retries exhausted"
        job.finished_at = now
        job.lease_owner = None
        db.flush()
        return None
    job.status = LEASED
    job.lease_owner = worker_id
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.attempts += 1
    db.flush()
    return job


def heartbeat(
    db: Session, job_id: int, worker_id: str, lease_seconds: int = 60, now: datetime | None = None
) -> bool:
    """Extend a lease. False if the lease was lost (expired and re-claimed)."""
    now = now or utcnow()
    res = db.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.lease_owner == worker_id,
            DurableJob.status == LEASED,
        )
        .values(lease_expires_at=now + timedelta(seconds=lease_seconds))
    )
    return bool(getattr(res, "rowcount", 0))


def complete(db: Session, job_id: int, worker_id: str, now: datetime | None = None) -> bool:
    res = db.execute(
        update(DurableJob)
        .where(
            DurableJob.id == job_id,
            DurableJob.lease_owner == worker_id,
            DurableJob.status == LEASED,
        )
        .values(status=DONE, finished_at=now or utcnow(), lease_owner=None, last_error=None)
    )
    return bool(getattr(res, "rowcount", 0))


def fail(
    db: Session,
    job_id: int,
    worker_id: str,
    error: str,
    permanent: bool = False,
    now: datetime | None = None,
) -> str:
    """Record a failure. Returns the job's new status (PENDING = will retry, DEAD = terminal)."""
    now = now or utcnow()
    job = db.get(DurableJob, job_id, with_for_update=True)
    if job is None or job.lease_owner != worker_id or job.status != LEASED:
        return "LEASE_LOST"
    job.last_error = error[:1000]
    job.lease_owner = None
    if permanent or job.attempts >= job.max_attempts:
        job.status = DEAD
        job.failure_class = PERMANENT if permanent else TRANSIENT
        job.finished_at = now
    else:
        job.status = PENDING
        job.failure_class = TRANSIENT
        job.run_after = now + timedelta(seconds=backoff_seconds(job.attempts))
    db.flush()
    return job.status


def cancel_session_jobs(db: Session, session_id: uuid.UUID, now: datetime | None = None) -> int:
    """Cancel work not yet started. A leased job finishes or expires on its own."""
    res = db.execute(
        update(DurableJob)
        .where(DurableJob.session_id == session_id, DurableJob.status == PENDING)
        .values(status=CANCELLED, finished_at=now or utcnow())
    )
    return int(getattr(res, "rowcount", 0) or 0)


def queue_stats(db: Session, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    counts: dict[str, int] = {
        str(k): int(v)
        for k, v in db.execute(
            select(DurableJob.status, func.count()).group_by(DurableJob.status)
        ).all()
    }
    oldest = db.execute(
        select(func.min(DurableJob.run_after)).where(
            DurableJob.status == PENDING, DurableJob.run_after <= now
        )
    ).scalar_one()
    return {
        "counts": dict(counts),
        "backlog": int(counts.get(PENDING, 0)),
        "dead": int(counts.get(DEAD, 0)),
        "oldest_runnable_age_s": None if oldest is None else (now - oldest).total_seconds(),
    }


# ------------------------------------------------------------------ worker liveness
def worker_beat(
    db: Session, worker_id: str, role: str, info: dict[str, Any], now: datetime | None = None
) -> None:
    now = now or utcnow()
    stmt = pg_insert(WorkerLease).values(
        worker_id=worker_id, role=role, started_at=now, heartbeat_at=now, info=info
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["worker_id"],
            set_={"heartbeat_at": now, "info": info, "role": role},
        )
    )


def workers_alive(
    db: Session, max_age_s: int = 30, role: str | None = None, now: datetime | None = None
) -> list[WorkerLease]:
    now = now or utcnow()
    q = select(WorkerLease).where(WorkerLease.heartbeat_at >= now - timedelta(seconds=max_age_s))
    if role:
        q = q.where(WorkerLease.role == role)
    return list(db.execute(q).scalars())
