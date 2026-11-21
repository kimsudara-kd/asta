"""Synthetic-only request signing for the future Binance Spot Testnet connector (M3-B-FINAL phase 6).

Offline readiness: the *interface* a vault-backed (OpenBao transit Ed25519) signer must satisfy, a synthetic
in-memory implementation whose private key is not exportable BY INTERFACE (a test double only: in-process Python cannot truly hide a key), and the request builder with its hard
boundaries. Nothing here performs I/O, reads a real credential or enables an order path: the module has no
network import, ``ExecutionGateway`` stays default-deny, and a *real* key cannot be loaded into it.

Boundaries enforced by construction:
* only the Spot Testnet base URL is accepted; production hosts (and anything else) raise ``ForbiddenEndpoint``;
* only an allow-list of **spot trading/read** paths is signable; withdrawals, transfers, margin, futures,
  loans, sub-accounts are refused by path *and* by parameter name;
* the signer protocol exposes ``sign`` and ``public_key_b64`` only (no way to read the private key);
* the signature, the key and the signed query string are never in ``repr`` or in the audit records.
"""

from __future__ import annotations

import base64
import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

TESTNET_BASE = "https://testnet.binance.vision"
SIGNABLE_PATHS = frozenset(
    {
        "/api/v3/order",  # POST new order, DELETE cancel, GET query (spot)
        "/api/v3/order/test",
        "/api/v3/openOrders",
        "/api/v3/allOrders",
        "/api/v3/myTrades",
        "/api/v3/account",
    }
)
FORBIDDEN_PARAMS = frozenset(
    {
        "sideeffecttype",
        "isisolated",
        "marginbuyborrowamount",
        "withdraworderid",
        "network",
        "address",
    }
)  # lower-case; compared case-insensitively
# Per-path ALLOW-list of parameter names (exact, case-sensitive). Anything else is refused, so margin/borrow/withdraw
# style parameters can never be signed even if a new one is invented (review R-11). recvWindow/timestamp are added here.
_ORDER_PARAMS = frozenset(
    {
        "symbol", "side", "type", "timeInForce", "quantity", "quoteOrderQty", "price", "newClientOrderId",
        "stopPrice", "trailingDelta", "icebergQty", "newOrderRespType", "selfTradePreventionMode",
        "orderId", "origClientOrderId", "cancelRestrictions",
    }
)  # fmt: skip
ALLOWED_PARAMS: dict[str, frozenset[str]] = {
    "/api/v3/order": _ORDER_PARAMS,
    "/api/v3/order/test": _ORDER_PARAMS,
    "/api/v3/openOrders": frozenset({"symbol"}),
    "/api/v3/allOrders": frozenset({"symbol", "orderId", "startTime", "endTime", "limit"}),
    "/api/v3/myTrades": frozenset({"symbol", "orderId", "startTime", "endTime", "fromId", "limit"}),
    "/api/v3/account": frozenset({"omitZeroBalances"}),
}
MAX_RECV_WINDOW_MS = 5000


class SignerError(Exception):
    pass


class ForbiddenEndpoint(SignerError):
    """Not the Spot Testnet host, or a path outside the spot allow-list."""


class SignerUnavailable(SignerError):
    """The signing service failed. The caller must fail closed; there is no local fallback."""


class Signer(ABC):
    """What a vault-backed signer exposes. Deliberately no ``private_key`` accessor."""

    @abstractmethod
    def sign(self, message: bytes) -> bytes: ...

    @abstractmethod
    def public_key_b64(self) -> str: ...


class SyntheticEd25519Signer(Signer):
    """Random in-memory key, generated here, marked synthetic, not exportable through this class."""

    synthetic = True

    def __init__(self, on_event: Callable[[str, dict[str, str]], None] | None = None) -> None:
        self.__key = Ed25519PrivateKey.generate()
        self._log = on_event or (lambda action, payload: None)
        self.available = True
        pub = self.public_key_b64()
        self._fp = hashlib.sha256(pub.encode()).hexdigest()[:12]

    def sign(self, message: bytes) -> bytes:
        if not self.available:
            self._log("signer.unavailable", {"key": self._fp})
            raise SignerUnavailable("signing service unavailable")
        self._log("signer.sign", {"key": self._fp, "bytes": str(len(message))})  # never the content
        return self.__key.sign(message)

    def public_key_b64(self) -> str:
        raw = self.__key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode()

    def __repr__(self) -> str:  # nothing key-derived beyond the public fingerprint
        return f"<SyntheticEd25519Signer key={self._fp}>"

    def __getstate__(self) -> None:
        raise SignerError("a signer is not serialisable")


@dataclass(frozen=True)
class SignedRequest:
    method: str
    url: str  # base + path + query, signature included
    headers: dict[str, str]

    def __repr__(self) -> str:  # the URL carries the signature
        return f"<SignedRequest {self.method} {urlsplit(self.url).path}>"


def build_signed_request(
    signer: Signer,
    method: str,
    path: str,
    params: dict[str, str],
    timestamp_ms: int,
    recv_window_ms: int = MAX_RECV_WINDOW_MS,
    base_url: str = TESTNET_BASE,
    api_key_id: str = "SYNTHETIC-KEY-ID",
) -> SignedRequest:
    if base_url.rstrip("/") != TESTNET_BASE:
        raise ForbiddenEndpoint("only the Binance Spot Testnet host may be signed for")
    if path not in SIGNABLE_PATHS:
        raise ForbiddenEndpoint("path is not in the spot allow-list")
    if method not in {"GET", "POST", "DELETE"}:
        raise ForbiddenEndpoint("method not allowed")
    bad = FORBIDDEN_PARAMS & {p.lower() for p in params}  # defence in depth, case-insensitive
    if bad or "signature" in params or not set(params) <= ALLOWED_PARAMS[path]:
        raise ForbiddenEndpoint("parameter not permitted")
    if not 0 < recv_window_ms <= MAX_RECV_WINDOW_MS:
        raise SignerError("recvWindow out of range")
    query = urlencode({**params, "recvWindow": recv_window_ms, "timestamp": timestamp_ms})
    sig = base64.b64encode(signer.sign(query.encode())).decode()
    full = f"{TESTNET_BASE}{path}?{query}&{urlencode({'signature': sig})}"
    return SignedRequest(method, full, {"X-MBX-APIKEY": api_key_id})


def mock_endpoint_verify(
    public_key_b64: str, url: str, now_ms: int, seen: set[str] | None = None
) -> tuple[bool, str]:
    """What an isolated mock exchange does: recomputes the query, verifies the Ed25519 signature,
    enforces timestamp/recvWindow, and (optionally) rejects a replay. Pure function, no I/O."""
    parts = urlsplit(url)
    if f"{parts.scheme}://{parts.netloc}" != TESTNET_BASE:
        return False, "WRONG_HOST"
    query, sep, tail = parts.query.rpartition("&signature=")
    if not sep:
        return False, "NO_SIGNATURE"
    from urllib.parse import parse_qs, unquote

    try:
        sig = base64.b64decode(unquote(tail), validate=True)
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        pub.verify(sig, query.encode())
    except (InvalidSignature, ValueError):
        return False, "BAD_SIGNATURE"
    q = parse_qs(query)
    ts, rw = int(q["timestamp"][0]), int(q["recvWindow"][0])
    if not ts - 1000 <= now_ms <= ts + rw:
        return False, "OUTSIDE_RECV_WINDOW"
    if seen is not None:
        if tail in seen:
            return False, "REPLAY"
        seen.add(tail)
    return True, "OK"
