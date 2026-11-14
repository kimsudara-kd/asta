"""Order-path logic: lifecycle, pre-trade guard, exactly-once dispatch and recovery under chaos."""

import random
from decimal import Decimal as D

import pytest

from asta.core.environment import Environment
from asta.orders.dispatch import Dispatcher, Handled, Policy
from asta.orders.lifecycle import (
    ALLOWED,
    TERMINAL,
    UNRESOLVED,
    IllegalTransition,
    State,
    check_transition,
)
from asta.orders.pretrade import DayState, PreTradeLimits, refusals
from tests.orders.fakes import Crash, FakeExchange, MemoryStore, make_intent

LIMITS = PreTradeLimits(
    allowed_venues=frozenset({"BINANCE_TESTNET"}),
    allowed_symbols=frozenset({"BTC-USDT", "ETH-USDT"}),
    max_order_notional=D("25"),
    max_daily_orders=50,
    max_daily_notional=D("500"),
    max_price_deviation_bps=D("100"),
)
DAY = DayState(orders_today=0, notional_today=D("0"), kill_switch=False)


def make_dispatcher(exchange, store, day=DAY, now=1_000, policy=Policy(), limits=LIMITS):  # noqa: B008
    clock = {"t": now}
    d = Dispatcher(
        exchange, store, limits, lambda: day, lambda s: D("100"), lambda: clock["t"], policy
    )
    d.clock = clock  # type: ignore[attr-defined]
    return d


# ------------------------------------------------------------------ lifecycle
def test_terminal_states_have_no_exit_and_every_state_has_a_row():
    assert set(ALLOWED) == set(State)
    for s in TERMINAL:
        assert ALLOWED[s] == frozenset()
    assert State.ABANDONED not in TERMINAL  # a late appearance is still adoptable
    assert UNRESOLVED == {State.PENDING, State.DISPATCHED, State.UNKNOWN}


def test_illegal_transitions_raise_and_a_filled_order_cannot_come_back():
    check_transition(State.PENDING, State.DISPATCHED)
    for src, dst in [
        (State.PENDING, State.FILLED),  # cannot fill what was never sent
        (State.FILLED, State.ACCEPTED),
        (State.REJECTED, State.DISPATCHED),
        (State.DISPATCHED, State.PENDING),
        (State.CANCELED, State.FILLED),
    ]:
        with pytest.raises(IllegalTransition):
            check_transition(src, dst)


def test_intent_validation_refuses_paper_unsupported_and_ambiguous_sizing():
    make_intent(1).validate()
    for bad in (
        {"environment": Environment.PAPER},
        {"side": "SHORT"},
        {"order_type": "STOP"},
        {"quote_notional": None},  # neither size
        {"quantity": D("1")},  # both sizes
        {"order_type": "LIMIT"},  # no price
        {"quote_notional": D("-1")},
        {"decision_id": "client-1"},  # not a deterministic id
    ):
        with pytest.raises(ValueError):
            make_intent(1, **bad).validate()


# ------------------------------------------------------------------ pre-trade guard
def test_a_normal_demo_intent_is_allowed():
    assert refusals(make_intent(1), LIMITS, DAY, D("100")) == ()


@pytest.mark.parametrize(
    ("kw", "day", "limits", "reason"),
    [
        ({}, DayState(0, D("0"), True), LIMITS, "KILL_SWITCH"),
        ({}, DAY, None, "NO_LIMITS_CONFIGURED"),
        ({"venue": "BINANCE"}, DAY, LIMITS, "VENUE_NOT_ALLOWED"),
        ({"symbol": "DOGE-USDT"}, DAY, LIMITS, "SYMBOL_NOT_ALLOWED"),
        ({"quote_notional": D("26")}, DAY, LIMITS, "ORDER_NOTIONAL_CAP"),
        ({}, DayState(0, D("495"), False), LIMITS, "DAILY_NOTIONAL_CAP"),
        ({}, DayState(50, D("0"), False), LIMITS, "DAILY_ORDER_COUNT_CAP"),
        ({"environment": Environment.PAPER}, DAY, LIMITS, "ENVIRONMENT_NOT_EXCHANGE_DEMO"),
        (
            {
                "order_type": "LIMIT",
                "quote_notional": None,
                "quantity": D("0.1"),
                "limit_price": D("103"),
            },
            DAY,
            LIMITS,
            "LIMIT_PRICE_OUTSIDE_BAND",
        ),
    ],
)
def test_each_guard_refuses_for_its_own_reason(kw, day, limits, reason):
    assert reason in refusals(make_intent(1, **kw), limits, day, D("100"))


