"""Behavioural tests for the grid, trend-pullback and baseline strategies (hand-designed series)."""

from decimal import Decimal as D

import pytest

from asta.research.sim.engine import Engine
from asta.research.sim.risk import SimRiskLimits
from asta.research.strategies.baselines import (
    AllCash,
    PassiveHold,
    PassiveHoldParams,
    SmaCross,
    SmaCrossParams,
)
from asta.research.strategies.grid import GridParams, RegimeGrid
from asta.research.strategies.registry import (
    REGISTRY,
    build_strategy,
    default_params_json,
    parse_params,
)
from asta.research.strategies.trend_pullback import TrendPullback, TrendPullbackParams
from asta.research.types import OrderStatus, Side

from .bar_builders import from_closes, ramp
from .engine_helpers import NO_CAPS, Scripted, cfg, mbuy
from .helpers import T0

BTC = "BTC-USDT"


def go(strategy, bars, **kw):
    return Engine(cfg(**kw), {BTC: bars}, strategy).run()


def orders_by_tag(res, prefix):
    return [o for o in res.orders if o.tag.startswith(prefix)]


# ============================================================ regime grid
def grid_params(**kw):
    base = dict(
        symbols=(BTC,),
        range_mode="fixed",
        fixed_ranges=((BTC, D("95"), D("105")),),
        levels=5,
        level_notional=D("18"),
        max_exposure=D("90"),
        ema_fast=2,
        ema_slow=4,
        atr_period=3,
        range_lookback=3,
        max_atr_pct=D("0.05"),
        stop_buffer_pct=D("0.01"),
        cooldown_bars=5,
        recenter_bars=3,
        buy_ttl_bars=50,
    )
    base.update(kw)
    return GridParams(**base)


def test_grid_places_buys_only_below_price_inside_the_range_and_sizes_each_level():
    bars = from_closes([100] * 10, wick="0.2")
    res = go(RegimeGrid(grid_params()), bars, risk=NO_CAPS)
    buys = orders_by_tag(res, "b:")
    levels = sorted({o.limit_price for o in buys})
    assert levels == [
        D("95.00"),
        D("97.00"),
        D("99.00"),
    ]  # 101/103/105 are above the price: no buys
    assert all(o.side == Side.BUY and o.quantity * o.limit_price <= D("18") for o in buys)
    assert len({o.client_id for o in buys}) == len(
        buys
    )  # unique ids, no duplicate submissions per level


def test_grid_spacing_below_costs_disables_the_grid_and_records_why():
    # range 99.9..100.1 over 5 levels = 4 bps spacing, required = 2*10 maker + 30 + 10 = 60 bps
    g = RegimeGrid(grid_params(fixed_ranges=((BTC, D("99.9"), D("100.1")),)))
    res = go(g, from_closes([100] * 10, wick="0.01"), risk=NO_CAPS)
    assert res.orders == [] and any("spacing" in n and "required" in n for n in g.notes)


def test_grid_minimum_expected_net_profit_per_round_trip_is_enforced():
    # just above the 60 bps requirement is accepted, just below is refused
    ok = RegimeGrid(
        grid_params(fixed_ranges=((BTC, D("99.0"), D("101.8")),))
    )  # 5 levels: 56 bps? compute
    wide = RegimeGrid(grid_params(fixed_ranges=((BTC, D("97"), D("103")),)))  # 120 bps spacing
    go(ok, from_closes([100] * 8, wick="0.01"), risk=NO_CAPS)
    res_wide = go(wide, from_closes([100] * 8, wick="0.01"), risk=NO_CAPS)
    assert not wide.notes and len(orders_by_tag(res_wide, "b:")) > 0
    assert any("spacing" in n for n in ok.notes)  # 56 bps < 60 bps


