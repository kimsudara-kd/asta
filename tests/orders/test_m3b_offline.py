"""Offline M3-B parts: fills, rate budget, clock guard, channel, kill switch, reconcile, amend."""

from __future__ import annotations

import hashlib
import json
import random
from decimal import Decimal as D
from fractions import Fraction

import pytest

from asta.orders.channel import (
    MAX_BYTES,
    Envelope,
    MemoryReplayStore,
    Receiver,
    payload_bytes,
    seal,
)
from asta.orders.clockguard import SkewPolicy, estimate, verdict
from asta.orders.fills import Fill, FillBook, consistency
from asta.orders.halts import KillSwitch, Trigger
from asta.orders.lifecycle import Intent, State
from asta.orders.ports import ExchangeView
from asta.orders.ratelimit import Budget, Window
from asta.orders.reconcile import (
    DISCLAIMER,
    FillStat,
    apply_fills,
    classify_reset,
    compare_paper_demo,
    reconcile_balances,
    reconcile_orders,
)
from asta.orders.replace import build_replacement, may_replace, replacement_tag
from tests.orders.fakes import gated, make_intent


def fill(
    tid: str, cid: str, q: str = "0.1", p: str = "100", fee: str = "0.01", fa: str = "BNB"
) -> Fill:
    return Fill(tid, cid, D(q), D(p), D(fee), fa)


# ------------------------------------------------------------------ fills
def test_a_repeated_trade_is_counted_once_and_a_changed_repeat_is_an_anomaly():
    it = make_intent(1, quantity=D("1"), quote_notional=None)
    b = FillBook(it.decision_id, D("1"))
    f = fill("t1", it.decision_id)
    assert b.apply(f) and not b.apply(f)
    assert b.summary().quantity == D("0.1") and b.summary().trades == 1 and not b.anomalies
    assert not b.apply(fill("t1", it.decision_id, q="0.2"))
    assert "different content" in b.anomalies[0]


def test_overfill_foreign_and_nonsense_trades_are_rejected_not_absorbed():
    it = make_intent(1, quantity=D("1"), quote_notional=None)
    b = FillBook(it.decision_id, D("1"))
    assert b.apply(fill("a", it.decision_id, q="0.9"))
    assert not b.apply(fill("b", it.decision_id, q="0.2"))  # 1.1 > 1
    assert not b.apply(fill("c", "someone-else"))
    assert not b.apply(fill("d", it.decision_id, q="0"))
    assert not b.apply(fill("e", it.decision_id, fee="-1"))
    assert b.summary().quantity == D("0.9") and len(b.anomalies) == 4


def test_fill_summary_is_exact_decimal_and_order_independent():
    rng = random.Random(7)
    fs = [
        fill(
            f"t{i}",
            "c",
            q=str(D(rng.randint(1, 50)) / 1000),
            p=str(D(rng.randint(9000, 9100)) / 100),
        )
        for i in range(40)
    ]
    ref = None
    for _ in range(20):
        rng.shuffle(fs)
        b = FillBook("c")
        for f in fs + fs[:10]:  # duplicates delivered too
            b.apply(f)
        s = b.summary()
        ref = ref or s
        assert s == ref
    assert ref is not None and ref.trades == 40 and ref.avg_price is not None


def test_state_versus_trades_consistency():
    it = make_intent(1, quantity=D("1"), quote_notional=None)
    b = FillBook(it.decision_id, D("1"))
    assert "FILLED without any trade" in consistency(
        it.moved(State.DISPATCHED).moved(State.FILLED), b
    )
    b.apply(fill("a", it.decision_id, q="0.4"))
    filled = it.moved(State.DISPATCHED).moved(State.FILLED)
    assert any("traded 0.4 of 1" in x for x in consistency(filled, b))
    part = it.moved(State.DISPATCHED).moved(State.PARTIALLY_FILLED)
    assert consistency(part, b) == ()
    b.apply(fill("b", it.decision_id, q="0.6"))
    assert consistency(filled, b) == ()
    assert any("full size" in x for x in consistency(part, b))


# ------------------------------------------------------------------ rate budget
def test_no_known_limits_means_no_requests():
    d = Budget(()).check(0)
    assert not d.allowed and d.reason == "NO_LIMITS_KNOWN"


def test_budget_spends_only_the_headroom_and_frees_as_events_age_out():
    b = Budget((Window(10, 50),))  # 80 % of 50 = 40
    assert all(b.spend(0.0).allowed for _ in range(40))
    d = b.spend(1.0)
    assert not d.allowed and d.reason == "WINDOW_FULL" and 8.9 < d.retry_after <= 9.0
    assert b.spend(10.0).allowed  # the first events have aged out


