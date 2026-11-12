"""Binance Spot PUBLIC market-data adapter (read-only; no keys, no orders)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from typing import Any

from asta.core.events import (
    CandleEvent,
    MarketEvent,
    QuoteEvent,
    SymbolRules,
    TradeEvent,
    Venue,
)
from asta.core.money import loads_decimal, to_decimal
from asta.core.timeutil import from_epoch_ms, utcnow
from asta.exchanges.common.base import SymbolMapper
from asta.exchanges.common.http import PublicHttp
from asta.exchanges.common.ws import Reconnected, ws_messages

REST_BASE = "https://data-api.binance.vision"  # public market data only
WS_BASE = "wss://data-stream.binance.vision"  # market-data-only stream host


def parse_rules(
    info: dict[str, Any], symbols: Sequence[str], mapper: SymbolMapper, now: datetime
) -> list[SymbolRules]:
    wanted = {mapper.native(s) for s in symbols}
    out: list[SymbolRules] = []
    for item in info["symbols"]:
        if item["symbol"] not in wanted:
            continue
        flt = {f["filterType"]: f for f in item["filters"]}
        notional = flt.get("NOTIONAL") or flt.get("MIN_NOTIONAL")
        out.append(
            SymbolRules(
                venue=Venue.BINANCE,
                symbol=mapper.canonical(item["symbol"]),
                base=item["baseAsset"],
                quote=item["quoteAsset"],
                tick_size=to_decimal(flt["PRICE_FILTER"]["tickSize"]),
                step_size=to_decimal(flt["LOT_SIZE"]["stepSize"]),
                min_qty=to_decimal(flt["LOT_SIZE"]["minQty"]),
                min_notional=to_decimal(notional["minNotional"]) if notional else None,
                trading=item.get("status") == "TRADING",
                retrieved_at=now,
            )
        )
    return out


def parse_stream_message(
    raw: str | bytes, mapper: SymbolMapper, now: datetime
) -> list[MarketEvent]:
    """Parse one combined-stream frame ({"stream":..., "data":...}) or a raw payload."""
    msg = loads_decimal(raw)
    data = msg.get("data", msg) if isinstance(msg, dict) else None
    if not isinstance(data, dict):
        return []
    if data.get("e") == "trade":
        return [
            TradeEvent(
                venue=Venue.BINANCE,
                symbol=mapper.canonical(data["s"]),
                event_id=str(data["t"]),
                exchange_ts=from_epoch_ms(int(data["T"])),
                ingested_ts=now,
                price=to_decimal(data["p"]),
                quantity=to_decimal(data["q"]),
                taker_side="SELL" if data["m"] else "BUY",
            )
        ]
    if data.get("e") == "kline":
        k = data["k"]
        return [
            CandleEvent(
                venue=Venue.BINANCE,
                symbol=mapper.canonical(k["s"]),
                event_id=f"{k['i']}:{k['t']}:{int(bool(k['x']))}",
                exchange_ts=from_epoch_ms(int(data["E"])),
                ingested_ts=now,
                interval=k["i"],
                open_time=from_epoch_ms(int(k["t"])),
                open=to_decimal(k["o"]),
                high=to_decimal(k["h"]),
                low=to_decimal(k["l"]),
                close=to_decimal(k["c"]),
                volume=to_decimal(k["v"]),
                closed=bool(k["x"]),
            )
        ]
    if {"u", "s", "b", "B", "a", "A"} <= data.keys():  # spot bookTicker has no event time
        return [
            QuoteEvent(
                venue=Venue.BINANCE,
                symbol=mapper.canonical(data["s"]),
                event_id=str(data["u"]),
                exchange_ts=None,
                ingested_ts=now,
                bid_price=to_decimal(data["b"]),
                bid_qty=to_decimal(data["B"]),
                ask_price=to_decimal(data["a"]),
                ask_qty=to_decimal(data["A"]),
            )
        ]
    return []


class BinancePublicAdapter:
    venue = Venue.BINANCE

    def __init__(self, mapper: SymbolMapper, http: PublicHttp | None = None) -> None:
        self.mapper = mapper
        self._http = http or PublicHttp(REST_BASE)

    async def server_time(self) -> datetime:
        data = await self._http.get("/api/v3/time")
        return from_epoch_ms(int(data["serverTime"]))

    async def symbol_rules(self, symbols: Sequence[str]) -> list[SymbolRules]:
        natives = json.dumps([self.mapper.native(s) for s in symbols], separators=(",", ":"))
        info = await self._http.get("/api/v3/exchangeInfo", {"symbols": natives})
        return parse_rules(info, symbols, self.mapper, utcnow())

    async def recent_candles(self, symbol: str, interval: str, limit: int) -> list[CandleEvent]:
        rows = await self._http.get(
            "/api/v3/klines",
            {"symbol": self.mapper.native(symbol), "interval": interval, "limit": min(limit, 1000)},
        )
        now = utcnow()
        return [
            CandleEvent(
                venue=Venue.BINANCE,
                symbol=symbol,
                event_id=f"{interval}:{r[0]}:1",
                exchange_ts=from_epoch_ms(int(r[6])),
                ingested_ts=now,
                interval=interval,
                open_time=from_epoch_ms(int(r[0])),
                open=to_decimal(r[1]),
                high=to_decimal(r[2]),
                low=to_decimal(r[3]),
                close=to_decimal(r[4]),
                volume=to_decimal(r[5]),
                closed=from_epoch_ms(int(r[6])) <= now,
            )
            for r in rows
        ]

    async def best_quote(self, symbol: str) -> QuoteEvent:
        d = await self._http.get(
            "/api/v3/ticker/bookTicker", {"symbol": self.mapper.native(symbol)}
        )
        now = utcnow()
        return QuoteEvent(
            venue=Venue.BINANCE,
            symbol=symbol,
            event_id=f"rest:{now.timestamp()}",
            exchange_ts=None,
            ingested_ts=now,
            bid_price=to_decimal(d["bidPrice"]),
            bid_qty=to_decimal(d["bidQty"]),
            ask_price=to_decimal(d["askPrice"]),
            ask_qty=to_decimal(d["askQty"]),
        )

    def stream_url(self, symbols: Sequence[str]) -> str:
        names: list[str] = []
        for s in symbols:
            n = self.mapper.native(s).lower()
            names += [f"{n}@trade", f"{n}@bookTicker", f"{n}@kline_1m"]
        return f"{WS_BASE}/stream?streams={'/'.join(names)}"

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent | Reconnected]:
        async for frame in ws_messages(self.stream_url(symbols)):
            if isinstance(frame, Reconnected):
                yield frame
                continue
            for ev in parse_stream_message(frame, self.mapper, utcnow()):
                yield ev

    async def aclose(self) -> None:
        await self._http.aclose()
