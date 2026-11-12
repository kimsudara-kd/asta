"""M2-A: public market-data persistence, completeness, gaps, duplicates, ordering, staleness and the
scheduler that turns complete 1-minute buckets into durable STEP jobs.

All candles here are SYNTHETIC test inputs (no network)."""

from datetime import timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from asta.core.events import CandleEvent, QuoteEvent, Venue
from asta.db.paper_models import (
    DurableJob,
    MarketDataGap,
    MarketFeedConnection,
    MarketObservation,
    PaperSession,
)
from asta.paper import jobs, scheduler, store
from asta.paper.bars import aggregate_1m, bucket_start, load_bar
from asta.paper.collector import Collector, set_feed_state
from asta.paper.states import SessionState
from asta.paper.worker import max_decision_lag, plan_inputs
from tests.paper.helpers import SYMS, T0, make_cfg

pytestmark = pytest.mark.db
TF = 300  # 5-minute session bars keep the fixtures small
CFG = make_cfg(
    enabled_venues=("BINANCE",),
    timeframe=__import__("asta.research.types", fromlist=["x"]).Timeframe.M5,
)


def candle(venue, sym, minute, closed=True, price="100", interval="1m", ingested=None):
    t = T0 + timedelta(minutes=minute)
    return CandleEvent(
        venue=Venue(venue),
        symbol=sym,
        event_id=f"{interval}:{t.timestamp()}:{int(closed)}",
        exchange_ts=t + timedelta(minutes=1),
        ingested_ts=ingested or t + timedelta(minutes=1, seconds=1),
        interval=interval,
        open_time=t,
        open=D(price),
        high=D(price) + 1,
        low=D(price) - 1,
        close=D(price),
        volume=D("2"),
        closed=closed,
    )


@pytest.fixture
def factory(migrated_engine, db):
    return sessionmaker(migrated_engine, expire_on_commit=False)


def feed_minutes(col, db, venue, minutes, syms=SYMS):
    for m in minutes:
        for s in syms:
            col.on_candle(db, candle(venue, s, m))
    db.commit()


# ------------------------------------------------------------------ parsing -> observations
def test_only_closed_one_minute_candles_become_observations(db):
    col = Collector()
    assert col.on_candle(db, candle("BINANCE", "BTC-USDT", 0, closed=False)) is False
    assert col.on_candle(db, candle("BINANCE", "BTC-USDT", 0, closed=True)) is True
    assert col.on_candle(db, candle("BINANCE", "BTC-USDT", 1, interval="1h")) is False  # not 1m
    db.commit()
    assert col.stats["ignored_open_candles"] == 1
    rows = db.execute(select(MarketObservation)).scalars().all()
    assert len(rows) == 1 and rows[0].complete and rows[0].source == "LIVE"
    assert (
        rows[0].ingested_ts > rows[0].event_ts and rows[0].exchange_ts is not None
    )  # both stamps kept
    assert rows[0].venue == "BINANCE" and rows[0].symbol == "BTC-USDT"


def test_duplicates_are_ignored_and_counted(db):
    col = Collector()
    ev = candle("BINANCE", "ETH-USDT", 3)
    assert col.on_candle(db, ev) and not col.on_candle(db, ev)
    assert col.stats["duplicates"] == 1
    db.commit()
    assert db.execute(select(func.count()).select_from(MarketObservation)).scalar_one() == 1


def test_out_of_order_candle_fills_a_hole_and_is_counted_without_a_false_gap(db):
    col = Collector()
    for m in (0, 1, 3):  # 2 is late
        col.on_candle(db, candle("BINANCE", "BTC-USDT", m))
    assert col.stats["gaps_opened"] == 1  # minute 2 looked missing...
    col.on_candle(db, candle("BINANCE", "BTC-USDT", 2))  # ...then it arrives out of order
    assert col.stats["out_of_order"] == 1
    db.commit()
    gap = db.execute(select(MarketDataGap)).scalar_one()
    # the late arrival completed the hole, so the gap closed itself (nothing left to backfill)
    assert gap.status == "BACKFILLED" and gap.missing == 1 and "late" in gap.detail