def test_unsizable_intent_fails_closed_and_all_reasons_are_listed():
    unsizable = make_intent(1, quote_notional=None, quantity=D("1"))  # MARKET qty, no price known
    assert "NOTIONAL_UNKNOWN" in refusals(unsizable, LIMITS, DAY, None)
    many = refusals(
        make_intent(1, symbol="X-USDT", venue="V"), LIMITS, DayState(0, D("0"), True), D("100")
    )
    assert {"KILL_SWITCH", "SYMBOL_NOT_ALLOWED", "VENUE_NOT_ALLOWED"} <= set(many)


# ------------------------------------------------------------------ dispatch basics
def test_happy_path_claims_dispatches_and_records_the_acknowledgement():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    r = make_dispatcher(ex, st).handle(make_intent(1))
    assert (r.outcome, r.state) == (Handled.SENT, State.ACCEPTED)
    assert st.attempts_of(make_intent(1).decision_id) == 1
    assert len(ex.orders) == 1 and ex.send_calls == 1


def test_a_repeated_decision_is_claimed_once_and_never_sent_again():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    d = make_dispatcher(ex, st)
    first = d.handle(make_intent(1))
    for _ in range(5):
        again = d.handle(make_intent(1))
        assert again.outcome == Handled.DUPLICATE_DECISION
    assert first.outcome == Handled.SENT and ex.send_calls == 1


def test_a_refused_decision_is_stored_rejected_and_nothing_is_sent():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    day = DayState(0, D("0"), True)
    r = make_dispatcher(ex, st, day=day).handle(make_intent(1))
    assert r.outcome == Handled.REFUSED and "KILL_SWITCH" in r.reasons
    assert ex.send_calls == 0 and st.state_of(make_intent(1).decision_id) == State.REJECTED
    assert make_dispatcher(ex, st).handle(make_intent(1)).outcome == Handled.DUPLICATE_DECISION


# ------------------------------------------------------------------ timeouts and recovery
def test_a_lost_reply_is_resolved_by_asking_by_client_id_never_by_a_new_order():
    ex, st = FakeExchange(random.Random(1), p_drop_response=1.0), MemoryStore()
    d = make_dispatcher(ex, st)
    assert d.handle(make_intent(1)).state == State.UNKNOWN  # sent, reply lost
    ex.healthy()
    rep = d.recover()
    assert rep.adopted == 1 and rep.resent == 0  # FOUND by id: adopted, NOT re-sent
    assert ex.send_calls == 1 and len(ex.orders) == 1
    assert st.state_of(make_intent(1).decision_id) == State.ACCEPTED


def test_a_request_that_never_arrived_is_resent_with_the_same_id():
    ex, st = FakeExchange(random.Random(1), p_drop_request=1.0), MemoryStore()
    d = make_dispatcher(ex, st)
    assert d.handle(make_intent(1)).state == State.UNKNOWN
    ex.healthy()
    first = d.recover()
    assert first.waiting == 1 and first.resent == 0 and not ex.orders  # one NOT_FOUND is not enough
    second = d.recover()
    assert second.resent == 1 and set(ex.orders) == {make_intent(1).decision_id}
    assert st.state_of(make_intent(1).decision_id) == State.ACCEPTED


