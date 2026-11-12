"""Read-only views handed to strategies. A strategy can only see bars that have already closed."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from asta.research.data.models import Bar
from asta.research.data.profiles import FeeSchedule, RuleProfile
from asta.research.types import OrderStatus, OrderType, Side, Timeframe


class BarHistory:
    """Delivered bars for one symbol, oldest first. The engine appends only CLOSED bars; there is
    no way to reach a bar that has not been delivered yet."""

    __slots__ = ("_bars",)

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def __len__(self) -> int:
        return len(self._bars)

    def __getitem__(self, i: int) -> Bar:
        return self._bars[i]

    def __iter__(self) -> Iterator[Bar]:
        return iter(tuple(self._bars))

    def last(self, n: int) -> tuple[Bar, ...]:
        return tuple(self._bars[-n:]) if n > 0 else ()


@dataclass(frozen=True)
class PositionView:
    symbol: str
    qty: Decimal
    cost_total: Decimal
    avg_cost: Decimal | None
    mark: Decimal | None
    market_value: Decimal


@dataclass(frozen=True)
class OrderView:
    order_id: int
    client_id: str | None
    symbol: str
    side: Side
    type: OrderType
    quantity: Decimal
    remaining: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    status: OrderStatus
    tag: str


@dataclass(frozen=True)
class FillView:
    order_id: int
    client_id: str | None
    symbol: str
    side: Side
    price: Decimal
    quantity: Decimal
    liquidity: str
    tag: str


@dataclass(frozen=True)
class RiskView:
    halted_new_buys: bool
    daily_tripped: bool
    drawdown_halted: bool


class StrategyContext:
    """Facade over the engine state. Constructed by the engine each bar; exposes no engine
    internals, no future data and no dataset-level statistics."""

    def __init__(
        self,
        now: datetime,
        index: int,
        is_warmup: bool,
        timeframe: Timeframe,
        symbols: Sequence[str],
        histories: dict[str, BarHistory],
        present: frozenset[str],
        rules: dict[str, RuleProfile],
        fees: FeeSchedule,
        equity: Decimal,
        cash_available: Decimal,
        positions: Callable[[str], PositionView],
        open_orders: Callable[[str], tuple[OrderView, ...]],
        exposure: Callable[[str], Decimal],
        total_exposure: Decimal,
        fills: tuple[FillView, ...],
        risk: RiskView,
        starting_equity: Decimal,
    ) -> None:
        self.now = now
        self.index = index
        self.is_warmup = is_warmup
        self.timeframe = timeframe
        self.symbols = tuple(symbols)
        self._hist = histories
        self.present = present
        self.rules = rules
        self.fees = fees
        self.equity = equity
        self.cash_available = cash_available
        self._positions = positions
        self._open_orders = open_orders
        self._exposure = exposure
        self.total_exposure = total_exposure
        self.fills = fills
        self.risk = risk
        self.starting_equity = starting_equity

    def bars(self, symbol: str) -> BarHistory:
        return self._hist[symbol]

    def has_bar(self, symbol: str) -> bool:
        """True if a bar for `symbol` closed at this timestamp (False during a data gap)."""
        return symbol in self.present

    def position(self, symbol: str) -> PositionView:
        return self._positions(symbol)

    def open_orders(self, symbol: str) -> tuple[OrderView, ...]:
        return self._open_orders(symbol)

    def exposure(self, symbol: str) -> Decimal:
        return self._exposure(symbol)
