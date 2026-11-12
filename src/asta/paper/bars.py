"""Session bars from stored 1-minute observations (M2-A). Pure functions + one DB reader.

A session bar exists ONLY when every constituent 1-minute candle is present and complete (closed);
nothing is interpolated or forward-filled. A missing minute means the bar is missing and the
affected symbol is blocked from new entries (DATA_GAP) until bars are contiguous again."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from asta.db.paper_models import MarketObservation
from asta.research.data.models import Bar

CANDLE_1M = "CANDLE_1M"
QUOTE = "QUOTE"


def bucket_start(ts: datetime, tf_seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - epoch % tf_seconds, tz=ts.tzinfo)


def aggregate_1m(
    rows: Sequence[MarketObservation], open_ts: datetime, tf_seconds: int
) -> Bar | None:
    """OHLCV of [open_ts, open_ts+tf) from complete 1m rows, or None if ANY minute is missing."""
    need = tf_seconds // 60
    by_ts = {r.event_ts: r for r in rows if r.complete and r.kind == CANDLE_1M}
    mins = [open_ts + timedelta(minutes=i) for i in range(need)]
    if any(m not in by_ts for m in mins):
        return None
    cs = [by_ts[m] for m in mins]
    highs: list[Decimal] = []
    lows: list[Decimal] = []
    vols: list[Decimal] = []
    for c in cs:
        if c.high is None or c.low is None or c.volume is None:
            return None
        highs.append(c.high)
        lows.append(c.low)
        vols.append(c.volume)
    first_open, last_close = cs[0].open, cs[-1].close
    if first_open is None or last_close is None:
        return None
    return Bar(open_ts, first_open, max(highs), min(lows), last_close, sum(vols, Decimal(0)))


def load_bar(
    db: Session, venue: str, symbol: str, open_ts: datetime, tf_seconds: int
) -> Bar | None:
    rows = (
        db.execute(
            select(MarketObservation).where(
                MarketObservation.venue == venue,
                MarketObservation.symbol == symbol,
                MarketObservation.kind == CANDLE_1M,
                MarketObservation.event_ts >= open_ts,
                MarketObservation.event_ts < open_ts + timedelta(seconds=tf_seconds),
            )
        )
        .scalars()
        .all()
    )
    return aggregate_1m(rows, open_ts, tf_seconds)
