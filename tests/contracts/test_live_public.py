"""Opt-in smoke tests against REAL public market-data endpoints (no keys, read-only).

Run with ASTA_RUN_NETWORK_TESTS=1. OKX tests are reported as skipped/failed honestly if the venue
is unreachable from this machine; they are never faked.
"""

import asyncio
import os
from datetime import timedelta
from decimal import Decimal

import pytest

from asta.core.events import QuoteEvent, TradeEvent
from asta.core.timeutil import ClockMonitor, utcnow
from asta.exchanges.binance.adapter import BinancePublicAdapter
from asta.exchanges.common.base import ALLOWLIST_SYMBOLS, BINANCE_ALLOWLIST
from asta.exchanges.common.ws import Reconnected
from asta.exchanges.okx.adapter import OkxPublicAdapter
from asta.market_data.service import check_clock

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("ASTA_RUN_NETWORK_TESTS") != "1", reason="set ASTA_RUN_NETWORK_TESTS=1"
    ),
]


async def test_binance_rest_live():
    a = BinancePublicAdapter(BINANCE_ALLOWLIST)
    try:
        mon = ClockMonitor(timedelta(seconds=5))
        assert await check_clock(a, mon)
        rules = await a.symbol_rules(ALLOWLIST_SYMBOLS)
        assert {r.symbol for r in rules} == set(ALLOWLIST_SYMBOLS)
        assert all(r.tick_size > 0 and r.step_size > 0 and r.trading for r in rules)
        candles = await a.recent_candles("BTC-USDT", "1m", 5)
        assert len(candles) == 5 and all(isinstance(c.close, Decimal) for c in candles)
        q = await a.best_quote("BTC-USDT")
        assert q.ask_price >= q.bid_price > 0
    finally:
        await a.aclose()


async def test_binance_ws_live_receives_events():
    a = BinancePublicAdapter(BINANCE_ALLOWLIST)
    got: list[object] = []

    async def consume() -> None:
        async for item in a.stream(ALLOWLIST_SYMBOLS):
            got.append(item)
            kinds = {type(x) for x in got}
            if TradeEvent in kinds and QuoteEvent in kinds:
                return

    try:
        await asyncio.wait_for(consume(), timeout=30)
    finally:
        await a.aclose()
    assert any(isinstance(x, Reconnected) for x in got)
    assert any(
        isinstance(x, QuoteEvent) and (utcnow() - x.ingested_ts) < timedelta(seconds=30)
        for x in got
    )


async def test_okx_rest_live_or_unreachable():
    a = OkxPublicAdapter()
    try:
        try:
            t = await a.server_time()
        except Exception as exc:  # noqa: BLE001 - report honestly
            pytest.skip(f"OKX unreachable from this machine: {type(exc).__name__}")
        assert t.year >= 2026
        rules = await a.symbol_rules(ALLOWLIST_SYMBOLS)
        assert rules
    finally:
        await a.aclose()
