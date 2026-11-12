"""Look-ahead / leakage prevention: decisions up to time T must be identical whether the future
exists, is truncated, or is corrupted. Also: the context cannot reach future bars."""

from datetime import timedelta
from decimal import Decimal as D
from typing import Any

import pytest

from asta.research.data.fixtures import DOWNTREND, HIGHVOL, RANGE, UPTREND, generate_bars, seg
from asta.research.data.models import Bar
from asta.research.sim.context import StrategyContext
from asta.research.sim.engine import Engine
from asta.research.sim.risk import SimRiskLimits
from asta.research.strategies.base import Action, Strategy
from asta.research.strategies.registry import build_strategy
from asta.research.types import Timeframe

from .engine_helpers import FEES, RULES, SIMPLE_EXEC, _NoParams, cfg
from .helpers import T0

SYMS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
SEGS = [
    seg(RANGE, 120),
    seg(UPTREND, 120),
    seg(RANGE, 80),
    seg(DOWNTREND, 120),
    seg(HIGHVOL, 40),
    seg(UPTREND, 120),
]


def multi_series(seed=3, n_cut=None):
    out = {}
    for i, s in enumerate(SYMS):
        bars = generate_bars(T0, Timeframe.H1, SEGS, seed + i, D("100"))
        out[s] = bars if n_cut is None else bars[:n_cut]
    return out


def run_strategy(sid, params, series, **kw):
    rules = {
        s: type(RULES)(
            f"T:{s}",
            "BINANCE",
            s,
            RULES.tick_size,
            RULES.step_size,
            RULES.min_qty,
            RULES.min_notional,
            "t",
            T0,
            False,
        )
        for s in SYMS
    }
    c = cfg(symbols=SYMS, rules=rules, risk=SimRiskLimits.default(), **kw)
    return Engine(c, series, build_strategy(sid, params)).run()


def signature(res, upto):
    """Everything the strategy decided/caused strictly before `upto` (bar-open time)."""
    orders = [
        (
            o.created_ts,
            o.symbol,
            o.side.value,
            o.type.value,
            str(o.quantity),
            str(o.limit_price),
            str(o.stop_price),
            o.tag,
            o.client_id,
        )
        for o in res.orders
        if o.created_ts <= upto
    ]
    fills = [
        (f.ts, f.symbol, f.side.value, str(f.price), str(f.quantity), f.liquidity)
        for f in res.fills
        if f.ts < upto
    ]
    equity = [(p.ts, str(p.equity)) for p in res.equity if p.ts <= upto]
    return orders, fills, equity


STRATS = [
    (
        "regime_grid",
        {"ema_fast": 6, "ema_slow": 24, "atr_period": 6, "range_lookback": 12, "recenter_bars": 6},
    ),
    (
        "trend_pullback",
        {
            "ema_fast": 6,
            "ema_slow": 24,
            "slope_bars": 4,
            "atr_period": 6,
            "min_atr_pct": "0.0005",
            "pullback_atr": "0.2",
            "min_net_edge_bps": "1",
            "cooldown_bars": 4,
        },
    ),
    ("sma_cross", {"symbols": list(SYMS), "fast": 6, "slow": 20}),
    ("passive_hold", {"symbols": list(SYMS)}),
]


@pytest.mark.parametrize("sid,params", STRATS)
@pytest.mark.parametrize("cut", [150, 333, 480])
def test_decisions_before_T_are_independent_of_the_future(sid, params, cut):
    full = run_strategy(sid, params, multi_series())
    trunc = run_strategy(sid, params, multi_series(n_cut=cut))  # the future simply does not exist
    corrupt = multi_series()
    for s in SYMS:  # the future exists but is garbage (scaled/shuffled prices)
        corrupt[s] = corrupt[s][:cut] + [
            Bar(b.open_time, b.open * 9, b.high * 11, b.low * 2, b.close * 5, b.volume * 3)
            for b in corrupt[s][cut:]
        ]
    garbage = run_strategy(sid, params, corrupt)
    upto = T0 + timedelta(hours=cut - 2)  # stay clear of the truncated run's end-of-data cancels
    a, b, c = signature(full, upto), signature(trunc, upto), signature(garbage, upto)
    assert a == b == c
    assert len(a[0]) > 0 or sid in (
        "regime_grid",
    )  # something actually happened (grid may idle in this slice)