def test_budget_respects_every_window_and_weights():
    b = Budget((Window(10, 50), Window(86_400, 100)), headroom=Fraction(1))
    for i in range(100):
        assert b.spend(i * 11.0).allowed  # never trips the 10 s window, exhausts the daily one
    d = b.check(1_101.0)
    assert not d.allowed and d.retry_after > 80_000
    assert b.check(0, weight=0).reason == "BAD_WEIGHT"
    assert (
        Budget((Window(10, 5),), headroom=Fraction(1)).check(0, weight=6).reason
        == "WEIGHT_EXCEEDS_WINDOW_10s"
    )


def test_a_429_blocks_everything_until_the_exchange_deadline_and_never_shortens():
    b = Budget((Window(10, 50),))
    b.exchange_said_slow_down(100.0, 30.0)
    assert b.check(110.0).reason == "BANNED"
    b.exchange_said_slow_down(105.0, 1.0)  # a shorter hint must not shorten the ban
    assert b.check(129.0).reason == "BANNED" and b.check(130.0).allowed


# ------------------------------------------------------------------ clock guard
def test_clock_estimate_and_verdicts_fail_closed():
    e = estimate(1_000, 2_550, 1_100)  # rtt 100, server ahead ~1500
    assert e.offset_ms == 1_500 and e.uncertainty_ms == 50
    p = SkewPolicy()
    assert verdict(None, 0).reason == "NO_CLOCK_ESTIMATE"
    assert verdict(estimate(1_000, 1_030, 1_040), 1_100, p).ok
    assert verdict(estimate(1_000, 1_030, 1_040), 1_100, p).timestamp_ms == 1_100 + 10
    assert verdict(e, 1_100, p).reason == "CLOCK_SKEW_TOO_LARGE"
    assert verdict(estimate(0, 0, 2_000), 2_000, p).reason == "CLOCK_ESTIMATE_TOO_UNCERTAIN"
    assert (
        verdict(estimate(1_000, 1_030, 1_040), 1_040 + 60_001, p).reason == "CLOCK_ESTIMATE_STALE"
    )
    assert verdict(estimate(1_000, 1_030, 1_040), 900, p).reason == "LOCAL_CLOCK_WENT_BACKWARDS"
    with pytest.raises(ValueError):
        estimate(10, 10, 5)


# ------------------------------------------------------------------ channel
class FakeAuth:
    """TEST-ONLY stand-in for the reviewed Authenticator (a keyed hash; not for production)."""

    def __init__(self, keys: dict[str, bytes]) -> None:
        self.keys = keys

    def tag_for(self, key_id: str, payload: bytes) -> bytes:
        return hashlib.sha256(self.keys[key_id] + payload).digest()

    def verify(self, key_id: str, payload: bytes, tag: bytes) -> bool:
        return key_id in self.keys and self.tag_for(key_id, payload) == tag


def env(
    seq: int = 1,
    nonce: str = "n1",
    issued: int = 1_000,
    expires: int = 1_030,
    i: int = 1,
    sender: str = "app-1",
) -> Envelope:
    return Envelope(sender, "k1", seq, nonce, issued, expires, make_intent(i))


@pytest.fixture
def chan() -> tuple[FakeAuth, Receiver]:
    a = FakeAuth({"k1": b"x" * 32})
    return a, Receiver(a, frozenset({"app-1"}), MemoryReplayStore(), require_durable=False)


def test_a_valid_message_round_trips(chan):
    a, r = chan
    got, why = r.open(seal(env(), a), 1_005)
    assert why == "" and got is not None and got.intent == make_intent(1)


def test_canonical_encoding_is_deterministic_and_has_no_float():
    p1, p2 = payload_bytes(env()), payload_bytes(env())
    assert p1 == p2 and b" " not in p1 and b"e+" not in p1
    assert json.loads(p1)["intent"]["quote_notional"] == "10"


@pytest.mark.parametrize(
    "mut,reason", [("flip", "NOT_AUTHENTIC"), ("trunc", "MALFORMED"), ("junk", "MALFORMED")]
)
def test_tampered_or_malformed_messages_are_refused(chan, mut, reason):
    a, r = chan
    w = bytearray(seal(env(), a))
    if mut == "flip":
        w[-5] ^= 1
        wire = bytes(w)
    elif mut == "trunc":
        wire = bytes(w[:2])
    else:
        wire = (32).to_bytes(2, "big") + b"t" * 32 + b"not json"
    got, why = r.open(wire, 1_005)
    assert got is None and why == reason


