"""In-memory market state: dedupe, freshness, gap marking."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from asta.core.events import MarketEvent, QuoteEvent, Venue
from asta.core.timeutil import utcnow


class Deduper:
    """Bounded LRU of seen dedupe keys. Duplicate events are dropped (idempotent ingest)."""

    def __init__(self, capacity: int = 50_000) -> None:
        self._seen: OrderedDict[tuple[str, str, str, str], None] = OrderedDict()
        self._cap = capacity

    def is_new(self, event: MarketEvent) -> bool:
        key = event.dedupe_key
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = None
        if len(self._seen) > self._cap:
            self._seen.popitem(last=False)
        return True


@dataclass
class FeedStatus:
    venue: Venue
    symbol: str
    last_ingested: datetime | None
    stale: bool
    source: str
    reconnects: int
    gap_open: bool


class FreshnessMonitor:
    """Unknown or old data is STALE (fail-closed)."""

    def __init__(self, stale_after: timedelta = timedelta(seconds=15),
                 now: Callable[[], datetime] = utcnow) -> None:
        self.stale_after = stale_after
        self._now = now
        self._last: dict[tuple[Venue, str], datetime] = {}

    def touch(self, venue: Venue, symbol: str, at: datetime) -> None:
        self._last[(venue, symbol)] = at

    def last(self, venue: Venue, symbol: str) -> datetime | None:
        return self._last.get((venue, symbol))

    def is_stale(self, venue: Venue, symbol: str) -> bool:
        ts = self._last.get((venue, symbol))
        return ts is None or (self._now() - ts) > self.stale_after


class MarketState:
    def __init__(self, freshness: FreshnessMonitor | None = None) -> None:
        self.freshness = freshness or FreshnessMonitor()
        self.dedupe = Deduper()
        self.quotes: dict[tuple[Venue, str], QuoteEvent] = {}
        self.reconnects: dict[Venue, int] = {}
        self.gap_open: dict[Venue, bool] = {}
        self.duplicates_dropped = 0
        self.events_accepted = 0

    def mark_reconnect(self, venue: Venue) -> None:
        """A reconnect means events may have been missed: open a gap until fresh data arrives."""
        self.reconnects[venue] = self.reconnects.get(venue, 0) + 1
        self.gap_open[venue] = True

    def ingest(self, event: MarketEvent) -> bool:
        if not self.dedupe.is_new(event):
            self.duplicates_dropped += 1
            return False
        self.events_accepted += 1
        self.freshness.touch(event.venue, event.symbol, event.ingested_ts)
        self.gap_open[event.venue] = False
        if isinstance(event, QuoteEvent):
            self.quotes[(event.venue, event.symbol)] = event
        return True

    def status(self, venue: Venue, symbol: str) -> FeedStatus:
        q = self.quotes.get((venue, symbol))
        return FeedStatus(
            venue=venue, symbol=symbol, last_ingested=self.freshness.last(venue, symbol),
            stale=self.freshness.is_stale(venue, symbol), source=q.source if q else "NONE",
            reconnects=self.reconnects.get(venue, 0), gap_open=self.gap_open.get(venue, False))
