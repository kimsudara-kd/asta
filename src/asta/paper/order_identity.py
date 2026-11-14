"""Deterministic order identity and exact-once gate (finding F-12, threat T-14, M3-B readiness B-6).

PURE and ADDITIVE: nothing in the accepted paper/simulation paths calls this module, so no digest,
checkpoint, persisted row or replay changes. It exists so that M3-B can build the order path on a design
that is already specified and tested BEFORE any authenticated connector exists.

Why a deterministic id: an exchange offers no idempotency key beyond the client order id (Binance: a
duplicate id is refused with "Duplicate order sent"; after a timeout the outcome is unknown and must be
reconciled by QUERYING BY CLIENT ID). If the id is a pure function of the decision, then
  * re-submitting the same decision after a crash/timeout/restart can only ever hit the same id
    (exchange refuses the duplicate: harmless), and
  * the order can be found again without storing the exchange's id.
A per-account counter (the strategies' ``x:<SYMBOL>:<n>``) is NOT such an id: it changes when the
process restarts or a rejected signal is dropped, and ``signal_id`` is only unique per account.

Format: 32 lowercase hex characters = first 128 bits of SHA-256 over a canonical, versioned encoding.
That satisfies Binance's ``^[a-zA-Z0-9-_]{1,36}$`` (documented for FIX; assumed for REST, UNVERIFIED
until a demo key exists) and OKX's alphanumeric 1-32 rule, and it leaks no strategy detail (the tag is
hashed, never embedded).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

ID_VERSION = "asta-coid-v1"
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


class IdentityError(ValueError):
    pass


@dataclass(frozen=True)
class DecisionKey:
    """Everything that makes one trading decision distinct. ``session_hash`` is the paper/demo session
    configuration hash, so the same signal in two sessions (or after a config change) gets a different id.
    ``ordinal`` separates identical intents emitted in the same bar (0, 1, ...)."""

    session_hash: str
    account: str
    symbol: str
    side: str
    order_type: str
    tag: str
    decision_close: datetime
    ordinal: int = 0

    def canonical(self) -> bytes:
        if self.decision_close.tzinfo is None or self.decision_close.utcoffset() is None:
            raise IdentityError("decision_close must be timezone-aware")
        if self.ordinal < 0:
            raise IdentityError("ordinal must be >= 0")
        ts = self.decision_close.astimezone(UTC).isoformat(timespec="microseconds")
        fields = (
            ID_VERSION,
            self.session_hash,
            self.account,
            self.symbol,
            self.side,
            self.order_type,
            self.tag,
            ts,
            str(self.ordinal),
        )
        for f in fields:
            if "\x1f" in f or "\x00" in f:
                raise IdentityError("control character in an identity field")
        return "\x1f".join(fields).encode("utf-8")


def decision_id(key: DecisionKey) -> str:
    """Globally unique, deterministic id of a decision (also usable as the client order id)."""
    return hashlib.sha256(key.canonical()).hexdigest()[:32]


def client_order_id(key: DecisionKey) -> str:
    """The id sent to an exchange. v1: identical to the decision id."""
    return decision_id(key)


def is_valid_client_order_id(value: str) -> bool:
    return bool(_ID_RE.fullmatch(value))


@dataclass
class OrdinalAssigner:
    """Gives repeated identical intents inside the same bar distinct ordinals (0, 1, ...). Deterministic:
    the n-th identical intent of a bar always gets ordinal n-1, so replays reproduce the same ids."""

    _seen: dict[tuple[str, str, str, str, str, str, datetime], int] = field(default_factory=dict)

    def assign(self, key: DecisionKey) -> DecisionKey:
        base = (
            key.session_hash,
            key.account,
            key.symbol,
            key.side,
            key.order_type,
            key.tag,
            key.decision_close,
        )
        n = self._seen.get(base, 0)
        self._seen[base] = n + 1
        return replace(key, ordinal=n)


class ExactOnceGate:
    """Refuses to let one decision id be submitted twice. In-memory reference implementation: M3-B must
    back it with a UNIQUE database constraint written in the same transaction that records the intent to
    submit (so a crash between 'recorded' and 'sent' resolves by re-sending the SAME id and, if the exchange
    reports a duplicate, by querying that id), never by minting a new id."""

    def __init__(self, already_claimed: set[str] | None = None) -> None:
        self._claimed: set[str] = set(already_claimed or ())

    def claim(self, did: str) -> bool:
        """True the first time a decision id is claimed, False for every later attempt."""
        if not is_valid_client_order_id(did):
            raise IdentityError("not a valid decision id")
        if did in self._claimed:
            return False
        self._claimed.add(did)
        return True

    def claimed(self) -> frozenset[str]:
        return frozenset(self._claimed)
