"""ASTA-DECISION-007 workstreams B, C, D on real PostgreSQL: fills, durable replay, latched halt."""

from __future__ import annotations

import random
import threading
import uuid
from decimal import Decimal as D
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from asta.db.audit_store import verify_audit_chain
from asta.orders.boundary import ExposureBlocked, GatedExchange, InternalGate, PortKind
from asta.orders.channel import (
    MAX_BYTES,
    Envelope,
    FailClosedAuthenticator,
    MemoryReplayStore,
    Receiver,
    seal,
)
from asta.orders.dispatch import Dispatcher, Handled
from asta.orders.fills import Fill
from asta.orders.lifecycle import State
from asta.orders.ports import ExchangeView
from asta.orders.pretrade import DayState, PreTradeLimits
from asta.orders.sql_store import (
    FillConflict,
    FillOutcome,
    FillRejected,
    SqlFillStore,
    SqlIntentStore,
    SqlKillSwitch,
    SqlReplayStore,
)
from asta.orders.supervisor import Ingest, Reconciler, ingest_fill
from tests.integration.test_order_store import raw  # noqa: F401  (shared helper)
from tests.orders.fakes import FakeExchange, make_intent
from tests.orders.test_m3b_offline import FakeAuth

pytestmark = pytest.mark.db


@pytest.fixture
def sessions(migrated_engine) -> sessionmaker[Session]:
    return sessionmaker(migrated_engine, expire_on_commit=False)


@pytest.fixture
def salt() -> str:
    return uuid.uuid4().hex * 2


@pytest.fixture(autouse=True)
def clean_halt(migrated_engine):
    """The kill switch is one global row: reset it (superuser, replica role bypasses the latch
    triggers, exactly like the conftest reset of other append-only tables) before and after."""

    def reset() -> None:
        with migrated_engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            c.execute(text("UPDATE kill_switch_state SET active = false"))
            c.execute(text("DELETE FROM kill_switch_releasers"))

    reset()
    yield
    reset()


def accepted(store: SqlIntentStore, i: int, salt: str, **kw: Any):
    it = make_intent(i, session=salt, **kw)
    assert store.insert_if_absent(it)
    it = it.moved(State.DISPATCHED)
    store.save(it)
    it = it.moved(State.ACCEPTED)
    store.save(it)
    return it


def fl(it, tid: str, q: str = "0.4", p: str = "100", fee: str = "0.001", fa: str = "BNB") -> Fill:
    return Fill(tid, it.decision_id, D(q), D(p), D(fee), fa)


def state_of(sessions, it) -> str:
    with sessions() as db:
        return str(
            db.execute(
                text("SELECT state FROM order_intents WHERE decision_id=:d"), {"d": it.decision_id}
            ).scalar_one()
        )


def n_fills(sessions, it) -> int:
    with sessions() as db:
        return int(
            db.execute(
                text("SELECT count(*) FROM order_fills WHERE decision_id=:d"), {"d": it.decision_id}
            ).scalar_one()
        )


# ================================================================== B: financial integrity
def test_exact_duplicate_is_a_no_op_and_a_conflicting_duplicate_is_recorded_and_raised(
    sessions, salt
):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    f = fl(it, "T1")
    assert fs.record_fill(it.decision_id, f) == FillOutcome.STORED
    assert fs.record_fill(it.decision_id, f) == FillOutcome.DUPLICATE
    assert n_fills(sessions, it) == 1 and fs.conflicts(it.decision_id) == []
    bad = fl(it, "T1", p="101")  # same trade id, different price
    with pytest.raises(FillConflict):
        fs.record_fill(it.decision_id, bad)
    assert n_fills(sessions, it) == 1  # the original stands; the conflict is not absorbed...
    assert fs.conflicts(it.decision_id) == [(it.decision_id, "T1", "CONFLICT")]  # ...and not hidden
    with sessions() as db:
        row = db.execute(
            text("SELECT existing, offered FROM order_fill_conflicts WHERE decision_id=:d"),
            {"d": it.decision_id},
        ).one()
    assert row[0].startswith("T1|0.4|100|") and row[1].startswith("T1|0.4|101|")


