"""Sensitivity and stress analysis: fees, slippage, latency, parameters, missing data, regimes.

Every scenario re-runs the SAME engine on the SAME data; only the stated assumption changes. A
strategy whose edge disappears under 2x fees/slippage or one extra bar of latency is not robust.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from asta.research.data.importer import build_dataset
from asta.research.data.models import Dataset
from asta.research.runner import BacktestConfig, run_backtest
from asta.research.validation.walkforward import RunSummary, summarize

ZERO = Decimal(0)


@dataclass(frozen=True)
class SensitivityPoint:
    scenario: str
    value: str
    summary: RunSummary

    def to_json(self) -> dict[str, Any]:
        return {"scenario": self.scenario, "value": self.value, "summary": self.summary.to_json()}


def fee_sensitivity(
    base: BacktestConfig,
    data: Mapping[str, Dataset],
    multipliers: Sequence[Decimal] = (Decimal(0), Decimal(1), Decimal(2), Decimal(3)),
) -> list[SensitivityPoint]:
    """0x = zero-fee scenario; 2x and 3x are worse-than-base assumptions."""
    out = []
    for m in multipliers:
        cfg = base.replace(fees=base.fees.scaled(m, f"x{m}"), label=f"fees x{m}")
        out.append(SensitivityPoint("fees", f"x{m}", summarize(run_backtest(cfg, data))))
    return out


def slippage_sensitivity(
    base: BacktestConfig,
    data: Mapping[str, Dataset],
    multipliers: Sequence[Decimal] = (Decimal(0), Decimal(1), Decimal(2), Decimal(4)),
) -> list[SensitivityPoint]:
    out = []
    for m in multipliers:
        cfg = base.replace(execution=base.execution.scaled_slippage(m), label=f"slippage x{m}")
        out.append(SensitivityPoint("slippage", f"x{m}", summarize(run_backtest(cfg, data))))
    return out


def latency_sensitivity(
    base: BacktestConfig, data: Mapping[str, Dataset], extra_bars: Sequence[int] = (0, 1, 2, 3)
) -> list[SensitivityPoint]:
    out = []
    for n in extra_bars:
        cfg = base.replace(latency_bars=n, label=f"latency +{n} bars")
        out.append(SensitivityPoint("latency_bars", str(n), summarize(run_backtest(cfg, data))))
    return out


def parameter_sensitivity(
    base: BacktestConfig, data: Mapping[str, Dataset], axes: Mapping[str, Sequence[Any]]
) -> list[SensitivityPoint]:
    """One-at-a-time perturbation around the base parameters."""
    out = []
    for name in sorted(axes):
        for v in axes[name]:
            params = dict(base.strategy_params)
            params[name] = v
            cfg = base.replace(strategy_params=params, label=f"{name}={v}")
            out.append(
                SensitivityPoint(f"param:{name}", str(v), summarize(run_backtest(cfg, data)))
            )
    return out


def instability_flags(points: Sequence[SensitivityPoint], base_return: Decimal) -> list[str]:
    """Flag parameter configurations whose neighbours change the sign or magnitude of the result."""
    flags: list[str] = []
    by: dict[str, list[SensitivityPoint]] = {}
    for p in points:
        by.setdefault(p.scenario, []).append(p)
    for scen, pts in by.items():
        rets = [p.summary.net_return_pct for p in pts]
        if any(r > 0 for r in rets) and any(r < 0 for r in rets):
            flags.append(f"{scen}: net return changes sign across neighbouring values")
        spread = max(rets) - min(rets)
        if abs(base_return) > 0 and spread > 2 * abs(base_return):
            flags.append(f"{scen}: result spread {spread:.4f} exceeds 2x the base result")
        if any(p.summary.risk_verdict != "PASSED" for p in pts):
            flags.append(f"{scen}: some neighbouring values breach the risk budget")
    return flags


def drop_bars(ds: Dataset, fraction: float, rng: random.Random) -> Dataset:
    """Randomly remove a fraction of interior bars. The result usually fails the quality policy
    (it is DEGRADED data) - for stress tests only, never for research results."""
    bars = list(ds.bars)
    keep = [b for i, b in enumerate(bars) if i in (0, len(bars) - 1) or rng.random() >= fraction]
    res = build_dataset(
        keep,
        ds.meta.venue,
        ds.meta.symbol,
        ds.meta.timeframe,
        f"stress:{ds.meta.source}",
        ds.meta.source_type,
        dict(ds.meta.provenance, stress_drop_fraction=fraction),
        imported_at=ds.meta.imported_at,
    )
    return res.dataset


def missing_data_stress(
    base: BacktestConfig,
    data: Mapping[str, Dataset],
    fractions: Sequence[float] = (0.01, 0.05),
    seed: int = 1,
) -> list[SensitivityPoint]:
    out = []
    for fr in fractions:
        rng = random.Random(seed)  # noqa: S311 - reproducible stress sampling, not security
        degraded = {s: drop_bars(d, fr, rng) for s, d in data.items()}
        cfg = base.replace(
            dataset_checksums={s: d.meta.checksum for s, d in degraded.items()},
            seed=seed,
            label=f"missing {fr:.0%} of bars",
        )
        run = run_backtest(cfg, degraded, allow_degraded=True)
        out.append(
            SensitivityPoint("missing_data", f"{fr:.0%}", summarize(run, f"missing {fr:.0%}"))
        )
    return out


def window_regimes(
    base: BacktestConfig,
    data: Mapping[str, Dataset],
    windows: Sequence[tuple[str, datetime, datetime]],
) -> list[SensitivityPoint]:
    """The same configuration over named historical windows (e.g. quarters / market regimes)."""
    out = []
    for name, a, b in windows:
        cfg = base.replace(period_start=a, period_end=b, label=name)
        out.append(SensitivityPoint("window", name, summarize(run_backtest(cfg, data), name)))
    return out