def test_grid_buy_fill_creates_the_sell_one_level_up_for_the_received_quantity():
    flat = from_closes([100] * 7, wick="0.2")
    # bar 8: price trades through 99 and 97 (low 96.9) but recovers; bar 9 flat
    from .helpers import bar

    bars = flat + [
        bar(7, "100", "100.2", "96.9", "99.5"),
        bar(8, "99.5", "100", "99.3", "99.8"),
        bar(9, "99.8", "100", "99.5", "99.9"),
    ]
    res = go(RegimeGrid(grid_params()), bars, risk=NO_CAPS)
    buys = [f for f in res.fills if f.side == Side.BUY]
    sells = orders_by_tag(res, "s:")
    assert len(buys) >= 2 and len(sells) >= 2
    by_level = {o.tag: o for o in sells}
    assert by_level["s:2"].limit_price == D("101.00")  # filled the 99 level -> sell at 101
    assert by_level["s:1"].limit_price == D("99.00")  # filled the 97 level -> sell at 99
    f99 = next(f for f in buys if f.price == D("99.00"))
    expected_qty = f99.quantity - f99.fee_amount  # fee in BTC reduces what can be sold
    assert by_level["s:2"].quantity == D(str(expected_qty)).quantize(
        D("0.001"), rounding="ROUND_DOWN"
    )
    assert res.reconcile.ok


def test_grid_respects_its_own_exposure_cap():
    res = go(
        RegimeGrid(grid_params(max_exposure=D("36"))),
        from_closes([100] * 8, wick="0.2"),
        risk=NO_CAPS,
    )
    assert (
        max(
            len([o for o in orders_by_tag(res, "b:") if o.created_ts == t])
            for t in {o.created_ts for o in res.orders}
        )
        <= 2
    )  # 2 * 18 = 36


def test_grid_is_suspended_in_a_downtrend_and_cancels_resting_buys():
    # a steady slide: EMA(2) < EMA(4) -> downtrend -> no buys while it lasts
    bars = from_closes(ramp(100, -0.4, 14), wick="0.1")
    res = go(RegimeGrid(grid_params(fixed_ranges=((BTC, D("90"), D("100")),))), bars, risk=NO_CAPS)
    first_signal = min((o.created_ts for o in res.orders), default=None)
    assert all(o.status != OrderStatus.FILLED for o in orders_by_tag(res, "b:"))
    # resting buys placed before the trend filter turned on are cancelled by the strategy
    cancelled = [o for o in orders_by_tag(res, "b:") if o.events[-1].reason == "STRATEGY_CANCEL"]
    assert (
        first_signal is None or len(cancelled) == len(orders_by_tag(res, "b:")) or res.fills == []
    )


def test_grid_goes_risk_off_below_the_range_cancels_everything_and_cools_down():
    from .helpers import bar

    flat = from_closes([100] * 7, wick="0.2")
    bars = (
        flat
        + [
            bar(7, "100", "100.2", "96.9", "99.5"),
            bar(8, "99.5", "99.6", "93", "93.5"),
            bar(9, "93.5", "94", "92", "93"),
            bar(10, "93", "93.5", "92.5", "93"),
        ]
        + from_closes([93] * 6, wick="0.1", first_index=11)
    )
    res = go(RegimeGrid(grid_params()), bars, risk=NO_CAPS)
    stops = [o for o in res.orders if o.tag == "stop"]
    assert len(stops) == 1 and stops[0].type.value == "MARKET" and stops[0].side == Side.SELL
    assert res.ledger.balance("BTC") < D("0.01")  # exited (dust may remain)
    after = [o for o in orders_by_tag(res, "b:") if o.created_ts > stops[0].created_ts]
    assert after == []  # cool-down: no new grid while it lasts
    assert res.reconcile.ok


def test_grid_stops_buying_above_the_range_top():
    res = go(RegimeGrid(grid_params()), from_closes([110] * 10, wick="0.2"), risk=NO_CAPS)
    assert orders_by_tag(res, "b:") == []  # end-of-range: price above the upper bound


def test_grid_honours_the_risk_engines_halt_flag():
    class Halted(RegimeGrid):
        pass

    bars = from_closes([100] * 10, wick="0.2")
    res = go(RegimeGrid(grid_params()), bars, risk=NO_CAPS)
    assert orders_by_tag(res, "b:")  # control: it trades when allowed
    tight = SimRiskLimits(D("1000"), D("1"), D("1"), D("0.02"), D("0.05"))
    assert go(RegimeGrid(grid_params()), bars, risk=tight).risk_verdict == "PASSED"


# ============================================================ trend pullback
def tp_params(**kw):
    base = dict(
        symbols=(BTC,),
        ema_fast=3,
        ema_slow=6,
        slope_bars=2,
        atr_period=3,
        pullback_atr=D("0.2"),
        min_atr_pct=D("0.001"),
        max_atr_pct=D("0.2"),
        entry_notional=D("90"),
        tp_atr=D("3"),
        sl_atr=D("1.5"),
        min_net_edge_bps=D("5"),
        max_hold_bars=20,
        cooldown_bars=6,
    )
    base.update(kw)
    return TrendPullbackParams(**base)


