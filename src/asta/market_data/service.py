"""Ingestion service: runs one adapter stream into MarketState, with clock-drift checks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence

from asta.core.events import Venue
from asta.core.timeutil import ClockMonitor, utcnow
from asta.exchanges.common.base import PublicMarketDataAdapter
from asta.exchanges.common.ws import Reconnected
from asta.market_data.state import MarketState

log = logging.getLogger("asta.market_data")


async def check_clock(adapter: PublicMarketDataAdapter, monitor: ClockMonitor) -> bool:
    before = utcnow()
    venue_time = await adapter.server_time()
    after = utcnow()
    monitor.record(adapter.venue.value, before, venue_time, after)
    return monitor.is_drift_ok(adapter.venue.value)


async def run_stream(
    adapter: PublicMarketDataAdapter, symbols: Sequence[str], state: MarketState
) -> None:
    async for item in adapter.stream(symbols):
        if isinstance(item, Reconnected):
            state.mark_reconnect(adapter.venue)
            continue
        state.ingest(item)


class IngestSupervisor:
    """Owns background tasks per venue. A failure on one venue never stops another."""

    def __init__(self, state: MarketState, clock: ClockMonitor) -> None:
        self.state = state
        self.clock = clock
        self.venue_errors: dict[Venue, str] = {}
        self._tasks: list[asyncio.Task[None]] = []

    async def _guard(self, adapter: PublicMarketDataAdapter, symbols: Sequence[str]) -> None:
        while True:
            try:
                await check_clock(adapter, self.clock)
                self.venue_errors.pop(adapter.venue, None)
                await run_stream(adapter, symbols, self.state)
                await asyncio.sleep(1)  # stream ended unexpectedly: never restart in a tight loop
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # isolate per venue; record and retry with delay
                self.venue_errors[adapter.venue] = f"{type(exc).__name__}: {exc}"[:300]
                log.warning("venue %s failed: %s", adapter.venue, exc)
                await asyncio.sleep(15)

    def start(self, adapter: PublicMarketDataAdapter, symbols: Sequence[str]) -> None:
        self._tasks.append(asyncio.create_task(self._guard(adapter, symbols)))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
