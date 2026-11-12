"""Public market-data persistence (M2-A): normalised events -> durable observations, gaps, status.

Synchronous and network-free: the async service feeds it events produced by the (egress-checked)
public adapters. Only CLOSED 1-minute candles feed the simulation; quotes are sampled once per
minute for spread context. Every irregularity is recorded, never smoothed over:

* duplicates      -> ignored (unique key), counted
* out-of-order    -> accepted if it fills a hole, counted
* gaps            -> OPEN gap rows; backfilled from REST when possible, else UNRECOVERABLE
* completeness    -> a feed never claims continuity it cannot prove
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from asta.core.events import CandleEvent, MarketEvent, QuoteEvent
from asta.core.timeutil import utcnow
from asta.db.paper_models import (
    MarketDataGap,
    MarketFeedConnection,
    MarketObservation,
)
from asta.paper.bars import CANDLE_1M, QUOTE

MINUTE = timedelta(minutes=1)
BACKFILL_MAX_MINUTES = 1000  # REST history window we are willing to rely on


class Collector:
    def __init__(self, clock: Callable[[], datetime] = utcnow, connection_id: str = "") -> None:
        self.clock = clock
        self.connection_id = connection_id
        self.last_open: dict[tuple[str, str], datetime] = {}
        self.stats: dict[str, int] = {
            "candles": 0,
            "duplicates": 0,
            "out_of_order": 0,
            "ignored_open_candles": 0,
            "gaps_opened": 0,
            "quotes": 0,
        }
        self._last_quote_minute: dict[tuple[str, str], datetime] = {}
        self._last_touch: dict[str, datetime] = {}

    # ------------------------------------------------------------------ restore after restart
    def restore(self, db: Session) -> None:
        """Re-learn the newest stored minute per market so gap detection survives restarts."""
        rows = db.execute(
            select(
                MarketObservation.venue,
                MarketObservation.symbol,
                MarketObservation.event_ts,
            )
            .where(MarketObservation.kind == CANDLE_1M)
            .order_by(MarketObservation.event_ts.desc())
            .limit(5000)
        ).all()
        for v, s, ts in rows:
            self.last_open.setdefault((v, s), ts)

    # ------------------------------------------------------------------ events
    def on_event(self, db: Session, ev: MarketEvent, native_symbol: str = "") -> None:
        if isinstance(ev, CandleEvent):
            self.on_candle(db, ev, native_symbol)
        elif isinstance(ev, QuoteEvent):
            self.on_quote(db, ev, native_symbol)
        self.touch(db, ev.venue.value, ev.ingested_ts)

    def on_candle(
        self, db: Session, ev: CandleEvent, native: str = "", source: str = "LIVE"
    ) -> bool:
        """Store a CLOSED 1m candle. Returns True if a new observation row was created."""
        if ev.interval not in ("1m", "1M") or ev.interval == "1M":  # OKX bar names: 1m
            if ev.interval != "1m":
                return False
        if not ev.closed:
            self.stats["ignored_open_candles"] += 1
            return False
        key = (ev.venue.value, ev.symbol)
        prev = self.last_open.get(key)
        if prev is not None and ev.open_time < prev:
            self.stats["out_of_order"] += 1
        stmt = (
            pg_insert(MarketObservation)
            .values(
                venue=ev.venue.value,
                symbol=ev.symbol,
                native_symbol=native or ev.symbol,
                kind=CANDLE_1M,
                event_ts=ev.open_time,
                exchange_ts=ev.exchange_ts,
                ingested_ts=ev.ingested_ts,
                source=source,
                connection_id=self.connection_id,
                open=ev.open,
                high=ev.high,
                low=ev.low,
                close=ev.close,
                volume=ev.volume,
                complete=True,
            )
            .on_conflict_do_nothing(constraint="uq_observation")
            .returning(MarketObservation.id)
        )
        created = db.execute(stmt).first() is not None
        if not created:
            self.stats["duplicates"] += 1
            return False
        self.stats["candles"] += 1
        if prev is not None and ev.open_time > prev + MINUTE:
            self._open_gap(db, ev.venue.value, ev.symbol, prev + MINUTE, ev.open_time - MINUTE)
        if prev is None or ev.open_time > prev:
            self.last_open[key] = ev.open_time
        else:
            self._settle_late(db, ev.venue.value, ev.symbol, ev.open_time)
        return True

    def _settle_late(self, db: Session, venue: str, symbol: str, ts: datetime) -> None:
        """A late / out-of-order candle may complete an OPEN gap: close it only when every missing
        minute is actually present."""
        for g in db.execute(
            select(MarketDataGap).where(
                MarketDataGap.venue == venue,
                MarketDataGap.symbol == symbol,
                MarketDataGap.status == "OPEN",
                MarketDataGap.gap_start <= ts,
                MarketDataGap.gap_end >= ts,
            )
        ).scalars():
            have = {
                t
                for (t,) in db.execute(
                    select(MarketObservation.event_ts).where(
                        MarketObservation.venue == venue,
                        MarketObservation.symbol == symbol,
                        MarketObservation.kind == CANDLE_1M,
                        MarketObservation.event_ts >= g.gap_start,
                        MarketObservation.event_ts <= g.gap_end,
                    )
                )
            }
            if {g.gap_start + MINUTE * i for i in range(g.missing)} <= have:
                db.execute(
                    update(MarketDataGap)
                    .where(MarketDataGap.id == g.id)
                    .values(
                        status="BACKFILLED",
                        detail="completed by late / out-of-order arrival",
                        closed_at=self.clock(),
                    )
                )

    def on_quote(self, db: Session, ev: QuoteEvent, native: str = "") -> None:
        minute = ev.ingested_ts.replace(second=0, microsecond=0)
        key = (ev.venue.value, ev.symbol)
        if self._last_quote_minute.get(key) == minute:
            return
        self._last_quote_minute[key] = minute
        db.execute(
            pg_insert(MarketObservation)
            .values(
                venue=ev.venue.value,
                symbol=ev.symbol,
                native_symbol=native or ev.symbol,
                kind=QUOTE,
                event_ts=minute,
                exchange_ts=ev.exchange_ts,
                ingested_ts=ev.ingested_ts,
                source="LIVE",
                connection_id=self.connection_id,
                bid=ev.bid_price,
                ask=ev.ask_price,
                complete=True,
            )
            .on_conflict_do_nothing(constraint="uq_observation")
        )
        self.stats["quotes"] += 1

    # ------------------------------------------------------------------ gaps
    def _open_gap(
        self, db: Session, venue: str, symbol: str, start: datetime, end: datetime
    ) -> None:
        missing = int((end - start) / MINUTE) + 1
        res = db.execute(
            pg_insert(MarketDataGap)
            .values(
                venue=venue,
                symbol=symbol,
                gap_start=start,
                gap_end=end,
                missing=missing,
                status="OPEN",
                detail="missing 1m candles detected on stream continuity check",
                detected_at=self.clock(),
            )
            .on_conflict_do_nothing(constraint="uq_gap")
            .returning(MarketDataGap.id)
        )
        if res.first() is not None:
            self.stats["gaps_opened"] += 1

    def open_gaps(self, db: Session, venue: str | None = None) -> list[MarketDataGap]:
        q = select(MarketDataGap).where(MarketDataGap.status == "OPEN")
        if venue:
            q = q.where(MarketDataGap.venue == venue)
        return list(db.execute(q.order_by(MarketDataGap.gap_start)).scalars())

    def apply_backfill(
        self,
        db: Session,
        gap: MarketDataGap,
        candles: Sequence[CandleEvent],
        native: str = "",
    ) -> str:
        """Insert REST candles for a gap and settle its status from what is actually present."""
        now = self.clock()
        if gap.gap_start < now - timedelta(minutes=BACKFILL_MAX_MINUTES):
            status, detail = "UNRECOVERABLE", "gap older than the REST backfill window"
        else:
            for c in candles:
                if gap.gap_start <= c.open_time <= gap.gap_end and c.closed:
                    self.on_candle(db, c, native, source="BACKFILL")
            have = {
                t
                for (t,) in db.execute(
                    select(MarketObservation.event_ts).where(
                        MarketObservation.venue == gap.venue,
                        MarketObservation.symbol == gap.symbol,
                        MarketObservation.kind == CANDLE_1M,
                        MarketObservation.event_ts >= gap.gap_start,
                        MarketObservation.event_ts <= gap.gap_end,
                    )
                )
            }
            want = {gap.gap_start + MINUTE * i for i in range(gap.missing)}
            if want <= have:
                status, detail = "BACKFILLED", f"{gap.missing} candles restored from public REST"
            else:
                status = "UNRECOVERABLE"
                detail = f"REST returned {len(want & have)} of {gap.missing} candles"
        db.execute(
            update(MarketDataGap)
            .where(MarketDataGap.id == gap.id)
            .values(status=status, detail=detail, closed_at=now)
        )
        return status

    # ------------------------------------------------------------------ feed status
    def touch(self, db: Session, venue: str, at: datetime, throttle_s: int = 5) -> None:
        last = self._last_touch.get(venue)
        if last is not None and (at - last).total_seconds() < throttle_s:
            return
        self._last_touch[venue] = at
        db.execute(
            update(MarketFeedConnection)
            .where(MarketFeedConnection.venue == venue)
            .values(last_event_at=at, state="CONNECTED", updated_at=self.clock())
        )


def set_feed_state(
    db: Session,
    venue: str,
    state: str,
    source: str = "LIVE",
    reason: str = "",
    connection_id: str = "",
    reconnects: int | None = None,
    clock_offset_ms: int | None = None,
    now: datetime | None = None,
) -> None:
    """Upsert the per-venue status row. DISABLED venues carry an explicit reason."""
    now = now or utcnow()
    vals: dict[str, Any] = {
        "state": state,
        "source": source,
        "reason": reason,
        "connection_id": connection_id,
        "updated_at": now,
    }
    if reconnects is not None:
        vals["reconnects"] = reconnects
    if clock_offset_ms is not None:
        vals["clock_offset_ms"] = clock_offset_ms
    stmt = pg_insert(MarketFeedConnection).values(venue=venue, since=now, **vals)
    db.execute(stmt.on_conflict_do_update(index_elements=["venue"], set_=vals))
