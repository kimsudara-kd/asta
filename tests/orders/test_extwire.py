"""Step B wire format: authenticate-before-parse, canonical bytes, replay/expiry, reply binding (pure, offline)."""

from __future__ import annotations

import json
from decimal import Decimal as D

import pytest

from asta.core.environment import Environment
from asta.orders.channel import MemoryReplayStore
from asta.orders.extwire import (
    Blake2bAuthenticator,
    Reply,
    Request,
    RequestReceiver,
    open_reply,
    seal_reply,
    seal_request,
)
from asta.orders.lifecycle import Intent

KEY = bytes(range(32))
AUTH = Blake2bAuthenticator({"k1": KEY, "k2": bytes(range(1, 33))})
CID = "a" * 32
NOW = 1_800_000_000


def intent(**kw) -> Intent:
    base = dict(
        decision_id=CID, environment=Environment.EXCHANGE_DEMO, venue="BINANCE_TESTNET",
        symbol="BTC-USDT", side="BUY", order_type="LIMIT", quantity=D("0.001"), limit_price=D("50000"),
    )  # fmt: skip
    base.update(kw)
    return Intent(**base)  # type: ignore[arg-type]


def req(op="SEND", seq=1, nonce="n1", **kw) -> Request:
    base = dict(
        sender="app", key_id="k1", seq=seq, nonce=nonce, issued_at=NOW, expires_at=NOW + 30, op=op,
        client_id=CID, symbol="BTC-USDT", intent=intent() if op == "SEND" else None,
    )  # fmt: skip
    base.update(kw)
    return Request(**base)


def rx(**kw) -> RequestReceiver:
    return RequestReceiver(
        AUTH, frozenset({"app"}), MemoryReplayStore(), allowed_key_ids=frozenset({"k1"}),
        require_durable=False, **kw,
    )  # fmt: skip


def test_roundtrip_for_every_operation():
    for op in ("SEND", "QUERY", "CANCEL"):
        r = rx()
        got, why = r.open(seal_request(req(op), AUTH), NOW)
        assert why == "" and got is not None and got.op == op and got.client_id == CID
    assert got is not None and got.intent is None  # a CANCEL carries no intent


def test_authenticate_before_parse_tamper_and_wrong_key_are_refused():
    wire = bytearray(seal_request(req(), AUTH))
    wire[-5] ^= 1  # flip a payload byte
    assert rx().open(bytes(wire), NOW)[1] == "NOT_AUTHENTIC"
    other = Blake2bAuthenticator({"k1": bytes(range(2, 34))})
    assert rx().open(seal_request(req(), other), NOW)[1] == "NOT_AUTHENTIC"
    assert rx().open(seal_request(req(key_id="k2"), AUTH), NOW)[1] == "KEY_NOT_ALLOWED"
    assert rx().open(b"\x00", NOW)[1] == "MALFORMED"
    assert rx().open(b"x" * 5000, NOW)[1] == "TOO_LARGE"


def test_replay_reuse_expiry_future_and_window_are_refused():
    r = rx()
    w1 = seal_request(req(seq=5, nonce="n1"), AUTH)
    assert r.open(w1, NOW)[1] == ""
    assert r.open(w1, NOW)[1] == "REPLAYED_OR_OUT_OF_ORDER"
    assert r.open(seal_request(req(seq=4, nonce="n2"), AUTH), NOW)[1] == "REPLAYED_OR_OUT_OF_ORDER"
    assert r.open(seal_request(req(seq=6, nonce="n1"), AUTH), NOW)[1] == "NONCE_REUSED"
    assert rx().open(seal_request(req(), AUTH), NOW + 31)[1] == "EXPIRED"
    assert (
        rx().open(seal_request(req(issued_at=NOW + 100, expires_at=NOW + 120), AUTH), NOW)[1]
        == "FROM_THE_FUTURE"
    )
    assert rx().open(seal_request(req(expires_at=NOW + 500), AUTH), NOW)[1] == "BAD_VALIDITY_WINDOW"
    assert rx().open(seal_request(req(sender="evil"), AUTH), NOW)[1] == "SENDER_NOT_ALLOWED"


def test_a_durable_store_is_required_by_default_and_ids_must_match_the_intent():
    with pytest.raises(ValueError):
        RequestReceiver(
            AUTH, frozenset({"app"}), MemoryReplayStore(), allowed_key_ids=frozenset({"k1"})
        )
    with pytest.raises(ValueError):
        req(client_id="b" * 32)  # intent.decision_id differs
    with pytest.raises(ValueError):
        req("QUERY", intent=intent())  # only SEND carries an intent
    with pytest.raises(ValueError):
        req("SEND", intent=None)
    with pytest.raises(ValueError):
        req("TRANSFER", intent=None)


def test_non_canonical_or_extra_fields_are_refused_even_with_a_valid_tag():
    good = json.loads(seal_request(req("QUERY"), AUTH)[34:])
    for mutate in (lambda b: b.update(extra="x"), lambda b: b.update(op="DROP")):
        body = dict(good)
        mutate(body)
        payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        wire = (
            len(AUTH.tag_for("k1", payload)).to_bytes(2, "big")
            + AUTH.tag_for("k1", payload)
            + payload
        )
        assert rx().open(wire, NOW)[1] in ("MALFORMED",)
    spaced = json.dumps(good, sort_keys=True).encode()  # valid JSON, valid tag, but not canonical
    wire = len(AUTH.tag_for("k1", spaced)).to_bytes(2, "big") + AUTH.tag_for("k1", spaced) + spaced
    assert rx().open(wire, NOW)[1] in ("NOT_CANONICAL", "MALFORMED")


def test_a_reply_is_bound_to_its_request_and_operation():
    ids = frozenset({"k1"})
    wire = seal_reply(Reply("k1", "n1", "SEND", "ACCEPTED"), AUTH)
    ok = open_reply(wire, AUTH, ids, "n1", "SEND")
    assert ok is not None and ok.outcome == "ACCEPTED"
    assert open_reply(wire, AUTH, ids, "n2", "SEND") is None  # another request's nonce
    assert open_reply(wire, AUTH, ids, "n1", "CANCEL") is None  # another operation
    assert open_reply(wire, AUTH, frozenset({"k2"}), "n1", "SEND") is None
    assert open_reply(b"REFUSED:NOT_AUTHENTIC", AUTH, ids, "n1", "SEND") is None
    forged = seal_reply(
        Reply("k1", "n1", "SEND", "ACCEPTED"), Blake2bAuthenticator({"k1": bytes(range(9, 41))})
    )
    assert open_reply(forged, AUTH, ids, "n1", "SEND") is None


def test_authenticator_rejects_weak_keys_and_odd_key_ids():
    for bad in ({"k": b"short"}, {"bad id": KEY}, {"k": b"x" * 65}):
        with pytest.raises(ValueError):
            Blake2bAuthenticator(bad)
