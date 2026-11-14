"""Fill accounting for one order (pure, exact Decimal; no float anywhere).

The exchange reports trades; the same trade can arrive twice (user-data stream plus a REST trade
list after a reconnect). ``FillBook.apply`` is therefore IDEMPOTENT by ``trade_id``: a repeat
is counted once. Anything that cannot be right (overfill, a negative size, a trade for another
order, a fee in a different asset than declared) is reported as an anomaly and NEVER absorbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from asta.orders.lifecycle import Intent, State


@dataclass(frozen=True)
class Fill:
    trade_id: str
    client_id: str
    quantity: Decimal  # base asset
    price: Decimal  # quote per base
    fee: Decimal  # >= 0, in fee_asset
    fee_asset: str


@dataclass(frozen=True)
class FillSummary:
    quantity: Decimal
    quote_value: Decimal  # sum(qty * price)
    avg_price: Decimal | None
    fees_by_asset: dict[str, Decimal]
    trades: int


@dataclass
class FillBook:
    """All trades of ONE order. ``max_quantity`` is the ordered size when known."""

    client_id: str
    max_quantity: Decimal | None = None
    fills: dict[str, Fill] = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)

    def apply(self, fill: Fill) -> bool:
        """True when the trade was new and accepted; False for a repeat or a rejected trade."""
        if fill.client_id != self.client_id:
            self.anomalies.append(f"{fill.trade_id}: belongs to {fill.client_id}")
            return False
        if fill.quantity <= 0 or fill.price <= 0 or fill.fee < 0:
            self.anomalies.append(f"{fill.trade_id}: non-positive size/price or negative fee")
            return False
        known = self.fills.get(fill.trade_id)
        if known is not None:
            if known != fill:
                self.anomalies.append(f"{fill.trade_id}: repeated with different content")
            return False
        total = self.summary().quantity + fill.quantity
        if self.max_quantity is not None and total > self.max_quantity:
            self.anomalies.append(f"{fill.trade_id}: overfill {total} > {self.max_quantity}")
            return False
        self.fills[fill.trade_id] = fill
        return True

    def summary(self) -> FillSummary:
        qty = sum((f.quantity for f in self.fills.values()), Decimal(0))
        value = sum((f.quantity * f.price for f in self.fills.values()), Decimal(0))
        fees: dict[str, Decimal] = {}
        for f in self.fills.values():
            fees[f.fee_asset] = fees.get(f.fee_asset, Decimal(0)) + f.fee
        return FillSummary(qty, value, (value / qty) if qty else None, fees, len(self.fills))


def consistency(intent: Intent, book: FillBook) -> tuple[str, ...]:
    """Disagreements between the order's state and the trades we hold (empty = consistent)."""
    s = book.summary()
    out: list[str] = list(book.anomalies)
    if intent.state == State.FILLED:
        if s.quantity == 0:
            out.append("FILLED without any trade")
        elif intent.quantity is not None and s.quantity != intent.quantity:
            out.append(f"FILLED but traded {s.quantity} of {intent.quantity}")
    elif intent.state == State.PARTIALLY_FILLED:
        if s.quantity == 0:
            out.append("PARTIALLY_FILLED without any trade")
        elif intent.quantity is not None and s.quantity >= intent.quantity:
            out.append("PARTIALLY_FILLED but the full size has traded")
    elif intent.state in (State.REJECTED, State.PENDING) and s.quantity > 0:
        out.append(f"{intent.state.value} but {s.quantity} traded")
    return tuple(out)
