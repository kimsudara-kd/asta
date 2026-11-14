"""Cancel flow and kill-switch flush: same write-ahead / ask-by-client-id discipline as dispatch."""

import random
from decimal import Decimal as D

import pytest

from asta.orders.dispatch import Dispatcher, Policy
from asta.orders.lifecycle import TERMINAL, UNRESOLVED, State
from asta.orders.pretrade import DayState
from tests.orders.fakes import Crash, FakeExchange, MemoryStore, make_intent
from tests.orders.test_order_path import LIMITS

OK = DayState(0, D("0"), False)
KILL = DayState(0, D("0"), True)


def build(ex, st, day=None, policy=Policy()):  # noqa: B008
    holder = {"day": day or OK}
    d = Dispatcher(ex, st, LIMITS, lambda: holder["day"], lambda s: D("100"), lambda: 1_000, policy)
    return d, holder


def placed(ex, st, n=1, **kw):
    d, holder = build(ex, st, **kw)
    ids = []
    for i in range(n):
        d.handle(make_intent(i))
        ids.append(make_intent(i).decision_id)
    return d, holder, ids


def test_cancel_a_resting_order():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    d, _, (cid,) = placed(ex, st)
    assert d.cancel(cid) == State.CANCELED
    assert ex.orders[cid] == State.CANCELED and st.state_of(cid) == State.CANCELED
    assert st.rows[cid].cancel_attempts == 1


def test_cancel_racing_a_fill_adopts_the_fill():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    d, _, (cid,) = placed(ex, st)
    ex.orders[cid] = State.FILLED  # filled at the exchange, we still believe ACCEPTED
    assert d.cancel(cid) == State.FILLED and st.state_of(cid) == State.FILLED
    assert ex.cancel_effects == 0


def test_a_lost_cancel_reply_is_resolved_by_asking_not_by_cancelling_again():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    d, _, (cid,) = placed(ex, st)
    ex.p_drop_response = 1.0
    assert d.cancel(cid) == State.CANCEL_REQUESTED  # cancelled at the exchange, reply lost
    ex.healthy()
    rep = d.recover()
    assert st.state_of(cid) == State.CANCELED and rep.adopted == 1 and rep.cancels == 0
    assert st.rows[cid].cancel_attempts == 1


def test_a_cancel_that_never_arrived_is_retried():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    d, _, (cid,) = placed(ex, st)
    ex.p_drop_request = 1.0
    assert d.cancel(cid) == State.CANCEL_REQUESTED
    ex.healthy()
    rep = d.recover()  # still resting -> cancel again
    assert rep.cancels == 1 and st.state_of(cid) == State.CANCELED
    assert st.rows[cid].cancel_attempts == 2


def test_a_cancel_that_never_takes_effect_is_reported_not_hidden():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    d, _, (cid,) = placed(ex, st, policy=Policy(max_cancel_attempts=2))
    ex.p_drop_request = 1.0
    d.cancel(cid)
    anomalies = []
    for _ in range(4):
        anomalies += d.recover().anomalies
    assert st.state_of(cid) == State.CANCEL_REQUESTED and ex.orders[cid] == State.ACCEPTED
    assert any("cancel not taking effect" in a for a in anomalies)


def test_things_that_cannot_be_cancelled_return_none():
    ex, st = FakeExchange(random.Random(1), p_drop_request=1.0), MemoryStore()
    d, _ = build(ex, st)
    d.handle(make_intent(1))  # UNKNOWN: may or may not exist
    assert d.cancel(make_intent(1).decision_id) is None
    assert d.cancel("0" * 32) is None  # unknown decision


def test_kill_switch_cancels_everything_resting_and_creates_nothing_new():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    d, holder, ids = placed(ex, st, n=5)
    sends = ex.send_calls
    holder["day"] = KILL
    rep = d.recover()
    assert rep.cancels == 5 and all(st.state_of(i) == State.CANCELED for i in ids)
    assert all(ex.orders[i] == State.CANCELED for i in ids)
    late = d.handle(make_intent(99))  # a new decision after the kill
    assert late.reasons and "KILL_SWITCH" in late.reasons and ex.send_calls == sends


def test_kill_switch_flush_also_catches_an_order_found_late():
    ex, st = FakeExchange(random.Random(1), p_drop_response=1.0), MemoryStore()
    d, holder = build(ex, st)
    d.handle(make_intent(1))  # sent, reply lost -> UNKNOWN, but it IS resting at the exchange
    cid = make_intent(1).decision_id
    ex.healthy()
    holder["day"] = KILL
    d.recover()  # adopts it as resting, then cancels it in the same pass
    assert ex.orders[cid] == State.CANCELED and st.state_of(cid) == State.CANCELED


# --------------------------------------------- chaos: a kill switch at a random moment
def chaos_kill(seed):
    rng = random.Random(seed)
    ex = FakeExchange(
        rng,
        p_drop_request=0.2,
        p_drop_response=0.25,
        p_unavailable=0.3,
        p_crash_after_send=0.08,
        visible_delay=rng.randrange(0, 3),
    )
    st = MemoryStore()
    policy = Policy(max_attempts=50, max_age_seconds=10**9, max_cancel_attempts=60)
    d, holder = build(ex, st, policy=policy)
    n = 25
    kill_at = rng.randrange(5, 30)
    kill_tick = None
    order = [i for i in range(n) for _ in range(rng.randrange(1, 3))]
    rng.shuffle(order)
    for step, i in enumerate(order):
        if step == kill_at and kill_tick is None:
            holder["day"], kill_tick = KILL, ex.tick
        try:
            if rng.random() < 0.1:
                st.crash_on_save = st.saves + rng.randrange(1, 4)
            d.handle(make_intent(i))
            if rng.random() < 0.3 and st.rows:
                d.cancel(rng.choice(sorted(st.rows)))  # random operator cancels as well
            if step % 3 == 0:
                d.recover()
            ex.advance()
        except Crash:
            d, holder2 = build(ex, st, day=holder["day"], policy=policy)
            holder = holder2
            ex.advance()
    if kill_tick is None:
        holder["day"], kill_tick = KILL, ex.tick
    ex.healthy()
    st.crash_on_save = None
    for _ in range(80):
        d.recover()
        ex.advance()
    d.recover()
    return ex, st, kill_tick


@pytest.mark.parametrize("seed", range(200))
def test_after_a_kill_switch_nothing_new_exists_and_nothing_keeps_resting(seed):
    ex, st, kill_tick = chaos_kill(seed)
    # K1: no order was CREATED at the exchange after the kill switch tripped
    assert all(t <= kill_tick for t in ex.created_at_tick.values())
    # K2: nothing is left resting at the exchange
    assert all(s in TERMINAL for s in ex.orders.values()), set(ex.orders.values())
    # K3: nothing is unresolved locally, and local state equals exchange state wherever it exists
    assert not st.by_state(UNRESOLVED)
    for cid, seen in ex.orders.items():
        assert st.state_of(cid) == seen, (cid, st.state_of(cid), seen)


def test_a_kill_switch_that_trips_between_the_guard_and_the_send_still_stops_the_send():
    """The guard sees 'no kill', then the switch is thrown before the request would leave."""
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    answers = iter([OK, KILL, KILL, KILL])
    d = Dispatcher(
        ex, st, LIMITS, lambda: next(answers), lambda s: D("100"), lambda: 1_000, Policy()
    )
    d.handle(make_intent(1))
    assert ex.send_calls == 0 and not ex.orders
    assert st.state_of(make_intent(1).decision_id) == State.ABANDONED
