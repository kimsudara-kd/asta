"""Intent lifecycle: the only legal state changes of one trading decision (pure)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from asta.core.environment import ORDER_CAPABLE_ENVIRONMENTS, Environment
from asta.paper.order_identity import is_valid_client_order_id


class State(StrEnum):
    PENDING = "PENDING"  # durably recorded (the unique claim), nothing sent yet
    DISPATCHED = "DISPATCHED"  # write-ahead mark: a request MAY be on the wire or already received
    UNKNOWN = "UNKNOWN"  # transport timeout / ambiguous reply: outcome must be learned by query
    ACCEPTED = "ACCEPTED"  # resting at the exchange
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"  # write-ahead mark: a cancel MAY be on the wire
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"  # explicit refusal by the exchange (nothing exists there)
    EXPIRED = "EXPIRED"
    ABANDONED = "ABANDONED"  # we gave up; a late appearance at the exchange is still adopted


TERMINAL = frozenset({State.FILLED, State.CANCELED, State.REJECTED, State.EXPIRED})
#: states that still need work (recovery looks at these)
UNRESOLVED = frozenset({State.PENDING, State.DISPATCHED, State.UNKNOWN})
_LIVE_EXCHANGE = frozenset({State.ACCEPTED, State.PARTIALLY_FILLED})
_EXCHANGE_OUTCOMES = _LIVE_EXCHANGE | TERMINAL

ALLOWED: dict[State, frozenset[State]] = {
    State.PENDING: frozenset({State.DISPATCHED, State.REJECTED, State.ABANDONED}),
    State.DISPATCHED: frozenset({State.UNKNOWN, State.ABANDONED} | _EXCHANGE_OUTCOMES),
    State.UNKNOWN: frozenset({State.DISPATCHED, State.ABANDONED} | _EXCHANGE_OUTCOMES),
    State.ACCEPTED: frozenset({State.PARTIALLY_FILLED, State.CANCEL_REQUESTED} | TERMINAL),
    State.PARTIALLY_FILLED: frozenset({State.PARTIALLY_FILLED, State.CANCEL_REQUESTED} | TERMINAL),
    # the cancel may race a fill; a cancel that did not take effect returns to the resting state
    State.CANCEL_REQUESTED: frozenset({State.CANCEL_REQUESTED} | _EXCHANGE_OUTCOMES),
    # a request we gave up on may still have reached the exchange: adopt whatever is found there
    State.ABANDONED: _EXCHANGE_OUTCOMES,
    State.FILLED: frozenset(),
    State.CANCELED: frozenset(),
    State.REJECTED: frozenset(),
    State.EXPIRED: frozenset(),
}


class IllegalTransition(ValueError):
    pass


def check_transition(src: State, dst: State) -> None:
    if dst not in ALLOWED[src]:
        raise IllegalTransition(f"{src.value} -> {dst.value}")


@dataclass(frozen=True)
class Intent:
    """One decision to trade. ``decision_id`` is also the exchange client order id."""

    decision_id: str
    environment: Environment
    venue: str
    symbol: str
    side: str  # BUY | SELL
    order_type: str  # MARKET | LIMIT
    quote_notional: Decimal | None = None
    quantity: Decimal | None = None
    limit_price: Decimal | None = None
    state: State = State.PENDING
    attempts: int = 0
    misses: int = 0  # consecutive NOT_FOUND answers while UNKNOWN (re-send needs several)
    cancel_attempts: int = 0
    created_ts: int = (
        0  # integer seconds of the decision bar (deterministic, supplied by the caller)
    )
    note: str = ""

    def validate(self) -> None:
        if not is_valid_client_order_id(self.decision_id):
            raise ValueError("decision_id is not a valid deterministic id")
        if (
            self.environment not in ORDER_CAPABLE_ENVIRONMENTS
            or self.environment == Environment.PAPER
        ):
            raise ValueError("this order path only exists for EXCHANGE_DEMO")
        if self.side not in ("BUY", "SELL") or self.order_type not in ("MARKET", "LIMIT"):
            raise ValueError("unsupported side/type (spot BUY/SELL, MARKET/LIMIT only)")
        if (self.quote_notional is None) == (self.quantity is None):
            raise ValueError("exactly one of quote_notional / quantity")
        if self.order_type == "LIMIT" and (self.limit_price is None or self.limit_price <= 0):
            raise ValueError("LIMIT needs a positive limit_price")
        for v in (self.quote_notional, self.quantity):
            if v is not None and v <= 0:
                raise ValueError("size must be positive")

    def moved(
        self,
        to: State,
        note: str = "",
        attempts: int | None = None,
        cancel_attempts: int | None = None,
    ) -> Intent:
        check_transition(self.state, to)
        return replace(
            self,
            state=to,
            note=note or self.note,
            attempts=self.attempts if attempts is None else attempts,
            cancel_attempts=(self.cancel_attempts if cancel_attempts is None else cancel_attempts),
            misses=0,  # every state change restarts the NOT_FOUND count
        )
