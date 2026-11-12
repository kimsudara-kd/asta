"""Simulation-only orders. Nothing here can reach an exchange: these are plain data objects.

Lifecycle (ADR-0006): CREATED -> VALIDATED -> SIMULATED_OPEN -> PARTIALLY_FILLED -> FILLED, or
CANCELLED / EXPIRED / REJECTED. Every transition is recorded as an immutable OrderEvent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from asta.research.types import TERMINAL_STATUSES, OrderStatus, OrderType, Side

_ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.VALIDATED, OrderStatus.REJECTED}),
    OrderStatus.VALIDATED: frozenset(
        {OrderStatus.SIMULATED_OPEN, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.SIMULATED_OPEN: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
}


class IllegalTransition(Exception):
    pass


@dataclass(frozen=True)
class OrderIntent:
    """What a strategy asks for. Sizing is a base quantity OR a quote notional (not both)."""

    symbol: str
    side: Side
    type: OrderType
    quantity: Decimal | None = None
    quote_notional: Decimal | None = None
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    expire_after_bars: int = 24
    tag: str = ""
    client_id: str | None = None  # idempotency key within a run
    oco_group: str | None = None  # members share one inventory reservation; stop wins ties


@dataclass(frozen=True)
class CancelIntent:
    client_id: str


@dataclass(frozen=True)
class OrderEvent:
    seq: int
    ts: datetime
    order_id: int
    status: OrderStatus
    reason: str = ""


@dataclass
class SimOrder:
    order_id: int
    symbol: str
    side: Side
    type: OrderType
    quantity: Decimal  # base, already floored to the lot step
    limit_price: Decimal | None
    stop_price: Decimal | None
    created_ts: datetime  # decision time (close of the bar that produced it)
    active_from: datetime  # earliest bar open at which it can interact with the market
    expire_ts: datetime
    tag: str = ""
    client_id: str | None = None
    oco_group: str | None = None
    status: OrderStatus = OrderStatus.CREATED
    filled_qty: Decimal = Decimal(0)
    reserved_remaining: Decimal = Decimal(0)  # quote reserved by a BUY (sells use pools)
    ref_price: Decimal = Decimal(0)  # price used for sizing/exposure of this order
    last_seen_bar: datetime | None = None  # last bar this order was exposed to (gap detection)
    pool: str = ""  # sell-side inventory reservation pool (oco group or own id)
    reject_reason: str = ""
    events: list[OrderEvent] = field(default_factory=list)

    @property
    def remaining(self) -> Decimal:
        return self.quantity - self.filled_qty

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def transition(self, new: OrderStatus, seq: int, ts: datetime, reason: str = "") -> OrderEvent:
        if new == self.status and new == OrderStatus.PARTIALLY_FILLED:
            pass
        elif new not in _ALLOWED.get(self.status, frozenset()):
            raise IllegalTransition(f"{self.status.value} -> {new.value}")
        self.status = new
        if new == OrderStatus.REJECTED:
            self.reject_reason = reason
        ev = OrderEvent(seq, ts, self.order_id, new, reason)
        self.events.append(ev)
        return ev


@dataclass(frozen=True)
class SimFill:
    fill_id: int
    order_id: int
    symbol: str
    side: Side
    ts: datetime  # bar open time in which the fill occurred
    price: Decimal
    quantity: Decimal  # gross base quantity traded
    liquidity: str  # "MAKER" | "TAKER"
    fee_asset: str
    fee_amount: Decimal
    notional: Decimal  # quantized quote amount
    reference_price: Decimal
    slippage_cost: Decimal  # quote cost vs the reference price (>= 0)
    realized_pnl: Decimal  # sells only; 0 for buys
    tag: str = ""
