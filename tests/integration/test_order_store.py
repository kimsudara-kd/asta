"""Durable order-intent store on real PostgreSQL: constraints, triggers, concurrency, chaos."""

import threading
import uuid
from dataclasses import replace
from decimal import Decimal as D
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from asta.orders.lifecycle import ALLOWED, UNRESOLVED, State
from asta.orders.sql_store import SqlIntentStore
from tests.orders.fakes import MemoryStore, make_intent
from tests.orders.test_order_path import chaos

pytestmark = pytest.mark.db


@pytest.fixture
def sessions(migrated_engine) -> sessionmaker[Session]:
    return sessionmaker(migrated_engine, expire_on_commit=False)


@pytest.fixture
def salt() -> str:
    """A fresh session hash per test: decision ids never collide in the shared database."""
    return uuid.uuid4().hex * 2


@pytest.fixture
def store(sessions) -> SqlIntentStore:
    return SqlIntentStore(sessions)


def raw(sessions, sql: str, **params: Any):
    with sessions() as db:
        try:
            res = db.execute(text(sql), params)
            db.commit()
            return res
        except Exception:
            db.rollback()
            raise


# ------------------------------------------------------------------ schema == state machine
def test_seeded_transition_table_is_exactly_the_python_state_machine(sessions):
    with sessions() as db:
        rows = db.execute(text("SELECT src, dst FROM order_intent_transitions")).all()
    assert {(a, b) for a, b in rows} == {
        (s.value, d.value) for s, ds in ALLOWED.items() for d in ds
    }
    assert len(rows) == sum(len(v) for v in ALLOWED.values())


# ------------------------------------------------------------------ constraints
def test_claim_is_unique_and_returns_false_for_a_repeat(store, salt):
    i = make_intent(1, salt)
    assert store.insert_if_absent(i) is True
    assert store.insert_if_absent(i) is False
    assert store.get(i.decision_id) == i


@pytest.mark.parametrize(
    "bad",
    [
        {"environment": "LIVE"},
        {"environment": "PAPER"},
        {"decision_id": "not-hex"},
        {"decision_id": "A" * 32},
        {"side": "SHORT"},
        {"order_type": "STOP"},
        {"state": "WHATEVER"},
        {"quote_notional": None},  # neither size
        {"quantity": D("1")},  # both sizes
        {"quote_notional": D("-1")},
        {"attempts": -1},
    ],
)
def test_database_refuses_invalid_rows_even_if_the_application_did_not(sessions, salt, bad):
    base = {
        "decision_id": make_intent(2, salt).decision_id,
        "environment": "EXCHANGE_DEMO",
        "venue": "BINANCE_TESTNET",
        "symbol": "BTC-USDT",
        "side": "BUY",
        "order_type": "MARKET",
        "quote_notional": D("10"),
        "quantity": None,
        "limit_price": None,
        "state": "PENDING",
        "attempts": 0,
        "created_ts": 1,
    }
    base.update(bad)
    with pytest.raises(DBAPIError, match="violates check constraint"):
        raw(
            sessions,
            "INSERT INTO order_intents (decision_id, environment, venue, symbol, side, order_type,"
            " quote_notional, quantity, limit_price, state, attempts, created_ts)"
            " VALUES (:decision_id,"
            " :environment, :venue, :symbol, :side, :order_type, :quote_notional, :quantity,"
            " :limit_price, :state, :attempts, :created_ts)",
            **base,
        )


def test_a_limit_order_needs_a_positive_price(sessions, salt):
    with pytest.raises(DBAPIError, match="ck_oi_limit_price"):
        raw(
            sessions,
            "INSERT INTO order_intents (decision_id, environment, venue, symbol, side, order_type,"
            " quantity, state, created_ts) VALUES (:d, 'EXCHANGE_DEMO', 'V', 'S', 'BUY',"
            " 'LIMIT', 1,"
            " 'PENDING', 1)",
            d=make_intent(3, salt).decision_id,
        )


