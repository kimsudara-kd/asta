"""Helpers for paper-trading tests: deterministic synthetic closed bars for two 'venues'.

Bars are SYNTHETIC test data: nothing here is market data and nothing touches a network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from typing import Any

from asta.paper.config import PaperSessionConfig, StrategySpec
from asta.research.data.fixtures import DOWNTREND, HIGHVOL, RANGE, UPTREND, generate_bars, seg
from asta.research.data.models import Bar
from asta.research.data.profiles import FeeAssetPolicy, FeeSchedule, RuleProfile
from asta.research.types import Timeframe

SYMS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
T0 = datetime(2026, 1, 1, tzinfo=UTC)
VENUES = ("BINANCE", "OKX")


def rules(venue: str) -> tuple[RuleProfile, ...]:
    return tuple(
        RuleProfile(
            f"T:{venue}:{s}",
            venue,
            s,
            D("0.01"),
            D("0.001"),
            D("0.001"),
            D("5"),
            "unit-test",
            T0,
            False,
        )
        for s in SYMS
    )


FEES = FeeSchedule(
    "T-FEES", "X", D("10"), D("10"), FeeAssetPolicy.RECEIVED_ASSET, "unit-test", False
)

DEFAULT_SEGMENTS = [
    seg(RANGE, 200),
    seg(UPTREND, 250),
    seg(RANGE, 150),
    seg(DOWNTREND, 250),
    seg(HIGHVOL, 100),
    seg(UPTREND, 300),
]


def make_cfg(**kw: Any) -> PaperSessionConfig:
    base: dict[str, Any] = {
        "name": "t",
        "wallets": {"BINANCE": D("500"), "OKX": D("500")},
        "enabled_venues": ("BINANCE", "OKX"),
        "strategies": (
            StrategySpec("regime_grid"),
            StrategySpec("trend_pullback"),
            StrategySpec("sma_cross"),
        ),
        "rules": {v: rules(v) for v in VENUES},
        "fees": {v: FEES for v in VENUES},
        "timeframe": Timeframe.H1,
    }
    base.update(kw)
    return PaperSessionConfig(**base)


def make_series(
    n: int | None = None, segments: Any = None, seed: int = 3, venues: tuple[str, ...] = VENUES
) -> list[tuple[datetime, dict[str, Bar]]]:
    """[(bar_open_ts, {"VENUE:SYM": Bar})] for every hour."""
    segs = segments or DEFAULT_SEGMENTS
    per: dict[str, list[Bar]] = {}
    for vi, v in enumerate(venues):
        for si, s in enumerate(SYMS):
            per[f"{v}:{s}"] = generate_bars(T0, Timeframe.H1, segs, seed + 10 * vi + si, D("100"))
    total = min(len(b) for b in per.values())
    n = min(n or total, total)
    return [(T0 + timedelta(hours=i), {k: b[i] for k, b in per.items()}) for i in range(n)]
