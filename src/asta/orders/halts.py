"""Kill-switch controller: automatic triggers that LATCH, manual release only (pure).

The guard (pretrade.py) and the dispatcher already treat ``DayState.kill_switch`` as an input; this
module decides when it turns on. Every trigger is an explicit, named event; once tripped the switch
stays on until a named human releases it with a reason, and every change is recorded in ``history``
(the caller appends that to the audit chain). Releasing never clears the recorded trips.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Trigger(StrEnum):
    MANUAL = "MANUAL"
    RISK_HALT = "RISK_HALT"  # the paper/risk engine halted (drawdown, daily loss)
    RECONCILIATION_ANOMALY = "RECONCILIATION_ANOMALY"
    UNKNOWN_EXCHANGE_ORDER = "UNKNOWN_EXCHANGE_ORDER"  # an order at the exchange that we never made
    AUTH_FAILURES = "AUTH_FAILURES"
    DAILY_CAP_HIT = "DAILY_CAP_HIT"
    CLOCK_SKEW = "CLOCK_SKEW"
    EXCHANGE_RESET = "EXCHANGE_RESET"  # testnet reset detected: stop until a human re-baselines
    RATE_LIMIT_BAN = "RATE_LIMIT_BAN"


@dataclass(frozen=True)
class Change:
    at: int
    action: str  # "TRIP" | "RELEASE"
    trigger: Trigger | None
    by: str
    reason: str


@dataclass
class KillSwitch:
    max_auth_failures: int = 3
    active: bool = False
    trips: list[Trigger] = field(default_factory=list)
    history: list[Change] = field(default_factory=list)
    _auth_failures: int = 0

    def trip(self, trigger: Trigger, at: int, by: str = "system", reason: str = "") -> None:
        if trigger not in self.trips:
            self.trips.append(trigger)
        if not self.active or not self.history or self.history[-1].trigger != trigger:
            self.history.append(Change(at, "TRIP", trigger, by, reason))
        self.active = True

    def auth_failed(self, at: int) -> None:
        self._auth_failures += 1
        if self._auth_failures >= self.max_auth_failures:
            self.trip(
                Trigger.AUTH_FAILURES, at, reason=f"{self._auth_failures} consecutive failures"
            )

    def auth_ok(self) -> None:
        self._auth_failures = 0  # consecutive means consecutive; a trip itself stays latched

    def release(self, at: int, by: str, reason: str) -> bool:
        """Only a named human with a reason may release; anything else is refused."""
        if not by.strip() or by == "system" or not reason.strip():
            return False
        if not self.active:
            return False
        self.active = False
        self._auth_failures = 0
        self.history.append(Change(at, "RELEASE", None, by, reason))
        return True
