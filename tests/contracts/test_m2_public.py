"""M2-A contracts for the public-data additions: OKX paged history, rate limits, endpoint policy."""

import asyncio
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest

from asta.core.egress import EgressDenied, assert_url_allowed
from asta.exchanges.common.http import PublicHttp, RateLimited
from asta.exchanges.okx.adapter import WS_PUBLIC, OkxPublicAdapter

HOUR = 3_600_000
BASE = int(datetime(2026, 11, 1, 12, tzinfo=UTC).timestamp() * 1000)


def row(i, confirm="1"):
    t = BASE - i * HOUR
    return [str(t), "100", "101", "99", "100.5", "7", "700", "700", confirm]


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path == "/api/v5/market/candles":
            return {"code": "0", "data": [row(0, "0"), *[row(i) for i in range(1, 4)]]}
        if path == "/api/v5/market/history-candles":
            after = int(params["after"])
            n_done = (BASE - after) // HOUR  # hours between BASE and the page cursor
            return {"code": "0", "data": [row(n_done + 1 + k) for k in range(params["limit"])]}
        raise AssertionError(path)

    async def aclose(self):
        pass


def test_okx_paged_history_is_contiguous_newest_first_and_flags_the_open_candle():
    fake = FakeHttp()
    ad = OkxPublicAdapter(cast(PublicHttp, fake))
    out = asyncio.run(ad.candles_paged("BTC-USDT", "1H", 10))
    assert len(out) == 10
    times = [c.open_time for c in out]
    assert times == sorted(times, reverse=True) and len(set(times)) == 10
    gaps = {(a - b).total_seconds() for a, b in zip(times, times[1:], strict=False)}
    assert gaps == {3600.0}  # no hole between the latest page and the history pages
    assert out[0].closed is False and all(c.closed for c in out[1:])  # confirm flag respected
    paths = [c[0] for c in fake.calls]
    assert paths[0] == "/api/v5/market/candles" and set(paths[1:]) == {
        "/api/v5/market/history-candles"
    }
    assert all(c[1]["limit"] <= 100 for c in fake.calls[1:])


def test_rate_limit_responses_raise_instead_of_retrying_in_a_tight_loop():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "7"})

    http = PublicHttp(
        "https://www.okx.com", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(RateLimited, match="Retry-After=7"):
        asyncio.run(http.get("/api/v5/market/candles", {"instId": "BTC-USDT"}))
    assert calls["n"] == 1  # one request, the caller backs off (never a retry storm)
    http418 = PublicHttp(
        "https://data-api.binance.vision",
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(418))),
    )
    with pytest.raises(RateLimited):
        asyncio.run(http418.get("/api/v3/time"))


def test_endpoint_policy_for_the_m2_feeds():
    assert_url_allowed(WS_PUBLIC)  # public market-data WebSocket (TLS port 443)
    assert WS_PUBLIC.startswith("wss://ws.okx.com/ws/v5/public")
    assert_url_allowed("https://www.okx.com/api/v5/market/history-candles?instId=BTC-USDT")
    assert_url_allowed("https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT")
    for bad in (
        "wss://ws.okx.com/ws/v5/business",  # candle channel lives here: deliberately NOT allowed
        "wss://ws.okx.com/ws/v5/private",
        "https://www.okx.com/api/v5/trade/order",
        "https://www.okx.com/api/v5/account/balance",
        "https://data-api.binance.vision/api/v3/order",
        "https://data-api.binance.vision/api/v3/userDataStream",
        "http://data-api.binance.vision/api/v3/time",
        "https://api.binance.com/api/v3/time",
        "https://evil.example/api/v3/time",
        "https://user:pw@www.okx.com/api/v5/public/time",
    ):
        with pytest.raises(EgressDenied):
            assert_url_allowed(bad)


def test_redirects_to_unapproved_destinations_are_never_followed():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    http = PublicHttp(
        "https://data-api.binance.vision", httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(http.get("/api/v3/time"))
    assert seen == [
        "https://data-api.binance.vision/api/v3/time"
    ]  # the redirect target is never requested
