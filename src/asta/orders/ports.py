"""Abstract ports of the order path. Real adapters (connector, durable store) are M3-B work."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from asta.orders.lifecycle import Intent, State


class SendOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"  # the exchange acknowledged a resting/working order
    REJECTED = "REJECTED"  # explicit refusal: nothing exists at the exchange
    DUPLICATE_ID = (
        "DUPLICATE_ID"  # the exchange already has this client id (it is OURS: same decision)
    )
    UNKNOWN = "UNKNOWN"  # timeout / transport error / ambiguous: the request may or may not exist


class QueryKind(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = (
        "NOT_FOUND"  # at THIS moment; an in-flight order may still appear (eventual consistency)
    )
    UNAVAILABLE = "UNAVAILABLE"  # could not ask: learn nothing


@dataclass(frozen=True)
class ExchangeView:
    client_id: str
    state: State  # one of ACCEPTED, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED


@dataclass(frozen=True)
class QueryResult:
    kind: QueryKind
    view: ExchangeView | None = None


class ExchangePort(Protocol):
    def send(self, intent: Intent) -> SendOutcome: ...

    def query(self, client_id: str) -> QueryResult: ...


class IntentStore(Protocol):
    """Durable store. ``insert_if_absent`` MUST be backed by a UNIQUE constraint on decision_id and
    be atomic: it is the exact-once gate. ``save`` must persist before the caller proceeds."""

    def insert_if_absent(self, intent: Intent) -> bool: ...

    def get(self, decision_id: str) -> Intent | None: ...

    def save(self, intent: Intent) -> None: ...

    def by_state(self, states: frozenset[State]) -> list[Intent]: ...
