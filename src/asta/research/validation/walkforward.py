"""Walk-forward optimisation with structural train/test separation.

Parameters are chosen ONLY from runs whose `period_end` is the window's train end (the runner's
`select_bars` physically withholds later bars), by an objective computed from the training run. The
chosen parameters are then evaluated once on the following test window. Results record every
trial, the selection data cut-off, the validation cycle and overfitting diagnostics.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from asta.core.money import dec_str
from asta.research.data.models import Dataset
from asta.research.runner import BacktestConfig, BacktestRun, run_backtest
from asta.research.validation.holdout import KIND_WF, HoldoutRegistry
from asta.research.validation.splits import Window

ZERO = Decimal(0)
Objective = Callable[[BacktestRun], Decimal | None]
Runner = Callable[[BacktestConfig, Mapping[str, Dataset]], BacktestRun]


@dataclass(frozen=True)
class RunSummary:
    label: str
    config_hash: str
    result_checksum: str
    status: str
    risk_verdict: str
    research_grade: bool
    period_start: datetime | None
    period_end: datetime | None
    ending_equity: Decimal
    liquidation_equity: Decimal
    net_pnl: Decimal
    net_return_pct: Decimal
    max_drawdown_pct: Decimal
    sharpe: Decimal | None
    sortino: Decimal | None
    round_trips: int
    fills: int
    fees: Decimal
    slippage: Decimal

    def to_json(self) -> dict[str, Any]:
        return {
            k: (
                str(v)
                if isinstance(v, Decimal)
                else v.isoformat()
                if isinstance(v, datetime)
                else v
            )
            for k, v in self.__dict__.items()
        }


def summarize(run: BacktestRun, label: str = "") -> RunSummary:
    p = run.performance
    return RunSummary(
        label=label or run.config.label,
        config_hash=run.config_hash,
        result_checksum=run.result_checksum,
        status=run.result.status,
        risk_verdict=run.result.risk_verdict,
        research_grade=run.research_grade,
        period_start=run.config.period_start,
        period_end=run.config.period_end,
        ending_equity=p.returns["ending_equity_marked"],
        liquidation_equity=p.returns["ending_equity_liquidation_adjusted"],
        net_pnl=p.returns["total_net_pnl"],
        net_return_pct=p.returns["net_return_pct"],
        max_drawdown_pct=p.risk["max_drawdown_pct"],
        sharpe=p.value("risk", "sharpe"),
        sortino=p.value("risk", "sortino"),
        round_trips=p.trading["closed_round_trips"],
        fills=p.trading["fills"],
        fees=p.returns["total_fees_quote_equivalent"],
        slippage=p.returns["total_slippage_cost"],
    )


def default_objective(run: BacktestRun) -> Decimal | None:
    """Net return, but ONLY for runs that stayed inside the risk budget (a breached run is
    inadmissible however attractive its P&L) and whose ledger reconciled."""
    if run.result.risk_verdict != "PASSED" or not run.result.reconcile.ok:
        return None
    return Decimal(str(run.performance.returns["net_return_pct"]))


def expand_grid(base: dict[str, Any], axes: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Deterministic cartesian product (axes sorted by name) layered over `base`."""
    names = sorted(axes)
    out = []
    for combo in itertools.product(*(axes[n] for n in names)):
        d = dict(base)
        d.update(dict(zip(names, combo, strict=True)))
        out.append(d)
    return out


def params_key(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:12]


@dataclass
class WindowResult:
    window: Window
    status: str  # OK | NO_ADMISSIBLE_PARAMETERS
    chosen_params: dict[str, Any] | None
    chosen_key: str | None
    train: RunSummary | None
    test: RunSummary | None
    trials: list[tuple[str, Decimal | None]]
    selection_data_end: datetime  # == window.train_end: nothing later was visible to the selection
    train_overlaps_prior_oos: bool = False


@dataclass
class WalkForwardReport:
    strategy_id: str
    cycle: int
    oos_reused: bool
    grid_size: int
    windows: list[WindowResult] = field(default_factory=list)
    pooled: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "cycle": self.cycle,
            "oos_reused": self.oos_reused,
            "grid_size": self.grid_size,
            "windows": [
                {
                    "index": w.window.index,
                    "train": [w.window.train_start.isoformat(), w.window.train_end.isoformat()],
                    "test": [w.window.test_start.isoformat(), w.window.test_end.isoformat()],
                    "status": w.status,
                    "chosen_params": w.chosen_params,
                    "chosen_key": w.chosen_key,
                    "train_summary": w.train.to_json() if w.train else None,
                    "test_summary": w.test.to_json() if w.test else None,
                    "trials": [[k, None if s is None else dec_str(s)] for k, s in w.trials],
                    "selection_data_end": w.selection_data_end.isoformat(),
                    "train_overlaps_prior_oos": w.train_overlaps_prior_oos,
                }
                for w in self.windows
            ],
            "pooled": {
                k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.pooled.items()
            },
            "warnings": self.warnings,
        }


