"""Deterministic, CAUSAL market-regime classification (trend x volatility).

Each label is computed from data up to and including the bar it labels, with fixed thresholds (no
dataset-wide statistics), so it is a legitimate decision-time input as well as a reporting tool.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from asta.research.data.models import Bar
from asta.research.sim.engine import EquityPoint
from asta.research.strategies.indicators import ATR, EMA


@dataclass(frozen=True)
class RegimeParams:
    ema_fast: int = 24
    ema_slow: int = 96
    trend_threshold: Decimal = Decimal("0.005")  # |EMA_fast / EMA_slow - 1| beyond this = trend
    atr_period: int = 24
    high_vol_atr_pct: Decimal = Decimal("0.015")  # ATR / close above this = HIGHVOL


DEFAULT_REGIME_PARAMS = RegimeParams()


def classify(bars: Sequence[Bar], params: RegimeParams = DEFAULT_REGIME_PARAMS) -> list[str | None]:
    f, s, a = EMA(params.ema_fast), EMA(params.ema_slow), ATR(params.atr_period)
    out: list[str | None] = []
    for b in bars:
        fv, sv, av = f.update(b.close), s.update(b.close), a.update(b)
        if fv is None or sv is None or av is None:
            out.append(None)
            continue
        gap = fv / sv - 1
        trend = (
            "UP"
            if gap > params.trend_threshold
            else "DOWN"
            if gap < -params.trend_threshold
            else "RANGE"
        )
        vol = "HIGHVOL" if b.close > 0 and av / b.close > params.high_vol_atr_pct else "LOWVOL"
        out.append(f"{trend}/{vol}")
    return out


def regime_counts(labels: Sequence[str | None]) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for x in labels:
        c[x or "WARMUP"] += 1
    return dict(sorted(c.items()))


def attribute_equity(
    points: Sequence[EquityPoint],
    bars: Sequence[Bar],
    labels: Sequence[str | None],
    tf_seconds: int,
) -> dict[str, dict[str, Any]]:
    """Equity change of each bar attributed to the regime known at the START of that bar (the label
    of the previous bar's close) - never to a label that uses the bar itself."""
    label_at: dict[datetime, str | None] = {
        b.open_time + timedelta(seconds=tf_seconds): labels[i] for i, b in enumerate(bars)
    }
    agg: dict[str, dict[str, Any]] = {}
    prev_ts: datetime | None = None
    prev_eq: Decimal | None = None
    for p in points:
        if prev_ts is not None and prev_eq is not None:
            lab = label_at.get(prev_ts) or "WARMUP"
            slot = agg.setdefault(lab, {"bars": 0, "pnl": Decimal(0)})
            slot["bars"] += 1
            slot["pnl"] += p.equity - prev_eq
        prev_ts, prev_eq = p.ts, p.equity
    return dict(sorted(agg.items()))
