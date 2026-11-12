"""Baseline strategies. They run through the SAME engine, data, costs and accounting as every other
strategy; only their decision rules differ."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from asta.research.sim.context import StrategyContext
from asta.research.sim.orders import OrderIntent
from asta.research.strategies.base import Action, Strategy, sellable_qty
from asta.research.strategies.indicators import SMA
from asta.research.types import OrderType, Side


@dataclass(frozen=True)
class PassiveHoldParams:
    symbols: tuple[str, ...] = ("BTC-USDT",)
    notional_per_symbol: Decimal = Decimal(100)  # USDT bought once at the first bar


class PassiveHold(Strategy):
    """Buy once at the first available bar and hold to the end. With several symbols this is the
    equal-weight passive portfolio (equal `notional_per_symbol`). Never sells."""

    strategy_id: ClassVar[str] = "passive_hold"
    version: ClassVar[str] = "1.0.0"

    def __init__(self, params: PassiveHoldParams) -> None:
        super().__init__(params)
        self._done: set[str] = set()

    def on_bar(self, ctx: StrategyContext) -> Sequence[Action]:
        if ctx.is_warmup:
            return []
        out: list[Action] = []
        for sym in self.params.symbols:
            if sym in self._done or not ctx.has_bar(sym):
                continue
            self._done.add(sym)
            out.append(
                OrderIntent(
                    sym,
                    Side.BUY,
                    OrderType.MARKET,
                    quote_notional=self.params.notional_per_symbol,
                    client_id=f"hold:{sym}",
                    expire_after_bars=48,
                    tag="hold",
                )
            )
        return out


@dataclass(frozen=True)
class AllCashParams:
    pass


class AllCash(Strategy):
    """Never trades. Equity must equal the starting cash for the whole run."""

    strategy_id: ClassVar[str] = "all_cash"
    version: ClassVar[str] = "1.0.0"

    def __init__(self, params: AllCashParams | None = None) -> None:
        super().__init__(params or AllCashParams())

    def on_bar(self, ctx: StrategyContext) -> Sequence[Action]:
        return []


@dataclass(frozen=True)
class SmaCrossParams:
    symbols: tuple[str, ...] = ("BTC-USDT",)
    fast: int = 20
    slow: int = 50
    notional: Decimal = Decimal(90)


class SmaCross(Strategy):
    """Simple deterministic rules baseline: hold a long position while SMA(fast) > SMA(slow)."""

    strategy_id: ClassVar[str] = "sma_cross"
    version: ClassVar[str] = "1.0.0"

    def __init__(self, params: SmaCrossParams) -> None:
        super().__init__(params)
        if params.fast >= params.slow:
            raise ValueError("fast period must be shorter than slow period")
        self._fast = {s: SMA(params.fast) for s in params.symbols}
        self._slow = {s: SMA(params.slow) for s in params.symbols}
        self._n = 0

    def on_bar(self, ctx: StrategyContext) -> Sequence[Action]:
        out: list[Action] = []
        for sym in self.params.symbols:
            if not ctx.has_bar(sym):
                continue
            close = ctx.bars(sym)[-1].close
            f, s = self._fast[sym].update(close), self._slow[sym].update(close)
            if f is None or s is None or ctx.is_warmup:
                continue
            pos = sellable_qty(ctx, sym)
            busy = len(ctx.open_orders(sym)) > 0
            if f > s and pos == 0 and not busy and not ctx.risk.halted_new_buys:
                self._n += 1
                out.append(
                    OrderIntent(
                        sym,
                        Side.BUY,
                        OrderType.MARKET,
                        quote_notional=self.params.notional,
                        client_id=f"x:{sym}:{self._n}",
                        expire_after_bars=6,
                        tag="entry",
                    )
                )
            elif f <= s and pos > 0 and not busy:
                self._n += 1
                out.append(
                    OrderIntent(
                        sym,
                        Side.SELL,
                        OrderType.MARKET,
                        quantity=pos,
                        client_id=f"x:{sym}:{self._n}",
                        expire_after_bars=6,
                        tag="exit",
                    )
                )
        return out