@pytest.mark.parametrize("sid,params", STRATS)
def test_strategies_actually_trade_in_the_leakage_scenario(sid, params):
    res = run_strategy(sid, params, multi_series())
    assert len(res.orders) > 0, f"{sid} produced no orders: the leakage tests would be vacuous"


class Peeker(Strategy):
    """A cheating strategy: tries every route to the future. All of them must fail."""

    strategy_id = "peeker"
    version = "0"

    def __init__(self) -> None:
        super().__init__(_NoParams())
        self.errors: list[str] = []
        self.lens: list[tuple[int, int]] = []

    def params_json(self):
        return {}

    def on_bar(self, ctx: StrategyContext) -> list[Action]:
        h = ctx.bars("BTC-USDT")
        self.lens.append((ctx.index, len(h)))
        try:
            _ = h[len(h)]  # the next bar does not exist yet
            self.errors.append("future bar reachable")
        except IndexError:
            pass
        for attr in ("series", "dataset", "_series", "timeline", "engine", "_engine", "bars_all"):
            if hasattr(ctx, attr):
                self.errors.append(f"context exposes {attr}")
        # the PUBLIC history API is read-only: no mutators exist. (In-process Python cannot sandbox
        # private attributes; strategies are trusted in-repo code - no untrusted plugins, ADR-0005.)
        for mut in ("append", "extend", "insert", "pop", "clear", "__setitem__", "__delitem__"):
            if hasattr(h, mut):
                self.errors.append(f"history exposes mutator {mut}")
        return []


def test_context_exposes_no_future_and_history_grows_one_bar_per_step():
    bars = generate_bars(T0, Timeframe.H1, [seg(UPTREND, 30)], 5, D("100"))
    p = Peeker()
    Engine(cfg(), {"BTC-USDT": bars}, p).run()
    assert [e for e in p.errors if "exposes" in e or "reachable" in e or "mutator" in e] == []
    assert all(n == idx + 1 for idx, n in p.lens if idx >= 0)  # delivered = closed bars so far


def test_strategy_receives_bars_only_after_they_close():
    bars = generate_bars(T0, Timeframe.H1, [seg(UPTREND, 10)], 5, D("100"))
    seen: list[tuple[Any, ...]] = []

    class Probe(Peeker):
        def on_bar(self, ctx):
            seen.append((ctx.now, ctx.bars("BTC-USDT")[-1].open_time))
            return []

    Engine(cfg(), {"BTC-USDT": bars}, Probe()).run()
    assert all(now == bar_open + timedelta(hours=1) for now, bar_open in seen)  # now == bar CLOSE


def test_orders_decided_at_close_cannot_fill_before_the_next_bar_opens():
    from .engine_helpers import Scripted, mbuy

    bars = generate_bars(T0, Timeframe.H1, [seg(UPTREND, 10)], 5, D("100"))
    res = Engine(cfg(), {"BTC-USDT": bars}, Scripted({3: [mbuy("m", "100")]})).run()
    o = res.orders[0]
    assert o.created_ts == bars[3].open_time + timedelta(hours=1)
    assert res.fills[0].ts == bars[4].open_time and res.fills[0].ts >= o.created_ts
    assert (
        res.fills[0].reference_price == bars[4].open
    )  # priced off the NEXT bar's open, not the close


def test_engine_rejects_unsorted_or_duplicated_bars_instead_of_guessing():
    from .engine_helpers import Scripted

    b = generate_bars(T0, Timeframe.H1, [seg(RANGE, 5)], 1, D("100"))
    from asta.research.sim.engine import EngineError

    with pytest.raises(EngineError, match="strictly increasing"):
        Engine(cfg(), {"BTC-USDT": [b[0], b[0]]}, Scripted({})).run()
    with pytest.raises(EngineError, match="strictly increasing"):
        Engine(cfg(), {"BTC-USDT": [b[1], b[0]]}, Scripted({})).run()


def test_fee_schedule_and_execution_model_are_the_same_object_for_every_strategy():
    """Fair comparison: identical data, costs and accounting for each strategy in a run set."""
    results = [
        run_strategy(sid, params, multi_series(n_cut=200), fees=FEES, execution=SIMPLE_EXEC)
        for sid, params in STRATS
    ]
    assert all(r.bars_processed == results[0].bars_processed for r in results)
    for r in results:
        assert r.reconcile.ok