# ------------------------------------------------------------------ triggers
def test_an_illegal_transition_is_refused_by_the_database(store, salt):
    """Forge the row past the Python state machine: the DATABASE must still refuse it."""
    i = make_intent(4, salt)
    store.insert_if_absent(i)
    with pytest.raises(DBAPIError, match="illegal order intent transition"):
        store.save(replace(i, state=State.FILLED))  # PENDING -> FILLED: nothing was ever sent
    with pytest.raises(DBAPIError, match="illegal order intent transition"):
        store.save(replace(i, state=State.ACCEPTED))
    assert (got := store.get(i.decision_id)) is not None and got.state == State.PENDING


def test_identity_and_sizing_are_immutable_after_the_claim(sessions, store, salt):
    i = make_intent(5, salt)
    store.insert_if_absent(i)
    for col, val in [
        ("symbol", "ETH-USDT"),
        ("side", "SELL"),
        ("quote_notional", D("99")),
        ("venue", "OTHER"),
        ("created_ts", 7),
    ]:
        with pytest.raises(DBAPIError, match="immutable"):
            raw(
                sessions,
                f"UPDATE order_intents SET {col} = :v WHERE decision_id = :d",  # noqa: S608
                v=val,
                d=i.decision_id,
            )  # noqa: S608


def test_intents_are_never_deleted_or_truncated(sessions, store, salt):
    i = make_intent(6, salt)
    store.insert_if_absent(i)
    with pytest.raises(DBAPIError, match="never deleted"):
        raw(sessions, "DELETE FROM order_intents WHERE decision_id = :d", d=i.decision_id)
    with pytest.raises(DBAPIError, match="never deleted"):
        raw(sessions, "TRUNCATE order_intents CASCADE")


def test_every_change_is_recorded_by_trigger_in_an_append_only_log(sessions, store, salt):
    i = make_intent(7, salt)
    store.insert_if_absent(i)
    a = i.moved(State.DISPATCHED, attempts=1)
    store.save(a)
    store.save(a.moved(State.ACCEPTED))
    with sessions() as db:
        ev = db.execute(
            text(
                "SELECT from_state, to_state FROM order_intent_events"
                " WHERE decision_id = :d ORDER BY id"
            ),
            {"d": i.decision_id},
        ).all()
    assert [(a, b) for a, b in ev] == [
        (None, "PENDING"),
        ("PENDING", "DISPATCHED"),
        ("DISPATCHED", "ACCEPTED"),
    ]
    with pytest.raises(DBAPIError, match="append-only"):
        raw(sessions, "UPDATE order_intent_events SET note = 'x'")
    with pytest.raises(DBAPIError, match="append-only"):
        raw(sessions, "DELETE FROM order_intent_events")
    with pytest.raises(DBAPIError, match="append-only"):
        raw(sessions, "TRUNCATE order_intent_events")
    with pytest.raises(DBAPIError, match="append-only"):
        raw(sessions, "UPDATE order_intent_transitions SET dst = 'FILLED'")


# ------------------------------------------- the store contract, both implementations
@pytest.fixture(params=["memory", "sql"])
def any_store(request, sessions):
    return MemoryStore() if request.param == "memory" else SqlIntentStore(sessions)


def test_port_contract_roundtrip_and_by_state(any_store, salt):
    a, b = make_intent(10, salt), make_intent(11, salt, quote_notional=None, quantity=D("0.5"))
    assert any_store.insert_if_absent(a) and any_store.insert_if_absent(b)
    assert not any_store.insert_if_absent(a)
    any_store.save(a.moved(State.DISPATCHED, attempts=1))
    got = {
        x.decision_id: x for x in any_store.by_state(frozenset({State.PENDING, State.DISPATCHED}))
    }
    assert got[a.decision_id].state == State.DISPATCHED and got[a.decision_id].attempts == 1
    assert got[b.decision_id].quantity == D("0.5") and got[b.decision_id].quote_notional is None
    assert any_store.get("0" * 32) is None


def test_two_threads_racing_for_the_same_decision_produce_exactly_one_claim(sessions, salt):
    i = make_intent(12, salt)
    results: list[bool] = []
    barrier = threading.Barrier(8)

    def go() -> None:
        s = SqlIntentStore(sessions)
        barrier.wait()
        results.append(s.insert_if_absent(i))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1 and results.count(False) == 7