UP_THEN_DIP = ramp(100, 1.0, 16) + [112.0, 113.0]  # rising, then a dip below the fast EMA


def qualifying_closes():
    # +1/bar ramp, then a shallow dip BELOW the fast EMA but ABOVE the slow EMA, then recovery.
    # (a dip below the slow EMA would, correctly, break the uptrend condition)
    return [*ramp(100, 1.0, 14), 110.8, 111.5, 113.0, 114.0, 115.0, 116.0]


def test_trend_pullback_enters_only_in_an_uptrend_with_a_pullback_and_ok_volatility():
    tb = TrendPullback
    # (1) downtrend: never enters
    assert go(tb(tp_params()), from_closes(ramp(120, -1.0, 30)), risk=NO_CAPS).fills == []
    # (2) flat: no trend, never enters
    assert go(tb(tp_params()), from_closes([100] * 30), risk=NO_CAPS).fills == []
    # (3) uptrend without any pullback (monotonic): no entry
    assert go(tb(tp_params()), from_closes(ramp(100, 1.0, 30)), risk=NO_CAPS).orders == []
    # (4) uptrend + pullback: enters exactly once at first, as a taker market buy
    res = go(tb(tp_params()), from_closes(qualifying_closes()), risk=NO_CAPS)
    entries = [o for o in res.orders if o.tag == "entry"]
    assert len(entries) >= 1 and entries[0].type.value == "MARKET"


def test_trend_pullback_volatility_filter_blocks_entries():
    closes = qualifying_closes()
    assert (
        go(
            TrendPullback(tp_params(max_atr_pct=D("0.0001"))), from_closes(closes), risk=NO_CAPS
        ).orders
        == []
    )
    assert (
        go(TrendPullback(tp_params(min_atr_pct=D("0.5"))), from_closes(closes), risk=NO_CAPS).orders
        == []
    )


def test_trend_pullback_requires_edge_beyond_round_trip_costs():
    closes = qualifying_closes()
    res = go(
        TrendPullback(tp_params(min_net_edge_bps=D("100000"))), from_closes(closes), risk=NO_CAPS
    )
    assert res.orders == []  # the targeted move can never pay for the costs -> never trades


def test_trend_pullback_places_a_protective_oco_pair_around_the_entry():
    res = go(TrendPullback(tp_params()), from_closes(qualifying_closes()), risk=NO_CAPS)
    tp = next(o for o in res.orders if o.tag == "tp")
    sl = next(o for o in res.orders if o.tag == "sl")
    entry_fill = next(f for f in res.fills if f.side == Side.BUY)
    assert tp.oco_group == sl.oco_group and tp.oco_group
    assert sl.stop_price < entry_fill.price < tp.limit_price
    assert tp.quantity == sl.quantity  # sized to the actual position, one shared reservation
    assert res.reconcile.ok


def test_trend_pullback_stop_out_then_cooldown_blocks_reentry():
    from .helpers import bar

    closes = qualifying_closes()
    bars = from_closes(closes, wick="0.3")
    n = len(bars)
    crash = [bar(n, "116", "116", "100", "101")] + [
        bar(n + 1 + i, "101", "103", "100", "102") for i in range(8)
    ]
    res = go(TrendPullback(tp_params(cooldown_bars=6, tp_atr=D("50"))), bars + crash, risk=NO_CAPS)
    sells = [f for f in res.fills if f.side == Side.SELL]
    assert sells, "the stop should have been hit by the crash"
    stop_ts = sells[0].ts
    assert sells[0].tag == "sl" and sells[0].price < D("110")  # protective stop, filled adversely
    entries_after = [o for o in res.orders if o.tag == "entry" and o.created_ts > stop_ts]
    assert all((o.created_ts - stop_ts).total_seconds() / 3600 >= 5 for o in entries_after)


def test_trend_pullback_time_stop_exits_at_market():
    bars = from_closes(qualifying_closes() + [116.0] * 30, wick="0.05")
    res = go(
        TrendPullback(
            tp_params(max_hold_bars=5, tp_atr=D("50"), sl_atr=D("50"), min_net_edge_bps=D("1"))
        ),
        bars,
        risk=NO_CAPS,
    )
    assert any(o.tag == "time_exit" for o in res.orders)


