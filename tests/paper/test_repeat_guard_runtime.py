"""Opt-in repeat guard inside the full multi-account paper runtime (6 accounts, shared caps)."""

from dataclasses import replace
from decimal import Decimal as D

from asta.paper.runtime import PaperRuntime
from asta.research.sim.repeat_guard import HEADROOM_REASONS
from asta.research.types import OrderStatus
from asta.research.validation.audit import independent_audit
from tests.paper.helpers import make_cfg, make_series

WARM = 300
SERIES = make_series()


def _run(cfg):
    rt = PaperRuntime(cfg)
    digests = []
    for k, (ts, bars) in enumerate(SERIES):
        rows = rt.step(k, ts, bars, {}, True, k < WARM)
        assert rows.checkpoint is not None
        digests.append(rows.checkpoint["digest"])
    return rt, digests


def _headroom_refusals(rt) -> int:
    return sum(
        1
        for acc in rt.accounts.values()
        for o in acc.engine.orders
        if o.status == OrderStatus.REJECTED and o.reject_reason in HEADROOM_REASONS
    )


def test_guard_removes_repeated_headroom_refusals_and_every_account_still_reconciles():
    base = make_cfg()
    off, d_off = _run(base)
    on, d_on = _run(replace(base, suppress_repeated_rejections=True))
    refusals_off, refusals_on = _headroom_refusals(off), _headroom_refusals(on)
    assert refusals_off > 0, (
        "scenario must produce headroom refusals for the comparison to mean anything"
    )
    assert refusals_on < refusals_off
    suppressed = sum(a.engine.counters.get("suppressed_repeat", 0) for a in on.accounts.values())
    assert suppressed > 0
    # global accounting still exact with the guard on
    n = len(SERIES)
    for key, acc in on.accounts.items():
        res = acc.engine.finish(n)
        rets = {
            "total_net_pnl": res.reconcile.equity - acc.spec.starting_cash,
            "realized_pnl": res.ledger.realized_total,
            "total_fees_quote_equivalent": D(10**9),
        }
        bad = [c for c in independent_audit(res, acc.spec.starting_cash, "USDT", rets) if not c.ok]
        assert not bad, (key, bad)
    assert on.global_equity() > 0 and len(d_on) == len(d_off)


def test_guard_on_is_deterministic_step_for_step():
    cfg = replace(make_cfg(), suppress_repeated_rejections=True)
    _, a = _run(cfg)
    _, b = _run(cfg)
    assert a == b


def test_guard_off_counters_have_no_guard_key_and_behaviour_is_the_accepted_one():
    rt, _ = _run(make_cfg())
    assert all("suppressed_repeat" not in a.engine.counters for a in rt.accounts.values())
    assert all(a.engine.repeat_guard is None for a in rt.accounts.values())