def test_gap_detected_then_backfilled_from_rest_or_marked_unrecoverable(db):
    col = Collector(clock=lambda: T0 + timedelta(minutes=30))
    for m in (0, 1, 5):  # minutes 2,3,4 missing
        col.on_candle(db, candle("OKX", "BTC-USDT", m))
    db.commit()
    gap = db.execute(select(MarketDataGap)).scalar_one()
    assert (gap.missing, gap.status) == (3, "OPEN")
    assert gap.gap_start == T0 + timedelta(minutes=2) and gap.gap_end == T0 + timedelta(minutes=4)
    rest = [candle("OKX", "BTC-USDT", m) for m in (2, 3, 4)]
    assert col.apply_backfill(db, gap, rest) == "BACKFILLED"
    db.commit()
    assert {
        r.source
        for r in db.execute(
            select(MarketObservation).where(
                MarketObservation.event_ts > T0 + timedelta(minutes=1),
                MarketObservation.event_ts < T0 + timedelta(minutes=5),
            )
        ).scalars()
    } == {"BACKFILL"}
    # partial REST answer -> honestly UNRECOVERABLE, never pretended complete
    col2 = Collector(clock=lambda: T0 + timedelta(minutes=30))
    for m in (10, 11, 15):
        col2.on_candle(db, candle("OKX", "ETH-USDT", m))
    db.commit()
    g2 = db.execute(select(MarketDataGap).where(MarketDataGap.symbol == "ETH-USDT")).scalar_one()
    assert col2.apply_backfill(db, g2, [candle("OKX", "ETH-USDT", 12)]) == "UNRECOVERABLE"
    # a gap older than the REST window is unrecoverable without even asking
    col3 = Collector(clock=lambda: T0 + timedelta(days=3))
    col3.on_candle(db, candle("OKX", "SOL-USDT", 0))
    col3.on_candle(db, candle("OKX", "SOL-USDT", 4))
    db.commit()
    g3 = db.execute(select(MarketDataGap).where(MarketDataGap.symbol == "SOL-USDT")).scalar_one()
    assert col3.apply_backfill(db, g3, []) == "UNRECOVERABLE"


def test_gap_detection_survives_a_collector_restart(db):
    col = Collector()
    feed_minutes(col, db, "BINANCE", [0, 1, 2], ("BTC-USDT",))
    again = Collector()  # new process
    again.restore(db)
    again.on_candle(db, candle("BINANCE", "BTC-USDT", 7))  # 3..6 missing across the restart
    db.commit()
    assert db.execute(select(MarketDataGap.missing)).scalar_one() == 4


def test_quote_snapshots_are_sampled_once_per_minute_with_provenance(db):
    col = Collector()
    base = T0 + timedelta(minutes=5)
    for sec in (1, 20, 59):
        q = QuoteEvent(
            venue=Venue.BINANCE,
            symbol="BTC-USDT",
            event_id=str(sec),
            exchange_ts=None,
            ingested_ts=base + timedelta(seconds=sec),
            bid_price=D("99"),
            bid_qty=D("1"),
            ask_price=D("101"),
            ask_qty=D("1"),
        )
        col.on_quote(db, q, "BTCUSDT")
    db.commit()
    rows = (
        db.execute(select(MarketObservation).where(MarketObservation.kind == "QUOTE"))
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].bid == D("99") and rows[0].native_symbol == "BTCUSDT"


