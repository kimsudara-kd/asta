"""The mandatory internal gateway boundary of the order path (ASTA-DECISION-007, workstream A).

Nothing in ``asta.orders`` may talk to an exchange port except through ``GatedExchange``; the
``Dispatcher`` refuses (TypeError) to be built with anything else. Every send/cancel/query passes
``InternalGate`` first:

1. the shared contract ``asta.core.order_gate`` (DEMO_INTENT: environment must be EXCHANGE_DEMO);
2. the durable kill switch (a latched halt refuses anything that could increase exposure);
3. the port kind: a SIMULATED port (fake/offline) may run; an EXTERNAL port is passed to
   ``asta.risk.gateway.ExecutionGateway``, which denies everything except the Binance Spot Testnet
   cell, and only while the reviewed constant AND the runtime enablement hold (both default OFF); a
   port that declares no kind is refused (fail closed).

This module is policy only: it does not implement a connector and holds no credential.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol

from asta.core.order_gate import Surface, authorize
from asta.orders.lifecycle import Intent
from asta.orders.ports import CancelResult, ExchangePort, QueryResult, SendOutcome
from asta.risk.gateway import Decision, ExecutionGateway, OrderRequest


class PortKind(StrEnum):
    SIMULATED = "SIMULATED"  # in-process fake/replay: no network
    EXTERNAL = "EXTERNAL"  # would reach an exchange: always denied in this build


class KillState(Protocol):
    def active(self) -> bool: ...


class ExecutionDenied(Exception):
    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("denied: " + ",".join(reasons))
        self.reasons = reasons


class ExposureBlocked(Exception):
    """A state advancement that could increase exposure was refused (kill switch latched)."""


@dataclass(frozen=True)
class Authorization:
    allowed: bool
    reasons: tuple[str, ...] = ()


class InternalGate:
    def __init__(self, kill: KillState, external: ExecutionGateway | None = None) -> None:
        self._kill = kill
        self._external = external or ExecutionGateway()

    def killed(self) -> bool:
        return self._kill.active()

    def authorize(self, intent: Intent, kind: PortKind | None) -> Authorization:
        reasons: list[str] = []
        d = authorize(Surface.DEMO_INTENT, intent.environment.value)
        if not d.allowed:
            reasons.extend(d.reasons)
        if self.killed():
            reasons.append("KILL_SWITCH_LATCHED")
        if kind == PortKind.SIMULATED:
            pass
        elif kind == PortKind.EXTERNAL:
            ext = self._external.authorize(
                OrderRequest(
                    intent.environment.value,
                    intent.venue,
                    "demo",
                    intent.symbol,
                    intent.side,
                    intent.quantity or intent.quote_notional or Decimal(0),
                    intent.limit_price,
                )
            )
            if ext.decision != Decision.ALLOW:
                reasons.extend(ext.reason_codes)
        else:
            reasons.append("PORT_KIND_UNDECLARED")
        return Authorization(not reasons, tuple(dict.fromkeys(reasons)))

    def authorize_reducing(self, intent: Intent, kind: PortKind | None) -> Authorization:
        """Query / cancel of an existing order: same contract and gateway as ``authorize``, but a
        latched kill switch does NOT block it (cancel / learning the truth only reduce exposure)."""
        reasons: list[str] = []
        d = authorize(Surface.DEMO_INTENT, intent.environment.value)
        if not d.allowed:
            reasons.extend(d.reasons)
        if kind == PortKind.SIMULATED:
            pass
        elif kind == PortKind.EXTERNAL:
            ext = self._external.authorize_reducing(
                OrderRequest(
                    intent.environment.value,
                    intent.venue,
                    "demo",
                    intent.symbol,
                    intent.side,
                    intent.quantity or intent.quote_notional or Decimal(0),
                    intent.limit_price,
                )
            )
            if ext.decision != Decision.ALLOW:
                reasons.extend(ext.reason_codes)
        else:
            reasons.append("PORT_KIND_UNDECLARED")
        return Authorization(not reasons, tuple(dict.fromkeys(reasons)))


class GatedExchange:
    """The only way the order path reaches an exchange port. Re-checks the gate on every call."""

    def __init__(self, port: ExchangePort, kind: PortKind | None, gate: InternalGate) -> None:
        self._port, self.kind, self.gate = port, kind, gate

    def permit(self, intent: Intent) -> Authorization:
        return self.gate.authorize(intent, self.kind)

    def send(self, intent: Intent) -> SendOutcome:
        a = self.permit(intent)
        if not a.allowed:
            raise ExecutionDenied(a.reasons)
        return self._port.send(intent)

    def _external_gate(self, intent: Intent | None) -> None:
        """EXTERNAL query/cancel: the same gate as send (minus the kill switch); intent required."""
        if intent is None:
            raise ExecutionDenied(("INTENT_REQUIRED",))
        a = self.gate.authorize_reducing(intent, self.kind)
        if not a.allowed:
            raise ExecutionDenied(a.reasons)
        bind = getattr(self._port, "bind_intent", None)
        if bind is not None:
            bind(intent)

    def query(self, client_id: str, intent: Intent | None = None) -> QueryResult:
        if self.kind == PortKind.EXTERNAL:  # reading an external exchange is execution-adjacent
            self._external_gate(intent)
        elif self.kind != PortKind.SIMULATED:
            raise ExecutionDenied(("PORT_NOT_SIMULATED",))
        return self._port.query(client_id)

    def trades(self, client_id: str, intent: Intent | None = None, skip: int = 0) -> Any:
        """Read-only: one page of the venue's trades of an order. Same gate as query/cancel."""
        reader = self._reader("trades", intent)
        return reader(client_id, skip)

    def balances(self, intent: Intent | None = None) -> Any:
        """Read-only: the venue account's non-zero balances. Same gate as query/cancel."""
        return self._reader("balances", intent)()

    def _reader(self, name: str, intent: Intent | None) -> Callable[..., Any]:
        if self.kind == PortKind.EXTERNAL:
            self._external_gate(intent)  # also binds the intent's symbol to the port
        elif self.kind != PortKind.SIMULATED:
            raise ExecutionDenied(("PORT_NOT_SIMULATED",))
        elif intent is not None and (bind := getattr(self._port, "bind_intent", None)) is not None:
            bind(intent)
        fn = getattr(self._port, name, None)
        if fn is None:
            raise ExecutionDenied(("READ_NOT_SUPPORTED",))
        return fn  # type: ignore[no-any-return]

    def cancel(self, client_id: str, intent: Intent | None = None) -> CancelResult:
        if self.kind == PortKind.EXTERNAL:
            self._external_gate(intent)
        elif self.kind != PortKind.SIMULATED:
            raise ExecutionDenied(("PORT_NOT_SIMULATED",))
        return self._port.cancel(client_id)
