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

from asta.research.sim.context import StrategyContext
from asta.research.sim.orders import CancelIntent, OrderIntent

Action = OrderIntent | CancelIntent


def params_to_json(params: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in dataclasses.fields(params):
        v = getattr(params, f.name)
        out[f.name] = str(v) if isinstance(v, Decimal) else v
    return out


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
