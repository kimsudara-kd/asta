"""Shared research enums and constants. Pure: no I/O, no network."""

from __future__ import annotations

from enum import StrEnum

SCHEMA_VERSION = "1"

#: Labels every research result must carry (UI + API). Simulation is never "live" or "realised".
RESULT_LABELS: tuple[str, ...] = (
    "SIMULATED",
    "HISTORICAL DATA",
    "ESTIMATED FILLS",
    "NOT LIVE TRADING",
)


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return _SECONDS[self]


_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
}


class SourceType(StrEnum):
    EXCHANGE_HISTORICAL = "EXCHANGE_HISTORICAL"
    DERIVED = "DERIVED"
    FIXTURE = "FIXTURE"
    SYNTHETIC = "SYNTHETIC"


#: Source types that may never be presented as real exchange history nor reach research results.
NON_RESEARCH_SOURCES: frozenset[SourceType] = frozenset({SourceType.FIXTURE, SourceType.SYNTHETIC})


class Severity(StrEnum):
    PASS = "PASS"  # noqa: S105 - severity label, not a credential
    WARNING = "WARNING"
    REJECTED = "REJECTED"

    @property
    def rank(self) -> int:
        return {"PASS": 0, "WARNING": 1, "REJECTED": 2}[self.value]


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"  # protective sell only (spot has no short entries)


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SIMULATED_OPEN = "SIMULATED_OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.REJECTED}
)
