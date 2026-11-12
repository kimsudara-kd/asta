import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from asta.core.events import QuoteEvent, TradeEvent, Venue
from asta.exchanges.common.ws import Backoff, Reconnected
from asta.market_data.service import run_stream
from asta.market_data.state import FreshnessMonitor, MarketState

T0 = datetime(2026, 11, 12, tzinfo=UTC)


def quote(eid="1", at=T0, venue=Venue.BINANCE):
    return QuoteEvent(venue=venue, symbol="BTC-USDT", event_id=eid, exchange_ts=None,
                      ingested_ts=at, bid_price=D("1"), bid_qty=D("1"), ask_price=D("2"),
                      ask_qty=D("1"))


def test_duplicate_events_dropped():
    st = MarketState()
    assert st.ingest(quote("1")) is True
    assert st.ingest(quote("1")) is False
    assert st.ingest(quote("2")) is True
    assert (st.events_accepted, st.duplicates_dropped) == (2, 1)


def test_same_id_different_venue_not_duplicate():
    st = MarketState()
    assert st.ingest(quote("1", venue=Venue.BINANCE))
    assert st.ingest(quote("1", venue=Venue.OKX))


def test_stale_data_detection_and_unknown_is_stale():
    now = [T0]
    fm = FreshnessMonitor(timedelta(seconds=15), now=lambda: now[0])
    st = MarketState(fm)
    assert fm.is_stale(Venue.BINANCE, "BTC-USDT")  # never seen -> stale
    st.ingest(quote("1", at=T0))
    assert not fm.is_stale(Venue.BINANCE, "BTC-USDT")
    now[0] = T0 + timedelta(seconds=16)
    assert fm.is_stale(Venue.BINANCE, "BTC-USDT")
    assert st.status(Venue.BINANCE, "BTC-USDT").stale


def test_reconnect_opens_gap_until_fresh_data():
    st = MarketState()
    st.mark_reconnect(Venue.OKX)
    assert st.gap_open[Venue.OKX] and st.reconnects[Venue.OKX] == 1
    st.ingest(quote("9", venue=Venue.OKX))
    assert not st.gap_open[Venue.OKX]


class FakeAdapter:
    venue = Venue.BINANCE

    def __init__(self, items):
        self.items = items

    async def stream(self, symbols):
        for i in self.items:
            yield i


async def test_run_stream_recovery_dedupes_replay_after_reconnect():
    """Disconnect -> reconnect replays an already-seen event: no double count, gap closes."""
    st = MarketState()
    trade = TradeEvent(venue=Venue.BINANCE, symbol="BTC-USDT", event_id="t1", exchange_ts=T0,
                       ingested_ts=T0, price=D("1"), quantity=D("1"))
    feed = [Reconnected(0), trade, Reconnected(1), trade, quote("q2")]
    await run_stream(FakeAdapter(feed), ["BTC-USDT"], st)
    assert st.events_accepted == 2 and st.duplicates_dropped == 1
    assert st.reconnects[Venue.BINANCE] == 2 and not st.gap_open[Venue.BINANCE]


def test_backoff_bounded_with_jitter():
    b = Backoff(base=1.0, cap=60.0)
    for attempt in range(0, 30):
        lo, hi = b.delay(attempt, rng=lambda: 0.0), b.delay(attempt, rng=lambda: 1.0)
        assert 0 < lo <= hi <= 60.0
    assert b.delay(30, rng=lambda: 1.0) == 60.0


async def test_supervisor_isolates_venue_failure():
    from asta.core.timeutil import ClockMonitor
    from asta.market_data.service import IngestSupervisor

    class Boom:
        venue = Venue.OKX

        async def server_time(self):
            raise OSError("unreachable")

    class Good(FakeAdapter):
        async def server_time(self):
            return datetime.now(UTC)

    st = MarketState()
    sup = IngestSupervisor(st, ClockMonitor())
    sup.start(Boom(), ["BTC-USDT"])  # type: ignore[arg-type]
    sup.start(Good([quote("g1")]), ["BTC-USDT"])  # type: ignore[arg-type]
    await asyncio.sleep(0.2)
    await sup.stop()
    assert Venue.OKX in sup.venue_errors and "unreachable" in sup.venue_errors[Venue.OKX]
    assert st.events_accepted == 1