def test_an_in_flight_order_that_is_not_visible_yet_cannot_be_doubled():
    """Eventual consistency: the query says NOT_FOUND although the order exists. The re-send
    carries the same id, the exchange refuses it as a duplicate: still exactly one order."""
    ex, st = FakeExchange(random.Random(1), p_drop_response=1.0, visible_delay=5), MemoryStore()
    d = make_dispatcher(ex, st)
    d.handle(make_intent(1))
    ex.healthy()
    ex.visible_delay = 0
    ex.visible_at[make_intent(1).decision_id] = 99  # still invisible to queries
    d.recover()  # first NOT_FOUND: wait
    d.recover()  # second: the SAME id is re-sent and refused as a duplicate
    assert len(ex.orders) == 1 and ex.duplicates_refused == 1
    ex.tick = 100
    d.recover()
    assert st.state_of(make_intent(1).decision_id) in {State.ACCEPTED, State.PARTIALLY_FILLED}
    assert len(ex.orders) == 1


def test_max_attempts_then_abandoned_and_a_late_appearance_is_adopted():
    ex, st = FakeExchange(random.Random(1), p_drop_request=1.0), MemoryStore()
    d = make_dispatcher(ex, st, policy=Policy(max_attempts=3, max_age_seconds=10_000))
    d.handle(make_intent(1))
    for _ in range(12):  # two NOT_FOUND confirmations per re-send, three attempts in all
        d.recover()
    rec = st.get(make_intent(1).decision_id)
    assert rec is not None and rec.state == State.ABANDONED and ex.send_calls == 3
    # the first request turns out to have been delivered long ago (a delayed gateway)
    ex.orders[rec.decision_id] = State.FILLED
    ex.visible_at[rec.decision_id] = 0
    ex.healthy()
    d.recover()
    assert st.state_of(rec.decision_id) == State.FILLED


def test_a_stale_unknown_decision_is_abandoned_not_chased():
    ex, st = FakeExchange(random.Random(1), p_drop_request=1.0), MemoryStore()
    d = make_dispatcher(ex, st, policy=Policy(max_attempts=9, max_age_seconds=60))
    d.handle(make_intent(1))
    d.clock["t"] += 3_600
    ex.healthy()
    rep = d.recover()
    assert rep.abandoned == 1 and ex.send_calls == 1 and not ex.orders


def test_kill_switch_stops_pending_and_unknown_work_and_sends_nothing_new():
    ex, st = FakeExchange(random.Random(1), p_drop_request=1.0), MemoryStore()
    d = make_dispatcher(ex, st)
    d.handle(make_intent(1))
    sent_before = ex.send_calls
    ex.healthy()
    killed = make_dispatcher(ex, st, day=DayState(0, D("0"), True))
    rep = killed.recover()
    assert rep.abandoned == 1 and ex.send_calls == sent_before
    assert st.state_of(make_intent(1).decision_id) == State.ABANDONED


def test_exchange_unavailable_changes_nothing_and_recovery_is_retried_later():
    ex, st = FakeExchange(random.Random(1), p_drop_response=1.0), MemoryStore()
    d = make_dispatcher(ex, st)
    d.handle(make_intent(1))
    ex.p_unavailable = 1.0
    assert d.recover().unavailable == 1 and ex.send_calls == 1
    ex.healthy()
    assert d.recover().adopted == 1


def test_an_exchange_report_that_contradicts_our_state_is_an_anomaly_not_a_crash():
    ex, st = FakeExchange(random.Random(1)), MemoryStore()
    d = make_dispatcher(ex, st)
    d.handle(make_intent(1))
    rec = st.get(make_intent(1).decision_id)
    assert rec is not None
    st.rows[rec.decision_id] = rec.moved(State.FILLED)  # we believe FILLED ...
    ex.orders[rec.decision_id] = State.ACCEPTED  # ... the exchange says ACCEPTED
    assert d.recover().anomalies == [] and st.state_of(rec.decision_id) == State.FILLED


