"""Step C: with the reviewed constant ON (monkeypatched, it ships False) and the runtime enablement active, an EXTERNAL
port passes the boundary for the testnet cell ONLY; every other condition still closes it. Offline, no credentials."""

from __future__ import annotations

import random
from decimal import Decimal as D

import pytest

from asta.orders.boundary import ExecutionDenied, GatedExchange, InternalGate, PortKind
from asta.orders.dispatch import Dispatcher, Handled
from asta.orders.lifecycle import State
from asta.orders.pretrade import DayState, PreTradeLimits
from asta.risk import gateway
from asta.risk.gateway import ExecutionGateway
from tests.orders.fakes import FakeExchange, FlagKill, MemoryStore, make_intent

LIM = PreTradeLimits(
    frozenset({"BINANCE_TESTNET"}), frozenset({"BTC-USDT"}), D("50"), 99, D("5000"), D("100")
)
DAY = DayState(0, D(0), False)


class Switch:
    def __init__(self, on: bool = True) -> None:
        self.on = on

    def active(self, scope: str) -> bool:
        return self.on and scope == "BINANCE_TESTNET"


@pytest.fixture(autouse=True)
def constant_on(monkeypatch):
    monkeypatch.setattr(gateway, "TESTNET_EXECUTION_ALLOWED", True)


def rig(on: bool = True, kill: FlagKill | None = None):
    ex, sw, k = FakeExchange(random.Random(1)), Switch(on), kill or FlagKill()
    gx = GatedExchange(ex, PortKind.EXTERNAL, InternalGate(k, ExecutionGateway(sw)))
    d = Dispatcher(gx, MemoryStore(), LIM, lambda: DAY, lambda s: D("100"), lambda: 1_000)
    return ex, sw, k, gx, d


def test_when_everything_holds_the_testnet_order_goes_through_the_boundary():
    ex, _, _, _, d = rig()
    r = d.handle(make_intent(1))
    assert r.outcome == Handled.SENT and r.state == State.ACCEPTED and ex.send_calls == 1


def test_without_the_runtime_enablement_nothing_reaches_the_port_for_any_call():
    ex, _, _, gx, d = rig(on=False)
    r = d.handle(make_intent(1))
    assert r.state == State.REJECTED and ex.send_calls == 0
    i = make_intent(2)
    for call in (
        lambda: gx.send(i),
        lambda: gx.query(i.decision_id, i),
        lambda: gx.cancel(i.decision_id, i),
    ):
        with pytest.raises(ExecutionDenied) as e:
            call()
        assert "TESTNET_NOT_ENABLED" in e.value.reasons
    assert ex.send_calls == 0


def test_switching_the_enablement_off_closes_the_port_immediately():
    ex, sw, _, gx, d = rig()
    i = make_intent(1)
    assert d.handle(i).state == State.ACCEPTED
    gx.query(i.decision_id, i)  # allowed while enabled
    sw.on = False
    with pytest.raises(ExecutionDenied):
        gx.query(i.decision_id, i)
    with pytest.raises(ExecutionDenied):
        gx.cancel(i.decision_id, i)


def test_a_latched_kill_switch_blocks_sends_but_never_a_cancel_or_a_query():
    ex, _, k, gx, d = rig()
    i = make_intent(1)
    assert d.handle(i).state == State.ACCEPTED
    k.flag = True
    with pytest.raises(ExecutionDenied) as e:
        gx.send(make_intent(2))
    assert "KILL_SWITCH_LATCHED" in e.value.reasons
    assert gx.query(i.decision_id, i).kind is not None  # learning the truth is allowed
    gx.cancel(i.decision_id, i)  # reducing exposure is allowed


def test_any_other_venue_or_environment_is_denied_even_when_everything_else_is_on():
    ex, _, _, gx, _ = rig()
    for bad in (make_intent(1, venue="BINANCE"), make_intent(2, venue="OKX")):
        with pytest.raises(ExecutionDenied) as e:
            gx.send(bad)
        assert "VENUE_NOT_ALLOWED" in e.value.reasons
    assert ex.send_calls == 0


def test_a_default_gateway_still_denies_even_with_the_constant_on():
    ex = FakeExchange(random.Random(1))
    gx = GatedExchange(ex, PortKind.EXTERNAL, InternalGate(FlagKill()))  # no enablement source
    with pytest.raises(ExecutionDenied) as e:
        gx.send(make_intent(1))
    assert "TESTNET_NOT_ENABLED" in e.value.reasons and ex.send_calls == 0
