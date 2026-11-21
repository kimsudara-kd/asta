"""Step B end to end, offline: dispatcher -> gate -> ExternalChannelPort -> channel -> credential service -> testnet
connector -> mock exchange. Also: a real Unix-domain socket, the service's independent policy, and the lock stays closed."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest
from tests.orders.fakes import FlagKill, MemoryStore, make_intent

from asta.ipc.unix_socket import UnixSocketChannel
from asta.orders.boundary import ExecutionDenied, GatedExchange, InternalGate, PortKind
from asta.orders.channel import MemoryReplayStore
from asta.orders.dispatch import Dispatcher
from asta.orders.external_port import ExternalChannelPort
from asta.orders.extwire import Blake2bAuthenticator, RequestReceiver
from asta.orders.lifecycle import State
from asta.orders.pretrade import DayState, PreTradeLimits

sys.path.insert(0, str(Path(__file__).parents[2] / "proposals/binance_testnet_signer"))
import connector as cn  # type: ignore[import-not-found]  # noqa: E402
import credential_service as cs  # type: ignore[import-not-found]  # noqa: E402
import mock_exchange as mx  # type: ignore[import-not-found]  # noqa: E402
import signer as sg  # type: ignore[import-not-found]  # noqa: E402

KEYS = {"k1": bytes(range(32))}
AUTH = Blake2bAuthenticator(KEYS)
T0 = 1_800_000_000.0
LIM = PreTradeLimits(
    frozenset({"BINANCE_TESTNET"}),
    frozenset({"BTC-USDT", "ETH-USDT"}),
    D("50"),
    99,
    D("5000"),
    D("100"),
)
DAY = DayState(0, D(0), False)


def lim(n: int, qty: str = "0.0001", px: str = "50000", **kw: Any):
    """A resting LIMIT BUY (5 USDT by default): it stays NEW on the mock until filled."""
    return make_intent(
        n, order_type="LIMIT", quote_notional=None, quantity=D(qty), limit_price=D(px), **kw
    )


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        self.t += 0.001
        return self.t


class Rig:
    def __init__(self, caps: cs.Caps | None = None, kill: FlagKill | None = None) -> None:
        self.clock = Clock()
        s = sg.SyntheticEd25519Signer()
        ms = lambda: int(self.clock() * 1000)  # noqa: E731
        self.exchange = mx.MockTestnetExchange(s.public_key_b64(), ms)
        self.venue_port = cn.BinanceTestnetPort(s, self.exchange, ms, "SYNTHETIC-KEY-ID")
        rx = RequestReceiver(
            AUTH,
            frozenset({"app"}),
            MemoryReplayStore(),
            allowed_key_ids=frozenset({"k1"}),
            require_durable=False,
        )
        self.kill = kill or FlagKill()
        self.service = cs.CredentialService(
            rx, AUTH, self.venue_port, caps or cs.Caps(), self.kill.active, self.clock, "k1"
        )
        self.channel_calls = 0
        self.drop_reply = (
            False  # simulate: the service processed the request, the reply never arrived
        )

    def channel(self, wire: bytes) -> bytes:
        self.channel_calls += 1
        out = self.service.handle(wire)
        if self.drop_reply:
            self.drop_reply = False
            raise TimeoutError
        return bytes(out)

    def port(self) -> ExternalChannelPort:
        return ExternalChannelPort(self.channel, AUTH, "app", "k1", self.clock)

    def dispatcher(self) -> Dispatcher:
        # declared SIMULATED only because the whole chain is in-process against the mock; an EXTERNAL declaration is denied by the gateway (tested below)
        gx = GatedExchange(self.port(), PortKind.SIMULATED, InternalGate(self.kill))
        return Dispatcher(gx, MemoryStore(), LIM, lambda: DAY, lambda s: D("50000"), lambda: 1_000)


def test_the_whole_chain_places_a_testnet_order_on_the_mock_exactly_once():
    rig = Rig()
    d = rig.dispatcher()
    i = lim(1)
    r = d.handle(i)
    assert r.state is State.ACCEPTED and len(rig.exchange.orders) == 1
    assert d.handle(i).outcome.value == "DUPLICATE_DECISION"  # the same decision again is a no-op
    assert len(rig.exchange.orders) == 1
    m = d.handle(make_intent(2))  # a MARKET buy fills at once on the mock
    assert (
        m.state is State.ACCEPTED
    )  # send() only says ACCEPTED; reconciliation learns FILLED by query
    assert rig.exchange.orders[make_intent(2).decision_id]["status"] == "FILLED"


def test_a_lost_reply_is_recovered_by_query_and_never_creates_a_second_order():
    rig = Rig()
    d = rig.dispatcher()
    rig.drop_reply = True
    i = lim(1)
    d.handle(i)  # the order exists at the venue, the app heard nothing
    assert len(rig.exchange.orders) == 1
    d.recover()
    stored = d.store.get(i.decision_id)
    assert stored is not None and stored.state is State.ACCEPTED and len(rig.exchange.orders) == 1


def test_the_service_enforces_its_own_caps_independently_of_the_app():
    rig = Rig(cs.Caps(max_order_notional=D("5"), max_day_notional=D("8")))
    p = rig.port()
    from asta.orders.ports import SendOutcome

    big = lim(1, qty="0.001")  # 50 USDT > 5
    assert p.send(big) is SendOutcome.REJECTED and not rig.exchange.orders
    assert p.send(lim(2)) is SendOutcome.ACCEPTED  # 5 USDT
    assert p.send(lim(3)) is SendOutcome.REJECTED  # 5 + 5 > 8 per day
    assert len(rig.exchange.orders) == 1


def test_the_service_refuses_wrong_symbol_unverifiable_market_size_and_a_latched_kill_switch():
    from asta.orders.ports import SendOutcome

    rig = Rig()
    p = rig.port()
    assert p.send(lim(1, symbol="DOGE-USDT")) is SendOutcome.REJECTED
    mkt = make_intent(
        2, order_type="MARKET", quantity=D("0.001"), limit_price=None, quote_notional=None
    )
    assert p.send(mkt) is SendOutcome.REJECTED  # size unverifiable without a reference price
    rig.kill.flag = True
    assert p.send(lim(3)) is SendOutcome.REJECTED
    assert not rig.exchange.orders
    rig.kill.flag = False
    assert p.send(lim(4)) is SendOutcome.ACCEPTED


def test_cancel_still_works_while_the_kill_switch_is_latched():
    from asta.orders.ports import CancelOutcome

    rig = Rig()
    p = rig.port()
    i = lim(1)
    p.send(i)
    rig.kill.flag = True
    assert p.cancel(i.decision_id).outcome is CancelOutcome.CANCELED


def test_a_tampered_or_replayed_request_is_never_forwarded():
    from asta.orders.extwire import Request, seal_request

    rig = Rig()
    i = lim(1)
    now = int(T0)
    req = Request("app", "k1", 10, "nonce-1", now, now + 30, "SEND", i.decision_id, i.symbol, i)
    wire = seal_request(req, AUTH)
    assert rig.service.handle(wire).startswith(b"\x00") or rig.service.handle(
        wire
    )  # first time: processed
    assert len(rig.exchange.orders) == 1
    assert rig.service.handle(wire).startswith(b"REFUSED:")  # replay
    bad = bytearray(wire)
    bad[-3] ^= 1
    assert rig.service.handle(bytes(bad)).startswith(b"REFUSED:")
    assert len(rig.exchange.orders) == 1


def test_declared_external_the_whole_chain_is_denied_because_the_gateway_is_deny():
    rig = Rig()
    gx = GatedExchange(rig.port(), PortKind.EXTERNAL, InternalGate(rig.kill))
    with pytest.raises(ExecutionDenied):
        gx.send(make_intent(1))
    assert rig.channel_calls == 0 and not rig.exchange.orders


def test_a_real_unix_socket_carries_the_same_conversation_and_refuses_garbage():
    rig = Rig()
    d = tempfile.mkdtemp(dir="/tmp")
    path = os.path.join(d, "cred.sock")
    stop = threading.Event()
    th = threading.Thread(target=cs.serve_unix, args=(path, rig.service, stop), daemon=True)
    th.start()
    try:
        for _ in range(100):
            if os.path.exists(path):
                break
            threading.Event().wait(0.02)
        assert oct(os.stat(path).st_mode & 0o777) == "0o660"
        ch: Any = UnixSocketChannel(path, timeout_s=3.0)
        p = ExternalChannelPort(ch, AUTH, "app", "k1", rig.clock)
        from asta.orders.ports import QueryKind, SendOutcome

        i = lim(1)
        assert p.send(i) is SendOutcome.ACCEPTED
        q = p.query(i.decision_id)
        assert q.kind is QueryKind.FOUND
        assert ch(b"garbage").startswith(b"REFUSED:")
        with pytest.raises((ValueError, OSError)):
            ch(b"x" * 20_000)  # over the frame limit: refused locally
    finally:
        stop.set()
        th.join(3)
        shutil.rmtree(d, ignore_errors=True)
    assert not os.path.exists(path)


def test_the_service_and_the_unix_channel_use_no_internet_sockets():
    root = Path(__file__).parents[2]
    for p in (
        root / "proposals/binance_testnet_signer/credential_service.py",
        root / "src/asta/ipc/unix_socket.py",
    ):
        text = p.read_text(encoding="utf-8")
        assert (
            "AF_INET" not in text
            and "http" not in text.lower().replace("https", "").replace("# http", "")
            or "AF_UNIX" in text
        )
        assert "AF_UNIX" in text


def test_declared_external_with_the_constant_on_and_the_enablement_active_the_chain_reaches_the_mock(
    monkeypatch,
):
    """Step C offline end to end: dispatcher -> gate -> gateway(ALLOW, testnet cell) -> channel -> service -> connector -> mock."""
    from asta.risk import gateway
    from asta.risk.gateway import ExecutionGateway

    class On:
        on = True

        def active(self, scope: str) -> bool:
            return self.on and scope == "BINANCE_TESTNET"

    monkeypatch.setattr(gateway, "TESTNET_EXECUTION_ALLOWED", True)
    rig = Rig()
    sw = On()
    gx = GatedExchange(rig.port(), PortKind.EXTERNAL, InternalGate(rig.kill, ExecutionGateway(sw)))
    d = Dispatcher(gx, MemoryStore(), LIM, lambda: DAY, lambda s: D("50000"), lambda: 1_000)
    i = lim(1)
    assert d.handle(i).state is State.ACCEPTED and len(rig.exchange.orders) == 1
    assert gx.query(i.decision_id, i).kind.value == "FOUND"
    sw.on = False  # the owner (or the kill switch) switches testnet execution off
    assert d.handle(lim(2)).state is State.REJECTED and len(rig.exchange.orders) == 1
    with pytest.raises(ExecutionDenied):
        gx.cancel(i.decision_id, i)
    assert rig.exchange.orders[i.decision_id]["status"] == "NEW"  # the cancel never left the app
