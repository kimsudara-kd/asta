"""Deterministic SYNTHETIC / FIXTURE bar generators (tests, demos, e2e).

Everything produced here is labelled SYNTHETIC or FIXTURE and can never be accepted as real
exchange history. Randomness is a seeded `random.Random`; same seed => identical bars.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from asta.core.money import round_down
from asta.research.data.importer import ImportResult, build_dataset
from asta.research.data.models import Bar
from asta.research.types import SourceType, Timeframe


@dataclass(frozen=True)
class Segment:
    """A market regime: drift and volatility per bar (fractions), length in bars."""

    bars: int
    drift: float
    vol: float
    label: str = ""


UPTREND = Segment(0, 0.0008, 0.004, "up")
DOWNTREND = Segment(0, -0.0008, 0.004, "down")
RANGE = Segment(0, 0.0, 0.003, "range")
HIGHVOL = Segment(0, 0.0, 0.015, "highvol")


def seg(template: Segment, bars: int) -> Segment:
    return Segment(bars, template.drift, template.vol, template.label)


def generate_bars(
    start: datetime,
    timeframe: Timeframe,
    segments: Sequence[Segment],
    seed: int,
    start_price: Decimal = Decimal("100"),
    tick: Decimal = Decimal("0.01"),
    base_volume: Decimal = Decimal("1000"),
) -> list[Bar]:
    rng = random.Random(seed)  # noqa: S311 - deterministic test data, not security
    price = start_price
    t = start
    out: list[Bar] = []
    for s in segments:
        for _ in range(s.bars):
            ret = s.drift + rng.gauss(0.0, s.vol)
            o = price
            c = max(tick, round_down(o * Decimal(str(1 + ret)), tick))
            wick = abs(rng.gauss(0.0, s.vol / 2))
            hi = max(o, c) * Decimal(str(1 + wick))
            lo = min(o, c) * Decimal(str(1 - min(wick, 0.5)))
            high = round_down(hi, tick)
            low = max(tick, round_down(lo, tick))
            high = max(high, o, c)
            low = min(low, o, c)
            vol = round_down(base_volume * Decimal(str(0.5 + rng.random())), Decimal("0.001"))
            out.append(Bar(t, o, high, low, c, vol))
            price = c
            t = t + timedelta(seconds=timeframe.seconds)
    return out


def synthetic_dataset(
    symbol: str = "BTC-USDT",
    venue: str = "BINANCE",
    timeframe: Timeframe = Timeframe.H1,
    segments: Sequence[Segment] | None = None,
    seed: int = 7,
    start: datetime | None = None,
    start_price: Decimal = Decimal("100"),
    source_type: SourceType = SourceType.SYNTHETIC,
    imported_at: datetime | None = None,
) -> ImportResult:
    """A labelled SYNTHETIC dataset. Never real data, never accepted_for_research."""
    if source_type not in (SourceType.SYNTHETIC, SourceType.FIXTURE):
        raise ValueError("generator output may only be SYNTHETIC or FIXTURE")
    segs = list(segments) if segments else [seg(UPTREND, 300), seg(RANGE, 300), seg(DOWNTREND, 300)]
    t0 = start or datetime(2024, 1, 1, tzinfo=UTC)
    bars = generate_bars(t0, timeframe, segs, seed, start_price)
    return build_dataset(
        bars,
        venue,
        symbol,
        timeframe,
        f"synthetic-generator seed={seed}",
        source_type,
        {
            "generator": "asta.research.data.fixtures",
            "seed": seed,
            "segments": [s.label for s in segs],
        },
        imported_at=imported_at or datetime(2026, 1, 1, tzinfo=UTC),
    )
