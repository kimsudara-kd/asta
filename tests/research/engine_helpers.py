"""Engine test helpers: scripted strategy + easy-to-calculate configuration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal as D
from typing import Any

from asta.research.data.models import Bar
from asta.research.data.profiles import FeeAssetPolicy, FeeSchedule, RuleProfile
from asta.research.sim.context import StrategyContext
from asta.research.sim.engine import Engine, EngineConfig, RunResult
from asta.research.sim.execution import ExecutionModel
from asta.research.sim.orders import CancelIntent, OrderIntent
from asta.research.sim.risk import SimRiskLimits
from asta.research.strategies.base import Action, Strategy
from asta.research.types import OrderType, Side, Timeframe

from .helpers import T0

RULES = RuleProfile(
    "TEST:BTC-USDT",
    "BINANCE",
    "BTC-USDT",
    D("0.01"),
    D("0.001"),
    D("0.001"),
    D("5"),
    "unit-test",
    T0,
    False,
)
FEES = FeeSchedule(
    "TEST-FEES", "BINANCE", D("10"), D("10"), FeeAssetPolicy.RECEIVED_ASSET, "unit-test", False
)
# easy-to-calculate model: 10 bps slippage, no impact, volume cap not binding
SIMPLE_EXEC = ExecutionModel(
    "test-exec", D("10"), D("0"), D("0"), D("1"), D("1"), 1, D("2"), D("150")
)


# exposure caps far above any test order so cash/inventory checks are what is exercised
NO_CAPS = SimRiskLimits(D("1000"), D("1000"), D("1000"), None, None, "TEST_NO_CAPS")


class _NoParams:
    pass


class Scripted(Strategy):
    """Emits pre-programmed actions keyed by timeline index (the bar that just closed)."""

    strategy_id = "scripted"
    version = "0"

    def __init__(self, script: Mapping[int, Sequence[Action]]) -> None:
        super().__init__(_NoParams())
        self.script = script
        self.ctx_log: list[StrategyContext] = []

    def params_json(self) -> dict[str, Any]:
        return {}

    def on_bar(self, ctx: StrategyContext) -> Sequence[Action]:
        self.ctx_log.append(ctx)
        return self.script.get(ctx.index, [])


def cfg(**kw: Any) -> EngineConfig:
    base: dict[str, Any] = {
        "symbols": ("BTC-USDT",),
        "timeframe": Timeframe.H1,
        "starting_cash": D("1000"),
        "quote_asset": "USDT",
        "rules": {"BTC-USDT": RULES},
        "fees": FEES,
        "execution": SIMPLE_EXEC,
        "risk": NO_CAPS,
        "checkpoint_every": 0,
    }
    base.update(kw)
    return EngineConfig(**base)


def run(bars: Sequence[Bar], script: Mapping[int, Sequence[Action]], **kw: Any) -> RunResult:
    strategy = kw.pop("strategy", None) or Scripted(script)
    return Engine(cfg(**kw), {"BTC-USDT": bars}, strategy).run()


def mbuy(cid: str, notional: str = "500", sym: str = "BTC-USDT", **k: Any) -> OrderIntent:
    return OrderIntent(
        sym, Side.BUY, OrderType.MARKET, quote_notional=D(notional), client_id=cid, **k
    )


def mbuy_qty(cid: str, qty: str, sym: str = "BTC-USDT", **k: Any) -> OrderIntent:
    return OrderIntent(sym, Side.BUY, OrderType.MARKET, quantity=D(qty), client_id=cid, **k)


def msell(cid: str, qty: str, sym: str = "BTC-USDT", **k: Any) -> OrderIntent:
    return OrderIntent(sym, Side.SELL, OrderType.MARKET, quantity=D(qty), client_id=cid, **k)


def lbuy(cid: str, price: str, qty: str, sym: str = "BTC-USDT", **k: Any) -> OrderIntent:
    return OrderIntent(
        sym, Side.BUY, OrderType.LIMIT, quantity=D(qty), limit_price=D(price), client_id=cid, **k
    )


def lsell(cid: str, price: str, qty: str, sym: str = "BTC-USDT", **k: Any) -> OrderIntent:
    return OrderIntent(
        sym, Side.SELL, OrderType.LIMIT, quantity=D(qty), limit_price=D(price), client_id=cid, **k
    )


def stop(cid: str, price: str, qty: str, sym: str = "BTC-USDT", **k: Any) -> OrderIntent:
    return OrderIntent(
        sym,
        Side.SELL,
        OrderType.STOP_MARKET,
        quantity=D(qty),
        stop_price=D(price),
        client_id=cid,
        **k,
    )


def cancel(cid: str) -> CancelIntent:
    return CancelIntent(cid)