def test_database_level_overfill_out_of_limit_and_wrong_state_guards_even_for_raw_sql(
    sessions, salt
):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    assert fs.record_fill(it.decision_id, fl(it, "a", q="0.7")) == FillOutcome.STORED
    with pytest.raises(FillRejected) as e:
        fs.record_fill(it.decision_id, fl(it, "b", q="0.4"))  # 1.1 > 1
    assert e.value.kind == "OVERFILL"
    with pytest.raises(DBAPIError):  # bypassing the Python store entirely
        raw(
            sessions,
            "INSERT INTO order_fills (decision_id, trade_id, quantity, price, fee, fee_asset) VALUES (:d, 'raw', 0.4, 100, 0, 'X')",
            d=it.decision_id,
        )
    assert n_fills(sessions, it) == 1
    # quote-sized order: cumulative quote value is bounded
    q = accepted(st, 2, salt, quote_notional=D("10"), quantity=None)
    assert fs.record_fill(q.decision_id, fl(q, "q1", q="0.05", p="100")) == FillOutcome.STORED  # 5
    with pytest.raises(FillRejected):
        fs.record_fill(q.decision_id, fl(q, "q2", q="0.06", p="100"))  # 5 + 6 > 10
    # LIMIT buy cannot fill above its limit
    lim = accepted(
        st, 3, salt, order_type="LIMIT", limit_price=D("100"), quantity=D("1"), quote_notional=None
    )
    with pytest.raises(FillRejected) as e2:
        fs.record_fill(lim.decision_id, fl(lim, "l1", p="100.5"))
    assert e2.value.kind == "OUT_OF_LIMIT"
    # fills for orders that cannot have traded
    pend = make_intent(4, session=salt, quantity=D("1"), quote_notional=None)
    assert st.insert_if_absent(pend)
    with pytest.raises(FillRejected) as e3:
        fs.record_fill(pend.decision_id, fl(pend, "p1"))
    assert e3.value.kind == "BAD_STATE"
    kinds = {k for _, _, k in fs.conflicts()} & {"OVERFILL", "OUT_OF_LIMIT", "BAD_STATE"}
    assert kinds == {"OVERFILL", "OUT_OF_LIMIT", "BAD_STATE"}  # every refusal is on the record


def test_fill_and_state_advance_together_partial_final_and_cancellation(sessions, salt):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    fs.record_fill(it.decision_id, fl(it, "a", q="0.4"))
    assert state_of(sessions, it) == "PARTIALLY_FILLED"
    fs.record_fill(it.decision_id, fl(it, "b", q="0.6"))
    assert state_of(sessions, it) == "FILLED" and fs.book(it.decision_id).summary().quantity == D(
        "1"
    )
    # partial fill, then cancel: later a late trade report still fits and the state stays CANCELED
    c = accepted(st, 2, salt, quantity=D("1"), quote_notional=None)
    fs.record_fill(c.decision_id, fl(c, "c1", q="0.3"))
    cur = st.get(c.decision_id)
    assert cur is not None
    cur = cur.moved(State.CANCEL_REQUESTED)
    st.save(cur)
    st.save(cur.moved(State.CANCELED))
    assert fs.record_fill(c.decision_id, fl(c, "c2", q="0.2")) == FillOutcome.STORED
    assert state_of(sessions, c) == "CANCELED"
    with pytest.raises(FillRejected):  # but never more than was ordered
        fs.record_fill(c.decision_id, fl(c, "c3", q="0.6"))


def test_history_is_immutable(sessions, salt):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    fs.record_fill(it.decision_id, fl(it, "a"))
    with pytest.raises(FillConflict):
        fs.record_fill(it.decision_id, fl(it, "a", q="0.3"))
    for sql in ("UPDATE order_fills SET price = 1", "DELETE FROM order_fills", "TRUNCATE order_fills",
                "UPDATE order_fill_conflicts SET kind = 'OVERFILL'", "DELETE FROM order_fill_conflicts", "TRUNCATE order_fill_conflicts"):  # fmt: skip
        with pytest.raises(DBAPIError):
            raw(sessions, sql)


def run_threads(fn, n: int):
    out: list[Any] = [None] * n
    gate = threading.Barrier(n)

    def work(i: int) -> None:
        gate.wait()
        try:
            out[i] = fn(i)
        except Exception as exc:  # noqa: BLE001
            out[i] = exc

    ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def test_concurrent_workers_same_trade_stored_once(sessions, salt):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    res = run_threads(lambda i: fs.record_fill(it.decision_id, fl(it, "T")), 8)
    assert res.count(FillOutcome.STORED) == 1 and res.count(FillOutcome.DUPLICATE) == 7
    assert n_fills(sessions, it) == 1


