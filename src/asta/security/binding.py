"""Demo environment binding contracts (M3A-C). Contracts only: nothing here opens a connection,
signs a request or places an order.

A future connector instance is created with ONE immutable EnvironmentBinding. The binding rejects
runtime host switching, URL overrides (absolute URLs, scheme/host/userinfo/port tricks, path
traversal), any redirect, responses whose final host differs from the binding, a credential whose
class does not match the binding, and requires a signed, reviewed configuration (two distinct
reviewer signatures over the canonical configuration) before an execution privilege can exist.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from asta.core.environment import Environment
from asta.security.vault import CredentialClass, CredentialLease


class BindingViolation(Exception):
    pass


class Venue(StrEnum):
    BINANCE = "BINANCE"
    OKX = "OKX"


@dataclass(frozen=True)
class EnvironmentBinding:
    venue: Venue
    environment: Environment
    rest_host: str
    ws_hosts: tuple[str, ...]
    credential_class: CredentialClass
    #: headers the venue requires to select the demo environment on a SHARED host (UNVERIFIED at
    #: runtime until M3-B: see docs/m3a/EXCHANGE-CAPABILITY-MATRIX.md). Immutable mapping as tuples.
    required_headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.environment is not Environment.EXCHANGE_DEMO:
            raise BindingViolation("only EXCHANGE_DEMO bindings can exist (no production binding)")

    # -- URL construction: path only, never a caller-supplied host ----------------------------
    def url(self, path: str) -> str:
        if not path.startswith("/") or path.startswith("//") or "\\" in path:
            raise BindingViolation("path must be absolute-path-only (no scheme/host/override)")
        if any(c in path for c in ("@", "#", "\r", "\n", " ")) or ".." in path.split("/"):
            raise BindingViolation("illegal characters or traversal in path")
        parts = urlsplit(f"https://{self.rest_host}{path}")
        if parts.hostname != self.rest_host or parts.username or parts.password or parts.port:
            raise BindingViolation("URL does not resolve to the bound host")
        return parts.geturl()

    def ws_url(self, host: str, path: str) -> str:
        if host not in self.ws_hosts:
            raise BindingViolation("WebSocket host is not part of this binding")
        if not path.startswith("/") or path.startswith("//") or "@" in path or ".." in path:
            raise BindingViolation("illegal WebSocket path")
        return f"wss://{host}{path}"

    # -- guards ----------------------------------------------------------------------------
    def reject_redirect(self, status: int, location: str | None) -> None:
        if 300 <= status < 400:
            raise BindingViolation(f"redirect refused ({status}); never followed, incident")

    def check_response(self, final_url: str, headers: dict[str, str] | None = None) -> None:
        host = urlsplit(final_url).hostname
        if host != self.rest_host and host not in self.ws_hosts:
            raise BindingViolation("response originated from an unbound host")

    def check_credential(self, lease: CredentialLease) -> None:
        if lease.credential_class != self.credential_class:
            raise BindingViolation("credential class does not match this environment binding")


DEMO_BINDINGS: dict[Venue, EnvironmentBinding] = {
    Venue.BINANCE: EnvironmentBinding(
        venue=Venue.BINANCE,
        environment=Environment.EXCHANGE_DEMO,
        rest_host="testnet.binance.vision",
        ws_hosts=("stream.testnet.binance.vision", "ws-api.testnet.binance.vision"),
        credential_class=CredentialClass.DEMO_BINANCE_SPOT_TESTNET,
    ),
    Venue.OKX: EnvironmentBinding(
        venue=Venue.OKX,
        environment=Environment.EXCHANGE_DEMO,
        rest_host="www.okx.com",
        ws_hosts=("wspap.okx.com",),
        credential_class=CredentialClass.DEMO_OKX,
        required_headers=(("x-simulated-trading", "1"),),
    ),
}


def bind(venue: Venue, environment: Environment) -> EnvironmentBinding:
    """Immutable selection; only the demo environment can be selected."""
    if environment is not Environment.EXCHANGE_DEMO:
        raise BindingViolation("demo environment only")
    return DEMO_BINDINGS[venue]


# ---- signed, reviewed configuration for any future execution privilege -------------------------
def canonical(config: dict[str, Any]) -> bytes:
    return json.dumps(config, sort_keys=True, separators=(",", ":")).encode()


def verify_privilege_config(
    config: dict[str, Any],
    signatures: dict[str, bytes],
    trusted_reviewers: dict[str, bytes],
    min_signers: int = 2,
) -> str:
    """Returns the SHA-256 digest of the canonical configuration iff at least ``min_signers``
    DISTINCT trusted reviewers (raw Ed25519 public keys) validly signed exactly these bytes."""
    msg = canonical(config)
    ok: set[str] = set()
    for reviewer, sig in signatures.items():
        pub = trusted_reviewers.get(reviewer)
        if pub is None:
            continue
        try:
            Ed25519PublicKey.from_public_bytes(pub).verify(sig, msg)
            ok.add(reviewer)
        except (InvalidSignature, ValueError):
            continue
    if len(ok) < min_signers:
        raise BindingViolation(f"configuration needs {min_signers} valid reviewer signatures")
    if config.get("environment") != Environment.EXCHANGE_DEMO.value:
        raise BindingViolation("a privilege configuration may only name the demo environment")
    if set(config.get("permissions", [])) - {"READ", "SPOT_TRADE"}:
        raise BindingViolation("forbidden permission in privilege configuration")
    return hashlib.sha256(msg).hexdigest()
