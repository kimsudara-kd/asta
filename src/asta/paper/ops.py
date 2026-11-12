"""Operational incidents and in-app alerts (M2-E). Database-backed only: no external messaging,
no credentials. Reuses the M0 `incidents` table (extended in migration 0004).

An incident is OPEN at most once per ``dedupe_key`` (partial unique index), so a flapping feed or a
repeated failure produces ONE incident and ONE alert until it is resolved."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from asta.core.timeutil import utcnow
from asta.db.models import Incident
from asta.db.paper_models import OperationalAlert

FEED_DISCONNECTED = "FEED_DISCONNECTED"
PRICE_STALE = "PRICE_STALE"
DATA_GAP = "DATA_GAP"
WORKER_OFFLINE = "WORKER_OFFLINE"
DB_UNAVAILABLE = "DB_UNAVAILABLE"
RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
REPLAY_MISMATCH = "REPLAY_MISMATCH"
DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
MAX_DRAWDOWN = "MAX_DRAWDOWN"
REPEATED_REJECTION = "REPEATED_REJECTION"
UNEXPECTED_ERROR = "UNEXPECTED_ERROR"
QUEUE_BACKLOG = "QUEUE_BACKLOG"
CLOCK_DRIFT = "CLOCK_DRIFT"
RECOVERY = "RECOVERY"

SEV_INFO, SEV_WARN, SEV_CRIT = "INFO", "WARNING", "CRITICAL"


def open_incident(
    db: Session,
    kind: str,
    severity: str,
    detail: str,
    dedupe_key: str,
    session_id: uuid.UUID | None = None,
    venue: str | None = None,
    symbol: str | None = None,
) -> tuple[Incident, bool]:
    """Idempotent: returns (incident, created). Creates one in-app alert when newly opened."""
    existing = db.execute(
        select(Incident).where(Incident.dedupe_key == dedupe_key, Incident.resolved_at.is_(None))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    inc = Incident(
        severity=severity,
        kind=kind,
        detail=detail[:2000],
        session_id=session_id,
        venue=venue,
        symbol=symbol,
        dedupe_key=dedupe_key,
    )
    db.add(inc)
    db.flush()
    db.add(
        OperationalAlert(
            session_id=session_id,
            incident_id=inc.id,
            kind=kind,
            severity=severity,
            message=detail[:2000],
        )
    )
    db.flush()
    return inc, True


def resolve_incident(
    db: Session, dedupe_key: str, by: str = "system", now: datetime | None = None
) -> bool:
    res = db.execute(
        update(Incident)
        .where(Incident.dedupe_key == dedupe_key, Incident.resolved_at.is_(None))
        .values(resolved_at=now or utcnow(), resolved_by=by)
    )
    return bool(getattr(res, "rowcount", 0))


def open_incidents(db: Session, session_id: uuid.UUID | None = None) -> list[Incident]:
    q = select(Incident).where(Incident.resolved_at.is_(None))
    if session_id is not None:
        q = q.where((Incident.session_id == session_id) | (Incident.session_id.is_(None)))
    return list(db.execute(q.order_by(Incident.opened_at)).scalars())


def incident_json(i: Incident) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "kind": i.kind,
        "severity": i.severity,
        "detail": i.detail,
        "venue": i.venue,
        "symbol": i.symbol,
        "session_id": str(i.session_id) if i.session_id else None,
        "opened_at": i.opened_at.isoformat(),
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "resolved_by": i.resolved_by,
    }


def acknowledge_alert(db: Session, alert_id: int, user: str) -> bool:
    res = db.execute(
        update(OperationalAlert)
        .where(OperationalAlert.id == alert_id, OperationalAlert.acknowledged_at.is_(None))
        .values(acknowledged_at=utcnow(), acknowledged_by=user)
    )
    return bool(getattr(res, "rowcount", 0))