# ------------------------------------------- the chaos proof through the real database
class ChaosSqlStore(SqlIntentStore):
    """Adds the crash injection and inspection helpers the chaos scenario uses."""

    def __init__(self, sessions, salt) -> None:
        super().__init__(sessions)
        self.saves = 0
        self.crash_on_save: int | None = None
        self._ids = {make_intent(i, salt).decision_id for i in range(25)}

    @property
    def rows(self):
        out = {}
        for s in State:
            for r in self.by_state(frozenset({s})):
                if r.decision_id in self._ids:
                    out[r.decision_id] = r
        return out

    def by_state(self, states):
        # the shared test database also holds other tests' rows: this scenario sees only its own
        return [r for r in super().by_state(states) if r.decision_id in self._ids]

    def state_of(self, decision_id):
        got = self.get(decision_id)
        assert got is not None
        return got.state

    def save(self, intent) -> None:
        super().save(intent)
        self.saves += 1
        if self.crash_on_save is not None and self.saves == self.crash_on_save:
            self.crash_on_save = None
            from tests.orders.fakes import Crash

            raise Crash("after save")


@pytest.mark.parametrize("seed", range(12))
def test_exactly_once_under_chaos_with_the_real_database_store(sessions, seed):
    salt = uuid.uuid4().hex * 2
    store = ChaosSqlStore(sessions, salt)
    ex, st, ids = chaos(seed, store=store, session=salt)
    mine = set(store.rows)
    assert set(ex.orders) <= set(ids)
    assert not [r for r in store.rows.values() if r.state in UNRESOLVED]
    assert set(ex.orders) == mine  # exactly one exchange order per claimed decision
    for cid, seen in ex.orders.items():
        got = store.get(cid)
        assert got is not None and got.state == seen


# ------------------------------------------------------------------ fill ledger (migration 0007)
def test_fill_ledger_is_idempotent_append_only_and_rebuilds_the_book(store, sessions, salt):
    from asta.orders.fills import Fill
    from asta.orders.sql_store import SqlFillStore

    it = make_intent(1, session=salt, quantity=D("1"), quote_notional=None)
    assert store.insert_if_absent(it)
    fs = SqlFillStore(sessions)
    f1 = Fill("T-1", it.decision_id, D("0.4"), D("100.5"), D("0.001"), "BNB")
    assert fs.record(it.decision_id, f1) and not fs.record(it.decision_id, f1)  # repeat stored once
    assert fs.record(
        it.decision_id, Fill("T-2", it.decision_id, D("0.6"), D("101"), D("0"), "USDT")
    )
    book = fs.book(it.decision_id, D("1"))
    s = book.summary()
    assert (
        s.trades == 2
        and s.quantity == D("1")
        and s.fees_by_asset == {"BNB": D("0.001"), "USDT": D("0")}
    )
    assert not book.anomalies
    for sql in (
        "UPDATE order_fills SET quantity = 9",
        "DELETE FROM order_fills",
        "TRUNCATE order_fills",
    ):
        with pytest.raises(DBAPIError):
            raw(sessions, sql)


def test_fill_ledger_rejects_nonsense_and_orphans(store, sessions, salt):
    from asta.orders.fills import Fill
    from asta.orders.sql_store import SqlFillStore

    it = make_intent(2, session=salt)
    assert store.insert_if_absent(it)
    fs = SqlFillStore(sessions)
    for bad in (
        Fill("a", it.decision_id, D("0"), D("1"), D("0"), "X"),
        Fill("b", it.decision_id, D("1"), D("-1"), D("0"), "X"),
        Fill("c", it.decision_id, D("1"), D("1"), D("-0.1"), "X"),
    ):
        with pytest.raises(DBAPIError):
            fs.record(it.decision_id, bad)
    with pytest.raises(DBAPIError):  # a fill for an intent that was never claimed
        fs.record("f" * 32, Fill("d", "f" * 32, D("1"), D("1"), D("0"), "X"))