# ----------------------------------------------------- chaos: exactly-once under crashes
def chaos(seed: int) -> tuple[FakeExchange, MemoryStore, dict[str, int]]:
    rng = random.Random(seed)
    ex = FakeExchange(
        rng,
        p_drop_request=0.2,
        p_drop_response=0.25,
        p_unavailable=0.3,
        p_crash_after_send=0.1,
        visible_delay=rng.randrange(0, 4),
    )
    st = MemoryStore()
    n = 25
    intents = [make_intent(i) for i in range(n)]
    deliveries = [i for i in range(n) for _ in range(rng.randrange(1, 4))]  # repeated delivery
    rng.shuffle(deliveries)
    d = make_dispatcher(ex, st, policy=Policy(max_attempts=50, max_age_seconds=10**9))
    for step, i in enumerate(deliveries):
        try:
            if rng.random() < 0.15:
                st.crash_on_save = st.saves + rng.randrange(1, 4)  # die mid-protocol
            d.handle(intents[i])
            if step % 4 == 0:
                d.recover()
            ex.advance()
        except Crash:
            d = make_dispatcher(
                ex, st, policy=Policy(max_attempts=50, max_age_seconds=10**9)
            )  # restart
            ex.advance()
    # quiesce: a healthy exchange, then recovery until nothing is unresolved
    ex.healthy()
    st.crash_on_save = None
    for _ in range(40):
        d.recover()
        ex.advance()
        if not st.by_state(UNRESOLVED) and all(
            ex.orders[c] in TERMINAL
            for c in ex.orders  # everything settled
        ):
            break
    d.recover()
    return ex, st, {i.decision_id: k for k, i in enumerate(intents)}


@pytest.mark.parametrize("seed", range(300))
def test_exactly_once_under_loss_delay_unavailability_and_crashes(seed):
    ex, st, ids = chaos(seed)
    delivered = set(st.rows)  # every claimed decision
    # A. recovery never mints an id: every exchange order is one of OUR decision ids
    assert set(ex.orders) <= set(ids)
    # B. liveness: nothing is left unresolved once the exchange is healthy
    assert not st.by_state(UNRESOLVED), [r.state for r in st.by_state(UNRESOLVED)]
    # C. exactness: every claimed decision has exactly one exchange order (never zero, never two)
    assert set(ex.orders) == delivered
    # D. local state agrees with the exchange for everything that exists there
    for cid, seen in ex.orders.items():
        assert st.rows[cid].state == seen, (cid, st.rows[cid].state, seen)


def test_chaos_actually_exercises_the_hard_paths():
    refused = crashed = 0
    for seed in range(60):
        ex, st, _ = chaos(seed)
        refused += ex.duplicates_refused
        crashed += sum(1 for r in st.rows.values() if r.attempts > 1)
    assert refused > 0 and crashed > 0  # duplicates were really refused; re-sends really happened


# ------------------------------------------- the hazard the confirmations exist for
def _fast_fill_with_lagging_visibility(confirmations: int) -> FakeExchange:
    """Hazard: a market order fills at once, the reply is lost and the order is invisible to queries
    for one round. If the exchange re-accepts a client id once the previous order is filled,
    re-sending too early creates a SECOND real order."""
    ex = FakeExchange(random.Random(1), p_drop_response=1.0, reuse_after_fill=True)
    st = MemoryStore()
    d = make_dispatcher(ex, st, policy=Policy(not_found_confirmations=confirmations))
    d.handle(make_intent(1))
    cid = make_intent(1).decision_id
    ex.orders[cid] = State.FILLED
    ex.visible_at[cid] = ex.tick + 1  # not queryable yet, visible next round
    ex.p_drop_response = 0.0
    for _ in range(4):
        d.recover()
        ex.advance()
    return ex


def test_one_not_found_confirmation_would_double_the_order_on_such_an_exchange():
    assert _fast_fill_with_lagging_visibility(confirmations=1).double_orders == 1


def test_two_not_found_confirmations_prevent_the_double_order():
    assert _fast_fill_with_lagging_visibility(confirmations=2).double_orders == 0