# ------------------------------------------------------------------ completeness -> bars
def test_a_session_bar_exists_only_when_every_minute_is_present(db):
    col = Collector()
    feed_minutes(col, db, "BINANCE", [0, 1, 2, 3], ("BTC-USDT",))  # 4 of 5 minutes
    assert load_bar(db, "BINANCE", "BTC-USDT", T0, TF) is None
    feed_minutes(col, db, "BINANCE", [4], ("BTC-USDT",))
    b = load_bar(db, "BINANCE", "BTC-USDT", T0, TF)
    assert b is not None and b.volume == D("10") and b.open == D("100") and b.high == D("101")
    assert bucket_start(T0 + timedelta(minutes=7), TF) == T0 + timedelta(minutes=5)
    assert aggregate_1m([], T0, TF) is None  # nothing is ever invented


def test_incomplete_rows_never_form_a_bar(db):
    col = Collector()
    feed_minutes(col, db, "BINANCE", range(5), ("BTC-USDT",))
    rows = db.execute(select(MarketObservation)).scalars().all()
    rows[2].complete = False  # in-memory flip only: aggregate_1m must treat it as missing
    assert aggregate_1m(rows, T0, TF) is None


# ------------------------------------------------------------------ feed state / stale policy
def fresh_feed(db, now, age_s=1, state="CONNECTED"):
    set_feed_state(db, "BINANCE", state, "LIVE", "", "c1", now=now)
    f = db.get(MarketFeedConnection, "BINANCE")
    f.last_event_at = now - timedelta(seconds=age_s)
    db.commit()


def test_plan_inputs_blocks_stale_feeds_gaps_and_disabled_feeds(db):
    col = Collector()
    feed_minutes(col, db, "BINANCE", range(5), ("BTC-USDT", "ETH-USDT"))  # SOL missing entirely
    now = T0 + timedelta(minutes=5, seconds=20)
    fresh_feed(db, now)
    bars, blocked = plan_inputs(db, CFG, T0, now)
    assert set(bars) == {"BINANCE:BTC-USDT", "BINANCE:ETH-USDT"}
    assert blocked == {"BINANCE:SOL-USDT": "DATA_GAP"}
    fresh_feed(db, now, age_s=CFG.stale_after_seconds + 5)  # feed went quiet
    _, blocked = plan_inputs(db, CFG, T0, now)
    assert (
        blocked["BINANCE:BTC-USDT"] == "STALE_DATA" and blocked["BINANCE:ETH-USDT"] == "STALE_DATA"
    )
    fresh_feed(db, now, state="DISABLED")
    _, blocked = plan_inputs(db, CFG, T0, now)
    assert blocked["BINANCE:BTC-USDT"] == "STALE_DATA"
    # a feed that was never recorded is stale too (fail-closed)
    db.execute(MarketFeedConnection.__table__.delete())
    db.commit()
    _, blocked = plan_inputs(db, CFG, T0, now)
    assert blocked["BINANCE:BTC-USDT"] == "STALE_DATA"


def test_catching_up_on_old_bars_after_downtime_does_not_block_on_current_feed_age(db):
    col = Collector()
    feed_minutes(col, db, "BINANCE", range(5))
    now = T0 + timedelta(hours=6)  # long after the bar
    fresh_feed(db, now, age_s=999)
    assert (now - (T0 + timedelta(seconds=TF))) > max_decision_lag(CFG)
    bars, blocked = plan_inputs(db, CFG, T0, now)
    assert (
        len(bars) == 3 and blocked == {}
    )  # recorded bars are absorbed; the worker mutes strategies


# ------------------------------------------------------------------ scheduler
def make_running_session(factory, last_step_ts):
    with factory() as db:
        s = store.create_session(db, CFG, "t")
        for st in (SessionState.INITIALIZING, SessionState.READY, SessionState.RUNNING):
            store.transition(db, s.id, st, "t")
        s.last_step_seq, s.last_step_ts = 10, last_step_ts
        db.commit()
        return s.id


def step_keys(factory, sid):
    with factory() as db:
        return [
            j.payload["step_ts"]
            for j in db.execute(
                select(DurableJob).where(DurableJob.session_id == sid).order_by(DurableJob.id)
            ).scalars()
        ]


