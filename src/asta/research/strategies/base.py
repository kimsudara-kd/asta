"""Common deterministic strategy interface with versioned parameters.

A strategy sees only the `StrategyContext` (closed bars up to now, its own portfolio/orders, risk
flags). It returns order intents; it has no handle to any exchange, network, database or the
future. Strategies must be deterministic functions of the context they were shown.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, ClassVar

from asta.core.money import dec_str, floor_to_step
from asta.research.sim.context import StrategyContext
from asta.research.sim.orders import CancelIntent, OrderIntent

Action = OrderIntent | CancelIntent


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return dec_str(v)
    if isinstance(v, tuple | list):
        return [_jsonable(x) for x in v]
    return v


def params_to_json(params: Any) -> dict[str, Any]:
    return {f.name: _jsonable(getattr(params, f.name)) for f in dataclasses.fields(params)}


def sellable_qty(ctx: StrategyContext, symbol: str) -> Decimal:
    """Inventory that an exit order could actually sell: floored to the lot step and large enough
    for the minimum quantity/notional. Fee remainders ("dust") below these limits cannot be sold on
    an exchange and stay in the portfolio as unrealised inventory; strategies must not retry them."""
    pos = ctx.position(symbol)
    rules = ctx.rules[symbol]
    qty = floor_to_step(pos.qty, rules.step_size)
    if qty < rules.min_qty or pos.mark is None or qty * pos.mark < rules.min_notional:
        return Decimal(0)
    return qty


class Strategy(ABC):
    strategy_id: ClassVar[str]
    version: ClassVar[str]

    def __init__(self, params: Any) -> None:
        self.params = params

    @property
    def full_id(self) -> str:
        return f"{self.strategy_id}@{self.version}"

    def params_json(self) -> dict[str, Any]:
        return params_to_json(self.params)

    def on_start(self, ctx: StrategyContext) -> None:  # noqa: B027 - optional hook
        """Called once before the first bar (no bars delivered yet)."""

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> Sequence[Action]:
        """Called after each bar CLOSES. Orders become active from the next bar."""