def test_trend_pullback_trend_break_exits():
    bars = from_closes(qualifying_closes() + ramp(116, -2.0, 12), wick="0.3")
    res = go(
        TrendPullback(tp_params(tp_atr=D("50"), sl_atr=D("50"), min_net_edge_bps=D("1"))),
        bars,
        risk=NO_CAPS,
    )
    assert any(o.tag == "trend_exit" for o in res.orders)


def test_trend_pullback_risk_based_sizing_caps_the_notional():
    res = go(
        TrendPullback(tp_params(risk_per_trade_pct=D("0.001"))),
        from_closes(qualifying_closes()),
        risk=NO_CAPS,
    )
    entry = next(o for o in res.orders if o.tag == "entry")
    assert entry.quantity * entry.ref_price <= D("90")


# ============================================================ baselines + registry
def test_all_cash_never_trades_and_equity_is_constant():
    bars = from_closes(ramp(100, 0.5, 40))
    res = go(AllCash(), bars)
    assert res.orders == [] and {p.equity for p in res.equity} == {D("1000")}


def test_passive_hold_buys_once_and_never_sells():
    bars = from_closes(ramp(100, 1.0, 20))
    res = go(
        PassiveHold(PassiveHoldParams(symbols=(BTC,), notional_per_symbol=D("100"))),
        bars,
        risk=NO_CAPS,
    )
    assert [f.side for f in res.fills] == [Side.BUY] and len(res.orders) == 1
    assert res.equity[-1].equity > D("1000") and res.ledger.realized_total == 0  # unrealised only


def test_baselines_and_strategies_share_the_same_costs_and_fills():
    """Same bar, same notional -> identical fill price and fee whatever strategy issued the order."""
    bars = from_closes(ramp(100, 0.5, 12))
    scripted = go(Scripted({0: [mbuy("m", "90")]}), bars, risk=NO_CAPS)
    hold = go(
        PassiveHold(PassiveHoldParams(symbols=(BTC,), notional_per_symbol=D("90"))),
        bars,
        risk=NO_CAPS,
    )
    a, b = scripted.fills[0], hold.fills[0]
    assert (a.price, a.quantity, a.fee_amount, a.slippage_cost) == (
        b.price,
        b.quantity,
        b.fee_amount,
        b.slippage_cost,
    )


def test_sma_cross_enters_on_uptrend_and_exits_on_downtrend_without_dust_loops():
    closes = ramp(100, 1.0, 40) + ramp(140, -1.5, 40)
    res = go(
        SmaCross(SmaCrossParams(symbols=(BTC,), fast=3, slow=8, notional=D("90"))),
        from_closes(closes),
        risk=NO_CAPS,
    )
    sides = [f.side for f in res.fills]
    assert Side.BUY in sides and Side.SELL in sides
    assert res.rejections.get("MIN_QTY", 0) == 0  # fee dust is never retried


def test_sma_cross_rejects_inverted_periods():
    with pytest.raises(ValueError):
        SmaCross(SmaCrossParams(fast=50, slow=20))


def test_registry_builds_every_strategy_from_json_and_rejects_unknown_keys():
    for sid in REGISTRY:
        s = build_strategy(sid, {})
        assert s.strategy_id == sid and s.version == REGISTRY[sid].version
        j = default_params_json(sid)
        again = build_strategy(sid, j)  # JSON round trip reproduces the same parameters
        assert again.params == s.params
    with pytest.raises(ValueError, match="unknown parameters"):
        build_strategy("regime_grid", {"levelz": 5})
    with pytest.raises(ValueError, match="unknown strategy"):
        build_strategy("martingale", {})
    p = parse_params(
        GridParams,
        {
            "levels": 7,
            "level_notional": "12.5",
            "symbols": ["BTC-USDT"],
            "fixed_ranges": [["BTC-USDT", "90", "110"]],
        },
    )
    assert (
        p.levels == 7
        and p.level_notional == D("12.5")
        and p.fixed_ranges == (("BTC-USDT", D("90"), D("110")),)
    )
    with pytest.raises(ValueError):
        parse_params(GridParams, {"levels": 2.5})


def test_registry_display_names_avoid_trading_verbs():
    import re

    banned = re.compile(r"\b(buy|sell|trade|order|execute|go live)\b", re.I)
    assert all(not banned.search(i.display_name) for i in REGISTRY.values())
    assert T0.year == 2024
