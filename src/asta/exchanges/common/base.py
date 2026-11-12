"""Canonical PUBLIC market-data adapter contract.

M0 deliberately has no authenticated or order-writing methods anywhere (ADR-0002).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from asta.core.events import CandleEvent, MarketEvent, QuoteEvent, SymbolRules, Venue
from asta.exchanges.common.ws import Reconnected


@runtime_checkable
class PublicMarketDataAdapter(Protocol):
    venue: Venue

    async def server_time(self) -> datetime: ...
    async def symbol_rules(self, symbols: Sequence[str]) -> list[SymbolRules]: ...
    async def recent_candles(self, symbol: str, interval: str, limit: int) -> list[CandleEvent]: ...
    async def best_quote(self, symbol: str) -> QuoteEvent: ...
    def stream(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent | Reconnected]: ...


class SymbolMapper:
    """Maps canonical 'BASE-QUOTE' <-> venue-native symbols."""

    def __init__(self, pairs: dict[str, str]) -> None:  # canonical -> native
        self._to_native = dict(pairs)
        self._to_canonical = {v: k for k, v in pairs.items()}

    def native(self, canonical: str) -> str:
        return self._to_native[canonical]

    def canonical(self, native: str) -> str:
        return self._to_canonical[native]


ALLOWLIST_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
BINANCE_ALLOWLIST = SymbolMapper(
    {"BTC-USDT": "BTCUSDT", "ETH-USDT": "ETHUSDT", "SOL-USDT": "SOLUSDT"}
)