def walk_forward(
    base: BacktestConfig,
    datasets: Mapping[str, Dataset],
    grid: list[dict[str, Any]],
    windows: Sequence[Window],
    objective: Objective = default_objective,
    registry: HoldoutRegistry | None = None,
    runner: Runner = run_backtest,
    progress: Callable[[str], None] | None = None,
    edge_axes: Mapping[str, Sequence[Any]] | None = None,
) -> WalkForwardReport:
    reg = registry or HoldoutRegistry()
    key = (
        base.strategy_id
        + ":"
        + ",".join(f"{s}={c[:12]}" for s, c in sorted(base.dataset_checksums.items()))
    )
    rep = WalkForwardReport(base.strategy_id, reg.cycle, False, len(grid))
    for w in windows:
        overlap = reg.check_selection(
            key, w.train_start, w.train_end
        )  # raises on a consumed FINAL holdout
        if reg.reuse_in_new_cycle(key, w.train_start, w.train_end):
            rep.oos_reused = True
        trials: list[tuple[str, Decimal | None]] = []
        best: tuple[Decimal, str, dict[str, Any], BacktestRun] | None = None
        for params in grid:
            cfg = base.replace(
                strategy_params=params, period_start=w.train_start, period_end=w.train_end
            )
            run = runner(cfg, datasets)
            score = objective(run)
            k = params_key(params)
            trials.append((k, score))
            if score is not None and _better(score, k, best):
                best = (score, k, params, run)
        if best is None:
            rep.windows.append(
                WindowResult(
                    w,
                    "NO_ADMISSIBLE_PARAMETERS",
                    None,
                    None,
                    None,
                    None,
                    trials,
                    w.train_end,
                    overlap,
                )
            )
            continue
        _, k, params, train_run = best
        test_cfg = base.replace(
            strategy_params=params, period_start=w.test_start, period_end=w.test_end
        )
        test_run = runner(test_cfg, datasets)
        reg.consume(key, w.test_start, w.test_end, KIND_WF)
        rep.windows.append(
            WindowResult(
                w,
                "OK",
                params,
                k,
                summarize(train_run, f"train w{w.index}"),
                summarize(test_run, f"test w{w.index}"),
                trials,
                w.train_end,
                overlap,
            )
        )
        if progress:
            progress(f"window {w.index} done")
    _finalise(rep, grid, edge_axes)
    return rep


def _better(
    score: Decimal, k: str, best: tuple[Decimal, str, dict[str, Any], BacktestRun] | None
) -> bool:
    if best is None:
        return True
    # higher score wins; ties broken deterministically by the smaller parameter key
    return score > best[0] or (score == best[0] and k < best[1])


def _finalise(
    rep: WalkForwardReport,
    grid: list[dict[str, Any]],
    edge_axes: Mapping[str, Sequence[Any]] | None,
) -> None:
    ok = [w for w in rep.windows if w.status == "OK" and w.test and w.train]
    n = len(rep.windows)
    if ok:
        oos = [w.test.net_return_pct for w in ok if w.test]
        ins = [w.train.net_return_pct for w in ok if w.train]
        pooled_pnl = sum((w.test.net_pnl for w in ok if w.test), ZERO)
        compounded = Decimal(1)
        for r in oos:
            compounded *= 1 + r
        rep.pooled = {
            "windows": n,
            "windows_with_params": len(ok),
            "oos_positive_windows": sum(1 for r in oos if r > 0),
            "oos_net_pnl_sum": pooled_pnl,
            "oos_net_return_compounded": compounded - 1,
            "oos_mean_return": sum(oos, ZERO) / len(oos),
            "is_mean_return": sum(ins, ZERO) / len(ins),
            "oos_risk_breached_windows": sum(
                1 for w in ok if w.test and w.test.risk_verdict != "PASSED"
            ),
        }
        for w in ok:
            if w.train and w.test and w.train.net_return_pct > 0 and w.test.net_return_pct <= 0:
                rep.warnings.append(
                    f"window {w.window.index}: in-sample positive but out-of-sample non-positive"
                )
        is_mean, oos_mean = rep.pooled["is_mean_return"], rep.pooled["oos_mean_return"]
        if is_mean > 0 and oos_mean < is_mean / 2:
            rep.warnings.append(
                "out-of-sample mean return is less than half the in-sample mean (overfitting risk)"
            )
        keys = {w.chosen_key for w in ok}
        if len(ok) >= 3 and len(keys) >= max(2, int(0.6 * len(ok))):
            rep.warnings.append("selected parameters change in most windows (unstable optimum)")
        if edge_axes:
            edge = 0
            for w in ok:
                for name, vals in edge_axes.items():
                    if (
                        w.chosen_params
                        and w.chosen_params.get(name) in (vals[0], vals[-1])
                        and len(vals) > 2
                    ):
                        edge += 1
                        break
            if ok and edge / len(ok) >= 0.5:
                rep.warnings.append(
                    "chosen parameters sit on the edge of the search range in half the windows"
                )
        if rep.pooled["oos_risk_breached_windows"]:
            rep.warnings.append(
                "some out-of-sample windows breached the risk budget (reported as failures)"
            )
    if any(w.status != "OK" for w in rep.windows):
        rep.warnings.append(
            "some windows had no risk-admissible parameters (no trading in those windows)"
        )
    if len(grid) >= 20:
        rep.warnings.append(
            f"{len(grid)} parameter trials per window: multiple-comparison bias is likely"
        )
    if rep.oos_reused:
        rep.warnings.append(
            "OUT-OF-SAMPLE REUSED: selection touched data consumed in an earlier validation cycle"
        )
    if any(w.train_overlaps_prior_oos for w in rep.windows):
        rep.warnings.append(
            "later training windows include earlier test windows (forward-chaining protocol)"
        )
