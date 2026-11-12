"""Tamper-evident audit hash chain (ADR-0003)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

GENESIS = "0" * 64


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def row_hash(prev_hash: str, at: datetime, actor: str, action: str, payload: dict[str, Any]) -> str:
    stamp = at.astimezone(UTC).isoformat()
    material = "|".join([prev_hash, stamp, actor, action, canonical_json(payload)])
    return hashlib.sha256(material.encode()).hexdigest()


def verify_chain(rows: list[dict[str, Any]]) -> tuple[bool, int | None]:
    """rows ordered by id; each has at, actor, action, payload, prev_hash, row_hash.
    Returns (ok, index_of_first_bad_row)."""
    prev = GENESIS
    for i, r in enumerate(rows):
        expected = row_hash(prev, r["at"], r["actor"], r["action"], r["payload"])
        if r["prev_hash"] != prev or r["row_hash"] != expected:
            return False, i
        prev = r["row_hash"]
    return True, None
