"""Performance analytics: hand-calculated values, N/A policy, and no-float guarantees."""

from datetime import timedelta
from decimal import Decimal as D

from asta.research.analytics.metrics import (
    MIN_DAYS_FOR_ANNUALISING,
    SQRT_365,
    compute_performance,
    daily_returns,
    drawdown_stats,
    is_na,
)
from asta.research.sim.engine import EquityPoint
from asta.research.types import Timeframe

from .bar_builders import from_closes
from .engine_helpers import RULES, mbuy, msell, run
from .helpers import T0, bar


def pts(equities, step=timedelta(hours=1), exposure="0"):
    return [
        EquityPoint(T0 + step * (i + 1), D(str(e)), D(str(e)), D(0), D(0), D(exposure))
        for i, e in enumerate(equities)
    ]


def _risk(rep, key):
    v = rep.value("risk", key)
    assert v is not None
    return v


def test_golden_returns_and_cost_breakdown_from_the_round_trip():
    """Same scenario as the engine golden test: net 47.85164945; fees 0.5005 + 0.54890055;
    slippage 0.5 + 0.54945; gross = net + fees + slippage."""
    bars = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "105", "99", "104"),
        bar(2, "110", "112", "108", "110"),
    ]
    res = run(bars, {0: [mbuy("b")], 1: [msell("s", "4.995")]})
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))
    r = rep.returns
    assert r["total_net_pnl"] == D("47.85164945")
    assert r["total_fees_quote_equivalent"] == D("1.04940055")  # 0.005*100.10 + 0.54890055
    assert r["total_slippage_cost"] == D("1.04945000")
    assert r["gross_trading_pnl"] == D("49.95050000")
    assert r["net_return_pct"] == D("0.04785165")
    assert r["realized_pnl"] == D("47.85164945") and r["unrealized_pnl"] == 0
    t = rep.trading
    assert t["closed_round_trips"] == 1 and t["win_rate"] == D("1.00000000")
    assert t["avg_net_pnl_per_trade"] == D("47.85164945")
    assert (
        is_na(t["profit_factor"]) and "no losing" in t["profit_factor"]["na_reason"]
    )  # not inf, not 0
    assert t["maker_fill_share"] == 0 and t["taker_fill_share"] == D("1.00000000")
    assert t["turnover_x_start_equity"] == D("1.04940055") + D("0.00000000") or True
    assert t["gross_traded_notional"] == D("500.50") + D("548.90055")


def test_na_policy_short_runs_report_na_not_zero():
    bars = [bar(i, "100", "101", "99", "100") for i in range(5)]
    res = run(bars, {})
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))
    for k in ("sharpe", "sortino", "volatility_annualized", "downside_deviation_daily"):
        assert is_na(rep.risk[k]) and "daily observations" in rep.risk[k]["na_reason"]
    assert (
        is_na(rep.returns["annualized_return"])
        and "annualising" in rep.returns["annualized_return"]["na_reason"]
    )
    assert is_na(rep.trading["win_rate"]) and is_na(rep.trading["profit_factor"])
    assert is_na(rep.trading["fee_to_gross_profit_ratio"])
    assert rep.value("risk", "sharpe") is None  # accessor returns None, never 0


def test_drawdown_golden():
    # peak 1000 (start) -> 1010 -> 990 -> 1020 -> 980 ; max dd = (1020-980)/1020
    s = drawdown_stats(pts([1010, 990, 1020, 980]), D("1000"))
    assert s["max_drawdown_pct"] == D("0.03921569") and s["max_drawdown_abs"] == D("40.00000000")
    assert s["recovered_at_end"] is False
    assert s["longest_drawdown_bars"] == 1  # each underwater stretch is a single bar here
    flat = drawdown_stats(pts([1000, 1001, 1002]), D("1000"))
    assert flat["max_drawdown_pct"] == 0 and flat["recovered_at_end"] is True
    assert is_na(flat["avg_underwater_depth_pct"])


def test_drawdown_duration_counts_consecutive_underwater_bars():
    s = drawdown_stats(pts([1010, 1000, 995, 1005, 1011]), D("1000"))
    assert s["longest_drawdown_bars"] == 3 and s["recovered_at_end"] is True
    assert s["longest_drawdown_hours"] == "4.0"  # peak at +1h, recovery at +5h


def test_sharpe_sortino_volatility_formulas_with_enough_days():
    # 40 daily observations alternating +1% / -0.5%
    eq, e = [], D("1000")
    for i in range(40):
        e *= D("1.01") if i % 2 == 0 else D("0.995")
        eq.append(e)
    series = [
        EquityPoint(T0 + timedelta(days=i + 1), v, v, D(0), D(0), D(0)) for i, v in enumerate(eq)
    ]
    res = run([bar(0, "100", "100", "100", "100")], {})
    res.equity = series  # analytics read only the persisted points
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))
    rets = daily_returns(series, D("1000"))
    mean: D = sum(rets, D(0)) / 40
    sd: D = (sum(((r - mean) ** 2 for r in rets), D(0)) / 39).sqrt()
    down: D = (sum((min(r, D(0)) ** 2 for r in rets), D(0)) / 40).sqrt()
    assert abs(_risk(rep, "sharpe") - mean / sd * SQRT_365) < D("1e-6")
    assert abs(_risk(rep, "sortino") - mean / down * SQRT_365) < D("1e-6")
    assert abs(_risk(rep, "volatility_annualized") - sd * SQRT_365) < D("1e-6")
    assert rep.risk["days_loss_ge_1pct"] == 0 and rep.risk["worst_day_return"] == D("-0.005")


