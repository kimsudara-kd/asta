"""Clearly-labelled FIXTURE feed, used only when a venue is unreachable or explicitly requested.

Every event produced here has source="FIXTURE" so the dashboard can never present it as live data.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from asta.core.events import QuoteEvent, Venue
from asta.core.timeutil import utcnow
from asta.market_data.state import MarketState

_FIXTURE_QUOTES: dict[str, tuple[str, str]] = {
    "BTC-USDT": ("67000.00", "67000.10"),
    "ETH-USDT": ("3200.00", "3200.10"),
    "SOL-USDT": ("150.00", "150.02"),
}


async def run_fixture_feed(state: MarketState, venue: Venue, period: float = 5.0) -> None:
    n = 0
    while True:
        n += 1
        for sym, (bid, ask) in _FIXTURE_QUOTES.items():
            state.ingest(
                QuoteEvent(
                    venue=venue,
                    symbol=sym,
                    event_id=f"fixture:{n}",
                    exchange_ts=None,
                    ingested_ts=utcnow(),
                    source="FIXTURE",
                    bid_price=Decimal(bid),
                    bid_qty=Decimal("1"),
                    ask_price=Decimal(ask),
                    ask_qty=Decimal("1"),
                )
            )
        await asyncio.sleep(period)
