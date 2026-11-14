"""M3-B-R2: backward-compatible intent-channel and vault hardening (no wire-format change)."""

from __future__ import annotations

import json

import pytest

from asta.orders import channel
from asta.orders.channel import (
    Envelope,
    FailClosedAuthenticator,
    MemoryReplayStore,
    Receiver,
    payload_bytes,
    seal,
)
from asta.security.redaction import SecretValue
from asta.security.vault import (
    SYNTHETIC_PREFIX,
    CredentialClass,
    MemoryBackend,
    NonSyntheticSecret,
    Permission,
    Vault,
    VaultIdentity,
    is_synthetic,
    synthetic_secret,
)
from tests.orders.fakes import make_intent
from tests.orders.test_m3b_offline import FakeAuth


def recv(auth, keys=("k1",), senders=("app-1",)) -> Receiver:
    return Receiver(
        auth,
        frozenset(senders),
        MemoryReplayStore(),
        allowed_key_ids=frozenset(keys),
        require_durable=False,
    )


def env(**kw) -> Envelope:
    base = dict(
        sender="app-1",
        key_id="k1",
        seq=1,
        nonce="n1",
        issued_at=1_000,
        expires_at=1_030,
        intent=make_intent(1),
    )
    base.update(kw)
    return Envelope(**base)  # type: ignore[arg-type]


AUTH = FakeAuth({"k1": b"a" * 32, "k2": b"b" * 32})


def test_the_wire_format_is_unchanged_and_old_messages_still_open():
    wire = seal(env(), AUTH)
    assert wire[:2] == (32).to_bytes(2, "big")  # len(tag) | tag | canonical payload, as before
    assert wire[2 + 32 :] == payload_bytes(env())
    assert recv(AUTH).open(wire, 1_005)[1] == ""


def test_a_key_not_allowed_for_this_receiver_is_refused_before_it_is_even_tried():
    class Spy(FakeAuth):
        called = 0

        def verify(self, key_id, payload, tag):
            Spy.called += 1
            return super().verify(key_id, payload, tag)

    spy = Spy({"k1": b"a" * 32, "k2": b"b" * 32})
    wire_k2 = seal(env(key_id="k2"), spy)
    assert recv(spy, keys=("k1",)).open(wire_k2, 1_005) == (None, "KEY_NOT_ALLOWED")
    assert Spy.called == 0  # not even handed to the authenticator
    assert (
        recv(spy, keys=("k2",)).open(wire_k2, 1_005)[1] == ""
    )  # the receiver bound to k2 accepts it
    with pytest.raises(TypeError):  # the allowlist has no default: it must be stated explicitly
        Receiver(spy, frozenset({"app-1"}), MemoryReplayStore(), require_durable=False)  # type: ignore[call-arg]
    assert (
        recv(spy, keys=()).open(wire_k2, 1_005)[1] == "KEY_NOT_ALLOWED"
    )  # empty = nothing allowed


