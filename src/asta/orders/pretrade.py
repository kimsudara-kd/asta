"""Pre-trade guard: every refusal reason for an intent, as a pure function (fail closed).

All limits are explicit: there is no default that allows anything. A kill switch, a missing
limit, an unknown symbol, or an environment other than EXCHANGE_DEMO refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from asta.core.environment import Environment
from asta.orders.lifecycle import Intent


@dataclass(frozen=True)
class PreTradeLimits:
    allowed_venues: frozenset[str]
    allowed_symbols: frozenset[str]
    max_order_notional: Decimal  # far below the demo balance
    max_daily_orders: int
    max_daily_notional: Decimal
    max_price_deviation_bps: Decimal  # LIMIT price vs reference price band


@dataclass(frozen=True)
class DayState:
    orders_today: int
    notional_today: Decimal
    kill_switch: bool


def notional_of(intent: Intent, reference_price: Decimal | None) -> Decimal | None:
    if intent.quote_notional is not None:
        return intent.quote_notional
    price = intent.limit_price or reference_price
    return None if price is None or intent.quantity is None else intent.quantity * price


def refusals(
    intent: Intent,
    limits: PreTradeLimits | None,
    day: DayState,
    reference_price: Decimal | None,
) -> tuple[str, ...]:
    """Empty tuple = allowed. Every applicable reason is listed (not just the first)."""
    out: list[str] = []
    if limits is None:
        return ("NO_LIMITS_CONFIGURED",)
    if day.kill_switch:
        out.append("KILL_SWITCH")
    if intent.environment != Environment.EXCHANGE_DEMO:
        out.append("ENVIRONMENT_NOT_EXCHANGE_DEMO")
    if intent.venue not in limits.allowed_venues:
        out.append("VENUE_NOT_ALLOWED")
    if intent.symbol not in limits.allowed_symbols:
        out.append("SYMBOL_NOT_ALLOWED")
    value = notional_of(intent, reference_price)
    if value is None:
        out.append("NOTIONAL_UNKNOWN")  # fail closed: cannot size it, cannot bound it
    else:
        if value > limits.max_order_notional:
            out.append("ORDER_NOTIONAL_CAP")
        if day.notional_today + value > limits.max_daily_notional:
            out.append("DAILY_NOTIONAL_CAP")
    if day.orders_today + 1 > limits.max_daily_orders:
        out.append("DAILY_ORDER_COUNT_CAP")
    if intent.order_type == "LIMIT" and intent.limit_price is not None:
        if reference_price is None or reference_price <= 0:
            out.append("NO_REFERENCE_PRICE")
        else:
            dev = abs(intent.limit_price - reference_price) / reference_price * Decimal(10_000)
            if dev > limits.max_price_deviation_bps:
                out.append("LIMIT_PRICE_OUTSIDE_BAND")
    return tuple(out)
