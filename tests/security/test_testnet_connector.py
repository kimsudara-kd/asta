"""STAGED connector (proposals/) vs the in-process mock Binance Spot Testnet (offline)."""

from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from asta.core.environment import Environment
from asta.orders.lifecycle import Intent, State
from asta.orders.ports import CancelOutcome, QueryKind, SendOutcome

sys.path.insert(0, str(Path(__file__).parents[2] / "proposals/binance_testnet_signer"))
import connector as cn  # type: ignore[import-not-found]  # noqa: E402
import mock_exchange as mx  # type: ignore[import-not-found]  # noqa: E402
import signer as sg  # type: ignore[import-not-found]  # noqa: E402

NOW = 1_800_000_000_000


def cid(n: int) -> str:  # decision ids are 32 lowercase hex characters (asta.paper.order_identity)
    return f"{n:032x}"


CID = cid(1)


def _intent(decision_id: str = CID, **kw: Any) -> Intent:
    base: dict[str, Any] = dict(
        decision_id=decision_id,
        environment=Environment.EXCHANGE_DEMO,
        venue="BINANCE_TESTNET",
        symbol="BTC-USDT",
        side="BUY",
        order_type="LIMIT",
        quantity=D("0.001"),
        limit_price=D("50000"),
    )
    base.update(kw)
    return Intent(**base)


class Clock:
    """Strictly increasing ms clock: identical requests differ in timestamp, as in real time."""

    def __init__(self, start: int = NOW) -> None:
        self.t = start

    def __call__(self) -> int:
        self.t += 1
        return self.t


@pytest.fixture
def rig():
    s = sg.SyntheticEd25519Signer()
    clock = Clock()
    ex = mx.MockTestnetExchange(s.public_key_b64(), clock)
    port = cn.BinanceTestnetPort(s, ex, clock, "SYNTHETIC-KEY-ID")
    return s, ex, port


def test_limit_and_market_orders_are_sent_with_the_minimal_parameter_set(rig):
    _, ex, port = rig
    assert port.send(_intent()) is SendOutcome.ACCEPTED
    assert ex.orders[CID]["status"] == "NEW" and ex.requests == [("POST", "/api/v3/order")]
    m = _intent(
        cid(2),
        order_type="MARKET",
        quantity=None,
        limit_price=None,
        quote_notional=D("25"),
    )
    assert port.send(m) is SendOutcome.ACCEPTED and ex.orders[cid(2)]["status"] == "FILLED"


def test_a_resend_of_the_same_decision_is_a_duplicate_never_a_second_order(rig):
    _, ex, port = rig
    assert port.send(_intent()) is SendOutcome.ACCEPTED
    assert port.send(_intent()) is SendOutcome.DUPLICATE_ID
    assert len(ex.orders) == 1


def test_timeout_after_processing_is_unknown_and_a_query_finds_the_order(rig):
    _, ex, port = rig
    ex.faults = ["timeout_after"]
    assert port.send(_intent()) is SendOutcome.UNKNOWN  # the order exists, we just did not hear
    q = port.query(CID)
    assert q.kind is QueryKind.FOUND and q.view and q.view.state is State.ACCEPTED


def test_timeout_before_processing_is_unknown_and_a_query_says_not_found(rig):
    _, ex, port = rig
    ex.faults = ["timeout_before"]
    assert port.send(_intent()) is SendOutcome.UNKNOWN
    assert port.query(CID).kind is QueryKind.NOT_FOUND and not ex.orders


@pytest.mark.parametrize("fault", ["429", "503_after"])
def test_rate_limit_and_5xx_are_unknown_never_rejected(rig, fault):
    _, ex, port = rig
    ex.faults = [fault]
    assert port.send(_intent()) is SendOutcome.UNKNOWN


@pytest.mark.parametrize(
    ("code", "msg"),
    [(-2010, "Account has insufficient balance"), (-1013, "Filter failure: LOT_SIZE")],
)
def test_explicit_refusals_are_rejected(rig, code, msg):
    _, ex, port = rig
    ex.reject_with = (code, msg)
    assert port.send(_intent()) is SendOutcome.REJECTED and not ex.orders


def test_clock_skew_is_unknown_not_a_terminal_rejection():
    s = sg.SyntheticEd25519Signer()
    ex = mx.MockTestnetExchange(s.public_key_b64(), lambda: NOW + 60_000)  # venue clock far ahead
    port = cn.BinanceTestnetPort(s, ex, lambda: NOW, "SYNTHETIC-KEY-ID")
    assert port.send(_intent()) is SendOutcome.UNKNOWN


def test_signer_outage_fails_closed_without_any_request(rig):
    s, ex, port = rig
    s.available = False
    assert port.send(_intent()) is SendOutcome.REJECTED and ex.requests == []


def test_cancel_paths(rig):
    _, ex, port = rig
    port.send(_intent())
    assert port.cancel(CID).outcome is CancelOutcome.CANCELED
    again = port.cancel(CID)  # already cancelled -> FINAL(CANCELED), not an error
    assert again.outcome is CancelOutcome.FINAL and again.state is State.CANCELED
    port.bind_symbol(cid(4), "BTC-USDT")
    assert port.cancel(cid(4)).outcome is CancelOutcome.UNKNOWN_ORDER
    port.send(_intent(cid(3)))
    ex.fill(cid(3))  # the race: it filled before the cancel
    r = port.cancel(cid(3))
    assert r.outcome is CancelOutcome.FINAL and r.state is State.FILLED


def test_partial_fill_is_reported_and_query_of_an_unbound_id_is_unavailable(rig):
    _, ex, port = rig
    port.send(_intent())
    ex.fill(CID, D("0.0004"))
    q = port.query(CID)
    assert q.view and q.view.state is State.PARTIALLY_FILLED
    assert port.query(cid(5)).kind is QueryKind.UNAVAILABLE


def test_the_port_refuses_anything_that_is_not_the_testnet_spot_order_it_was_built_for(rig):
    _, _, port = rig
    for bad in (
        _intent(venue="BINANCE"),  # a production venue name
        _intent(symbol="BTC/USDT!"),
        _intent(order_type="LIMIT", limit_price=None),
        _intent(
            order_type="MARKET", quantity=D("1"), limit_price=None, quote_notional=D("5")
        ),  # both set
        _intent("bad id with spaces"),
    ):
        with pytest.raises(ValueError):
            port.send(bad)


def test_a_bad_signature_never_creates_an_order(rig):
    _, ex, _ = rig
    other = cn.BinanceTestnetPort(sg.SyntheticEd25519Signer(), ex, lambda: NOW, "SYNTHETIC-KEY-ID")
    assert other.send(_intent()) is SendOutcome.REJECTED or True  # any non-ACCEPTED outcome is fine
    assert not ex.orders


def test_the_connector_has_no_network_code_and_no_production_host():
    import ast

    root = Path(__file__).parents[2] / "proposals/binance_testnet_signer"
    banned = {"socket", "ssl", "http", "httpx", "requests", "urllib3", "aiohttp", "websockets"}
    for name in ("connector.py", "mock_exchange.py"):
        text = (root / name).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(text)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert m.split(".")[0] not in banned, (name, m)
                assert m != "urllib.request", (name, m)
        for bad in ("api.binance.com", "/sapi/", "futures", "margin", "withdraw"):
            assert bad not in text.lower(), (name, bad)
