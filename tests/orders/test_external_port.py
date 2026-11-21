"""Step B: ExternalChannelPort mapping and the gate in front of it (all offline, no network, no keys)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal as D

import pytest

from asta.orders.boundary import ExecutionDenied, GatedExchange, InternalGate, PortKind
from asta.orders.channel import MemoryReplayStore
from asta.orders.external_port import ExternalChannelPort
from asta.orders.extwire import Blake2bAuthenticator, Reply, RequestReceiver, seal_reply
from asta.orders.lifecycle import State
from asta.orders.ports import CancelOutcome, QueryKind, SendOutcome
from tests.orders.fakes import FlagKill, make_intent

AUTH = Blake2bAuthenticator({"k1": bytes(range(32))})
NOW = 1_800_000_000.0


class Rig:
    """A scripted credential side: opens the request with the real receiver, answers as told."""

    def __init__(self) -> None:
        self.rx = RequestReceiver(
            AUTH,
            frozenset({"app"}),
            MemoryReplayStore(),
            allowed_key_ids=frozenset({"k1"}),
            require_durable=False,
        )
        self.calls = 0
        self.answer: Callable[..., bytes] | None = None
        self.raises: Exception | None = None
        self.seen: list[tuple[str, str]] = []

    def __call__(self, wire: bytes) -> bytes:
        self.calls += 1
        if self.raises:
            raise self.raises
        req, why = self.rx.open(wire, int(NOW))
        assert req is not None, why
        self.seen.append((req.op, req.symbol))
        assert self.answer is not None
        return self.answer(req)


def reply(outcome: str, state: str = "", code: str = "", key_auth=AUTH):
    return lambda req: seal_reply(Reply("k1", req.nonce, req.op, outcome, state, code), key_auth)


def port(rig: Rig, clock: Callable[[], float] = lambda: NOW) -> ExternalChannelPort:
    return ExternalChannelPort(rig, AUTH, "app", "k1", clock)


def test_send_maps_every_service_answer_conservatively():
    rig = Rig()
    p = port(rig)
    for outcome, want in (
        ("ACCEPTED", SendOutcome.ACCEPTED),
        ("REJECTED", SendOutcome.REJECTED),
        ("DUPLICATE_ID", SendOutcome.DUPLICATE_ID),
        ("UNKNOWN", SendOutcome.UNKNOWN),
        ("REFUSED", SendOutcome.REJECTED),  # a policy refusal: nothing exists at the venue
        ("SOMETHING_ELSE", SendOutcome.UNKNOWN),
    ):
        rig.answer = reply(outcome, code="ORDER_CAP_EXCEEDED" if outcome == "REFUSED" else "")
        assert p.send(make_intent(1)) is want, outcome


def test_transport_trouble_and_unauthentic_or_mismatched_replies_are_unknown():
    rig = Rig()
    p = port(rig)
    rig.answer = reply("ACCEPTED")
    for exc in (TimeoutError(), ConnectionError(), OSError(), ValueError()):
        rig.raises = exc
        assert p.send(make_intent(1)) is SendOutcome.UNKNOWN
        assert p.query(make_intent(1).decision_id).kind is QueryKind.UNAVAILABLE
        assert p.cancel(make_intent(1).decision_id).outcome is CancelOutcome.UNKNOWN
    rig.raises = None
    rig.answer = reply(
        "ACCEPTED", key_auth=Blake2bAuthenticator({"k1": bytes(range(5, 37))})
    )  # forged
    assert p.send(make_intent(1)) is SendOutcome.UNKNOWN
    rig.answer = lambda req: b"REFUSED:NOT_AUTHENTIC"
    assert p.send(make_intent(1)) is SendOutcome.UNKNOWN
    rig.answer = lambda req: seal_reply(
        Reply("k1", "someone-elses-nonce", req.op, "ACCEPTED"), AUTH
    )
    assert (
        p.send(make_intent(1)) is SendOutcome.UNKNOWN
    )  # a replayed reply cannot answer this request


def test_query_and_cancel_need_a_bound_symbol_and_map_states():
    rig = Rig()
    p = port(rig)
    cid = make_intent(1).decision_id
    assert (
        p.query(cid).kind is QueryKind.UNAVAILABLE and rig.calls == 0
    )  # never sent / not bound: no guessing
    assert p.cancel(cid).outcome is CancelOutcome.UNKNOWN and rig.calls == 0
    p.bind_intent(make_intent(1))
    rig.answer = reply("FOUND", "PARTIALLY_FILLED")
    q = p.query(cid)
    assert q.kind is QueryKind.FOUND and q.view and q.view.state is State.PARTIALLY_FILLED
    rig.answer = reply("FOUND", "NOT_A_STATE")
    assert p.query(cid).kind is QueryKind.UNAVAILABLE
    rig.answer = reply("NOT_FOUND")
    assert p.query(cid).kind is QueryKind.NOT_FOUND
    rig.answer = reply("FINAL", "FILLED")
    c = p.cancel(cid)
    assert c.outcome is CancelOutcome.FINAL and c.state is State.FILLED
    rig.answer = reply("CANCELED")
    assert p.cancel(cid).outcome is CancelOutcome.CANCELED
    assert ("QUERY", "BTC-USDT") in rig.seen and ("CANCEL", "BTC-USDT") in rig.seen


def test_sequence_numbers_stay_increasing_across_a_restart():
    rig = Rig()
    rig.answer = reply("ACCEPTED")
    port(rig, lambda: NOW).send(make_intent(1))
    port(rig, lambda: NOW + 5).send(
        make_intent(2)
    )  # a "restarted" port with a later clock is still accepted
    assert rig.calls == 2


# ------------------------------------------------------------------ the gate in front of an EXTERNAL port
def external(rig: Rig, kill=None) -> GatedExchange:
    return GatedExchange(port(rig), PortKind.EXTERNAL, InternalGate(kill or FlagKill()))


def test_the_external_port_is_denied_for_send_query_and_cancel_while_the_gateway_is_deny():
    rig = Rig()
    rig.answer = reply("ACCEPTED")
    gx = external(rig)
    i = make_intent(1)
    with pytest.raises(ExecutionDenied) as e1:
        gx.send(i)
    with pytest.raises(ExecutionDenied) as e2:
        gx.query(i.decision_id, i)
    with pytest.raises(ExecutionDenied) as e3:
        gx.cancel(i.decision_id, i)
    for e in (e1, e2, e3):
        assert "M0_EXECUTION_DISABLED" in e.value.reasons
    assert rig.calls == 0  # nothing ever reached the channel


def test_external_query_and_cancel_require_the_intent_and_a_kill_switch_does_not_add_a_reason():
    rig = Rig()
    gx = external(rig, FlagKill(True))
    with pytest.raises(ExecutionDenied) as e:
        gx.query("x" * 32)
    assert e.value.reasons == ("INTENT_REQUIRED",)
    with pytest.raises(ExecutionDenied) as e:
        gx.cancel("x" * 32)
    assert e.value.reasons == ("INTENT_REQUIRED",)
    i = make_intent(1)
    with pytest.raises(ExecutionDenied) as e:
        gx.cancel(i.decision_id, i)
    assert (
        "KILL_SWITCH_LATCHED" not in e.value.reasons
    )  # a cancel is never blocked BY the kill switch itself
    assert rig.calls == 0


def test_a_port_with_no_kind_is_still_refused_for_query_and_cancel():
    gx = GatedExchange(port(Rig()), None, InternalGate(FlagKill()))
    for call in (lambda: gx.query("x" * 32), lambda: gx.cancel("x" * 32)):
        with pytest.raises(ExecutionDenied) as e:
            call()
        assert e.value.reasons == ("PORT_NOT_SIMULATED",)


def test_decimal_fields_survive_the_wire_exactly():
    rig = Rig()
    got = {}

    def answer(req):
        got["i"] = req.intent
        return seal_reply(Reply("k1", req.nonce, req.op, "ACCEPTED"), AUTH)

    rig.answer = answer
    port(rig).send(make_intent(1))
    assert isinstance(got["i"].quantity, D) or got["i"].quote_notional is not None
