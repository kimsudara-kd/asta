"""Canonical market-data models. Decimal-only, UTC-only (ADR-0003)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from asta.core.environment import Environment
from asta.core.timeutil import require_utc


class Venue(StrEnum):
    BINANCE = "BINANCE"
    OKX = "OKX"


class Channel(StrEnum):
    TRADE = "TRADE"
    QUOTE = "QUOTE"  # best bid/ask
    CANDLE = "CANDLE"


DataSource = Literal["LIVE", "FIXTURE"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _no_floats(cls, v: object) -> object:
        if isinstance(v, float):
            raise TypeError("float not allowed in canonical models; use Decimal/str")
        return v


class MarketEvent(_Frozen):
    venue: Venue
    symbol: str  # canonical "BASE-QUOTE", e.g. "BTC-USDT"
    channel: Channel
    event_id: str  # venue-stable id used for dedupe
    exchange_ts: datetime | None
    ingested_ts: datetime
    environment: Environment = Environment.PAPER
    source: DataSource = "LIVE"

    @field_validator("exchange_ts", "ingested_ts")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return None if v is None else require_utc(v)

    @property
    def dedupe_key(self) -> tuple[str, str, str, str]:
        return (self.venue.value, self.symbol, self.channel.value, self.event_id)


class TradeEvent(MarketEvent):
    channel: Channel = Channel.TRADE
    price: Decimal
    quantity: Decimal
    taker_side: Literal["BUY", "SELL"] | None = None


class QuoteEvent(MarketEvent):
    channel: Channel = Channel.QUOTE
    bid_price: Decimal
    bid_qty: Decimal
    ask_price: Decimal
    ask_qty: Decimal

    @property
    def spread_bps(self) -> Decimal:
        mid = (self.bid_price + self.ask_price) / 2
        return Decimal(0) if mid == 0 else (self.ask_price - self.bid_price) / mid * 10_000


class CandleEvent(MarketEvent):
    channel: Channel = Channel.CANDLE
    interval: str
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    closed: bool  # incomplete candles must never feed research (handoff §9)


class SymbolRules(_Frozen):
    venue: Venue
    symbol: str
    base: str
    quote: str
    tick_size: Decimal
    step_size: Decimal
    min_qty: Decimal
    min_notional: Decimal | None
    trading: bool
    retrieved_at: datetime
    source: DataSource = "LIVE"
