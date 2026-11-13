"""Credential vault contract + mock/test backends (M3A-2).

Scope (ASTA-M3A-IMPLEMENTATION-01 section 4): interface, policy and tests with RANDOM SYNTHETIC
secrets only. No real exchange credential exists in this repository, the vault is not wired into any
runtime path, and the real backend is a later, independently reviewed decision.

Policy enforced here, by construction:
* separate vault identities per venue; a credential is classified DEMO_* only (no production class
  exists, so a production credential cannot even be described);
* permissions are constrained: READ and SPOT_TRADE only; any withdrawal, transfer, margin, futures,
  borrowing, sub-account or account-management permission is refused at ``store`` time;
* only a ConnectorPrincipal explicitly GRANTED that identity (grant requires a verified, signed,
  reviewed configuration) can lease it; strategies, AI modules, UI and operators are always refused;
* every attempt (granted or refused) is appended to a hash-chained access log that never contains
  secret material;
* encryption at rest for the file backend (authenticated encryption, random per-record nonce);
* rotation and revocation: a revoked or expired credential can never be leased again.
"""

from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from asta.audit.chain import GENESIS, row_hash, verify_chain
from asta.security.redaction import REGISTRY, SecretValue


class VaultError(Exception):
    pass


class AccessDenied(VaultError):
    pass


class ForbiddenPermission(VaultError):
    pass


class VaultIdentity(StrEnum):
    BINANCE_SPOT_TESTNET = "vault/binance/spot-testnet"
    OKX_DEMO = "vault/okx/demo"


class CredentialClass(StrEnum):
    """Demo-only classification. There is intentionally no production class."""

    DEMO_BINANCE_SPOT_TESTNET = "DEMO_BINANCE_SPOT_TESTNET"
    DEMO_OKX = "DEMO_OKX"


IDENTITY_CLASS: dict[VaultIdentity, CredentialClass] = {
    VaultIdentity.BINANCE_SPOT_TESTNET: CredentialClass.DEMO_BINANCE_SPOT_TESTNET,
    VaultIdentity.OKX_DEMO: CredentialClass.DEMO_OKX,
}


class Permission(StrEnum):
    READ = "READ"
    SPOT_TRADE = "SPOT_TRADE"
    # Everything below must NEVER be granted; listed so that a request naming them is recognised.
    WITHDRAW = "WITHDRAW"
    TRANSFER = "TRANSFER"
    MARGIN = "MARGIN"
    FUTURES = "FUTURES"
    BORROW = "BORROW"
    SUBACCOUNT = "SUBACCOUNT"
    ACCOUNT_MANAGEMENT = "ACCOUNT_MANAGEMENT"


ALLOWED_PERMISSIONS: frozenset[Permission] = frozenset({Permission.READ, Permission.SPOT_TRADE})


class PrincipalKind(StrEnum):
    CONNECTOR = "CONNECTOR"
    STRATEGY = "STRATEGY"
    AI_MODULE = "AI_MODULE"
    ANALYTICS = "ANALYTICS"
    OPERATOR = "OPERATOR"
    UI = "UI"


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    name: str


@dataclass(frozen=True)
class CredentialMeta:
    identity: VaultIdentity
    credential_class: CredentialClass
    permissions: frozenset[Permission]
    version: int
    created_at: datetime
    expires_at: datetime
    revoked: bool = False


@dataclass(frozen=True)
class CredentialLease:
    """Short-lived; carries SecretValue objects (masked everywhere except ``reveal``)."""

    identity: VaultIdentity
    credential_class: CredentialClass
    permissions: frozenset[Permission]
    parts: dict[str, SecretValue]
    expires_at: datetime


