"""Helpers for runner-level tests: configs and datasets built from deterministic synthetic bars."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal as D
from typing import Any

from asta.research.data.fixtures import (
    DOWNTREND,
    HIGHVOL,
    RANGE,
    UPTREND,
    Segment,
    generate_bars,
    seg,
)
from asta.research.data.importer import build_dataset
from asta.research.data.models import Dataset
from asta.research.data.profiles import FeeAssetPolicy, FeeSchedule, RuleProfile
from asta.research.runner import BacktestConfig
from asta.research.sim.execution import ExecutionModel
from asta.research.sim.risk import SimRiskLimits
from asta.research.types import SourceType, Timeframe

SYMS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
START = datetime(2024, 1, 1, tzinfo=UTC)
TEST_FEES = FeeSchedule(
    "T-FEES", "BINANCE", D("10"), D("10"), FeeAssetPolicy.RECEIVED_ASSET, "unit-test", False
)


def rules_for(symbols: tuple[str, ...] = SYMS) -> tuple[RuleProfile, ...]:
    return tuple(
        RuleProfile(
            f"T:{s}",
            "BINANCE",
            s,
            D("0.01"),
            D("0.001"),
            D("0.001"),
            D("5"),
            "unit-test",
            START,
            False,
        )
        for s in symbols
    )


def make_datasets(
    segments: list[Segment] | None = None,
    seed: int = 3,
    symbols: tuple[str, ...] = SYMS,
    source_type: SourceType = SourceType.EXCHANGE_HISTORICAL,
    timeframe: Timeframe = Timeframe.H1,
) -> dict[str, Dataset]:
    """Deterministic bars labelled like exchange history FOR TESTING ONLY (source 'unit-test')."""
    segs = segments or [
        seg(RANGE, 300),
        seg(UPTREND, 300),
        seg(RANGE, 200),
        seg(DOWNTREND, 300),
        seg(HIGHVOL, 100),
        seg(UPTREND, 300),
    ]
    out: dict[str, Dataset] = {}
    for i, s in enumerate(symbols):
        bars = generate_bars(START, timeframe, segs, seed + i, D("100"))
        out[s] = build_dataset(
            bars, "BINANCE", s, timeframe, "unit-test", source_type, {}, imported_at=START
        ).dataset
    return out


def make_config(
    strategy_id: str,
    params: dict[str, Any] | None,
    data: dict[str, Dataset],
    **kw: Any,
) -> BacktestConfig:
    syms = tuple(sorted(data))
    base: dict[str, Any] = {
        "strategy_id": strategy_id,
        "strategy_params": params or {},
        "dataset_checksums": {s: d.meta.checksum for s, d in data.items()},
        "timeframe": next(iter(data.values())).meta.timeframe,
        "rules": rules_for(syms),
        "fees": TEST_FEES,
        "execution": ExecutionModel(),
        "risk": SimRiskLimits.default(),
        "checkpoint_every": 100,
    }
    base.update(kw)
    return BacktestConfig(**base)


SMA_PARAMS = {"symbols": list(SYMS), "fast": 6, "slow": 20}
GRID_PARAMS = {
    "ema_fast": 6,
    "ema_slow": 24,
    "atr_period": 6,
    "range_lookback": 12,
    "recenter_bars": 6,
}
