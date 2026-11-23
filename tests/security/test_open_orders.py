"""OPEN_ORDERS: read-only listing of the orders resting at the venue; an order we never created is serious."""

from __future__ import annotations

import sys
from decimal import Decimal as Dec
from pathlib import Path

import pytest
from tests.security.test_credential_service import Rig, lim
from tests.security.test_venue_sync import Kill, Ledger, place, setup

from asta.orders.boundary import ExecutionDenied, GatedExchange, InternalGate, PortKind
from asta.orders.extwire import Request
from asta.orders.ports import QueryKind
from asta.orders.venue_sync import reconcile_session
from asta.risk import gateway
from asta.risk.gateway import ExecutionGateway

sys.path.insert(0, str(Path(__file__).parents[2] / "proposals/binance_testnet_signer"))

BASE = {"USDT": Dec("10000")}


def rogue(rig: Rig, cid: str, symbol: str = "BTCUSDT") -> None:
    """An order at the venue that our system never created (placed by someone else)."""
    rig.exchange.orders[cid] = {
        "symbol": symbol, "clientOrderId": cid, "side": "BUY", "type": "LIMIT", "origQty": "1",
        "executedQty": "0", "status": "NEW", "orderId": 9_000 + len(rig.exchange.orders),
    }  # fmt: skip


def test_the_listing_returns_only_open_orders_of_that_symbol():
    rig = Rig()
    store, gx, ledger, _ = setup(rig)
    a, b = place(rig, store, 1), place(rig, store, 2)
    rig.exchange.fill(b.decision_id)  # finished: no longer open
    kind, ids, more = rig.port().open_orders("BTC-USDT")
    assert (kind, ids, more) == (QueryKind.FOUND, {a.decision_id}, False)
    assert rig.port().open_orders("ETH-USDT") == (QueryKind.FOUND, set(), False)


def test_an_open_order_we_never_created_is_serious_and_latches_the_kill_switch():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    i = place(rig, store, 1)
    rogue(rig, "web_stranger_1")
    rec = reconcile_session(gx, ledger, ledger.of, [i], BASE, kill, ledger.current)
    hit = [f for f in rec.findings if f.code == "UNKNOWN_AT_EXCHANGE"]
    assert [f.subject for f in hit] == ["web_stranger_1"] and hit[0].serious
    assert not rec.ok and rec.tripped == "RECONCILIATION_ANOMALY" and kill.trips


def test_our_own_open_orders_are_not_strangers_even_in_a_state_outside_the_ledger_states():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    i = place(rig, store, 1)
    other = lim(2)  # sent, then the app crashed before learning the outcome: state UNKNOWN locally
    assert rig.port().send(other).value == "ACCEPTED"
    rec = reconcile_session(
        gx, ledger, ledger.of, [i], BASE, kill, ledger.current,
        known_ids=[i.decision_id, other.decision_id],
    )  # fmt: skip
    assert not [f for f in rec.findings if f.code == "UNKNOWN_AT_EXCHANGE"] and not kill.trips
    # without that knowledge the same order WOULD be flagged: pass every id we created
    rec2 = reconcile_session(gx, ledger, ledger.of, [i], BASE, Kill(), ledger.current)
    assert [f.subject for f in rec2.findings if f.code == "UNKNOWN_AT_EXCHANGE"] == [
        other.decision_id
    ]


def test_symbols_to_scan_can_be_wider_than_the_symbols_we_traded():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    i = place(rig, store, 1)
    rogue(rig, "eth_stranger", "ETHUSDT")
    quiet = reconcile_session(gx, ledger, ledger.of, [i], BASE, Kill(), ledger.current)
    assert not [f for f in quiet.findings if f.code == "UNKNOWN_AT_EXCHANGE"]  # BTC only
    wide = reconcile_session(
        gx, ledger, ledger.of, [i], BASE, kill, ledger.current,
        scan_symbols=["BTC-USDT", "ETH-USDT"],
    )  # fmt: skip
    assert [f.subject for f in wide.findings if f.code == "UNKNOWN_AT_EXCHANGE"] == ["eth_stranger"]