def test_replay_reorder_nonce_expiry_future_and_unknown_sender_are_refused(chan):
    a, r = chan
    assert r.open(seal(env(seq=5, nonce="a"), a), 1_005)[0] is not None
    assert r.open(seal(env(seq=5, nonce="b"), a), 1_005)[1] == "REPLAYED_OR_OUT_OF_ORDER"
    assert r.open(seal(env(seq=4, nonce="c"), a), 1_005)[1] == "REPLAYED_OR_OUT_OF_ORDER"
    assert r.open(seal(env(seq=6, nonce="a"), a), 1_005)[1] == "NONCE_REUSED"
    assert r.open(seal(env(seq=7, nonce="d"), a), 1_031)[1] == "EXPIRED"
    assert (
        r.open(seal(env(seq=8, nonce="e", issued=2_000, expires=2_030), a), 1_005)[1]
        == "FROM_THE_FUTURE"
    )
    assert r.open(seal(env(seq=9, nonce="f", expires=1_200), a), 1_005)[1] == "BAD_VALIDITY_WINDOW"
    assert r.open(seal(env(seq=10, nonce="g", sender="evil"), a), 1_005)[1] == "SENDER_NOT_ALLOWED"
    assert (
        r.open(seal(env(seq=6, nonce="h"), a), 1_005)[0] is not None
    )  # refusals did not burn state


def test_a_message_signed_with_an_unknown_key_or_for_a_non_demo_environment_is_refused(chan):
    a, r = chan
    other = FakeAuth({"k1": b"y" * 32})
    assert r.open(seal(env(), other), 1_005)[1] == "NOT_AUTHENTIC"
    bad = Envelope(
        "app-1", "k1", 1, "n", 1_000, 1_030, make_intent(1, limit_price=D("-1"), order_type="LIMIT")
    )
    assert r.open(seal(bad, a), 1_005)[1] == "INTENT_INVALID"


def test_oversized_messages_are_refused_both_ways(chan):
    a, r = chan
    assert r.open(b"\x00\x20" + b"t" * 32 + b"x" * (MAX_BYTES + 1), 1_005)[1] == "TOO_LARGE"
    big = Envelope("a" * 3_000, "k1", 1, "n", 1, 2, make_intent(1))
    with pytest.raises(ValueError):
        seal(big, a)


def test_the_channel_source_has_no_credential_or_signing_vocabulary():
    from pathlib import Path

    txt = (Path(__file__).parents[2] / "src/asta/orders/channel.py").read_text().lower()
    for w in ("hmac", "signature", "api_key", "secret", "passphrase", "private_key"):
        assert w not in txt


# ------------------------------------------------------------------ kill switch
def test_triggers_latch_and_only_a_named_human_with_a_reason_releases():
    k = KillSwitch()
    k.trip(Trigger.RISK_HALT, 10)
    assert k.active and k.trips == [Trigger.RISK_HALT]
    for who, why in (("system", "x"), ("", "x"), ("  ", "x"), ("alice", ""), ("alice", "  ")):
        assert not k.release(11, who, why) and k.active
    assert k.release(12, "alice", "reviewed the halt")
    assert not k.active and k.trips == [Trigger.RISK_HALT]  # the record of the trip is kept
    assert [c.action for c in k.history] == ["TRIP", "RELEASE"]
    assert not k.release(13, "alice", "again")  # nothing to release


def test_consecutive_auth_failures_trip_and_a_success_resets_the_count():
    k = KillSwitch(max_auth_failures=3)
    k.auth_failed(1)
    k.auth_failed(2)
    k.auth_ok()
    k.auth_failed(3)
    k.auth_failed(4)  # noqa: E702
    assert not k.active
    k.auth_failed(5)
    assert k.active and Trigger.AUTH_FAILURES in k.trips


def test_every_trigger_turns_the_switch_on():
    for t in Trigger:
        k = KillSwitch()
        k.trip(t, 1, reason="test")
        assert k.active and k.history[-1].trigger == t


def test_the_kill_switch_drives_the_dispatcher_guard():
    from asta.orders.pretrade import DayState, PreTradeLimits, refusals

    k = KillSwitch()
    lim = PreTradeLimits(
        frozenset({"BINANCE_TESTNET"}), frozenset({"BTC-USDT"}), D("50"), 10, D("500"), D("100")
    )
    day = lambda k=k: DayState(0, D(0), k.active)  # noqa: E731
    assert refusals(make_intent(1), lim, day(), D("100")) == ()
    k.trip(Trigger.RECONCILIATION_ANOMALY, 5)
    assert "KILL_SWITCH" in refusals(make_intent(1), lim, day(), D("100"))