class AccessLog:
    """Append-only, hash-chained; entries hold names, identities, decisions - never secrets."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def append(self, actor: str, action: str, payload: dict[str, Any]) -> None:
        at = datetime.now(UTC)
        prev = self.rows[-1]["row_hash"] if self.rows else GENESIS
        self.rows.append(
            {
                "at": at,
                "actor": actor,
                "action": action,
                "payload": payload,
                "prev_hash": prev,
                "row_hash": row_hash(prev, at, actor, action, payload),
            }
        )

    def verify(self) -> tuple[bool, int | None]:
        return verify_chain(self.rows)


class VaultBackend(ABC):
    """Storage SPI: opaque records by identity. Implementations never log values."""

    @abstractmethod
    def put(self, identity: VaultIdentity, record: dict[str, Any]) -> None: ...

    @abstractmethod
    def get(self, identity: VaultIdentity) -> dict[str, Any] | None: ...


class MemoryBackend(VaultBackend):
    def __init__(self) -> None:
        self._d: dict[VaultIdentity, dict[str, Any]] = {}

    def put(self, identity: VaultIdentity, record: dict[str, Any]) -> None:
        self._d[identity] = json.loads(json.dumps(record))

    def get(self, identity: VaultIdentity) -> dict[str, Any] | None:
        r = self._d.get(identity)
        return None if r is None else json.loads(json.dumps(r))


class EncryptedFileBackend(VaultBackend):
    """One authenticated-encrypted file per identity (Fernet: AES-128-CBC + auth tag, random IV).
    The key is supplied by the caller (the production key custody design is a later milestone)."""

    def __init__(self, directory: Path, key: bytes) -> None:
        self._dir = directory
        self._f = Fernet(key)
        directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def generate_key() -> bytes:
        return Fernet.generate_key()

    def _path(self, identity: VaultIdentity) -> Path:
        return self._dir / (identity.value.replace("/", "__") + ".enc")

    def put(self, identity: VaultIdentity, record: dict[str, Any]) -> None:
        blob = self._f.encrypt(json.dumps(record).encode())
        tmp = self._path(identity).with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self._path(identity))

    def get(self, identity: VaultIdentity) -> dict[str, Any] | None:
        p = self._path(identity)
        if not p.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(self._f.decrypt(p.read_bytes()))
        except InvalidToken as exc:
            raise VaultError("vault record failed authentication (tampered or wrong key)") from exc
        return data


@dataclass
class Vault:
    backend: VaultBackend
    log: AccessLog = field(default_factory=AccessLog)
    ttl: timedelta = timedelta(days=30)
    lease_ttl: timedelta = timedelta(seconds=60)
    _grants: set[tuple[str, VaultIdentity]] = field(default_factory=set)

    # ---- administration (never callable by strategies: no Principal-less path to lease) ----
    def store(
        self,
        identity: VaultIdentity,
        credential_class: CredentialClass,
        permissions: set[Permission] | frozenset[Permission],
        parts: dict[str, SecretValue],
        actor: str,
    ) -> CredentialMeta:
        if credential_class != IDENTITY_CLASS[identity]:
            self.log.append(actor, "store.refused", {"identity": identity, "why": "class-mismatch"})
            raise VaultError("credential class does not match the vault identity")
        bad = set(permissions) - ALLOWED_PERMISSIONS
        if bad or not permissions:
            self.log.append(
                actor,
                "store.refused",
                {"identity": identity, "why": "forbidden-permission", "perms": sorted(bad)},
            )
            raise ForbiddenPermission(f"permissions not allowed: {sorted(bad) or 'none given'}")
        if not parts:
            raise VaultError("empty credential")
        prev = self.backend.get(identity)
        version = (prev["version"] + 1) if prev else 1
        now = datetime.now(UTC)
        rec = {
            "version": version,
            "class": credential_class.value,
            "permissions": sorted(p.value for p in permissions),
            "parts": {k: v.reveal() for k, v in parts.items()},
            "created_at": now.isoformat(),
            "expires_at": (now + self.ttl).isoformat(),
            "revoked": False,
        }
        for v in parts.values():
            REGISTRY.register(v.reveal())  # so later log/diagnostic scrubbing can find it
        self.backend.put(identity, rec)
        self.log.append(
            actor, "store" if version == 1 else "rotate", {"identity": identity, "version": version}
        )
        return self._meta(identity, rec)

    def revoke(self, identity: VaultIdentity, actor: str) -> None:
        rec = self.backend.get(identity)
        if rec is None:
            raise VaultError("no such credential")
        rec["revoked"] = True
        rec["parts"] = {k: "" for k in rec["parts"]}  # destroy the material, keep the metadata
        self.backend.put(identity, rec)
        self.log.append(actor, "revoke", {"identity": identity, "version": rec["version"]})

    def grant(
        self, connector_id: str, identity: VaultIdentity, verified_config_digest: str
    ) -> None:
        """Records a grant. The caller must have verified the signed, reviewed configuration
        (see asta.security.binding.verify_privilege_config); the digest is logged for audit."""
        if not verified_config_digest:
            raise AccessDenied("a verified configuration digest is required")
        self._grants.add((connector_id, identity))
        self.log.append(
            connector_id, "grant", {"identity": identity, "config": verified_config_digest}
        )

    # ---- retrieval ----
    def lease(self, principal: Principal, identity: VaultIdentity, purpose: str) -> CredentialLease:
        if principal.kind is not PrincipalKind.CONNECTOR:
            self.log.append(
                principal.name,
                "lease.denied",
                {"identity": identity, "kind": principal.kind, "why": "principal-not-connector"},
            )
            raise AccessDenied("only an authorised exchange connector may lease credentials")
        if (principal.name, identity) not in self._grants:
            self.log.append(
                principal.name, "lease.denied", {"identity": identity, "why": "no-grant"}
            )
            raise AccessDenied("connector has no grant for this vault identity")
        rec = self.backend.get(identity)
        now = datetime.now(UTC)
        if rec is None or rec["revoked"] or datetime.fromisoformat(rec["expires_at"]) <= now:
            self.log.append(
                principal.name,
                "lease.denied",
                {"identity": identity, "why": "revoked-expired-none"},
            )
            raise AccessDenied("credential unavailable (missing, revoked or expired)")
        self.log.append(
            principal.name,
            "lease",
            {"identity": identity, "version": rec["version"], "purpose": purpose},
        )
        return CredentialLease(
            identity=identity,
            credential_class=CredentialClass(rec["class"]),
            permissions=frozenset(Permission(p) for p in rec["permissions"]),
            parts={k: SecretValue(v) for k, v in rec["parts"].items()},
            expires_at=min(now + self.lease_ttl, datetime.fromisoformat(rec["expires_at"])),
        )

    def meta(self, identity: VaultIdentity) -> CredentialMeta | None:
        rec = self.backend.get(identity)
        return None if rec is None else self._meta(identity, rec)

    @staticmethod
    def _meta(identity: VaultIdentity, rec: dict[str, Any]) -> CredentialMeta:
        return CredentialMeta(
            identity=identity,
            credential_class=CredentialClass(rec["class"]),
            permissions=frozenset(Permission(p) for p in rec["permissions"]),
            version=rec["version"],
            created_at=datetime.fromisoformat(rec["created_at"]),
            expires_at=datetime.fromisoformat(rec["expires_at"]),
            revoked=rec["revoked"],
        )


def synthetic_secret(n: int = 32) -> str:
    """Random, NON-credential test value (never valid at any exchange)."""
    return "SYNTH-" + base64.urlsafe_b64encode(os.urandom(n)).decode().rstrip("=")