def test_an_unreadable_listing_is_unavailable_never_serious(monkeypatch):
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    i = place(rig, store, 1)
    rig.exchange.faults = ["429"]  # the venue refuses the listing
    assert rig.port().open_orders("BTC-USDT") == (QueryKind.UNAVAILABLE, set(), False)
    monkeypatch.setattr(
        gx, "open_orders", lambda sym, intent=None: (QueryKind.UNAVAILABLE, set(), False)
    )
    rec = reconcile_session(gx, ledger, ledger.of, [i], BASE, kill, ledger.current)
    assert any("open orders" in u for u in rec.unavailable) and not rec.ok and not kill.trips
    assert not any(f.code == "UNKNOWN_AT_EXCHANGE" for f in rec.findings)
    # a malformed answer is not guessed at either
    bad = {"clientOrderId": 5, "symbol": "BTCUSDT", "status": "NEW", "executedQty": "0",
           "origQty": "1", "side": "BUY", "type": "LIMIT", "orderId": 1}  # fmt: skip
    rig.exchange.orders["bad"] = bad
    assert rig.venue_port.open_orders("BTC-USDT") == (QueryKind.UNAVAILABLE, [], False)


def test_more_open_orders_than_a_reply_can_carry_is_flagged_not_silently_cut():
    rig = Rig()
    store, gx, ledger, kill = setup(rig)
    i = place(rig, store, 1)
    for n in range(70):
        rogue(rig, f"stranger_{n:03d}")
    kind, ids, more = rig.port().open_orders("BTC-USDT")
    assert kind is QueryKind.FOUND and len(ids) == 64 and more
    rec = reconcile_session(gx, ledger, ledger.of, [i], BASE, kill, ledger.current)
    assert any(f.code == "OPEN_ORDERS_OVERFLOW" and f.serious for f in rec.findings)
    assert kill.trips


def test_the_service_refuses_a_symbol_outside_its_allow_list():
    rig = Rig()
    assert rig.port().open_orders("DOGE-USDT") == (QueryKind.UNAVAILABLE, set(), False)
    assert rig.port().open_orders("not a symbol") == (QueryKind.UNAVAILABLE, set(), False)


def test_it_is_a_read_it_places_nothing_and_signs_only_the_listing_path():
    rig = Rig()
    rig.port().open_orders("BTC-USDT")
    assert rig.exchange.requests == [("GET", "/api/v3/openOrders")] and not rig.exchange.orders


def test_the_gate_applies_denied_while_deny_allowed_when_the_cell_is_open_and_intent_required(
    monkeypatch,
):
    class On:
        def active(self, scope: str) -> bool:
            return True

    rig = Rig()
    store, _, _, _ = setup(rig)
    i = place(rig, store, 1)
    ext = GatedExchange(
        rig.port(), PortKind.EXTERNAL, InternalGate(rig.kill, ExecutionGateway(On()))
    )
    with pytest.raises(ExecutionDenied):
        ext.open_orders("BTC-USDT", i)
    monkeypatch.setattr(gateway, "TESTNET_EXECUTION_ALLOWED", True)
    assert ext.open_orders("BTC-USDT", i)[1] == {i.decision_id}
    rig.kill.flag = True  # reading the truth stays possible while latched
    assert ext.open_orders("BTC-USDT", i)[0] is QueryKind.FOUND
    with pytest.raises(ExecutionDenied):
        ext.open_orders("BTC-USDT", None)


def test_the_wire_rules_open_orders_uses_the_no_order_id_and_only_it():
    Request("app", "k1", 1, "n", 1, 2, "OPEN_ORDERS", "0" * 32, "BTC-USDT")
    with pytest.raises(ValueError):
        Request("app", "k1", 1, "n", 1, 2, "OPEN_ORDERS", "a" * 32, "BTC-USDT")
    with pytest.raises(ValueError):
        Request("app", "k1", 1, "n", 1, 2, "CANCEL", "0" * 32, "BTC-USDT")


def test_the_ledger_helper_is_the_same_one_the_sync_tests_use():
    assert Ledger is not None