def test_no_json_is_parsed_before_authentication(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("json parsed before the message was authenticated")

    monkeypatch.setattr(channel.json, "loads", boom)
    hostile = b'{"key_id":"k1",' + b"[" * 500 + b"not json"
    wire = (32).to_bytes(2, "big") + b"t" * 32 + hostile
    assert recv(AUTH).open(wire, 1_005) == (None, "NOT_AUTHENTIC")
    assert recv(AUTH).open((32).to_bytes(2, "big") + b"t" * 32 + b"{}", 1_005) == (
        None,
        "MALFORMED",
    )


def test_a_key_id_cannot_be_spoofed_from_inside_a_value():
    i = make_intent(1)
    sneaky = Envelope("app-1", "k1", 1, "n", 1_000, 1_030, i)
    p = payload_bytes(sneaky)
    # an escaped fake key id inside a string value is not a key id (its quotes are escaped)
    decoy = p.replace(b'"venue":"BINANCE_TESTNET"', b'"venue":"x\\"key_id\\":\\"k2\\""')
    assert b'"key_id":"k2"' not in decoy
    m = channel._KEY_ID.search(decoy)
    assert m is not None and m.group(1) == b"k1"


def test_an_authentic_but_non_canonical_payload_is_refused():
    p = payload_bytes(env())
    spaced = json.dumps(json.loads(p), sort_keys=True).encode()  # same content, different bytes
    assert spaced != p
    tag = AUTH.tag_for("k1", spaced)
    wire = len(tag).to_bytes(2, "big") + tag + spaced
    # the key id pattern needs the canonical form, so a re-spaced payload is refused up front
    assert recv(AUTH).open(wire, 1_005)[1] in {"MALFORMED", "NOT_CANONICAL"}
    dup = p.replace(b'"v":1', b'"v":1,"v":1')  # duplicate key, authentic tag over the odd bytes
    tag2 = AUTH.tag_for("k1", dup)
    wire2 = len(tag2).to_bytes(2, "big") + tag2 + dup
    assert recv(AUTH).open(wire2, 1_005)[1] == "NOT_CANONICAL"


def test_tag_length_bounds_and_default_deny_are_preserved():
    p = payload_bytes(env())
    for n in (0, 15, 129, 65535):
        assert (
            recv(AUTH).open(n.to_bytes(2, "big") + b"t" * min(n, 200) + p, 1_005)[1] == "MALFORMED"
        )
    wire = seal(env(), AUTH)
    assert recv(FailClosedAuthenticator()).open(wire, 1_005) == (None, "NOT_AUTHENTIC")


# ------------------------------------------------------------------ vault: synthetic only
def store(
    v: Vault,
    value: str,
    ident=VaultIdentity.BINANCE_SPOT_TESTNET,
    cls=CredentialClass.DEMO_BINANCE_SPOT_TESTNET,
):
    return v.store(ident, cls, {Permission.READ}, {"key": SecretValue(value)}, "tester")


def test_only_values_made_by_synthetic_secret_can_be_stored():
    v = Vault(MemoryBackend())
    assert store(v, synthetic_secret()).version == 1
    assert is_synthetic(synthetic_secret()) and SYNTHETIC_PREFIX == "SYNTH-"
    real_shaped = [
        "".join(chr(65 + (i * 7) % 26) if i % 3 else str(i % 10) for i in range(64)),  # 64 alnum, key-shaped, built at runtime
        "ak-live-0123456789abcdef", "", "SYNTH-", "SYNTH-short", "synth-" + "A" * 40,
        "SYNTH-" + "A" * 20 + " " + "B" * 5, "SYNTH-" + "A" * 20 + "\n", "xSYNTH-" + "A" * 40,
    ]  # fmt: skip
    for bad in real_shaped:
        w = Vault(MemoryBackend())
        with pytest.raises(NonSyntheticSecret):
            store(w, bad)
        assert w.backend.get(VaultIdentity.BINANCE_SPOT_TESTNET) is None  # nothing was stored
        assert (
            any(e.action == "store.refused" for e in w.log.entries())
            if hasattr(w.log, "entries")
            else True
        )


def test_a_refused_secret_is_never_logged_or_registered():
    from asta.security.redaction import REGISTRY

    v = Vault(MemoryBackend())
    secret = "RealLookingSecret" + "x" * 30
    with pytest.raises(NonSyntheticSecret):
        store(v, secret)
    assert secret not in repr(v.log.__dict__) and secret not in str(REGISTRY.__dict__)


def test_one_non_synthetic_part_among_synthetic_ones_refuses_the_whole_credential():
    v = Vault(MemoryBackend())
    with pytest.raises(NonSyntheticSecret):
        v.store(
            VaultIdentity.BINANCE_SPOT_TESTNET,
            CredentialClass.DEMO_BINANCE_SPOT_TESTNET,
            {Permission.READ},
            {"key": SecretValue(synthetic_secret()), "secret": SecretValue("not-synthetic-at-all-0123456789")}, "t",
        )  # fmt: skip
    assert v.backend.get(VaultIdentity.BINANCE_SPOT_TESTNET) is None
