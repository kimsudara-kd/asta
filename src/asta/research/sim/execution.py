"""Conservative candle-only fill model (ADR-0006). Every result is an ESTIMATE.

No queue-priority claims. Resting limit orders fill only when price trades strictly THROUGH the
limit (touch is not a fill). Intrabar event order is never inferred: the engine forbids intrabar
round trips and lets a stop win ties. Unfilled orders never produce P&L.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any

from asta.core.money import floor_to_step
from asta.research.data.models import Bar
from asta.research.data.profiles import RuleProfile
from asta.research.sim.orders import SimOrder
from asta.research.types import OrderType, Side

BPS = Decimal(10_000)
HUNDRED = Decimal(100)
EXEC_MODEL_VERSION = "exec-1"


@dataclass(frozen=True)
class ExecutionModel:
    version: str = EXEC_MODEL_VERSION
    base_slippage_bps: Decimal = Decimal(5)
    half_spread_bps: Decimal = Decimal(1)
    impact_bps_per_pct: Decimal = Decimal(2)  # extra bps per 1 % of the bar's volume consumed
    max_participation: Decimal = Decimal("0.05")  # cap on share of bar volume per order per bar
    maker_volume_share: Decimal = Decimal("0.5")  # resting orders get at most this share of the cap
    through_ticks: int = 1
    stop_slippage_multiplier: Decimal = Decimal(2)
    reserve_buffer_bps: Decimal = Decimal(150)  # cash reserved above the reference price

    def scaled_slippage(self, multiplier: Decimal) -> ExecutionModel:
        """Slippage-sensitivity helper (0 = frictionless scenario)."""
        return ExecutionModel(
            f"{self.version}*slip{multiplier}",
            self.base_slippage_bps * multiplier,
            self.half_spread_bps * multiplier,
            self.impact_bps_per_pct * multiplier,
            self.max_participation,
            self.maker_volume_share,
            self.through_ticks,
            self.stop_slippage_multiplier,
            self.reserve_buffer_bps,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "base_slippage_bps": str(self.base_slippage_bps),
            "half_spread_bps": str(self.half_spread_bps),
            "impact_bps_per_pct": str(self.impact_bps_per_pct),
            "max_participation": str(self.max_participation),
            "maker_volume_share": str(self.maker_volume_share),
            "through_ticks": self.through_ticks,
            "stop_slippage_multiplier": str(self.stop_slippage_multiplier),
            "reserve_buffer_bps": str(self.reserve_buffer_bps),
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> ExecutionModel:
        return ExecutionModel(
            str(d["version"]),
            Decimal(str(d["base_slippage_bps"])),
            Decimal(str(d["half_spread_bps"])),
            Decimal(str(d["impact_bps_per_pct"])),
            Decimal(str(d["max_participation"])),
            Decimal(str(d["maker_volume_share"])),
            int(d["through_ticks"]),
            Decimal(str(d["stop_slippage_multiplier"])),
            Decimal(str(d["reserve_buffer_bps"])),
        )


@dataclass(frozen=True)
class FillQuote:
    price: Decimal
    quantity: Decimal
    liquidity: str  # MAKER | TAKER
    reference: Decimal


def tick_up(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_UP) * tick


def tick_down(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def _slip(
    model: ExecutionModel, qty: Decimal, bar: Bar, multiplier: Decimal = Decimal(1)
) -> Decimal:
    pct = qty / bar.volume * HUNDRED
    bps = model.base_slippage_bps + model.half_spread_bps + model.impact_bps_per_pct * pct
    return bps * multiplier / BPS


def _taker_price(side: Side, ref: Decimal, slip: Decimal, bar: Bar, tick: Decimal) -> Decimal:
    """Adverse price, kept inside the bar's traded range (a fill cannot happen outside it)."""
    if side == Side.BUY:
        p = tick_up(ref * (1 + slip), tick)
        return min(p, max(bar.high, ref))
    p = tick_down(ref * (1 - slip), tick)
    return max(p, min(bar.low, ref))


def quote_fill(
    order: SimOrder, bar: Bar, model: ExecutionModel, rules: RuleProfile, remaining: Decimal
) -> FillQuote | None:
    """What (if anything) the candle supports for this order. `remaining` is already capped by
    reservations; this function applies the liquidity cap and the price policy."""
    if bar.volume <= 0 or remaining <= 0:
        return None
    step, tick = rules.step_size, rules.tick_size
    cap = floor_to_step(bar.volume * model.max_participation, step)

    def qty_for(share: Decimal) -> Decimal:
        return min(remaining, floor_to_step(cap * share, step))

    if order.type == OrderType.MARKET:
        qty = qty_for(Decimal(1))
        if qty <= 0:
            return None
        price = _taker_price(order.side, bar.open, _slip(model, qty, bar), bar, tick)
        return FillQuote(price, qty, "TAKER", bar.open)

    if order.type == OrderType.LIMIT:
        assert order.limit_price is not None
        limit = order.limit_price
        through = tick * model.through_ticks
        if order.side == Side.BUY:
            if bar.open <= limit:  # marketable at activation: taker with a price cap
                qty = qty_for(Decimal(1))
                if qty <= 0:
                    return None
                p = min(limit, _taker_price(Side.BUY, bar.open, _slip(model, qty, bar), bar, tick))
                return FillQuote(p, qty, "TAKER", bar.open)
            if bar.low <= limit - through:  # traded strictly through: resting (maker) fill
                qty = qty_for(model.maker_volume_share)
                return FillQuote(limit, qty, "MAKER", limit) if qty > 0 else None
            return None
        if bar.open >= limit:
            qty = qty_for(Decimal(1))
            if qty <= 0:
                return None
            p = max(limit, _taker_price(Side.SELL, bar.open, _slip(model, qty, bar), bar, tick))
            return FillQuote(p, qty, "TAKER", bar.open)
        if bar.high >= limit + through:
            qty = qty_for(model.maker_volume_share)
            return FillQuote(limit, qty, "MAKER", limit) if qty > 0 else None
        return None

    if order.type == OrderType.STOP_MARKET:
        assert order.stop_price is not None and order.side == Side.SELL
        stop = order.stop_price
        if bar.open <= stop:  # gap through the stop: fills at the open, not at the stop
            ref = bar.open
        elif bar.low <= stop:
            ref = stop
        else:
            return None
        qty = qty_for(Decimal(1))
        if qty <= 0:
            return None
        slip = _slip(model, qty, bar, model.stop_slippage_multiplier)
        return FillQuote(_taker_price(Side.SELL, ref, slip, bar, tick), qty, "TAKER", ref)

    return None
