"""Test helpers: tiny hand-built bar series (manually calculable)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from asta.research.data.importer import ImportResult, build_dataset
from asta.research.data.models import Bar
from asta.research.types import SourceType, Timeframe

T0 = datetime(2024, 1, 1, tzinfo=UTC)


def bar(
    i: int, o: str, h: str, lo: str, c: str, v: str = "1000", tf: Timeframe = Timeframe.H1
) -> Bar:
    return Bar(T0 + timedelta(seconds=tf.seconds * i), D(o), D(h), D(lo), D(c), D(v))


def flat_bars(
    n: int, price: str = "100", tf: Timeframe = Timeframe.H1, vol: str = "1000"
) -> list[Bar]:
    return [bar(i, price, price, price, price, vol, tf) for i in range(n)]


def dataset(
    bars: Sequence[Bar],
    tf: Timeframe = Timeframe.H1,
    symbol: str = "BTC-USDT",
    venue: str = "BINANCE",
    source_type: SourceType = SourceType.EXCHANGE_HISTORICAL,
) -> ImportResult:
    return build_dataset(bars, venue, symbol, tf, "unit-test", source_type, {}, imported_at=T0)
