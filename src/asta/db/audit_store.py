"""Append audit events (hash-chained) and read/verify them."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from asta.audit.chain import GENESIS, canonical_json, row_hash, verify_chain
from asta.core.environment import Environment
from asta.core.timeutil import utcnow
from asta.db.models import AuditEvent

_ADVISORY_LOCK_KEY = 7_001_001  # serialises chain appends


def append_audit(
    db: Session,
    actor: str,
    action: str,
    payload: dict[str, Any] | None = None,
    environment: Environment = Environment.PAPER,
) -> AuditEvent:
    """Caller commits. Secrets must never be placed in payload (callers pass ids/flags only)."""
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK_KEY})
    prev = db.execute(
        select(AuditEvent.row_hash).order_by(AuditEvent.id.desc()).limit(1)
    ).scalar_one_or_none()
    prev_hash = prev or GENESIS
    at = utcnow()
    # store exactly what is hashed: JSON-normalised (Decimal/datetime -> str)
    body: dict[str, Any] = json.loads(canonical_json(payload or {}))
    ev = AuditEvent(
        at=at,
        actor=actor,
        action=action,
        environment=environment.value,
        payload=body,
        prev_hash=prev_hash,
        row_hash=row_hash(prev_hash, at, actor, action, body),
    )
    db.add(ev)
    db.flush()
    return ev


def recent_audit(db: Session, limit: int = 100) -> list[AuditEvent]:
    return list(
        db.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)).scalars()
    )


def verify_audit_chain(db: Session) -> tuple[bool, int | None]:
    rows = db.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars().all()
    return verify_chain(
        [
            {
                "at": r.at,
                "actor": r.actor,
                "action": r.action,
                "payload": r.payload,
                "prev_hash": r.prev_hash,
                "row_hash": r.row_hash,
            }
            for r in rows
        ]
    )
