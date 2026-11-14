"""Single internal order-authorization contract (ASTA-DECISION-007, workstream A).

Every place that CREATES or SUBMITS an order, of any kind, asks this module first:

* ``Surface.SIMULATION``  - the research/paper simulator creating a simulated order (plain data:
  it can never reach an exchange). Allowed; tests prove the gate runs before every creation.
* ``Surface.DEMO_INTENT`` - the order-path logic (``asta.orders``) handling an EXCHANGE_DEMO
  intent. Allowed only for that environment; the orders boundary adds kill-switch and port checks.
* ``Surface.EXTERNAL``    - anything that would leave this process towards an exchange.
  **Always DENY.** No flag, setting or environment variable changes this: it is a constant.

Pure, no I/O, no imports from the rest of the application, so the simulator may use it without
breaking the research module's purity rule. It is deliberately not a replacement for
``asta.risk.gateway.ExecutionGateway`` (the external-execution live-lock, unchanged): the orders
boundary consults both.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Surface(StrEnum):
    SIMULATION = "SIMULATION"
    DEMO_INTENT = "DEMO_INTENT"
    EXTERNAL = "EXTERNAL"


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    surface: Surface
    reasons: tuple[str, ...] = ()


class OrderGateDenied(Exception):
    def __init__(self, decision: GateDecision) -> None:
        super().__init__(f"{decision.surface.value} denied: {','.join(decision.reasons)}")
        self.decision = decision


def authorize(surface: Surface, environment: str = "") -> GateDecision:
    if surface == Surface.EXTERNAL:
        return GateDecision(False, surface, ("EXTERNAL_EXECUTION_DENIED_BY_DEFAULT",))
    if surface == Surface.SIMULATION:
        return GateDecision(True, surface, ())
    if surface == Surface.DEMO_INTENT:
        if environment != "EXCHANGE_DEMO":
            return GateDecision(False, surface, ("ENVIRONMENT_NOT_EXCHANGE_DEMO",))
        return GateDecision(True, surface, ())
    return GateDecision(False, surface, ("UNKNOWN_SURFACE",))  # fail closed


def require(surface: Surface, environment: str = "") -> GateDecision:
    d = authorize(surface, environment)
    if not d.allowed:
        raise OrderGateDenied(d)
    return d
