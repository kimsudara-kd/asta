"""Historical data models: bars, ticks, dataset metadata. Decimal-only, UTC-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from asta.core.timeutil import require_utc
from asta.research.types import NON_RESEARCH_SOURCES, Severity, SourceType, Timeframe


@dataclass(frozen=True, slots=True)
class Bar:
    """One CLOSED candle. `open_time` is the UTC start of the interval."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal  # base-asset volume

    def __post_init__(self) -> None:
        require_utc(self.open_time)


@dataclass(frozen=True, slots=True)
class TradeTick:
    ts: datetime
    price: Decimal
    quantity: Decimal
    trade_id: str


@dataclass(frozen=True, slots=True)
class QuoteTick:
    ts: datetime
    bid: Decimal
    ask: Decimal
    bid_qty: Decimal
    ask_qty: Decimal


@dataclass(frozen=True)
class DatasetMeta:
    venue: str
    symbol: str  # canonical BASE-QUOTE
    timeframe: Timeframe
    source: str
    source_type: SourceType
    schema_version: str
    checksum: str
    row_count: int
    period_start: datetime
    period_end: datetime  # exclusive: last open_time + one timeframe
    imported_at: datetime
    provenance: dict[str, Any] = field(default_factory=dict)
    parent_checksums: tuple[str, ...] = ()
    validation_status: Severity | None = None

    @property
    def is_real_exchange_data(self) -> bool:
        return self.source_type in (SourceType.EXCHANGE_HISTORICAL, SourceType.DERIVED) and (
            self.provenance.get("root_source_type") != SourceType.FIXTURE
            and self.provenance.get("root_source_type") != SourceType.SYNTHETIC
        )

    @property
    def root_source_type(self) -> SourceType:
        """The ORIGIN of the data: a dataset derived from synthetic bars is still synthetic."""
        root = self.provenance.get("root_source_type")
        if root in (SourceType.FIXTURE, SourceType.SYNTHETIC):
            return SourceType(root)
        return self.source_type

    @property
    def label(self) -> str:
        """Human label; fixture/synthetic data (or anything derived from it) is never described as
        historical exchange data."""
        root = self.root_source_type
        if root in NON_RESEARCH_SOURCES:
            suffix = " [derived]" if self.source_type == SourceType.DERIVED else ""
            return f"{root.value} (not real exchange data){suffix}"
        return self.source_type.value

    @property
    def accepted_for_research(self) -> bool:
        return (
            self.validation_status in (Severity.PASS, Severity.WARNING)
            and self.is_real_exchange_data
        )


@dataclass(frozen=True)
class Dataset:
    meta: DatasetMeta
    bars: tuple[Bar, ...]

    def slice(self, start: datetime, end: datetime) -> tuple[Bar, ...]:
        """Bars with start <= open_time < end (pure view; the dataset is never mutated)."""
        return tuple(b for b in self.bars if start <= b.open_time < end)


def bar_close_time(bar: Bar, tf: Timeframe) -> datetime:
    return bar.open_time + timedelta(seconds=tf.seconds)
