"""Outbound-request allowlist (ADR-0002). Hard-coded; NOT configurable from the environment.

Only public market-data hosts are allowed, over TLS, and never a private/trading path.
"""
from __future__ import annotations

from urllib.parse import urlsplit

ALLOWED_HOSTS: frozenset[str] = frozenset({
    "data-api.binance.vision",      # Binance public market-data REST
    "data-stream.binance.vision",   # Binance public market-data WS
    "testnet.binance.vision",       # Binance Spot Testnet (public endpoints only in M0)
    "stream.testnet.binance.vision",
    "www.okx.com",                  # OKX public REST/WS
    "ws.okx.com",
})

ALLOWED_SCHEMES: frozenset[str] = frozenset({"https", "wss"})

#: Path fragments that indicate private, trading, funds-movement or margin endpoints.
FORBIDDEN_PATH_FRAGMENTS: tuple[str, ...] = (
    "/api/v3/order", "/api/v3/account", "/api/v3/myTrades", "/api/v3/openOrders",
    "/api/v3/userDataStream", "/sapi/", "/wapi/", "/fapi/", "/dapi/", "/papi/",
    "/api/v5/trade", "/api/v5/asset", "/api/v5/account", "/api/v5/finance",
    "/api/v5/users", "/api/v5/sprd", "/api/v5/copytrading", "/api/v5/broker",
    "/ws/v5/private", "/ws/v5/business",
)


class EgressDenied(Exception):
    """Raised when an outbound URL is not permitted."""


def assert_url_allowed(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ALLOWED_SCHEMES:
        raise EgressDenied(f"scheme not allowed: {parts.scheme!r}")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise EgressDenied(f"host not allowed: {host!r}")
    if parts.username or parts.password:
        raise EgressDenied("credentials in URL are not allowed")
    path = parts.path.lower()
    for fragment in FORBIDDEN_PATH_FRAGMENTS:
        if fragment.lower() in path:
            raise EgressDenied(f"private/trading path not allowed: {parts.path!r}")
    return url
