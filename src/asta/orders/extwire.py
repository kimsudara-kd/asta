"""Request/reply wire format: order path (app side) <-> credential-side service (step B).

One request = one operation on ONE order: SEND (carries the intent), QUERY or CANCEL (client id and
symbol). The credential side re-validates everything and trusts nothing: authenticate BEFORE
parsing, canonical bytes only, bounded size, validity window, strictly increasing sequence and a
never-reused nonce (same ``ReplayStore`` as ``channel``). A reply is authenticated and bound to the
request's nonce and operation, so a captured reply cannot answer another request.

PURE encoding and checks: no I/O and no key material in this module (keys are passed to
``Blake2bAuthenticator``). Signing of exchange requests exists only on the credential side, outside
``src/``. Fail closed everywhere.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from asta.core.environment import Environment
from asta.orders.channel import (
    MAX_BYTES,
    MAX_TAG,
    MIN_TAG,
    Authenticator,
    ReplayStore,
)
from asta.orders.lifecycle import Intent

VERSION = 1
OPS = ("SEND", "QUERY", "CANCEL")
_KEY_ID = re.compile(rb'"key_id":"([A-Za-z0-9._-]{1,64})"')
_CLIENT_ID = re.compile(r"^[0-9a-f]{32}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,12}-[A-Z0-9]{2,12}$")
_REQUEST_FIELDS = frozenset(
    {"kind", "v", "sender", "key_id", "seq", "nonce", "issued_at", "expires_at", "op",
     "client_id", "symbol", "intent"}
)  # fmt: skip
_INTENT_FIELDS = (
    "decision_id", "environment", "venue", "symbol", "side", "order_type",
    "quote_notional", "quantity", "limit_price", "created_ts",
)  # fmt: skip


class Blake2bAuthenticator:
    """Keyed BLAKE2b-256 tag over the canonical payload (symmetric key per key id)."""

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        for k, v in keys.items():
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", k) or not 32 <= len(v) <= 64:
                raise ValueError("key ids are [A-Za-z0-9._-]{1,64}; keys are 32..64 bytes")
        self._keys = dict(keys)

    def tag_for(self, key_id: str, payload: bytes) -> bytes:
        return hashlib.blake2b(payload, key=self._keys[key_id], digest_size=32).digest()

    def verify(self, key_id: str, payload: bytes, tag: bytes) -> bool:
        if key_id not in self._keys:
            return False
        return secrets.compare_digest(self.tag_for(key_id, payload), tag)


def _dec(v: Decimal | None) -> str | None:
    return None if v is None else format(v, "f")


def _opt_dec(v: object) -> Decimal | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("decimal must be a string")
    d = Decimal(v)
    if not d.is_finite():
        raise ValueError("non-finite decimal")
    return d


@dataclass(frozen=True)
class Request:
    sender: str
    key_id: str
    seq: int
    nonce: str
    issued_at: int
    expires_at: int
    op: str
    client_id: str
    symbol: str
    intent: Intent | None = field(default=None)  # SEND only

    def __post_init__(self) -> None:
        if self.op not in OPS:
            raise ValueError("unknown operation")
        if (self.op == "SEND") != (self.intent is not None):
            raise ValueError("exactly SEND carries an intent")
        if not _CLIENT_ID.match(self.client_id) or not _SYMBOL.match(self.symbol):
            raise ValueError("bad client id or symbol")
        if self.intent is not None and (
            self.intent.decision_id != self.client_id or self.intent.symbol != self.symbol
        ):
            raise ValueError("intent does not match the request ids")


def payload_bytes(r: Request) -> bytes:
    body: dict[str, object] = {
        "kind": "ext-request", "v": VERSION, "sender": r.sender, "key_id": r.key_id, "seq": r.seq,
        "nonce": r.nonce, "issued_at": r.issued_at, "expires_at": r.expires_at, "op": r.op,
        "client_id": r.client_id, "symbol": r.symbol, "intent": None,
    }  # fmt: skip
    if r.intent is not None:
        i = r.intent
        body["intent"] = {
            "decision_id": i.decision_id, "environment": i.environment.value, "venue": i.venue,
            "symbol": i.symbol, "side": i.side, "order_type": i.order_type,
            "quote_notional": _dec(i.quote_notional), "quantity": _dec(i.quantity),
            "limit_price": _dec(i.limit_price), "created_ts": i.created_ts,
        }  # fmt: skip
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _seal(payload: bytes, key_id: str, auth: Authenticator) -> bytes:
    if len(payload) > MAX_BYTES:
        raise ValueError("message too large")
    tag = auth.tag_for(key_id, payload)
    return len(tag).to_bytes(2, "big") + tag + payload


def seal_request(r: Request, auth: Authenticator) -> bytes:
    return _seal(payload_bytes(r), r.key_id, auth)


def _split(wire: bytes) -> tuple[bytes, bytes] | str:
    if len(wire) > MAX_BYTES + 2 + 64:
        return "TOO_LARGE"
    if len(wire) < 3:
        return "MALFORMED"
    n = int.from_bytes(wire[:2], "big")
    if not MIN_TAG <= n <= MAX_TAG:
        return "MALFORMED"
    tag, payload = wire[2 : 2 + n], wire[2 + n :]
    if not payload or len(payload) > MAX_BYTES:
        return "TOO_LARGE" if payload else "MALFORMED"
    return tag, payload


@dataclass
class RequestReceiver:
    """Credential-side checks (mirrors ``channel.Receiver``)."""

    auth: Authenticator
    allowed_senders: frozenset[str]
    replay: ReplayStore
    allowed_key_ids: frozenset[str] = field(kw_only=True)
    max_skew_seconds: int = 30
    max_validity_seconds: int = 60
    require_durable: bool = True

    def __post_init__(self) -> None:
        if self.require_durable and not getattr(self.replay, "durable", False):
            raise ValueError(
                "the replay store must be durable (a restart would re-accept messages)"
            )

    def open(self, wire: bytes, now: int) -> tuple[Request | None, str]:
        parts = _split(wire)
        if isinstance(parts, str):
            return None, parts
        tag, payload = parts
        m = _KEY_ID.search(payload)
        if m is None:
            return None, "MALFORMED"
        key_id = m.group(1).decode("ascii")
        if key_id not in self.allowed_key_ids:
            return None, "KEY_NOT_ALLOWED"
        if not self.auth.verify(key_id, payload, tag):  # authenticate BEFORE trusting any field
            return None, "NOT_AUTHENTIC"
        try:
            body = json.loads(payload.decode("utf-8"))
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError):
            return None, "MALFORMED"
        if canonical != payload or str(body.get("key_id")) != key_id:
            return None, "NOT_CANONICAL"
        try:
            req = self._parse(body)
        except (ValueError, KeyError, TypeError, InvalidOperation):
            return None, "MALFORMED"
        if req.sender not in self.allowed_senders:
            return None, "SENDER_NOT_ALLOWED"
        if (
            req.expires_at - req.issued_at > self.max_validity_seconds
            or req.expires_at <= req.issued_at
        ):
            return None, "BAD_VALIDITY_WINDOW"
        if now > req.expires_at:
            return None, "EXPIRED"
        if req.issued_at > now + self.max_skew_seconds:
            return None, "FROM_THE_FUTURE"
        if req.intent is not None:
            try:
                req.intent.validate()
            except ValueError:
                return None, "INTENT_INVALID"
        why = self.replay.accept(req.sender, req.seq, req.nonce, req.expires_at, now)
        if why:
            return None, why
        return req, ""

    @staticmethod
    def _parse(body: dict) -> Request:  # type: ignore[type-arg]
        if set(body) != _REQUEST_FIELDS:
            raise ValueError("unexpected request fields")
        if body["kind"] != "ext-request" or body["v"] != VERSION:
            raise ValueError("kind/version")
        if not all(
            isinstance(body[k], int) and not isinstance(body[k], bool)
            for k in ("seq", "issued_at", "expires_at")
        ):
            raise ValueError("integer fields")
        intent = None
        if body["intent"] is not None:
            i = body["intent"]
            if set(i) != set(_INTENT_FIELDS):
                raise ValueError("unexpected intent fields")
            intent = Intent(
                decision_id=str(i["decision_id"]), environment=Environment(i["environment"]),
                venue=str(i["venue"]), symbol=str(i["symbol"]), side=str(i["side"]),
                order_type=str(i["order_type"]), quote_notional=_opt_dec(i["quote_notional"]),
                quantity=_opt_dec(i["quantity"]), limit_price=_opt_dec(i["limit_price"]),
                created_ts=int(i["created_ts"]),
            )  # fmt: skip
        return Request(
            sender=str(body["sender"]), key_id=str(body["key_id"]), seq=body["seq"],
            nonce=str(body["nonce"]), issued_at=body["issued_at"], expires_at=body["expires_at"],
            op=str(body["op"]), client_id=str(body["client_id"]), symbol=str(body["symbol"]),
            intent=intent,
        )  # fmt: skip


@dataclass(frozen=True)
class Reply:
    key_id: str
    request_nonce: str  # binds the reply to ONE request
    op: str
    outcome: str  # SendOutcome / QueryKind / CancelOutcome value, or "REFUSED"
    state: str = ""  # a lifecycle State name when relevant
    code: str = ""  # reason code for REFUSED


def seal_reply(r: Reply, auth: Authenticator) -> bytes:
    body = {
        "kind": "ext-reply", "v": VERSION, "key_id": r.key_id, "request_nonce": r.request_nonce,
        "op": r.op, "outcome": r.outcome, "state": r.state, "code": r.code,
    }  # fmt: skip
    return _seal(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8"), r.key_id, auth
    )


def open_reply(
    wire: bytes, auth: Authenticator, key_ids: frozenset[str], request_nonce: str, op: str
) -> Reply | None:
    """The reply if authentic AND answering exactly this request; else None (caller: UNKNOWN)."""
    parts = _split(wire)
    if isinstance(parts, str):
        return None
    tag, payload = parts
    m = _KEY_ID.search(payload)
    if m is None:
        return None
    key_id = m.group(1).decode("ascii")
    if key_id not in key_ids or not auth.verify(key_id, payload, tag):
        return None
    try:
        body = json.loads(payload.decode("utf-8"))
        if json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8") != payload:
            return None
        if body["kind"] != "ext-reply" or body["v"] != VERSION or body["key_id"] != key_id:
            return None
        if body["request_nonce"] != request_nonce or body["op"] != op:
            return None
        return Reply(key_id, str(body["request_nonce"]), str(body["op"]), str(body["outcome"]),
                     str(body["state"]), str(body["code"]))  # fmt: skip
    except (ValueError, TypeError, KeyError):
        return None
