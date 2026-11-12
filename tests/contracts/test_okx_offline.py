"""OKX behaviour verified OFFLINE (live OKX is unreachable from the dev PC; see M0-R1 evidence).

REST shapes are mocked with respx; WebSocket behaviour (subscribe, text ping/pong, drop and
reconnect, replay dedupe) runs against a real local `websockets` server. Payload shapes follow
OKX API v5 as recalled from the handoff/SDK; they are NOT confirmed against the official docs.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

import httpx
import pytest
import pytest_asyncio
import respx
import websockets
from websockets.asyncio.server import ServerConnection

from asta.core import egress
from asta.core.events import QuoteEvent, Venue
from asta.core.timeutil import ClockMonitor, from_epoch_ms
from asta.exchanges.common.base import ALLOWLIST_SYMBOLS
from asta.exchanges.common.ws import Backoff, Reconnected, ws_messages
from asta.exchanges.okx.adapter import OkxPublicAdapter, parse_ws_message
from asta.market_data.service import check_clock
from asta.market_data.state import MarketState

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def tick(ts: int, bid: str = "67000.0") -> str:
    return json.dumps(
        {
            "arg": {"channel": "tickers", "instId": "BTC-USDT"},
            "data": [
                {
                    "instId": "BTC-USDT",
                    "bidPx": bid,
                    "bidSz": "1",
                    "askPx": "67000.2",
                    "askSz": "1",
                    "ts": str(ts),
                }
            ],
        }
    )


@respx.mock
async def test_okx_rest_time_rules_candles_book() -> None:
    respx.get("https://www.okx.com/api/v5/public/time").mock(
        return_value=httpx.Response(
            200, text='{"code":"0","msg":"","data":[{"ts":"1794464233952"}]}'
        )
    )
    respx.get("https://www.okx.com/api/v5/public/instruments").mock(
        return_value=httpx.Response(
            200,
            text=json.dumps(
                {
                    "code": "0",
                    "data": [
                        {
                            "instId": s,
                            "baseCcy": s.split("-")[0],
                            "quoteCcy": "USDT",
                            "tickSz": "0.1",
                            "lotSz": "0.00000001",
                            "minSz": "0.00001",
                            "state": "live",
                        }
                        for s in ALLOWLIST_SYMBOLS
                    ],
                }
            ),
        )
    )
    respx.get("https://www.okx.com/api/v5/market/candles").mock(
        return_value=httpx.Response(
            200,
            text=json.dumps(
                {
                    "code": "0",
                    "data": [
                        ["1794464220000", "1", "2", "0.5", "1.5", "10", "15", "15", "1"],
                        ["1794464280000", "1.5", "2", "1", "1.7", "4", "6", "6", "0"],
                    ],
                }
            ),
        )
    )
    respx.get("https://www.okx.com/api/v5/market/books").mock(
        return_value=httpx.Response(
            200,
            text=json.dumps(
                {
                    "code": "0",
                    "data": [
                        {
                            "asks": [["67000.2", "0.4", "0", "1"]],
                            "bids": [["67000.0", "0.5", "0", "1"]],
                            "ts": "1794464233123",
                        }
                    ],
                }
            ),
        )
    )
    a = OkxPublicAdapter()
    try:
        assert (await a.server_time()).year >= 2026
        rules = await a.symbol_rules(ALLOWLIST_SYMBOLS)
        assert {r.symbol for r in rules} == set(ALLOWLIST_SYMBOLS)
        assert all(r.trading and r.step_size == D("0.00000001") for r in rules)
        candles = await a.recent_candles("BTC-USDT", "1m", 2)
        assert [c.closed for c in candles] == [True, False]  # incomplete candle flagged
        assert candles[0].high == D("2") and isinstance(candles[0].open, D)
        q = await a.best_quote("BTC-USDT")
        assert (q.bid_price, q.ask_price) == (D("67000.0"), D("67000.2")) and q.spread_bps > 0
    finally:
        await a.aclose()


@respx.mock
async def test_okx_clock_drift_fail_closed() -> None:
    """Venue clock 10 s ahead => drift NOT ok; healthy => ok."""
    route = respx.get("https://www.okx.com/api/v5/public/time")
    mon = ClockMonitor(timedelta(seconds=2))
    a = OkxPublicAdapter()
    try:
        ahead = int((datetime.now(UTC) + timedelta(seconds=10)).timestamp() * 1000)
        route.mock(
            return_value=httpx.Response(200, json={"code": "0", "data": [{"ts": str(ahead)}]})
        )
        assert await check_clock(a, mon) is False
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        route.mock(
            return_value=httpx.Response(200, json={"code": "0", "data": [{"ts": str(now_ms)}]})
        )
        assert await check_clock(a, mon) is True
    finally:
        await a.aclose()


@respx.mock
async def test_okx_error_code_and_rate_limit_are_not_swallowed() -> None:
    from asta.exchanges.common.http import RateLimited

    respx.get("https://www.okx.com/api/v5/public/instruments").mock(
        return_value=httpx.Response(200, json={"code": "50011", "msg": "Too Many Requests"})
    )
    a = OkxPublicAdapter()
    with pytest.raises(ValueError, match="50011"):
        await a.symbol_rules(ALLOWLIST_SYMBOLS)
    respx.get("https://www.okx.com/api/v5/public/time").mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimited):
        await a.server_time()
    await a.aclose()


def test_okx_subscribe_message_shape() -> None:
    (msg,) = OkxPublicAdapter().subscribe_messages(ALLOWLIST_SYMBOLS)
    body = json.loads(msg)
    assert body["op"] == "subscribe" and len(body["args"]) == 9
    assert {a["channel"] for a in body["args"]} == {"tickers", "trades", "candle1m"}
    assert "apiKey" not in msg and "sign" not in msg  # public, unauthenticated


# ---------------- real local WebSocket server: keepalive, drop, reconnect, resubscribe ----------


class FakeOkxServer:
    def __init__(self) -> None:
        self.connections = 0
        self.subscribes: list[dict[str, Any]] = []
        self.pings = 0
        self.three_pings = asyncio.Event()

    async def handler(self, ws: ServerConnection) -> None:
        self.connections += 1
        n = self.connections
        async for raw in ws:
            if raw == "ping":
                self.pings += 1
                if self.pings >= 3:
                    self.three_pings.set()
                await ws.send("pong")
                continue
            self.subscribes.append(json.loads(raw))
            await ws.send('{"event":"subscribe","arg":{"channel":"tickers","instId":"BTC-USDT"}}')
            await ws.send(tick(1794464233123))  # replayed on every connection (same event id)
            await ws.send(tick(1794464234000 + n))
            if n == 1:
                await ws.close(code=1011)  # server drops us after the first batch
                return


@pytest_asyncio.fixture
async def local_ws(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[str, FakeOkxServer]]:
    # test-only: allow the loopback test server (the production allowlist is untouched)
    monkeypatch.setattr(egress, "ALLOWED_HOSTS", egress.ALLOWED_HOSTS | {"127.0.0.1"})
    monkeypatch.setattr(egress, "ALLOWED_SCHEMES", egress.ALLOWED_SCHEMES | {"ws"})
    srv = FakeOkxServer()
    server = await websockets.serve(srv.handler, "127.0.0.1", 0)
    port = next(iter(server.sockets)).getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}/ws/v5/public", srv
    finally:
        server.close()
        await server.wait_closed()


async def test_okx_ws_reconnect_resubscribe_dedupe_and_keepalive(
    local_ws: tuple[str, FakeOkxServer],
) -> None:
    url, srv = local_ws
    if True:
        adapter = OkxPublicAdapter()
        state = MarketState()
        sub = adapter.subscribe_messages(["BTC-USDT"])
        frames: list[Any] = []

        async def consume() -> None:
            async for item in ws_messages(
                url, sub, text_ping="ping", ping_every=0.2, backoff=Backoff(base=0.05, cap=0.1)
            ):
                frames.append(item)
                if isinstance(item, Reconnected):
                    if item.attempt > 0:
                        state.mark_reconnect(Venue.OKX)
                    continue
                for ev in parse_ws_message(item, NOW):
                    state.ingest(ev)
                if state.events_accepted >= 3:  # 2 from first conn + 1 new after reconnect
                    return

        await asyncio.wait_for(consume(), timeout=15)
        assert srv.connections >= 2  # dropped, then reconnected
        assert len(srv.subscribes) >= 2  # re-subscribed after reconnect
        assert all(s["op"] == "subscribe" for s in srv.subscribes)
        assert sum(isinstance(f, Reconnected) for f in frames) >= 2
        assert state.reconnects[Venue.OKX] == 1  # initial connect is not counted as a reconnect
        assert state.duplicates_dropped >= 1  # replayed event id dropped, not double counted
        assert state.events_accepted == 3
        q = state.quotes[(Venue.OKX, "BTC-USDT")]
        assert isinstance(q, QuoteEvent) and q.bid_price == D("67000.0")
        assert q.exchange_ts == from_epoch_ms(1794464234002)  # newest (post-reconnect) quote wins


async def test_ws_text_ping_answered_with_pong_is_filtered(
    local_ws: tuple[str, FakeOkxServer],
) -> None:
    url, srv = local_ws
    seen: list[str] = []

    async def consume() -> None:
        async for item in ws_messages(
            url,
            ['{"op":"subscribe","args":[]}'],
            text_ping="ping",
            ping_every=0.1,
            backoff=Backoff(base=0.05, cap=0.1),
        ):
            if isinstance(item, str):
                seen.append(item)

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(srv.three_pings.wait(), timeout=15)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert srv.pings >= 3  # client keepalive pings reach the server
    assert "pong" not in seen  # keepalive replies never reach the parser