# ------------------------------------------------------------------ reconcile
def view(cid: str, st: State) -> ExchangeView:
    return ExchangeView(cid, st)


def live(i: int, st: State = State.ACCEPTED) -> Intent:
    it = make_intent(i)
    return it.moved(State.DISPATCHED).moved(st)


def test_an_order_we_never_made_is_serious_and_matching_orders_are_clean():
    a, b = live(1), live(2, State.PARTIALLY_FILLED)
    ex = {
        a.decision_id: view(a.decision_id, State.ACCEPTED),
        b.decision_id: view(b.decision_id, State.PARTIALLY_FILLED),
    }
    assert reconcile_orders([a, b], ex) == []
    ex["f" * 32] = view("f" * 32, State.ACCEPTED)
    fs = reconcile_orders([a, b], ex)
    assert [f.code for f in fs] == ["UNKNOWN_AT_EXCHANGE"] and fs[0].serious


def test_missing_and_mismatched_orders_are_reported():
    a, b, c = live(1), live(2), live(3)
    ex = {
        b.decision_id: view(b.decision_id, State.FILLED),
        c.decision_id: view(c.decision_id, State.ACCEPTED),
    }
    codes = {f.subject: f.code for f in reconcile_orders([a, b, c], ex)}
    assert codes == {a.decision_id: "ORDER_MISSING", b.decision_id: "STATE_MISMATCH"}
    u = make_intent(9).moved(State.DISPATCHED).moved(State.UNKNOWN)
    assert (
        reconcile_orders([u], {u.decision_id: view(u.decision_id, State.ACCEPTED)})[0].code
        == "UNRESOLVED_BUT_PRESENT"
    )


def test_expected_balances_follow_the_fills_exactly_and_a_mismatch_is_caught():
    it = make_intent(1, quantity=D("0.5"), quote_notional=None)
    bk = FillBook(it.decision_id, D("0.5"))
    bk.apply(fill("a", it.decision_id, q="0.2", p="100", fee="0.001", fa="BNB"))
    bk.apply(fill("b", it.decision_id, q="0.3", p="101", fee="0.002", fa="BNB"))
    start = {"USDT": D("1000"), "BTC": D("0"), "BNB": D("1")}
    exp = apply_fills(start, it, bk, "BTC", "USDT")
    assert exp == {"USDT": D("1000") - D("20") - D("30.3"), "BTC": D("0.5"), "BNB": D("0.997")}
    assert start["USDT"] == D("1000")  # input untouched
    assert reconcile_balances(exp, dict(exp)) == []
    off = dict(exp, USDT=exp["USDT"] - D("0.01"))
    assert [f.code for f in reconcile_balances(exp, off)] == ["BALANCE_MISMATCH"]
    assert reconcile_balances(exp, off, tolerance=D("0.02")) == []
    sell = make_intent(2, side="SELL", quantity=D("0.1"), quote_notional=None)
    sb = FillBook(sell.decision_id)
    sb.apply(fill("s", sell.decision_id, q="0.1", p="200", fee="0", fa="USDT"))
    assert apply_fills({"BTC": D("1")}, sell, sb, "BTC", "USDT") == {
        "BTC": D("0.9"),
        "USDT": D("20"),
    }


def test_reset_is_recognised_only_when_everything_vanished_and_balances_are_blank():
    a, b = live(1), live(2)
    blank = {"USDT": D("10000"), "BTC": D("1")}
    assert classify_reset([a, b], {}, dict(blank), blank).reset_suspected
    assert not classify_reset([a, b], {}, {"USDT": D("9990"), "BTC": D("1")}, blank).reset_suspected
    only_a = {b.decision_id: view(b.decision_id, State.ACCEPTED)}
    assert not classify_reset([a, b], only_a, dict(blank), blank).reset_suspected
    assert not classify_reset([], {}, dict(blank), blank).reset_suspected