def test_concurrent_workers_never_overfill(sessions, salt):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    res = run_threads(lambda i: fs.record_fill(it.decision_id, fl(it, f"T{i}", q="0.3")), 8)
    stored = [r for r in res if r == FillOutcome.STORED]
    rejected = [r for r in res if isinstance(r, FillRejected)]
    assert len(stored) == 3 and len(rejected) == 5  # 0.3 x 3 = 0.9 <= 1 < 1.2
    assert fs.book(it.decision_id).summary().quantity == D("0.9")
    assert state_of(sessions, it) == "PARTIALLY_FILLED"
    assert len([c for c in fs.conflicts(it.decision_id) if c[2] == "OVERFILL"]) == 5


def test_concurrent_conflicting_reports_one_wins_the_rest_are_recorded(sessions, salt):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    res = run_threads(lambda i: fs.record_fill(it.decision_id, fl(it, "T", p=str(100 + i % 2))), 8)
    assert res.count(FillOutcome.STORED) == 1
    assert sum(isinstance(r, FillConflict) for r in res) + res.count(FillOutcome.DUPLICATE) == 7
    assert n_fills(sessions, it) == 1
    assert len(fs.conflicts(it.decision_id)) == sum(isinstance(r, FillConflict) for r in res)


def test_seeded_interleavings_with_duplicates_converge_to_the_same_ledger(sessions, salt):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    final: set[tuple[str, ...]] = set()
    for seed in range(12):
        it = accepted(st, 100 + seed, salt, quantity=D("1"), quote_notional=None)
        trades = [fl(it, f"t{k}", q="0.25") for k in range(4)]
        rng = random.Random(seed)
        seq = trades + [rng.choice(trades) for _ in range(6)]
        rng.shuffle(seq)
        for f in seq:
            fs.record_fill(it.decision_id, f)
        s = fs.book(it.decision_id).summary()
        final.add((str(s.quantity), str(s.trades), state_of(sessions, it)))
    assert len(final) == 1  # every interleaving ends in the same ledger
    assert next(iter(final))[1:] == ("4", "FILLED")


def test_a_crash_between_the_fill_and_the_state_advance_leaves_neither(sessions, salt, monkeypatch):
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    real = Session.commit

    class Boom(Exception):
        pass

    def crash_once(self):
        monkeypatch.setattr(Session, "commit", real)
        raise Boom("process died before commit")

    monkeypatch.setattr(Session, "commit", crash_once)
    with pytest.raises(Boom):
        fs.record_fill(it.decision_id, fl(it, "T", q="1"))
    assert n_fills(sessions, it) == 0 and state_of(sessions, it) == "ACCEPTED"  # nothing half-done
    assert (
        fs.record_fill(it.decision_id, fl(it, "T", q="1")) == FillOutcome.STORED
    )  # the retry is clean
    assert state_of(sessions, it) == "FILLED"


# ================================================================== C: durable intent security
def make_env(i=1, seq=1, nonce="n1", sender="app-1", issued=1_000, expires=1_030, salt="s" * 64):
    return Envelope(sender, "k1", seq, nonce, issued, expires, make_intent(i, session=salt))


def receiver(sessions, auth) -> Receiver:
    return Receiver(
        auth, frozenset({"app-1"}), SqlReplayStore(sessions), allowed_key_ids=frozenset({"k1"})
    )


def test_a_replay_is_refused_across_a_process_restart(sessions, salt):
    auth = FakeAuth({"k1": b"k" * 32})
    sender = "app-" + salt[:8]
    a = Receiver(
        auth, frozenset({sender}), SqlReplayStore(sessions), allowed_key_ids=frozenset({"k1"})
    )
    wire = seal(make_env(seq=5, nonce="x" + salt[:6], sender=sender, salt=salt), auth)
    assert a.open(wire, 1_005)[1] == ""
    restarted = Receiver(
        auth, frozenset({sender}), SqlReplayStore(sessions), allowed_key_ids=frozenset({"k1"})
    )  # new process, same database
    assert restarted.open(wire, 1_005) == (None, "REPLAYED_OR_OUT_OF_ORDER")
    older = seal(make_env(seq=4, nonce="o" + salt[:6], sender=sender, salt=salt), auth)
    assert restarted.open(older, 1_005)[1] == "REPLAYED_OR_OUT_OF_ORDER"
    reused = seal(make_env(seq=6, nonce="x" + salt[:6], sender=sender, salt=salt), auth)
    assert restarted.open(reused, 1_005)[1] == "NONCE_REUSED"


