"""Risk controls enforced before simulated orders are accepted (ADR-0007)."""

from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from decimal import Decimal as D

import pytest

from asta.research.data.models import Bar
from asta.research.sim.engine import Engine
from asta.research.sim.risk import RiskEngine, SimRiskLimits
from asta.research.types import OrderStatus

from .engine_helpers import FEES, RULES, SIMPLE_EXEC, Scripted, cfg, mbuy, msell, run
from .helpers import T0, bar

DEFAULT = SimRiskLimits.default(D("1000"))


def status_of(res, cid):
    o = next(o for o in res.orders if o.client_id == cid)
    return o.status, o.reject_reason or o.events[-1].reason


def test_default_limits_come_from_the_m0_risk_policy_and_are_explicit():
    assert DEFAULT.max_asset == D("100.0") and DEFAULT.max_total == D("300.0")
    assert DEFAULT.daily_loss_pct == D("0.02") and DEFAULT.max_drawdown_pct == D("0.05")
    assert DEFAULT.label == "RISK_COMPLIANT"
    with pytest.raises(FrozenInstanceError):
        DEFAULT.max_asset_pct = D("1")  # type: ignore[misc]  # limits cannot change mid-run
    ref = SimRiskLimits.unconstrained_reference()
    assert ref.label == "RISK_UNCONSTRAINED_REFERENCE" and ref.daily_loss_pct is None


def test_limits_roundtrip_json_exactly():
    assert SimRiskLimits.from_json(DEFAULT.to_json()) == DEFAULT


def test_per_asset_cap_is_enforced_at_acceptance():
    bars = [bar(i, "100", "101", "99", "100") for i in range(4)]
    res = run(
        bars, {0: [mbuy("big", "150"), mbuy("ok", "90")], 1: [mbuy("more", "20")]}, risk=DEFAULT
    )
    assert status_of(res, "big") == (OrderStatus.REJECTED, "RISK_ASSET_EXPOSURE_CAP")  # 150 > 100
    assert status_of(res, "ok")[0] == OrderStatus.FILLED
    # 90 already invested (+ price drift) -> another 20 would exceed the 100 cap
    assert status_of(res, "more") == (OrderStatus.REJECTED, "RISK_ASSET_EXPOSURE_CAP")


def test_open_buy_orders_count_toward_exposure():
    bars = [bar(i, "100", "101", "99.5", "100") for i in range(4)]
    from .engine_helpers import lbuy

    res = run(
        bars, {0: [lbuy("a", "90", "1"), lbuy("b", "90", "0.2")]}, risk=DEFAULT
    )  # 90 + 18 > 100
    assert status_of(res, "a")[0] != OrderStatus.REJECTED
    assert status_of(res, "b") == (OrderStatus.REJECTED, "RISK_ASSET_EXPOSURE_CAP")


def test_total_exposure_cap_across_assets():
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
        for s in ("BTC-USDT", "ETH-USDT", "SOL-USDT")
    }
    limits = SimRiskLimits(D("1000"), D("0.2"), D("0.3"), None, None)  # 200 per asset, 300 total
    series: dict[str, Sequence[Bar]] = {
        s: [bar(i, "100", "101", "99", "100") for i in range(4)] for s in rules
    }
    script = {
        0: [mbuy("a", "200", "BTC-USDT"), mbuy("b", "200", "ETH-USDT"), mbuy("c", "90", "SOL-USDT")]
    }
    res = Engine(
        cfg(symbols=tuple(rules), rules=rules, risk=limits), series, Scripted(script)
    ).run()
    assert status_of(res, "a")[0] == OrderStatus.FILLED
    assert status_of(res, "b") == (OrderStatus.REJECTED, "RISK_TOTAL_EXPOSURE_CAP")  # 400 > 300
    assert status_of(res, "c")[0] == OrderStatus.FILLED  # 200 + 90 = 290 <= 300
    assert res.reconcile.ok