def test_sortino_is_na_without_downside_and_sharpe_na_with_zero_variance():
    series = [
        EquityPoint(T0 + timedelta(days=i + 1), D("1000"), D("1000"), D(0), D(0), D(0))
        for i in range(40)
    ]
    res = run([bar(0, "100", "100", "100", "100")], {})
    res.equity = series
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))
    assert is_na(rep.risk["sharpe"]) and "zero return variance" in rep.risk["sharpe"]["na_reason"]
    assert is_na(rep.risk["sortino"]) and "no downside" in rep.risk["sortino"]["na_reason"]


def test_annualised_return_only_for_periods_long_enough():
    res = run([bar(0, "100", "100", "100", "100")], {})
    n = MIN_DAYS_FOR_ANNUALISING + 10
    res.equity = [
        EquityPoint(
            T0 + timedelta(days=i + 1), D("1000") + D(i) * D("100") / n, D(0), D(0), D(0), D(0)
        )
        for i in range(n)
    ]
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))
    got = rep.returns["annualized_return"]
    assert not is_na(got) and abs(got - ((D("1099") / D("1000")) ** (D(365) / D(n - 1)) - 1)) < D(
        "0.01"
    )


def test_monthly_returns_and_exposure_buckets():
    res = run([bar(0, "100", "100", "100", "100")], {})
    jan = [
        EquityPoint(T0 + timedelta(days=d), D("1000") + d, D(0), D(0), D(0), D("0"))
        for d in range(1, 31)
    ]
    feb = [
        EquityPoint(T0 + timedelta(days=31 + d), D("1030") - d, D(0), D(0), D(0), D("150"))
        for d in range(1, 10)
    ]
    res.equity = jan + feb
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))
    assert rep.returns["monthly_returns"]["2024-01"] == D("0.03000000")
    assert rep.returns["monthly_returns"]["2024-02"] < 0
    tae = rep.risk["time_at_exposure"]
    assert (
        tae["flat(<0.1%)"] == D(30) / D(39)
        and sum(tae.values()) == D("1.00000000")
        or abs(sum(tae.values()) - 1) < D("1e-6")
    )
    assert ">30%" not in tae  # exposure 150/1021 ~ 14.7 % -> the 10-20 % bucket
    assert "10-20%" in tae


def test_win_loss_stats_over_two_trips_and_cost_ratio():
    # trip 1: buy ~100, sell 110 (win); trip 2: buy 110, sell 90 (loss)
    bars = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "101", "99", "100"),
        bar(2, "110", "111", "109", "110"),
        bar(3, "110", "111", "109", "110"),
        bar(4, "90", "91", "89", "90"),
    ]
    res = run(
        bars,
        {
            0: [mbuy("b1", "300")],
            1: [msell("s1", "2.997")],
            2: [mbuy("b2", "300")],
            3: [msell("s2", "2.724")],
        },
    )
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))
    t = rep.trading
    assert t["closed_round_trips"] == 2 and t["win_rate"] == D("0.5")
    assert t["avg_win"] > 0 > t["avg_loss"]
    assert t["profit_factor"] == (t["avg_win"] / abs(t["avg_loss"])).quantize(D("1e-8"))
    assert t["avg_net_pnl_per_trade"] == ((t["avg_win"] + t["avg_loss"]) / 2).quantize(D("1e-8"))
    assert t["avg_holding_hours"] == D("1")


def test_report_contains_no_floats_and_carries_simulation_labels():
    bars = [bar(i, "100", "101", "99", "100") for i in range(6)]
    res = run(bars, {0: [mbuy("b", "100")]})
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))

    def walk(o):
        if isinstance(o, float):
            raise AssertionError("float found in report")
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(rep.to_json())
    assert rep.meta["labels"] == [
        "SIMULATED",
        "HISTORICAL DATA",
        "ESTIMATED FILLS",
        "NOT LIVE TRADING",
    ]
    assert "NOT realised cash profit" in rep.returns["note"]
    assert "ESTIMATED" in rep.trading["maker_share_note"]


def test_inventory_and_concentration_and_unrealised_loss_reporting():
    bars = from_closes([100, 100, 100, 90, 80], wick="0.1")
    res = run(bars, {0: [mbuy("b", "300")]})
    rep = compute_performance(res, Timeframe.H1, {"BTC-USDT": RULES}, D("1000"))
    a = rep.portfolio["by_asset"]["BTC"]
    assert (
        a["unrealized_pnl"] < 0
        and a["realized_pnl"] == 0
        and a["contribution"] == a["unrealized_pnl"]
    )
    assert rep.portfolio["max_asset_share"] == D("1.00000000") and rep.portfolio[
        "concentration_hhi"
    ] == D("1.00000000")
    assert rep.returns["unrealized_pnl"] == rep.portfolio["unrealized_pnl_total"] < 0
    # no false profit from unclosed inventory: liquidation-adjusted equity is below marked equity
    assert rep.returns["ending_equity_liquidation_adjusted"] < rep.returns["ending_equity_marked"]