def test_concurrent_receivers_accept_one_message_exactly_once(sessions, salt):
    auth = FakeAuth({"k1": b"k" * 32})
    sender = "app-" + salt[:8]
    wire = seal(make_env(seq=1, nonce="c" + salt[:6], sender=sender, salt=salt), auth)
    res = run_threads(
        lambda i: Receiver(
            auth, frozenset({sender}), SqlReplayStore(sessions), allowed_key_ids=frozenset({"k1"})
        ).open(wire, 1_005)[1],
        8,
    )
    assert res.count("") == 1 and res.count("REPLAYED_OR_OUT_OF_ORDER") == 7


def test_forged_expired_oversized_wrong_sender_and_unauthenticated_are_refused_and_burn_nothing(
    sessions, salt
):
    auth = FakeAuth({"k1": b"k" * 32})
    sender = "app-" + salt[:8]
    r = Receiver(
        auth, frozenset({sender}), SqlReplayStore(sessions), allowed_key_ids=frozenset({"k1"})
    )
    good = seal(make_env(seq=1, nonce="g" + salt[:6], sender=sender, salt=salt), auth)
    forged = bytearray(good)
    forged[5] ^= 1  # inside the authentication tag
    assert r.open(bytes(forged), 1_005)[1] == "NOT_AUTHENTIC"
    other = FakeAuth({"k1": b"z" * 32})
    assert (
        r.open(seal(make_env(seq=1, nonce="h" + salt[:6], sender=sender, salt=salt), other), 1_005)[
            1
        ]
        == "NOT_AUTHENTIC"
    )
    assert r.open(good, 1_031)[1] == "EXPIRED"
    assert (
        r.open(
            seal(make_env(seq=1, nonce="i" + salt[:6], sender="intruder", salt=salt), auth), 1_005
        )[1]
        == "SENDER_NOT_ALLOWED"
    )
    assert r.open(b"\x00\x20" + b"t" * 32 + b"x" * (MAX_BYTES + 1), 1_005)[1] == "TOO_LARGE"
    assert r.open(b"\x00", 1_005)[1] == "MALFORMED"
    assert r.open(good, 1_005)[1] == ""  # none of the refusals consumed the sequence or the nonce


def test_without_a_valid_authenticator_nothing_is_ever_accepted(sessions, salt):
    real = FakeAuth({"k1": b"k" * 32})
    sender = "app-" + salt[:8]
    wire = seal(make_env(seq=1, nonce="f" + salt[:6], sender=sender, salt=salt), real)
    r = Receiver(
        FailClosedAuthenticator(),
        frozenset({sender}),
        SqlReplayStore(sessions),
        allowed_key_ids=frozenset({"k1"}),
    )
    assert r.open(wire, 1_005) == (None, "NOT_AUTHENTIC")
    with pytest.raises(NotImplementedError):
        FailClosedAuthenticator().tag_for("k1", b"x")


def test_a_non_durable_replay_store_is_refused_and_the_sequence_cannot_move_backwards(
    sessions, salt
):
    with pytest.raises(ValueError):
        Receiver(
            FailClosedAuthenticator(),
            frozenset({"a"}),
            MemoryReplayStore(),
            allowed_key_ids=frozenset({"k1"}),
        )
    SqlReplayStore(sessions).accept("seq-" + salt[:6], 9, "n", 2_000, 1_000)
    with pytest.raises(DBAPIError):
        raw(
            sessions,
            "UPDATE channel_senders SET high_seq = 1 WHERE sender = :s",
            s="seq-" + salt[:6],
        )
    with pytest.raises(DBAPIError):
        raw(sessions, "DELETE FROM channel_senders")


# ================================================================== D: kill switch, reconciliation
LIM = PreTradeLimits(
    frozenset({"BINANCE_TESTNET"}), frozenset({"BTC-USDT"}), D("50"), 99, D("5000"), D("100")
)


