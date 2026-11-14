"""Amend = cancel + a NEW decision (pure helper). Orders are never mutated in place.

An amend is two independent decisions: cancel the old order (the proven cancel flow), then claim a
new intent with its own deterministic id. The new id is derived by the caller from the same decision
key with a distinct tag (see ``replacement_tag``), so a repeated amend maps to the SAME new id
(idempotent) and can never double. The new order is only released after the old one is confirmed
CANCELED: if the old one filled meanwhile, no replacement is made (that would double the exposure).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from asta.orders.lifecycle import Intent, State


def replacement_tag(old_tag: str, revision: int) -> str:
    if revision < 1:
        raise ValueError("revision starts at 1")
    return f"{old_tag}~r{revision}"


def may_replace(old: Intent) -> tuple[bool, str]:
    if old.state == State.CANCELED:
        return True, ""
    if old.state in (State.FILLED, State.PARTIALLY_FILLED):
        return (
            False,
            "FILLED_SO_NO_REPLACEMENT",
        )  # PARTIALLY_FILLED: cancel it first, then re-evaluate
    return False, f"OLD_ORDER_NOT_CANCELED:{old.state.value}"


def build_replacement(
    old: Intent, new_decision_id: str, *, limit_price: Decimal | None = None,
    quantity: Decimal | None = None, created_ts: int | None = None,
) -> Intent:  # fmt: skip
    ok, why = may_replace(old)
    if not ok:
        raise ValueError(why)
    if new_decision_id == old.decision_id:
        raise ValueError("a replacement needs its own decision id")
    return replace(
        old, decision_id=new_decision_id, state=State.PENDING, attempts=0, misses=0,
        cancel_attempts=0, note=f"replaces {old.decision_id}",
        limit_price=old.limit_price if limit_price is None else limit_price,
        quantity=old.quantity if quantity is None else quantity,
        created_ts=old.created_ts if created_ts is None else created_ts,
    )  # fmt: skip
