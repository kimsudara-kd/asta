"""M2-A service-level fault injection with FAKE adapters (no network): a venue that cannot be
verified stays DISABLED with its reason; a stream that drops mid-run is re-opened, candles that were
missed are detected as a gap and repaired from 'REST'; closed candles are never lost on a full queue.

The fake adapters implement the same public interface as the real ones and nothing else."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from asta.core.events import CandleEvent, SymbolRules, Venue
from asta.core.timeutil import utcnow
from asta.db.models import MarketRulesVersion
from asta.db.paper_models import MarketDataGap, MarketFeedConnection, MarketObservation
from asta.exchanges.common.ws import Reconnected
from asta.paper_service import PaperService, native_symbol, rest_interval, to_bars
from tests.paper.helpers import SYMS, T0

pytestmark = pytest.mark.db
NOW0 = utcnow().replace(second=0, microsecond=0)
REAL_SLEEP = asyncio.sleep  # tests patch asyncio.sleep; the harness itself must keep real time


def rules(venue):
    return [
        SymbolRules(
            venue=Venue(venue),
            symbol=s,
            base=s.split("-")[0],
            quote="USDT",
            tick_size=1,
            step_size=1,
            min_qty=1,
            min_notional=5,
            trading=True,
            retrieved_at=NOW0,
        )
        for s in SYMS
    ]


def candle(venue, sym, minute_offset, closed=True):
    t = NOW0 - timedelta(minutes=minute_offset)
    return CandleEvent(
        venue=Venue(venue),
        symbol=sym,
        event_id=f"{t.timestamp()}:{int(closed)}",
        exchange_ts=t,
        ingested_ts=t + timedelta(seconds=61),
        interval="1m",
        open_time=t,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=1,
        closed=closed,
    )


class FakeAdapter:
    """Scripted public adapter. `script` is a list of stream sessions; each is a list of items or an
    Exception to raise (an abrupt disconnect)."""

    def __init__(self, venue, script=None, fail_verify=None, rest=None):
        self.venue = Venue(venue)
        self.script = list(script or [])
        self.fail_verify = fail_verify
        self.rest = rest or {}
        self.closed = False
        self.streams = 0

    async def server_time(self):
        if self.fail_verify:
            raise ConnectionError(self.fail_verify)
        return utcnow()

    async def symbol_rules(self, symbols):
        return rules(self.venue.value)

    async def recent_candles(self, symbol, interval, limit):
        return list(self.rest.get(symbol, []))

    async def stream(self, symbols):
        self.streams += 1
        session = self.script.pop(0) if self.script else None
        if session is None:
            await asyncio.sleep(3600)
            return
        yield Reconnected(self.streams - 1)
        for item in session:
            if isinstance(item, Exception):
                raise item
            yield item
        await asyncio.sleep(3600)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def factory(migrated_engine, db):
    return sessionmaker(migrated_engine, expire_on_commit=False)


async def run_for(svc, seconds):
    task = asyncio.create_task(svc.run())
    await REAL_SLEEP(seconds)
    svc.stop.set()
    await asyncio.wait_for(task, 30)


def test_unverifiable_venue_is_disabled_with_a_reason_and_never_bypassed(factory, monkeypatch):
    monkeypatch.setattr("asta.paper_service.FEED_SILENCE_S", 3600)
    okx = FakeAdapter("OKX", fail_verify="ConnectError: simulated network block")
    binance = FakeAdapter("BINANCE")
    svc = PaperService(factory, binance, okx)
    asyncio.run(run_for(svc, 1.5))
    with factory() as db:
        f = db.get(MarketFeedConnection, "OKX")
        assert (
            f.state == "DISABLED"
            and f.source == "DISABLED"
            and "simulated network block" in f.reason
        )
        assert db.get(MarketFeedConnection, "BINANCE").source == "LIVE"
        n_rules = db.execute(
            select(func.count())
            .select_from(MarketRulesVersion)
            .where(MarketRulesVersion.venue == "OKX")
        ).scalar_one()
    assert n_rules == 0 and okx.streams == 0  # no stream was even attempted for the disabled venue
    assert svc.enabled["OKX"] and not svc.enabled["BINANCE"]


def test_stream_disconnect_is_reopened_and_missed_minutes_become_a_repaired_gap(
    factory, monkeypatch
):
    real_sleep = asyncio.sleep

    async def fast_sleep(s, *a, **k):  # keep the 10 s reconnect pause out of the test
        await real_sleep(min(s, 0.05))

    monkeypatch.setattr("asta.paper_service.asyncio.sleep", fast_sleep)
    sym = "BTC-USDT"
    first = [
        candle("BINANCE", sym, 6),
        candle("BINANCE", sym, 5),
        ConnectionResetError("server closed"),
    ]
    second = [candle("BINANCE", sym, 1), candle("BINANCE", sym, 0)]  # minutes 4,3,2 were missed
    rest = {sym: [candle("BINANCE", sym, m) for m in (4, 3, 2)]}
    ad = FakeAdapter("BINANCE", [first, second], rest=rest)
    svc = PaperService(factory, ad, None)

    async def go():
        task = asyncio.create_task(svc.run())
        await real_sleep(1.0)
        # run the 30 s backfill pass once, directly, instead of waiting for it
        gaps = await svc._db(lambda db: svc.collector.open_gaps(db, "BINANCE"))
        for g in gaps:
            await svc._db(
                svc._apply_backfill, g.id, await ad.recent_candles(g.symbol, "1m", 10), g.symbol
            )
        svc.stop.set()
        await asyncio.wait_for(task, 30)

    asyncio.run(go())
    assert ad.streams >= 2  # the dropped stream was re-opened
    with factory() as db:
        mins = sorted(
            t
            for (t,) in db.execute(
                select(MarketObservation.event_ts).where(MarketObservation.kind == "CANDLE_1M")
            )
        )
        gap = db.execute(select(MarketDataGap)).scalar_one()
        sources = {
            s
            for (s,) in db.execute(
                select(MarketObservation.source).where(MarketObservation.kind == "CANDLE_1M")
            )
        }
    assert [m - mins[0] for m in mins] == [
        timedelta(minutes=i) for i in range(7)
    ]  # contiguous again
    assert (gap.missing, gap.status) == (3, "BACKFILLED") and sources == {"LIVE", "BACKFILL"}


def test_open_candles_are_not_stored_and_duplicates_collapse(factory):
    sym = "ETH-USDT"
    items = [
        candle("BINANCE", sym, 3, closed=False),
        candle("BINANCE", sym, 3),
        candle("BINANCE", sym, 3),
    ]
    ad = FakeAdapter("BINANCE", [items])
    svc = PaperService(factory, ad, None)
    asyncio.run(run_for(svc, 1.5))
    with factory() as db:
        assert (
            db.execute(
                select(func.count())
                .select_from(MarketObservation)
                .where(MarketObservation.kind == "CANDLE_1M")
            ).scalar_one()
            == 1
        )
    assert (
        svc.collector.stats["duplicates"] == 1 and svc.collector.stats["ignored_open_candles"] == 1
    )


def test_silent_feed_is_marked_reconnecting_with_an_incident_then_recovers(factory, monkeypatch):
    real_sleep = asyncio.sleep

    async def fast_sleep(s, *a, **k):
        await real_sleep(min(s, 0.05))

    monkeypatch.setattr("asta.paper_service.asyncio.sleep", fast_sleep)
    monkeypatch.setattr("asta.paper_service.FEED_SILENCE_S", 0)  # any silence counts
    ad = FakeAdapter("BINANCE", [[]])  # connects, then says nothing
    svc = PaperService(factory, ad, None)
    asyncio.run(run_for(svc, 1.0))
    from asta.db.models import Incident

    with factory() as db:
        f = db.get(MarketFeedConnection, "BINANCE")
        inc = db.execute(select(Incident).where(Incident.dedupe_key == "feed:BINANCE")).scalar_one()
    assert f.state == "RECONNECTING" and inc.kind == "FEED_DISCONNECTED" and inc.resolved_at is None


def test_helpers_map_intervals_and_symbols_and_aggregate_only_closed_bars():
    assert rest_interval("OKX", 3600) == "1H" and rest_interval("BINANCE", 3600) == "1h"
    assert rest_interval("OKX", 300) == "5m" and native_symbol("BINANCE", "BTC-USDT") == "BTCUSDT"
    assert native_symbol("OKX", "BTC-USDT") == "BTC-USDT"
    bars = to_bars([candle("OKX", "BTC-USDT", 2, closed=False), candle("OKX", "BTC-USDT", 3)])
    assert len(bars) == 1 and bars[0].open_time == NOW0 - timedelta(minutes=3)
    assert T0 < NOW0
