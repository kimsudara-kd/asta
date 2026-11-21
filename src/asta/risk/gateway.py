"""ExecutionGateway: default-DENY (ADR-0002).

Everything is denied except ONE narrow cell, and only when every condition holds: the
environment is EXCHANGE_DEMO, the venue is exactly BINANCE_TESTNET (Binance Spot Testnet, virtual
funds), the reviewed code constant ``TESTNET_EXECUTION_ALLOWED`` is True (it ships False),
``LIVE_TRADING_ENABLED`` is False, and the runtime enablement (a time-limited, releaser-only,
default-off database state, see migration 0013) is active. No environment variable, setting or
flag can change any of this. A gateway built without an enablement source never allows anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from asta.core.environment import ORDER_CAPABLE_ENVIRONMENTS, Environment

#: Hard-coded development policy (handoff §4). Not read from settings or the environment.
LIVE_TRADING_ENABLED: bool = False
#: M0 has no execution at all; later milestones add a deliberate, reviewed change here.
EXECUTION_ENABLED_IN_THIS_BUILD: bool = False
#: M3-B step C: the ONLY execution that can ever be allowed is Binance Spot TESTNET. Ships False:
#: flipping it is a separate, reviewed, owner-approved commit, and even then the runtime enablement
#: and every other condition must hold.
TESTNET_EXECUTION_ALLOWED: bool = False
TESTNET_VENUE = "BINANCE_TESTNET"
TESTNET_ENVIRONMENT = "EXCHANGE_DEMO"


class Decision(StrEnum):
    DENY = "DENY"
    ALLOW = "ALLOW"


@dataclass(frozen=True)
class OrderRequest:
    environment: str
    venue: str
    account: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal | None = None


@dataclass(frozen=True)
class GatewayDecision:
    decision: Decision
    reason_codes: tuple[str, ...]


class Enablement(Protocol):
    """Runtime enablement source: a read-only view of the database state."""

    def active(self, scope: str) -> bool: ...


class ExecutionGateway:
    def __init__(self, enablement: Enablement | None = None) -> None:
        self._enablement = enablement

    def assert_environment_allowed(self, environment: str) -> Environment:
        try:
            env = Environment(environment)
        except ValueError as exc:
            raise EnvironmentNotAllowed(f"unknown environment: {environment!r}") from exc
        if env not in ORDER_CAPABLE_ENVIRONMENTS:
            raise EnvironmentNotAllowed(f"environment {env} cannot submit orders")
        return env

    def authorize(self, request: OrderRequest) -> GatewayDecision:
        reasons: list[str] = []
        if LIVE_TRADING_ENABLED:  # pragma: no cover - invariant: constant is False
            reasons.append("LIVE_FORBIDDEN")
        try:
            self.assert_environment_allowed(request.environment)
        except EnvironmentNotAllowed:
            reasons.append("ENVIRONMENT_NOT_ALLOWED")
        if request.environment != TESTNET_ENVIRONMENT:
            reasons.append("ENVIRONMENT_NOT_ALLOWED")
        if request.venue != TESTNET_VENUE:
            reasons.append("VENUE_NOT_ALLOWED")
        testnet_cell = request.environment == TESTNET_ENVIRONMENT and request.venue == TESTNET_VENUE
        if not (testnet_cell and TESTNET_EXECUTION_ALLOWED):
            reasons.append("M0_EXECUTION_DISABLED")
        if testnet_cell and TESTNET_EXECUTION_ALLOWED and not reasons:
            try:
                on = self._enablement is not None and bool(self._enablement.active(TESTNET_VENUE))
            except Exception:  # noqa: BLE001 - any failure to read the state is a refusal
                on = False
            if not on:
                reasons.append("TESTNET_NOT_ENABLED")
        if reasons:
            return GatewayDecision(Decision.DENY, tuple(dict.fromkeys(reasons)))
        return GatewayDecision(Decision.ALLOW, ("TESTNET_ONLY",))


def is_execution_enabled() -> bool:
    """Read-only view for status screens. Cannot be used to change the policy."""
    return EXECUTION_ENABLED_IN_THIS_BUILD


def is_live_trading_enabled() -> bool:
    return LIVE_TRADING_ENABLED


class EnvironmentNotAllowed(Exception):
    pass
