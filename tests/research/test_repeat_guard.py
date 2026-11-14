"""Opt-in repeated-rejection guard: pure rules, engine behaviour, and proof that OFF changes nothing."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

from asta.paper.config import PaperSessionConfig
from asta.research.sim.engine import Engine
from asta.research.sim.orders import OrderIntent
from asta.research.sim.repeat_guard import HEADROOM_REASONS, RepeatRejectionGuard
from asta.research.types import OrderStatus
from tests.paper.helpers import make_cfg

from .engine_helpers import Scripted, cfg, mbuy
from .helpers import T0, flat_bars

KEY = ("BTC-USDT", "BUY", "MARKET", "big")
FP = (D(0), D(0), D(100), D(0), D(0))
CAP = "RISK_ASSET_EXPOSURE_CAP"


# ------------------------------------------------------------------ pure guard
def test_same_headroom_refusal_with_unchanged_state_is_suppressed_until_rearm():
    g = RepeatRejectionGuard(rearm_after=timedelta(hours=3))
    assert not g.should_suppress(KEY, FP, T0)  # nothing recorded yet
    g.record_rejection(KEY, CAP, FP, T0)
    assert g.should_suppress(KEY, FP, T0 + timedelta(hours=1))
    assert g.should_suppress(KEY, FP, T0 + timedelta(hours=2, minutes=59))
    assert not g.should_suppress(KEY, FP, T0 + timedelta(hours=3))  # prices alone may open headroom
    assert g.suppressed == 2


def test_any_change_of_discrete_state_rearms_immediately():
    g = RepeatRejectionGuard()
    g.record_rejection(KEY, "INSUFFICIENT_CASH", FP, T0)
    changed = (D(1), *FP[1:])  # a fill happened
    assert not g.should_suppress(KEY, changed, T0 + timedelta(minutes=5))


def test_only_headroom_reasons_are_ever_suppressed():
    assert HEADROOM_REASONS == {
        "RISK_ASSET_EXPOSURE_CAP",
        "RISK_TOTAL_EXPOSURE_CAP",
        "INSUFFICIENT_CASH",
    }
    g = RepeatRejectionGuard()
    for reason in ("RISK_DRAWDOWN_HALT", "RISK_DAILY_LOSS_TRIP", "MIN_NOTIONAL", "WARMUP", "X"):
        g.record_rejection(KEY, reason, FP, T0)
        assert not g.should_suppress(KEY, FP, T0 + timedelta(minutes=1)), reason


def test_acceptance_and_other_reasons_clear_the_memory():
    g = RepeatRejectionGuard()
    g.record_rejection(KEY, CAP, FP, T0)
    g.record_accepted(KEY)
    assert not g.should_suppress(KEY, FP, T0 + timedelta(minutes=1))
    g.record_rejection(KEY, CAP, FP, T0)
    g.record_rejection(KEY, "MIN_QTY", FP, T0)  # a different refusal replaces the headroom one
    assert not g.should_suppress(KEY, FP, T0 + timedelta(minutes=1))


def test_different_intent_keys_are_independent():
    g = RepeatRejectionGuard()
    g.record_rejection(KEY, CAP, FP, T0)
    other = ("BTC-USDT", "BUY", "MARKET", "small")
    assert not g.should_suppress(other, FP, T0 + timedelta(minutes=1))


# ------------------------------------------------------------------ engine
def _script(
    indices: Iterable[int], tag: str = "big", notional: str = "500"
) -> dict[int, list[OrderIntent]]:
    return {i: [mbuy(f"{tag}{i}", notional, tag=tag)] for i in indices}


def _run(script, suppress: bool, rearm_hours: int | None = None, **kw):
    bars = flat_bars(12)
    eng = Engine(
        cfg(starting_cash=D("100"), **kw),
        {"BTC-USDT": bars},
        Scripted(script),
        suppress_repeated_rejections=suppress,
    )
    if suppress and rearm_hours is not None:
        assert eng.repeat_guard is not None
        eng.repeat_guard.rearm_after = timedelta(hours=rearm_hours)
    return eng.run(), eng


def test_off_by_default_every_repeat_becomes_a_rejected_order():
    res, eng = _run(_script(range(1, 9)), suppress=False)
    assert eng.repeat_guard is None and "suppressed_repeat" not in res.counters
    assert len(res.orders) == 8
    assert all(o.status == OrderStatus.REJECTED for o in res.orders)
    assert {o.reject_reason for o in res.orders} == {"INSUFFICIENT_CASH"}


def test_on_drops_identical_repeats_and_re_evaluates_after_the_rearm_window():
    res, eng = _run(_script(range(1, 9)), suppress=True, rearm_hours=3)
    refused = [o for o in res.orders if o.status == OrderStatus.REJECTED]
    # attempts at bars 1..8: evaluated at 1, 4, 7; suppressed at 2,3,5,6,8
    assert [o.created_ts for o in refused] == [
        T0 + timedelta(hours=h) for h in (2, 5, 8)
    ]  # close of bars 1, 4, 7
    assert res.counters["suppressed_repeat"] == 5 and len(res.orders) == 3
    assert eng.repeat_guard is not None and eng.repeat_guard.suppressed == 5


def test_a_fill_changes_the_state_and_re_arms_the_blocked_intent():
    script = {
        1: [mbuy("b1", "500", tag="big")],  # refused: cash 100
        2: [mbuy("b2", "500", tag="big")],  # suppressed
        3: [mbuy("s3", "50", tag="small")],  # accepted, fills on the next bar (cash/fills change)
        5: [mbuy("b5", "500", tag="big")],  # state changed -> evaluated (and refused) again
        6: [mbuy("b6", "500", tag="big")],  # suppressed again
    }
    res, _ = _run(script, suppress=True, rearm_hours=100)
    ids = {o.client_id: o.status for o in res.orders}
    assert "b2" not in ids and "b6" not in ids  # dropped before becoming orders
    assert ids["b1"] == ids["b5"] == OrderStatus.REJECTED
    assert ids["s3"] == OrderStatus.FILLED
    assert res.counters["suppressed_repeat"] == 2


def test_suppression_never_creates_exposure_or_changes_the_final_ledger():
    """Dropping refusals must leave equity, cash and fills exactly as the unsuppressed run."""
    script = {**_script(range(1, 9)), 3: [mbuy("s3", "50", tag="small")]}
    off, _ = _run(script, suppress=False)
    on, _ = _run(script, suppress=True, rearm_hours=2)
    assert [f.fill_id for f in on.fills] == [f.fill_id for f in off.fills]
    assert on.equity[-1].equity == off.equity[-1].equity
    assert on.equity[-1].cash == off.equity[-1].cash
    assert len(on.orders) < len(off.orders)


# ------------------------------------------------------------------ paper-session config
def test_default_config_hash_is_unchanged_and_the_flag_is_part_of_the_hash_only_when_on():
    base = make_cfg()
    assert (
        "suppress_repeated_rejections" not in base.to_json()
    )  # existing session hashes unaffected
    on = replace(base, suppress_repeated_rejections=True)
    assert on.to_json()["suppress_repeated_rejections"] is True
    assert on.config_hash() != base.config_hash()
    assert PaperSessionConfig.from_json(on.to_json()).suppress_repeated_rejections is True
    assert PaperSessionConfig.from_json(base.to_json()).config_hash() == base.config_hash()
