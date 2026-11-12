from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest
import respx

from asta.core.events import CandleEvent, QuoteEvent, TradeEvent, Venue
from asta.core.money import loads_decimal
from asta.exchanges.binance.adapter import (
    BinancePublicAdapter,
)
from asta.exchanges.binance.adapter import (
    parse_rules as b_rules,
)
from asta.exchanges.binance.adapter import (
    parse_stream_message as b_parse,
)
from asta.exchanges.common.base import BINANCE_ALLOWLIST, PublicMarketDataAdapter
from asta.exchanges.common.http import PublicHttp
from asta.exchanges.okx.adapter import OkxPublicAdapter, parse_ws_message
from asta.exchanges.okx.adapter import parse_rules as o_rules

FX = Path(__file__).parent.parent / "fixtures"
NOW = datetime(2026, 11, 12, tzinfo=UTC)


def fx(name: str) -> str:
    return (FX / name).read_text()


def test_binance_trade():
    (ev,) = b_parse(fx("binance_trade.json"), BINANCE_ALLOWLIST, NOW)
    assert isinstance(ev, TradeEvent)
    assert (ev.symbol, ev.price, ev.quantity, ev.taker_side) == (
        "BTC-USDT",
        D("67000.10"),
        D("0.00150000"),
        "SELL",
    )
    assert ev.venue is Venue.BINANCE and ev.exchange_ts is not None


def test_binance_bookticker_and_spread():
    (ev,) = b_parse(fx("binance_bookticker.json"), BINANCE_ALLOWLIST, NOW)
    assert isinstance(ev, QuoteEvent)
    assert ev.symbol == "ETH-USDT" and ev.exchange_ts is None
    assert ev.ask_price > ev.bid_price and ev.spread_bps > 0


def test_binance_kline_incomplete_flagged():
    (ev,) = b_parse(fx("binance_kline.json"), BINANCE_ALLOWLIST, NOW)
    assert isinstance(ev, CandleEvent) and ev.closed is False and ev.close == D("150.50")


def test_binance_rules_decimal_exact():
    info = loads_decimal(fx("binance_exchangeinfo.json"))
    (r,) = b_rules(info, ["BTC-USDT"], BINANCE_ALLOWLIST, NOW)
    assert (r.tick_size, r.step_size, r.min_qty, r.min_notional) == (
        D("0.01"),
        D("0.00001"),
        D("0.00001"),
        D("5"),
    )
    assert r.trading


def test_binance_unknown_frames_ignored():
    assert b_parse('{"result":null,"id":1}', BINANCE_ALLOWLIST, NOW) == []


def test_okx_ws_messages():
    (q,) = parse_ws_message(fx("okx_tickers.json"), NOW)
    assert isinstance(q, QuoteEvent) and q.bid_price == D("67000.0") and q.symbol == "BTC-USDT"
    (t,) = parse_ws_message(fx("okx_trades.json"), NOW)
    assert isinstance(t, TradeEvent) and t.taker_side == "BUY" and t.event_id == "987"
    (c,) = parse_ws_message(fx("okx_candle.json"), NOW)
    assert isinstance(c, CandleEvent) and c.closed is False and c.interval == "1m"
    assert parse_ws_message(fx("okx_subscribe_ack.json"), NOW) == []


def test_okx_rules_filter_and_error():
    payload = loads_decimal(fx("okx_instruments.json"))
    rules = o_rules(payload, ["BTC-USDT"], NOW)
    assert len(rules) == 1 and rules[0].min_qty == D("0.00001") and rules[0].min_notional is None
    with pytest.raises(ValueError):
        o_rules({"code": "50011", "msg": "rate limit"}, ["BTC-USDT"], NOW)


def test_adapters_satisfy_protocol_and_have_no_write_methods():
    b = BinancePublicAdapter(BINANCE_ALLOWLIST)
    o = OkxPublicAdapter()
    for a in (b, o):
        assert isinstance(a, PublicMarketDataAdapter)
        names = {n.lower() for n in dir(a)}
        for banned in (
            "place",
            "cancel",
            "order",
            "withdraw",
            "transfer",
            "borrow",
            "margin",
            "leverage",
            "futures",
            "sign",
            "api_key",
            "secret",
        ):
            assert not any(banned in n for n in names), (banned, names)


@respx.mock
async def test_binance_rest_via_mock_and_egress():
    respx.get("https://data-api.binance.vision/api/v3/time").mock(
        return_value=httpx.Response(200, json={"serverTime": 1794464233952})
    )
    respx.get("https://data-api.binance.vision/api/v3/ticker/bookTicker").mock(
        return_value=httpx.Response(
            200,
            text='{"symbol":"BTCUSDT","bidPrice":"1.10","bidQty":"2","askPrice":"1.20","askQty":"3"}',
        )
    )
    a = BinancePublicAdapter(BINANCE_ALLOWLIST)
    assert (await a.server_time()).year >= 2026
    q = await a.best_quote("BTC-USDT")
    assert q.bid_price == D("1.10")


@respx.mock
async def test_rate_limit_raises_not_retries():
    from asta.exchanges.common.http import RateLimited

    route = respx.get("https://data-api.binance.vision/api/v3/time").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "30"})
    )
    with pytest.raises(RateLimited):
        await PublicHttp("https://data-api.binance.vision").get("/api/v3/time")
    assert route.call_count == 1


async def test_http_client_refuses_trading_path_and_host():
    from asta.core.egress import EgressDenied

    with pytest.raises(EgressDenied):
        await PublicHttp("https://data-api.binance.vision").get("/api/v3/order")
    with pytest.raises(EgressDenied):
        PublicHttp("https://api.binance.com")