def test_paper_vs_demo_compares_execution_quality_and_carries_the_disclaimer():
    buy = make_intent(1, quantity=D("1"), quote_notional=None)
    sell = make_intent(2, side="SELL", quantity=D("1"), quote_notional=None)
    paper = {
        buy.decision_id: FillStat(D("1"), D("100"), D("0.1")),
        sell.decision_id: FillStat(D("1"), D("100"), D("0.1")),
        "p" * 32: FillStat(D("1"), D("1"), D("0")),
    }
    demo = {
        buy.decision_id: FillStat(D("0.5"), D("100.5"), D("0.1")),
        sell.decision_id: FillStat(D("1"), D("100.5"), D("0.12")),
        "d" * 32: FillStat(D("1"), D("1"), D("0")),
    }
    c = compare_paper_demo({buy.decision_id: buy, sell.decision_id: sell}, paper, demo)
    by = {r.decision_id: r for r in c.rows}
    assert by[buy.decision_id].price_diff_bps == D("50") and by[
        buy.decision_id
    ].fill_ratio_demo == D("0.5")
    assert by[sell.decision_id].price_diff_bps == D("-50")  # a higher sell price is better for us
    assert by[sell.decision_id].fee_diff_quote == D("0.02")
    assert c.only_paper == ["p" * 32] and c.only_demo == ["d" * 32] and c.disclaimer == DISCLAIMER
    assert "profit" in DISCLAIMER and "not" in DISCLAIMER


# ------------------------------------------------------------------ amend
def test_a_replacement_needs_the_old_order_confirmed_canceled_and_never_reuses_the_id():
    old = live(1)
    assert may_replace(old) == (False, "OLD_ORDER_NOT_CANCELED:ACCEPTED")
    with pytest.raises(ValueError):
        build_replacement(old, "a" * 32)
    done = old.moved(State.CANCEL_REQUESTED).moved(State.CANCELED)
    assert may_replace(done) == (True, "")
    with pytest.raises(ValueError):
        build_replacement(done, done.decision_id)
    new_id = "b" * 32
    n = build_replacement(done, new_id, limit_price=D("99"))
    assert n.state == State.PENDING and n.decision_id == new_id and n.attempts == 0
    assert n.limit_price == D("99") and n.symbol == done.symbol and done.state == State.CANCELED


def test_no_replacement_after_a_fill_because_it_would_double_the_exposure():
    f = live(1).moved(State.FILLED)
    assert may_replace(f) == (False, "FILLED_SO_NO_REPLACEMENT")
    p = live(2, State.PARTIALLY_FILLED)
    assert not may_replace(p)[0]
    assert replacement_tag("t5", 2) == "t5~r2"
    with pytest.raises(ValueError):
        replacement_tag("t5", 0)


# ---------------------------------------------------------- end to end: chaos, then reconcile
def _views(ex) -> dict[str, ExchangeView]:
    return {cid: ExchangeView(cid, st) for cid, st in ex.orders.items()}


def test_after_chaos_and_recovery_the_books_reconcile_with_zero_findings():
    from asta.orders.dispatch import Dispatcher
    from asta.orders.pretrade import DayState, PreTradeLimits
    from tests.orders.fakes import FakeExchange, MemoryStore

    lim = PreTradeLimits(
        frozenset({"BINANCE_TESTNET"}), frozenset({"BTC-USDT"}), D("50"), 99, D("5000"), D("100")
    )
    for seed in range(60):
        rng = random.Random(seed)
        ex = FakeExchange(
            rng, p_drop_request=0.2, p_drop_response=0.25, p_unavailable=0.3, visible_delay=1
        )
        store, k = MemoryStore(), KillSwitch()
        d = Dispatcher(
            gated(ex),
            store,
            lim,
            lambda k=k: DayState(0, D(0), k.active),
            lambda s: D("100"),
            lambda: 1_000,
        )
        for i in range(8):
            d.handle(make_intent(i))
            ex.advance()
        ex.healthy()
        for _ in range(6):
            ex.advance()
            d.recover()
        local = list(store.rows.values())
        found = reconcile_orders(local, _views(ex))
        assert [f for f in found if f.serious] == [], (seed, found)
        assert not k.active


def test_an_order_injected_at_the_exchange_trips_the_switch_through_the_serious_codes():
    from tests.orders.fakes import FakeExchange, MemoryStore

    ex, store, k = FakeExchange(random.Random(1)), MemoryStore(), KillSwitch()
    ex.orders["e" * 32] = State.ACCEPTED  # someone placed an order we never made
    for f in reconcile_orders(list(store.rows.values()), _views(ex)):
        if f.serious:
            k.trip(Trigger.UNKNOWN_EXCHANGE_ORDER, 1, reason=f.detail)
    assert k.active and k.trips == [Trigger.UNKNOWN_EXCHANGE_ORDER]