def disp(sessions, ex, kill) -> Dispatcher:
    gx = GatedExchange(ex, PortKind.SIMULATED, InternalGate(kill))
    return Dispatcher(
        gx,
        SqlIntentStore(sessions),
        LIM,
        lambda: DayState(0, D(0), False),
        lambda s: D("100"),
        lambda: 1_000,
    )


def authorize_releaser(sessions, name: str | None = None) -> None:
    """List a releaser. Default: the connection's own session user (the identity a release uses)."""
    who = name or SqlKillSwitch(sessions).session_actor()
    raw(
        sessions,
        "INSERT INTO kill_switch_releasers (name) VALUES (:n) ON CONFLICT DO NOTHING",
        n=who,
    )


def test_the_halt_latches_survives_restart_and_only_an_authorised_operator_with_a_reason_releases_it(
    sessions,
):
    k = SqlKillSwitch(sessions)
    assert not k.active()
    k.trip("RISK_HALT", "risk-engine", "drawdown halt")
    assert k.active() and SqlKillSwitch(sessions).active()  # a new process sees the same latch
    with pytest.raises(DBAPIError):
        raw(sessions, "UPDATE kill_switch_state SET active = false")  # no side door
    with pytest.raises(DBAPIError):
        k.release("I would like to trade now please")  # nobody is authorised by default
    authorize_releaser(sessions)
    with pytest.raises(DBAPIError):
        k.release("too short")
    with pytest.raises(DBAPIError):
        k.release("")
    assert k.active()
    assert k.release("reconciled by hand and reviewed the incident") is True
    assert not SqlKillSwitch(sessions).active()
    assert k.release("second release has nothing to do") is False
    h = k.history()
    assert [a for a, _, _ in h][-2:] == ["TRIP", "RELEASE"] and (
        "TRIP",
        "RISK_HALT",
        "risk-engine",
    ) in h


def test_halt_changes_and_refused_releases_are_in_the_audit_chain(sessions):
    k = SqlKillSwitch(sessions)
    authorize_releaser(sessions)
    k.trip("MANUAL", "alice", "testing the chain")
    with pytest.raises(DBAPIError):
        k.release("short")  # refused (reason too short) and logged
    k.release("chain test finished, releasing")
    with sessions() as db:
        acts = [
            str(a)
            for (a,) in db.execute(
                text(
                    "SELECT action FROM audit_events WHERE action LIKE 'killswitch.%' ORDER BY id DESC LIMIT 3"
                )
            ).all()
        ]
        ok, bad = verify_audit_chain(db)
    assert (
        acts == ["killswitch.release", "killswitch.release_refused", "killswitch.trip"]
        and ok
        and bad is None
    )


def test_a_latched_halt_stops_dispatch_in_every_worker_and_in_the_database_itself(sessions, salt):
    k = SqlKillSwitch(sessions)
    ex = FakeExchange(random.Random(1))
    st = SqlIntentStore(sessions)
    k.trip("RECONCILIATION_ANOMALY", "reconciler", "test")
    d = disp(sessions, ex, k)
    r = d.handle(make_intent(1, session=salt))
    assert r.outcome == Handled.REFUSED and "KILL_SWITCH" in r.reasons and ex.send_calls == 0
    # a second, careless worker that skips the gate still cannot advance an order to DISPATCHED
    it = make_intent(2, session=salt)
    assert st.insert_if_absent(it)
    with pytest.raises(ExposureBlocked):
        st.save(it.moved(State.DISPATCHED))
    with pytest.raises(DBAPIError):
        raw(
            sessions,
            "UPDATE order_intents SET state = 'DISPATCHED' WHERE decision_id = :d",
            d=it.decision_id,
        )
    authorize_releaser(sessions)
    k.release("incident reviewed, resuming simulated demo")
    assert disp(sessions, ex, k).handle(make_intent(3, session=salt)).state == State.ACCEPTED


