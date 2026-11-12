"""ExecutionGateway: default-DENY. In M0 EVERY request is denied (ADR-0002).

There is no configuration, environment variable or flag that can change this in M0.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from asta.core.environment import ORDER_CAPABLE_ENVIRONMENTS, Environment

#: Hard-coded development policy (handoff §4). Not read from settings or the environment.
LIVE_TRADING_ENABLED: bool = False
#: M0 has no execution at all; later milestones add a deliberate, reviewed change here.
EXECUTION_ENABLED_IN_THIS_BUILD: bool = False


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


class ExecutionGateway:
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
        if not EXECUTION_ENABLED_IN_THIS_BUILD:
            reasons.append("M0_EXECUTION_DISABLED")
        if LIVE_TRADING_ENABLED:  # pragma: no cover - invariant: constant is False
            reasons.append("LIVE_FORBIDDEN")
        try:
            self.assert_environment_allowed(request.environment)
        except EnvironmentNotAllowed:
            reasons.append("ENVIRONMENT_NOT_ALLOWED")
        if not reasons:  # unreachable in M0; kept so later code must opt in explicitly
            reasons.append("NO_POLICY_APPROVAL")
        return GatewayDecision(Decision.DENY, tuple(reasons))


def is_execution_enabled() -> bool:
    """Read-only view for status screens. Cannot be used to change the policy."""
    return EXECUTION_ENABLED_IN_THIS_BUILD


def is_live_trading_enabled() -> bool:
    return LIVE_TRADING_ENABLED


class EnvironmentNotAllowed(Exception):
    pass
