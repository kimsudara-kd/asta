"""Intent channel wire format: app VM -> credential VM (design s.2). Pure encoding and checks.

One message = one ``Envelope`` carrying one order intent. The credential side re-validates
EVERYTHING and trusts nothing, so this module only defines (a) a canonical byte encoding with a hard
size limit, (b) replay protection (strictly increasing sequence per sender, a validity window, a
nonce that is never accepted twice) and (c) an ``Authenticator`` PORT: the channel is useless until
a reviewed implementation of that port exists, and no implementation lives in this repository.
Fail closed everywhere: any malformed, oversized, stale, replayed or unauthenticated message is
refused with a reason code and never reaches the dispatcher.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from asta.core.environment import Environment
from asta.orders.lifecycle import Intent

VERSION = 1
MAX_BYTES = 2_048  # an intent is tiny; anything larger is hostile or broken
MAX_NONCE_MEMORY = 10_000


class Authenticator(Protocol):
    """Reviewed implementation required (M3-B). ``tag_for`` is used by the sender only."""

    def tag_for(self, key_id: str, payload: bytes) -> bytes: ...

    def verify(self, key_id: str, payload: bytes, tag: bytes) -> bool: ...


class FailClosedAuthenticator:
    """The ONLY authenticator shipped in this repository: it authenticates nothing. With it every
    message is refused: the required state until a reviewed implementation exists (M3-B)."""

    def tag_for(self, key_id: str, payload: bytes) -> bytes:
        raise NotImplementedError("no authenticator exists in this build")

    def verify(self, key_id: str, payload: bytes, tag: bytes) -> bool:
        return False


class ReplayStore(Protocol):
    """Replay/expiry state. ``accept`` is atomic: it either records (sender, seq, nonce) and returns
    "" or returns a refusal reason and changes nothing. ``durable`` must be True in any real use."""

    durable: bool

    def accept(self, sender: str, seq: int, nonce: str, expires_at: int, now: int) -> str: ...


class MemoryReplayStore:
    """Process-local state, tests only: a restart forgets it, so Receiver refuses it by default."""

    durable = False

    def __init__(self) -> None:
        self._last_seq: dict[str, int] = {}
        self._nonces: list[tuple[str, str]] = []

    def accept(self, sender: str, seq: int, nonce: str, expires_at: int, now: int) -> str:
        if seq <= self._last_seq.get(sender, 0):
            return "REPLAYED_OR_OUT_OF_ORDER"
        if (sender, nonce) in self._nonces:
            return "NONCE_REUSED"
        self._last_seq[sender] = seq
        self._nonces.append((sender, nonce))
        del self._nonces[:-MAX_NONCE_MEMORY]
        return ""


@dataclass(frozen=True)
class Envelope:
    sender: str  # which app instance
    key_id: str
    seq: int  # strictly increasing per sender
    nonce: str  # unique per message (never reused)
    issued_at: int  # unix seconds
    expires_at: int
    intent: Intent


_FIELDS = (
    "decision_id", "environment", "venue", "symbol", "side", "order_type",
    "quote_notional", "quantity", "limit_price", "created_ts",
)  # fmt: skip


def _dec(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


def payload_bytes(env: Envelope) -> bytes:
    """Canonical encoding: sorted keys, no whitespace, decimals as plain strings, UTF-8."""
    i = env.intent
    body = {
        "v": VERSION, "sender": env.sender, "key_id": env.key_id, "seq": env.seq,
        "nonce": env.nonce, "issued_at": env.issued_at, "expires_at": env.expires_at,
        "intent": {
            "decision_id": i.decision_id, "environment": i.environment.value, "venue": i.venue,
            "symbol": i.symbol, "side": i.side, "order_type": i.order_type,
            "quote_notional": _dec(i.quote_notional), "quantity": _dec(i.quantity),
            "limit_price": _dec(i.limit_price), "created_ts": i.created_ts,
        },
    }  # fmt: skip
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal(env: Envelope, auth: Authenticator) -> bytes:
    p = payload_bytes(env)
    if len(p) > MAX_BYTES:
        raise ValueError("message too large")
    tag = auth.tag_for(env.key_id, p)
    return len(tag).to_bytes(2, "big") + tag + p


def _opt_dec(v: object) -> Decimal | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("decimal must be a string")
    d = Decimal(v)
    if not d.is_finite():
        raise ValueError("non-finite decimal")
    return d


@dataclass
class Receiver:
    """Credential-side checks. Replay and expiry state lives in a ReplayStore, which must be durable
    (a process restart must never re-accept a message) unless ``require_durable`` is turned off,
    which only the unit tests do."""

    auth: Authenticator
    allowed_senders: frozenset[str]
    replay: ReplayStore
    max_skew_seconds: int = 30
    max_validity_seconds: int = 60
    require_durable: bool = True

    def __post_init__(self) -> None:
        if self.require_durable and not getattr(self.replay, "durable", False):
            raise ValueError(
                "the replay store must be durable (a restart would re-accept messages)"
            )

    def open(self, wire: bytes, now: int) -> tuple[Envelope | None, str]:
        """(envelope, "") if accepted, else (None, REASON). Replay state changes only on accept."""
        if len(wire) > MAX_BYTES + 2 + 64:
            return None, "TOO_LARGE"
        if len(wire) < 3:
            return None, "MALFORMED"
        n = int.from_bytes(wire[:2], "big")
        tag, payload = wire[2 : 2 + n], wire[2 + n :]
        if len(payload) > MAX_BYTES or not payload:
            return None, "TOO_LARGE" if payload else "MALFORMED"
        try:
            body = json.loads(payload.decode("utf-8"))
            key_id = str(body["key_id"])
        except (ValueError, KeyError, TypeError):
            return None, "MALFORMED"
        if not self.auth.verify(key_id, payload, tag):  # authenticate BEFORE trusting any field
            return None, "NOT_AUTHENTIC"
        try:
            env = self._parse(body)
        except (ValueError, KeyError, TypeError, InvalidOperation):
            return None, "MALFORMED"
        if env.sender not in self.allowed_senders:
            return None, "SENDER_NOT_ALLOWED"
        if (
            env.expires_at - env.issued_at > self.max_validity_seconds
            or env.expires_at <= env.issued_at
        ):
            return None, "BAD_VALIDITY_WINDOW"
        if now > env.expires_at:
            return None, "EXPIRED"
        if env.issued_at > now + self.max_skew_seconds:
            return None, "FROM_THE_FUTURE"
        try:
            env.intent.validate()
        except ValueError:
            return None, "INTENT_INVALID"
        why = self.replay.accept(env.sender, env.seq, env.nonce, env.expires_at, now)
        if why:
            return None, why
        return env, ""

    @staticmethod
    def _parse(body: dict) -> Envelope:  # type: ignore[type-arg]
        if body["v"] != VERSION:
            raise ValueError("version")
        i = body["intent"]
        if set(i) != set(_FIELDS):
            raise ValueError("unexpected intent fields")
        if not all(
            isinstance(body[k], int) and not isinstance(body[k], bool)
            for k in ("seq", "issued_at", "expires_at")
        ):
            raise ValueError("integer fields")
        intent = Intent(
            decision_id=str(i["decision_id"]), environment=Environment(i["environment"]),
            venue=str(i["venue"]), symbol=str(i["symbol"]), side=str(i["side"]),
            order_type=str(i["order_type"]), quote_notional=_opt_dec(i["quote_notional"]),
            quantity=_opt_dec(i["quantity"]), limit_price=_opt_dec(i["limit_price"]),
            created_ts=int(i["created_ts"]),
        )  # fmt: skip
        return Envelope(
            sender=str(body["sender"]), key_id=str(body["key_id"]), seq=body["seq"],
            nonce=str(body["nonce"]), issued_at=body["issued_at"], expires_at=body["expires_at"],
            intent=intent,
        )  # fmt: skip