def test_scheduler_creates_steps_in_time_order_only_for_complete_bars_and_is_idempotent(factory):
    sid = make_running_session(factory, T0 - timedelta(seconds=TF))  # next step = T0
    col = Collector()
    with factory() as db:
        feed_minutes(col, db, "BINANCE", range(10))  # bars T0 and T0+5m complete
        feed_minutes(col, db, "BINANCE", [10, 11])  # third bar incomplete
        now = T0 + timedelta(minutes=16)
        assert scheduler.schedule_steps(db, now, grace_s=90) == 2
        assert scheduler.schedule_steps(db, now, grace_s=90) == 0  # idempotent
        db.commit()
    assert step_keys(factory, sid) == [T0.isoformat(), (T0 + timedelta(minutes=5)).isoformat()]
    with (
        factory() as db
    ):  # grace passes: the incomplete bar is scheduled anyway (DATA_GAP, not a stall)
        assert scheduler.schedule_steps(db, T0 + timedelta(minutes=10 + 5 + 2), grace_s=90) == 1
        db.commit()
    assert len(step_keys(factory, sid)) == 3


def test_scheduler_ignores_stopped_and_unstarted_sessions(factory):
    with factory() as db:
        s = store.create_session(db, CFG, "t")  # CREATED
        db.commit()
        assert scheduler.schedule_steps(db, T0 + timedelta(days=1)) == 0
        assert db.get(PaperSession, s.id).state == "CREATED"


def test_scheduler_never_skips_ahead_when_the_oldest_bar_is_not_ready(factory):
    sid = make_running_session(factory, T0 - timedelta(seconds=TF))
    col = Collector()
    with factory() as db:
        feed_minutes(col, db, "BINANCE", range(5, 10))  # bar T0 missing, bar T0+5m complete
        assert scheduler.schedule_steps(db, T0 + timedelta(minutes=11), grace_s=600) == 0
        db.commit()
    assert step_keys(factory, sid) == []  # waits for T0; later bars are not enqueued out of order


# ------------------------------------------------------------------ bootstrap
def test_bootstrap_requires_full_warmup_history_and_creates_ordered_warm_steps(factory):
    from asta.research.data.models import Bar

    with factory() as db:
        s = store.create_session(db, CFG, "t")
        store.transition(db, s.id, SessionState.INITIALIZING, "t")
        db.commit()
        now = T0 + timedelta(days=2)
        last_closed = bucket_start(now, TF) - timedelta(seconds=TF)
        hist = {
            f"BINANCE:{sym}": [
                Bar(
                    last_closed - timedelta(seconds=TF * i),
                    D("100"),
                    D("101"),
                    D("99"),
                    D("100"),
                    D("1"),
                )
                for i in range(CFG.warmup_bars + 3)
            ]
            for sym in SYMS
        }
        n = scheduler.bootstrap_session(db, s.id, hist, now)
        assert n == CFG.warmup_bars
        db.commit()
        keys = step_keys(factory, s.id)
        assert keys == sorted(keys) and len(keys) == CFG.warmup_bars
        short = {k: v[:10] for k, v in hist.items()}
        s2 = store.create_session(db, CFG, "t")
        store.transition(db, s2.id, SessionState.INITIALIZING, "t")
        with pytest.raises(ValueError, match="only"):
            scheduler.bootstrap_session(db, s2.id, short, now)
        with pytest.raises(ValueError, match="missing"):
            scheduler.bootstrap_session(
                db, s2.id, {"BINANCE:BTC-USDT": hist["BINANCE:BTC-USDT"]}, now
            )


def test_unknown_feed_state_is_recorded_with_an_explicit_disable_reason(db):
    set_feed_state(db, "OKX", "DISABLED", "DISABLED", "ConnectError: simulated block", "svc")
    db.commit()
    f = db.get(MarketFeedConnection, "OKX")
    assert f.state == "DISABLED" and "simulated block" in f.reason and f.source == "DISABLED"
    assert jobs.queue_stats(db)["backlog"] == 0
