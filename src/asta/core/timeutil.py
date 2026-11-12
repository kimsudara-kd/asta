"""UTC time helpers and clock-drift tracking."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def utcnow() -> datetime:
    return datetime.now(UTC)


def require_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return ts


def from_epoch_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


@dataclass(frozen=True)
class DriftReading:
    venue: str
    offset: timedelta  # venue_time - local_time at measurement (midpoint-corrected)
    measured_at: datetime
    round_trip: timedelta


class ClockMonitor:
    """Tracks per-venue clock offset and reports whether drift exceeds a threshold (fail-closed)."""

    def __init__(self, max_drift: timedelta = timedelta(seconds=2),
                 now: Callable[[], datetime] = utcnow) -> None:
        self.max_drift = max_drift
        self._now = now
        self._readings: dict[str, DriftReading] = {}

    def record(self, venue: str, local_before: datetime, venue_time: datetime,
               local_after: datetime) -> DriftReading:
        require_utc(local_before), require_utc(venue_time), require_utc(local_after)
        rtt = local_after - local_before
        midpoint = local_before + rtt / 2
        reading = DriftReading(venue, venue_time - midpoint, self._now(), rtt)
        self._readings[venue] = reading
        return reading

    def is_drift_ok(self, venue: str) -> bool:
        """Unknown drift counts as NOT ok (fail-closed)."""
        reading = self._readings.get(venue)
        return reading is not None and abs(reading.offset) <= self.max_drift

    def reading(self, venue: str) -> DriftReading | None:
        return self._readings.get(venue)
