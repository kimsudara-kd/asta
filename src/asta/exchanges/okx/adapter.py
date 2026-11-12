"""OKX PUBLIC market-data adapter (read-only; no keys, no orders).

NOTE: endpoints and fields follow OKX API v5 as given in the handoff plus third-party sources;
the official docs were unreachable in M0 (see docs/exchange-matrix/capability-matrix.md).
Contract fixtures pin the parsing; confirm against the official docs before M3.
"""
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
from asta.exchanges.common.http import PublicHttp
from asta.exchanges.common.ws import Reconnected, ws_messages

REST_BASE = "https://www.okx.com"
WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"


def parse_rules(
    payload: dict[str, Any], symbols: Sequence[str], now: datetime
) -> list[SymbolRules]:
    if str(payload.get("code")) != "0":
        raise ValueError(f"OKX error: {payload.get('code')} {payload.get('msg')}")
    wanted = set(symbols)
    out: list[SymbolRules] = []
    for it in payload["data"]:
        if it["instId"] not in wanted:
            continue
        out.append(
            SymbolRules(
                venue=Venue.OKX,
                symbol=it["instId"],
                base=it["baseCcy"],
                quote=it["quoteCcy"],
                tick_size=to_decimal(it["tickSz"]),
                step_size=to_decimal(it["lotSz"]),
                min_qty=to_decimal(it["minSz"]),
                min_notional=None,  # OKX exposes minSz only
                trading=it.get("state") == "live",
                retrieved_at=now,
            )
        )
    return out


def parse_ws_message(raw: str | bytes, now: datetime) -> list[MarketEvent]:
    msg = loads_decimal(raw)
    if not isinstance(msg, dict) or "arg" not in msg or "data" not in msg:
        return []  # subscribe acks, errors, events
    channel = msg["arg"]["channel"]
    events: list[MarketEvent] = []
    for d in msg["data"]:
        if channel == "tickers":
            events.append(
                QuoteEvent(
                    venue=Venue.OKX,
                    symbol=d["instId"],
                    event_id=str(d["ts"]),
                    exchange_ts=from_epoch_ms(int(d["ts"])),
                    ingested_ts=now,
                    bid_price=to_decimal(d["bidPx"]),
                    bid_qty=to_decimal(d["bidSz"]),
                    ask_price=to_decimal(d["askPx"]),
                    ask_qty=to_decimal(d["askSz"]),
                )
            )
        elif channel == "trades":
            events.append(
                TradeEvent(
                    venue=Venue.OKX,
                    symbol=d["instId"],
                    event_id=str(d["tradeId"]),
                    exchange_ts=from_epoch_ms(int(d["ts"])),
                    ingested_ts=now,
                    price=to_decimal(d["px"]),
                    quantity=to_decimal(d["sz"]),
                    taker_side="BUY" if d["side"] == "buy" else "SELL",
                )
            )
        elif channel.startswith("candle"):
            ts, o, h, lo, c, vol = d[0], d[1], d[2], d[3], d[4], d[5]
            confirm = d[8] == "1"
            events.append(
                CandleEvent(
                    venue=Venue.OKX,
                    symbol=msg["arg"]["instId"],
                    event_id=f"{channel}:{ts}:{int(confirm)}",
                    exchange_ts=from_epoch_ms(int(ts)),
                    ingested_ts=now,
                    interval=channel.removeprefix("candle"),
                    open_time=from_epoch_ms(int(ts)),
                    open=to_decimal(o),
                    high=to_decimal(h),
                    low=to_decimal(lo),
                    close=to_decimal(c),
                    volume=to_decimal(vol),
                    closed=confirm,
                )
            )
    return events


class OkxPublicAdapter:
    venue = Venue.OKX

    def __init__(self, http: PublicHttp | None = None) -> None:
        self._http = http or PublicHttp(REST_BASE)

    async def server_time(self) -> datetime:
        d = await self._http.get("/api/v5/public/time")
        return from_epoch_ms(int(d["data"][0]["ts"]))

    async def symbol_rules(self, symbols: Sequence[str]) -> list[SymbolRules]:
        payload = await self._http.get("/api/v5/public/instruments", {"instType": "SPOT"})
        return parse_rules(payload, symbols, utcnow())

    async def recent_candles(self, symbol: str, interval: str, limit: int) -> list[CandleEvent]:
        payload = await self._http.get(
            "/api/v5/market/candles",
            {"instId": symbol, "bar": interval, "limit": min(limit, 300)},
        )
        now = utcnow()
        return [
            CandleEvent(
                venue=Venue.OKX,
                symbol=symbol,
                event_id=f"candle{interval}:{r[0]}:{r[8]}",
                exchange_ts=from_epoch_ms(int(r[0])),
                ingested_ts=now,
                interval=interval,
                open_time=from_epoch_ms(int(r[0])),
                open=to_decimal(r[1]),
                high=to_decimal(r[2]),
                low=to_decimal(r[3]),
                close=to_decimal(r[4]),
                volume=to_decimal(r[5]),
                closed=r[8] == "1",
            )
            for r in payload["data"]
        ]

    async def best_quote(self, symbol: str) -> QuoteEvent:
        payload = await self._http.get("/api/v5/market/books", {"instId": symbol, "sz": 1})
        book = payload["data"][0]
        bid, ask = book["bids"][0], book["asks"][0]
        return QuoteEvent(
            venue=Venue.OKX,
            symbol=symbol,
            event_id=str(book["ts"]),
            exchange_ts=from_epoch_ms(int(book["ts"])),
            ingested_ts=utcnow(),
            bid_price=to_decimal(bid[0]),
            bid_qty=to_decimal(bid[1]),
            ask_price=to_decimal(ask[0]),
            ask_qty=to_decimal(ask[1]),
        )

    def subscribe_messages(self, symbols: Sequence[str]) -> list[str]:
        args = [
            {"channel": ch, "instId": s} for s in symbols for ch in ("tickers", "trades", "candle1m")
        ]
        return [json.dumps({"op": "subscribe", "args": args}, separators=(",", ":"))]

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[MarketEvent | Reconnected]:
        async for frame in ws_messages(
            WS_PUBLIC, self.subscribe_messages(symbols), text_ping="ping", ping_every=20.0
        ):
            if isinstance(frame, Reconnected):
                yield frame
                continue
            for ev in parse_ws_message(frame, utcnow()):
                yield ev

    async def aclose(self) -> None:
        await self._http.aclose()