def test_daily_loss_breaker_stops_risk_increasing_orders_but_not_exits_then_resets():
    limits = SimRiskLimits(D("1000"), D("0.5"), D("0.5"), D("0.02"), None)
    bars = [
        bar(0, "100", "100", "100", "100"),
        bar(1, "100", "100", "95", "95"),
        bar(2, "95", "95", "95", "95"),
        bar(3, "95", "95", "95", "95"),
    ]
    # day 1: hours 0..23 ; day 2 starts at index 24
    bars += [bar(i, "95", "95", "95", "95") for i in range(4, 30)]
    script = {
        0: [mbuy("b1", "400")],  # fills bar1 @100.10, then price 95 -> -2 % equity
        1: [mbuy("blocked", "50")],  # decided after the trip, same UTC day
        2: [msell("exit", "2.0")],  # exits are never blocked
        26: [mbuy("next_day", "50")],  # new UTC day: breaker re-armed
    }
    res = run(bars, script, risk=limits)
    kinds = [e.kind for e in res.risk_events]
    assert "DAILY_LOSS_TRIP" in kinds and "DAILY_RESET" in kinds
    assert status_of(res, "blocked") == (OrderStatus.REJECTED, "RISK_DAILY_LOSS_TRIP")
    assert status_of(res, "exit")[0] == OrderStatus.FILLED
    assert status_of(res, "next_day")[0] == OrderStatus.FILLED
    assert res.risk_verdict == "BREACHED"  # a breach is reported even though the run completed
    assert res.status == "COMPLETED"


def test_trip_cancels_resting_buy_orders_but_keeps_the_exit_orders():
    from .engine_helpers import lbuy, lsell

    limits = SimRiskLimits(D("1000"), D("0.5"), D("0.5"), D("0.02"), None)
    bars = [bar(0, "100", "100", "100", "100"), bar(1, "100", "100", "95", "95")] + [
        bar(i, "95", "95", "95", "95") for i in range(2, 6)
    ]
    script = {0: [mbuy("b1", "400"), lbuy("rest", "50", "1")], 1: [lsell("exit", "200", "1")]}
    res = run(bars, script, risk=limits)
    assert status_of(res, "rest") == (OrderStatus.CANCELLED, "RISK_TRIP")
    assert status_of(res, "exit")[0] != OrderStatus.REJECTED
    assert res.ledger.reserved("USDT") == 0


def test_drawdown_halt_is_permanent_reported_failed_and_exits_still_work():
    limits = SimRiskLimits(D("1000"), D("0.5"), D("0.5"), None, D("0.05"))
    bars = [bar(0, "100", "100", "100", "100"), bar(1, "100", "100", "85", "85")] + [
        bar(i, "85", "85", "85", "85") for i in range(2, 60)
    ]
    script = {0: [mbuy("b1", "400")], 1: [msell("exit", "1")], 40: [mbuy("later", "50")]}
    res = run(bars, script, risk=limits)
    assert res.status == "STOPPED_RISK_HALT" and res.risk_verdict == "BREACHED"
    assert any(e.kind == "DRAWDOWN_HALT" for e in res.risk_events)
    assert status_of(res, "later") == (
        OrderStatus.REJECTED,
        "RISK_DRAWDOWN_HALT",
    )  # days later: still halted
    assert status_of(res, "exit")[0] == OrderStatus.FILLED


def test_strategy_sees_the_halt_flags():
    limits = SimRiskLimits(D("1000"), D("0.5"), D("0.5"), None, D("0.05"))
    bars = [
        bar(0, "100", "100", "100", "100"),
        bar(1, "100", "100", "85", "85"),
        bar(2, "85", "85", "85", "85"),
    ]
    s = Scripted({0: [mbuy("b1", "400")]})
    run(bars, {}, strategy=s, risk=limits)
    assert s.ctx_log[0].risk.drawdown_halted is False
    assert s.ctx_log[-1].risk.drawdown_halted and s.ctx_log[-1].risk.halted_new_buys


def test_risk_state_uses_marked_equity_including_unrealized_losses():
    eng = RiskEngine(SimRiskLimits(D("1000"), D("1"), D("1"), D("0.02"), D("0.05")))
    assert eng.on_equity(T0, D("1000")) == []
    assert eng.on_equity(T0, D("979")) == ["DAILY_LOSS_TRIP"]  # 2.1 % below session start
    assert eng.on_equity(T0, D("949")) == ["DRAWDOWN_HALT"]  # 5.1 % below peak 1000
    assert eng.check_buy(D(1), D(1)) == "RISK_DRAWDOWN_HALT"


def test_unconstrained_reference_never_blocks_buys():
    bars = [bar(i, "100", "101", "99", "100") for i in range(3)]
    res = run(bars, {0: [mbuy("all", "900")]}, risk=SimRiskLimits.unconstrained_reference())
    assert status_of(res, "all")[0] == OrderStatus.FILLED and res.risk_verdict == "PASSED"


def test_simulation_inputs_are_not_modified_by_a_run():
    bars = tuple(bar(i, "100", "101", "99", "100") for i in range(3))
    before = tuple(bars)
    run(bars, {0: [mbuy("m")]}, execution=SIMPLE_EXEC, fees=FEES)
    assert bars == before