def test_worker_recovery_after_a_restart_under_a_latched_halt_abandons_and_flushes(sessions, salt):
    k = SqlKillSwitch(sessions)
    rng = random.Random(3)
    ex = FakeExchange(
        rng, p_drop_response=1.0
    )  # replies lost: orders reach the exchange, state UNKNOWN
    d = disp(sessions, ex, k)
    d.handle(make_intent(1, session=salt))
    d.handle(make_intent(2, session=salt))
    assert {d.store.get(make_intent(i, session=salt).decision_id).state for i in (1, 2)} == {
        State.UNKNOWN
    }  # type: ignore[union-attr]
    resting = FakeExchange(rng)
    live = disp(sessions, resting, k)
    live.handle(make_intent(5, session=salt))  # one that rests at a healthy exchange
    k.trip("RISK_HALT", "risk-engine", "halt before the restart")
    for e in (ex, resting):
        e.healthy()
    before = (ex.send_calls, resting.send_calls)
    for e in (ex, resting):  # "restart": brand-new dispatchers and a brand-new kill-switch handle
        disp(sessions, e, SqlKillSwitch(sessions)).recover()
    assert (ex.send_calls, resting.send_calls) == before  # nothing was re-sent while halted
    for i in (1, 2):
        st = SqlIntentStore(sessions).get(make_intent(i, session=salt).decision_id)
        assert st is not None and st.state in {
            State.ACCEPTED,
            State.PARTIALLY_FILLED,
            State.FILLED,
            State.CANCELED,
            State.ABANDONED,
        }
    assert all(
        s != State.ACCEPTED for s in resting.orders.values()
    )  # the resting order was cancelled by the flush


def test_reconciler_halts_on_foreign_orders_immediately_and_on_persistent_mismatch(sessions, salt):
    k = SqlKillSwitch(sessions)
    st = SqlIntentStore(sessions)
    it = accepted(st, 1, salt)
    mine = {it.decision_id: ExchangeView(it.decision_id, State.ACCEPTED)}
    views: dict[str, ExchangeView] | None = dict(mine)
    rec = Reconciler(
        lambda: st.by_state(frozenset({State.ACCEPTED, State.PARTIALLY_FILLED, State.FILLED})),
        lambda: views,
        k,
    )
    rec.local = lambda: [
        x
        for x in st.by_state(frozenset({State.ACCEPTED, State.PARTIALLY_FILLED, State.FILLED}))
        if x.decision_id == it.decision_id
    ]
    assert not rec.cycle().halted and not k.active()
    views = None  # exchange unreachable: learn nothing, change nothing
    r = rec.cycle()
    assert r.skipped_exchange_unavailable and not k.active()
    views = {it.decision_id: ExchangeView(it.decision_id, State.FILLED)}  # lag: exchange is ahead
    assert not rec.cycle().halted
    views = dict(mine)  # caught up before the second look: the streak resets
    assert not rec.cycle().halted
    views = {it.decision_id: ExchangeView(it.decision_id, State.FILLED)}
    rec.cycle()
    assert rec.cycle().halted and k.active()  # persisted across two consecutive cycles
    # a foreign order halts at once (fresh switch state)
    k2 = SqlKillSwitch(sessions)
    authorize_releaser(sessions)
    k2.release("mismatch explained, releasing to test the next case")
    rec2 = Reconciler(
        lambda: [it], lambda: {**mine, "e" * 32: ExchangeView("e" * 32, State.ACCEPTED)}, k2
    )
    assert (
        rec2.cycle().halted
        and k2.active()
        and ("TRIP", "UNKNOWN_EXCHANGE_ORDER", "reconciler") in k2.history()
    )


def test_a_fill_conflict_or_overfill_halts_processing_through_the_ingest_path(sessions, salt):
    k = SqlKillSwitch(sessions)
    st, fs = SqlIntentStore(sessions), SqlFillStore(sessions)
    it = accepted(st, 1, salt, quantity=D("1"), quote_notional=None)
    assert ingest_fill(fs, k, it.decision_id, fl(it, "A", q="0.5")).status == Ingest.STORED
    assert ingest_fill(fs, k, it.decision_id, fl(it, "A", q="0.5")).status == Ingest.DUPLICATE
    assert not k.active()
    res = ingest_fill(fs, k, it.decision_id, fl(it, "A", q="0.5", p="120"))
    assert res.status == Ingest.HALTED and k.active()
    assert ("TRIP", "FILL_CONFLICT", "fill-ingest") in k.history()
    authorize_releaser(sessions)
    k.release("conflict investigated with the exchange statement")
    res2 = ingest_fill(fs, k, it.decision_id, fl(it, "B", q="0.9"))
    assert res2.status == Ingest.HALTED and ("TRIP", "FILL_REJECTED", "fill-ingest") in k.history()
