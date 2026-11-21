"""Venue sync: trades ingestion (idempotent, paged, conflict-safe) and reconciliation (orders, balances, kill switch)."""

from __future__ import annotations

import sys
from decimal import Decimal as Dec
from pathlib import Path
from typing import Any

import pytest
from tests.orders.fakes import MemoryStore
from tests.security.test_credential_service import AUTH, Rig, lim, wait_listening  # noqa: F401

from asta.orders.boundary import ExecutionDenied, GatedExchange, InternalGate, PortKind
from asta.orders.extwire import (
    MAX_REPLY_BYTES,
    Blake2bAuthenticator,
    Reply,
    Request,
    open_reply,
    seal_reply,
)
from asta.orders.fills import Fill, FillBook
from asta.orders.lifecycle import Intent, State
from asta.orders.ports import QueryKind
from asta.orders.venue_sync import (
    expected_balances,
    fetch_trades,
    reconcile_session,
    snapshot_baseline,
    sync_order,
)
from asta.risk import gateway
from asta.risk.gateway import ExecutionGateway

sys.path.insert(0, str(Path(__file__).parents[2] / "proposals/binance_testnet_signer"))


class Ledger:
    """What SqlFillStore does, in memory: idempotent by trade id, conflict on different content, state follows the total."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.fills: dict[tuple[str, str], Fill] = {}

    def record_fill(self, decision_id: str, fill: Fill) -> Any:
        key = (decision_id, fill.trade_id)
        if key in self.fills:
            if self.fills[key] == fill:
                return "DUPLICATE"
            raise RuntimeError("same trade id, different content")
        self.fills[key] = fill
        rec = self.store.rows[decision_id]
        total = sum((f.quantity for (d, _), f in self.fills.items() if d == decision_id), Dec(0))
        self.store.rows[decision_id] = rec.moved(
            State.FILLED if total == rec.quantity else State.PARTIALLY_FILLED
        )
        return "NEW"

    def of(self, decision_id: str) -> list[Fill]:
        return [f for (d, _), f in self.fills.items() if d == decision_id]

    def current(self, decision_id: str) -> Intent | None:
        return self.store.rows.get(decision_id)


class Kill:
    def __init__(self) -> None:
        self.trips: list[tuple[str, str]] = []

    def trip(self, cause: str, actor: str = "system", reason: str = "") -> None:
        self.trips.append((cause, reason))


def setup(rig: Rig, kind: PortKind = PortKind.SIMULATED):
    store = MemoryStore()
    gx = GatedExchange(rig.port(), kind, InternalGate(rig.kill))
    return store, gx, Ledger(store), Kill()


def place(rig: Rig, store: MemoryStore, n: int, qty: str = "0.0002", px: str = "50000") -> Intent:
    i = lim(n, qty=qty, px=px)
    store.insert_if_absent(i)
    assert rig.port().send(i).value == "ACCEPTED"
    store.rows[i.decision_id] = i.moved(State.DISPATCHED).moved(State.ACCEPTED)
    return store.rows[i.decision_id]


# ------------------------------------------------------------------ ingestion
def test_trades_are_ingested_once_and_a_second_sync_is_all_duplicates():
    rig = Rig()
    store, gx, ledger, _ = setup(rig)
    i = place(rig, store, 1)
    rig.exchange.fill(i.decision_id, trades=2)
    first = sync_order(gx, ledger, i)
    assert (first.status, first.venue_trades, first.new, first.duplicates, first.conflicts) == (
        "OK",
        2,
        2,
        0,
        [],
    )
    again = sync_order(gx, ledger, i)
    assert (again.new, again.duplicates) == (0, 2) and len(ledger.of(i.decision_id)) == 2
    assert store.rows[i.decision_id].state is State.FILLED
    book = FillBook(i.decision_id, i.quantity)
    for f in ledger.of(i.decision_id):
        assert book.apply(f)
    assert book.summary().quantity == Dec("0.0002") and set(book.summary().fees_by_asset) == {"BTC"}


def test_many_trades_are_paged_and_an_absurd_number_is_refused_not_ingested():
    rig = Rig()
    store, gx, ledger, _ = setup(rig)
    i = place(rig, store, 1, qty="0.002", px="1000")
    rig.exchange.fill(i.decision_id, trades=20)  # 3 pages of 8
    kind, fills, overflow = fetch_trades(gx, i)
    assert kind is QueryKind.FOUND and len(fills) == 20 and overflow is False
    assert len({f.trade_id for f in fills}) == 20
    j = place(rig, store, 2, qty="0.02", px="1000")
    rig.exchange.fill(j.decision_id, trades=170)  # more than 20 pages of 8
    rep = sync_order(gx, ledger, j)
    assert rep.status == "TOO_MANY_TRADES" and not ledger.of(j.decision_id)


def test_a_trade_that_changes_content_is_a_conflict_never_absorbed():
    rig = Rig()
    store, gx, ledger, _ = setup(rig)
    i = place(rig, store, 1)
    rig.exchange.fill(i.decision_id)
    assert sync_order(gx, ledger, i).new == 1
    rig.exchange.trade_log[-1]["price"] = (
        "49999"  # the venue now claims a different price for the same trade id
    )
    rep = sync_order(gx, ledger, i)
    assert rep.conflicts and rep.new == 0 and ledger.of(i.decision_id)[0].price == Dec("50000")


def test_unavailable_not_found_and_denied_are_reported_never_raised(monkeypatch):
    rig = Rig()
    store, gx, ledger, _ = setup(rig)
    i = place(rig, store, 1)
    rig.exchange.fill(i.decision_id)
    rig.drop_reply = True
    assert sync_order(gx, ledger, i).status == "UNAVAILABLE"
    ghost = lim(9)
    store.insert_if_absent(ghost)
    assert sync_order(gx, ledger, ghost).status == "NOT_FOUND"
    ext = GatedExchange(
        rig.port(), PortKind.EXTERNAL, InternalGate(rig.kill)
    )  # gateway DENY as shipped
    calls = rig.channel_calls
    assert sync_order(ext, ledger, i).status.startswith("DENIED:") and rig.channel_calls == calls


# ------------------------------------------------------------------ reconciliation
def test_a_consistent_session_reconciles_clean_and_balances_follow_our_ledger():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    probe = lim(0)
    snap = snapshot_baseline(gx, probe)
    assert snap is not None
    base = {a: Dec(v) for a, v in snap.items()}
    assert base == {"USDT": Dec("10000")}
    i = place(rig, store, 1)
    rig.exchange.fill(i.decision_id, trades=2)
    rec = reconcile_session(gx, ledger, ledger.of, [i], base, kill, ledger.current)
    assert rec.ok and not rec.findings and not kill.trips
    exp = expected_balances(base, [store.rows[i.decision_id]], ledger.of)
    assert exp["USDT"] == Dec("10000") - Dec("0.0002") * Dec("50000") and exp["BTC"] == Dec(
        "0.0002"
    ) - Dec("0.0000002")


def test_a_balance_the_ledger_cannot_explain_latches_the_kill_switch():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    base = {"USDT": Dec("10000")}
    i = place(rig, store, 1)
    rig.exchange.fill(i.decision_id)
    rig.exchange.balances["USDT"] -= Dec(
        "1"
    )  # money left the venue account without a trade we know of
    rec = reconcile_session(gx, ledger, ledger.of, [i], base, kill, ledger.current)
    assert not rec.ok and any(f.code == "BALANCE_MISMATCH" and f.serious for f in rec.findings)
    assert (
        rec.tripped == "RECONCILIATION_ANOMALY"
        and kill.trips
        and kill.trips[0][0] == "RECONCILIATION_ANOMALY"
    )


def test_a_state_mismatch_and_a_trade_conflict_latch_the_kill_switch():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    i = place(rig, store, 1)
    rig.exchange.orders[i.decision_id]["status"] = (
        "CANCELED"  # the venue cancelled it; we believe it rests
    )
    rec = reconcile_session(
        gx, ledger, ledger.of, [i], {"USDT": Dec("10000")}, kill, ledger.current
    )
    assert any(f.code == "STATE_MISMATCH" for f in rec.findings) and rec.tripped
    rig2 = Rig()
    store2, gx2, ledger2, kill2 = setup(rig2)
    j = place(rig2, store2, 1)
    rig2.exchange.fill(j.decision_id)
    sync_order(gx2, ledger2, j)
    rig2.exchange.trade_log[-1]["qty"] = "0.0001"
    rec2 = reconcile_session(
        gx2, ledger2, ledger2.of, [j], {"USDT": Dec("10000")}, kill2, ledger2.current
    )
    assert any(o.conflicts for o in rec2.orders) and rec2.tripped and not rec2.ok


def test_a_venue_outage_is_unavailable_not_ok_and_does_not_trip_the_kill_switch():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    i = place(rig, store, 1)

    def broken(wire: bytes) -> bytes:
        raise TimeoutError

    gx2 = GatedExchange(
        type(rig.port())(broken, AUTH, "app", "k1", rig.clock),
        PortKind.SIMULATED,
        InternalGate(rig.kill),
    )
    rec = reconcile_session(
        gx2, ledger, ledger.of, [i], {"USDT": Dec("10000")}, kill, ledger.current
    )
    assert (
        not rec.ok and rec.unavailable and not kill.trips
    )  # "could not ask" is not evidence of a problem


def test_reads_are_denied_while_the_gateway_is_deny_and_allowed_once_the_testnet_cell_is_open(
    monkeypatch,
):
    class On:
        def active(self, scope: str) -> bool:
            return True

    rig = Rig()
    store, _, ledger, kill = setup(rig)
    i = place(rig, store, 1)
    ext = GatedExchange(
        rig.port(), PortKind.EXTERNAL, InternalGate(rig.kill, ExecutionGateway(On()))
    )
    with pytest.raises(ExecutionDenied):
        ext.balances(i)  # constant False: denied
    monkeypatch.setattr(gateway, "TESTNET_EXECUTION_ALLOWED", True)
    assert ext.balances(i) is not None
    rig.kill.flag = True  # even latched, reading the truth stays possible
    assert ext.balances(i) is not None
    with pytest.raises(ExecutionDenied):
        ext.balances(None)  # the intent is required


# ------------------------------------------------------------------ wire limits
def test_reply_data_is_bounded_and_requests_for_balances_use_the_no_order_id():
    ids = frozenset({"k1"})
    ok = seal_reply(
        Reply("k1", "n", "TRADES", "FOUND", data=(("1", "0.1", "50000", "0.0001", "BTC"),)), AUTH
    )
    got = open_reply(ok, AUTH, ids, "n", "TRADES")
    assert got is not None and got.data == (("1", "0.1", "50000", "0.0001", "BTC"),)
    for bad in (
        (("x",) * 9,),  # too many columns
        (("y" * 49,),),  # cell too long
        tuple(("a",) for _ in range(65)),  # too many rows
    ):
        with pytest.raises(ValueError):
            seal_reply(Reply("k1", "n", "TRADES", "FOUND", data=bad), AUTH)
    assert len(ok) < MAX_REPLY_BYTES
    with pytest.raises(ValueError):
        Request(
            "app", "k1", 1, "n", 1, 2, "BALANCES", "a" * 32, "BTC-USDT"
        )  # BALANCES must use the no-order id
    with pytest.raises(ValueError):
        Request("app", "k1", 1, "n", 1, 2, "QUERY", "0" * 32, "BTC-USDT")  # and nothing else may
    with pytest.raises(ValueError):
        Request("app", "k1", 1, "n", 1, 2, "TRADES", "a" * 32, "BTC-USDT", skip=-1)
    Request("app", "k1", 1, "n", 1, 2, "BALANCES", "0" * 32, "BTC-USDT")
    Blake2bAuthenticator({"k1": bytes(range(32))})


def test_an_order_the_venue_confirms_it_does_not_have_is_missing_and_serious():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    i = place(rig, store, 1)
    rig.exchange.orders.clear()  # e.g. the monthly testnet wipe: the venue ANSWERS "no such order"
    rec = reconcile_session(
        gx, ledger, ledger.of, [i], {"USDT": Dec("10000")}, kill, ledger.current
    )
    assert any(f.code == "ORDER_MISSING" and f.serious for f in rec.findings) and rec.tripped
