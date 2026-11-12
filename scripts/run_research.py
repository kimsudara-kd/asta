"""Reproducible research run on the LOCAL real Binance datasets -> docs/evidence/m1/*.json + summary.

Offline only: reads local content-addressed datasets and rule/fee profiles, runs the pure engine.
Every figure is SIMULATED on HISTORICAL data with ESTIMATED fills. Nothing here claims profitability.

Usage: uv run python scripts/run_research.py [--out docs/evidence/m1] [--quick]
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from asta.core.money import dec_str
from asta.research.advisory import RuleBasedAdvisor
from asta.research.analytics.regimes import attribute_equity, classify, regime_counts
from asta.research.data.models import Dataset
from asta.research.data.profiles import BINANCE_DEFAULT_FEES, RuleProfile
from asta.research.data.store import DatasetFileStore
from asta.research.runner import BacktestConfig, BacktestRun, canonical, run_backtest
from asta.research.sim.execution import ExecutionModel
from asta.research.sim.risk import SimRiskLimits
from asta.research.types import Timeframe
from asta.research.validation.compare import compare_in_vs_out_of_sample, compare_runs
from asta.research.validation.holdout import KIND_FINAL, HoldoutRegistry
from asta.research.validation.sensitivity import (
    fee_sensitivity,
    instability_flags,
    latency_sensitivity,
    missing_data_stress,
    parameter_sensitivity,
    slippage_sensitivity,
    window_regimes,
)
from asta.research.validation.splits import rolling_windows
from asta.research.validation.walkforward import (
    default_objective,
    expand_grid,
    params_key,
    summarize,
    walk_forward,
)

SYMS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
START = datetime(2023, 11, 1, tzinfo=UTC)
END = datetime(2026, 11, 1, tzinfo=UTC)
HOLDOUT_DAYS = 180
WARMUP = 300

GRIDS: dict[str, dict[str, list[Any]]] = {
    "regime_grid": {
        "levels": [4, 6],
        "range_pct": ["0.06", "0.10"],
        "min_net_profit_bps": ["20", "40"],
    },
    "trend_pullback": {
        "tp_atr": ["2", "3", "4"],
        "sl_atr": ["1", "1.5", "2"],
        "pullback_atr": ["0.3", "0.8"],
    },
    "sma_cross": {"fast": [10, 20, 30], "slow": [50, 100, 150]},
}
BASE_PARAMS: dict[str, dict[str, Any]] = {
    "regime_grid": {},
    "trend_pullback": {},
    "sma_cross": {"symbols": list(SYMS)},
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_data(store_dir: Path) -> dict[str, Dataset]:
    store = DatasetFileStore(store_dir)
    found: dict[str, Dataset] = {}
    for meta in glob.glob(str(store_dir / "*.meta.json")):
        m = json.loads(Path(meta).read_text(encoding="utf-8"))
        if m["timeframe"] == "1h" and m["source_type"] == "DERIVED":
            found[m["symbol"]] = store.load(m["checksum"])
    missing = [s for s in SYMS if s not in found]
    if missing:
        raise SystemExit(f"missing 1h derived datasets for {missing}; run the import first")
    return {s: found[s] for s in SYMS}


def base_config(
    data: dict[str, Dataset],
    rules: tuple[RuleProfile, ...],
    sid: str,
    params: dict[str, Any],
    **kw: Any,
) -> BacktestConfig:
    return BacktestConfig(
        strategy_id=sid,
        strategy_params=params,
        dataset_checksums={s: d.meta.checksum for s, d in data.items()},
        timeframe=Timeframe.H1,
        rules=rules,
        fees=BINANCE_DEFAULT_FEES,
        execution=ExecutionModel(),
        risk=SimRiskLimits.default(),
        warmup_bars=WARMUP,
        checkpoint_every=2000,
        label=kw.pop("label", sid),
        **kw,
    )


class Memo:
    """Cache runs by config hash so the two objective variants share every simulation."""

    def __init__(self) -> None:
        self.cache: dict[str, BacktestRun] = {}
        self.hits = 0
        self.runs = 0

    def __call__(self, cfg: BacktestConfig, data: Any) -> BacktestRun:
        h = cfg.config_hash()
        if h in self.cache:
            self.hits += 1
            return self.cache[h]
        self.runs += 1
        r = run_backtest(cfg, data)
        self.cache[h] = r
        return r


def objective_flagged(run: BacktestRun) -> Decimal | None:
    """Net return ignoring the risk verdict (breaches are still REPORTED and counted as failures)."""
    if not run.result.reconcile.ok:
        return None
    return Decimal(str(run.performance.returns["net_return_pct"]))


def jdump(path: Path, obj: Any) -> None:
    path.write_text(
        json.dumps(json.loads(canonical(obj)), indent=1, sort_keys=True), encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/evidence/m1")
    ap.add_argument("--store", default="data/store")
    ap.add_argument("--rules", default=None)
    ap.add_argument("--quick", action="store_true", help="smaller grids/windows (smoke test)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data = load_data(Path(args.store))
    rules_file = args.rules or sorted(glob.glob("data/import/rules_binance_*.json"))[-1]
    rules = tuple(
        RuleProfile.from_json(p) for p in json.loads(Path(rules_file).read_text(encoding="utf-8"))
    )
    t0 = time.time()
    adv = RuleBasedAdvisor()

    # ------------------------------------------------------------ 1. data report
    log("data report")
    jdump(
        out / "data-report.json",
        {
            "labels": ["SIMULATED", "HISTORICAL DATA", "ESTIMATED FILLS", "NOT LIVE TRADING"],
            "datasets": {
                s: {
                    "checksum": d.meta.checksum,
                    "rows": d.meta.row_count,
                    "timeframe": "1h",
                    "source_type": d.meta.source_type.value,
                    "parents": list(d.meta.parent_checksums),
                    "provenance": d.meta.provenance,
                    "validation": d.meta.validation_status.value
                    if d.meta.validation_status
                    else None,
                    "accepted_for_research": d.meta.accepted_for_research,
                    "period": [d.meta.period_start.isoformat(), d.meta.period_end.isoformat()],
                }
                for s, d in data.items()
            },
            "rules": [r.to_json() for r in rules],
            "fees": BINANCE_DEFAULT_FEES.to_json(),
            "execution_model": ExecutionModel().to_json(),
            "risk_limits": SimRiskLimits.default().to_json(),
            "assumptions": [
                "Binance rules are the CURRENT exchangeInfo snapshot applied to history (assumption, unverified).",
                "Fees are standard base-tier assumptions, not account-specific (unverified).",
                "OKX data/rules are unreachable from this network (residual R-1); no OKX results exist.",
            ],
        },
    )

    # ------------------------------------------------------------ 2. baselines on the full period
    log("full-period strategies and baselines")
    full: dict[str, BacktestRun] = {}
    specs = {
        "all_cash": ("all_cash", {}, SimRiskLimits.default()),
        "passive_hold_equal_weight_300": (
            "passive_hold",
            {"symbols": list(SYMS), "notional_per_symbol": "100"},
            SimRiskLimits.default(),
        ),
        "passive_hold_BTC_only_100": (
            "passive_hold",
            {"symbols": ["BTC-USDT"], "notional_per_symbol": "100"},
            SimRiskLimits.default(),
        ),
        "passive_hold_reference_full_capital": (
            "passive_hold",
            {"symbols": list(SYMS), "notional_per_symbol": "333"},
            SimRiskLimits.unconstrained_reference(),
        ),
        "sma_cross_default": ("sma_cross", {"symbols": list(SYMS)}, SimRiskLimits.default()),
        "regime_grid_default": ("regime_grid", {}, SimRiskLimits.default()),
        "trend_pullback_default": ("trend_pullback", {}, SimRiskLimits.default()),
    }
    for name, (sid, params, risk) in specs.items():
        cfg = base_config(data, rules, sid, params, label=name).replace(risk=risk, warmup_bars=0)
        full[name] = run_backtest(cfg, data)
        log(
            f"  {name}: net {dec_str(full[name].performance.returns['total_net_pnl'])} {full[name].result.status}"
        )
    summaries = {k: summarize(v, k) for k, v in full.items()}
    comp = compare_runs(summaries, "passive_hold_equal_weight_300", "all_cash")
    btc = list(data["BTC-USDT"].bars)
    labels = classify(btc)
    regimes = {
        k: {
            lab: {"bars": v["bars"], "pnl": v["pnl"]}
            for lab, v in attribute_equity(r.result.equity, btc, labels, 3600).items()
        }
        for k, r in full.items()
    }
    jdump(
        out / "baseline-comparison.json",
        {
            "comparison": comp,
            "performance": {k: r.performance.to_json() for k, r in full.items()},
            "risk_events": {
                k: [
                    [e.ts, e.kind, e.detail]
                    for e in r.result.risk_events
                    if e.kind != "ORDER_REJECTED"
                ][:50]
                for k, r in full.items()
            },
            "btc_regime_counts": regime_counts(labels),
            "regime_attribution_btc_labels": regimes,
            "result_checksums": {k: r.result_checksum for k, r in full.items()},
        },
    )

    # ------------------------------------------------------------ 3. walk-forward + final holdout
    holdout_start = END - timedelta(days=HOLDOUT_DAYS)
    wf_end = holdout_start
    windows = rolling_windows(START, wf_end, train_days=270, test_days=90, step_days=90)
    if args.quick:
        windows = windows[:2]
    log(f"walk-forward: {len(windows)} windows, holdout {holdout_start.date()}..{END.date()}")
    reg = HoldoutRegistry()
    wf_reports: dict[str, Any] = {}
    for sid, axes in GRIDS.items():
        if args.quick:
            axes = {k: v[:2] for k, v in axes.items()}
        grid = expand_grid(BASE_PARAMS[sid], axes)
        if sid == "sma_cross":
            grid = [g for g in grid if g["fast"] < g["slow"]]
        memo = Memo()
        base = base_config(data, rules, sid, BASE_PARAMS[sid])
        variants = {}
        for vname, obj in (
            ("strict_risk_admissible", default_objective),
            ("net_return_flagged", objective_flagged),
        ):
            log(f"  {sid} [{vname}] grid={len(grid)}")
            variants[vname] = walk_forward(
                base,
                data,
                grid,
                windows,
                objective=obj,
                registry=HoldoutRegistry(),
                runner=memo,
                edge_axes=axes,
            )
        log(f"  {sid}: simulations run={memo.runs} reused={memo.hits}")
        wf_reports[sid] = variants
        for vname, rep in variants.items():
            jdump(
                out / f"walk-forward-{sid}-{vname}.json",
                {
                    "report": rep.to_json(),
                    "in_vs_out_of_sample": compare_in_vs_out_of_sample(rep),
                    "advisory": {
                        "summary": adv.summarize(rep.to_json()).text,
                        "hypotheses": adv.suggest_hypotheses(rep.to_json()).text,
                    },
                },
            )

    # final holdout: choose on the 365 days BEFORE the holdout (strict objective), evaluate once.
    log("final holdout")
    holdout: dict[str, Any] = {}
    sel_start = holdout_start - timedelta(days=365)
    for sid, axes in GRIDS.items():
        grid = expand_grid(BASE_PARAMS[sid], axes)
        if sid == "sma_cross":
            grid = [g for g in grid if g["fast"] < g["slow"]]
        key = f"{sid}:holdout"
        reg.check_selection(key, sel_start, holdout_start)
        best: tuple[Decimal, str, dict[str, Any]] | None = None
        for params in grid:
            run = run_backtest(
                base_config(data, rules, sid, params).replace(
                    period_start=sel_start, period_end=holdout_start
                ),
                data,
            )
            score = objective_flagged(run)
            if score is not None and (
                best is None or (score, params_key(params)) > (best[0], best[1])
            ):
                best = (score, params_key(params), params)
        assert best is not None
        test = run_backtest(
            base_config(data, rules, sid, best[2]).replace(
                period_start=holdout_start, period_end=END
            ),
            data,
        )
        reg.consume(
            key, holdout_start, END, KIND_FINAL
        )  # consumed: any later selection needs a new cycle
        holdout[sid] = {
            "selected_params": best[2],
            "selection_window": [sel_start.isoformat(), holdout_start.isoformat()],
            "selection_score_net_return": dec_str(best[0]),
            "holdout_window": [holdout_start.isoformat(), END.isoformat()],
            "holdout_summary": summarize(test, f"{sid} holdout").to_json(),
            "holdout_performance": test.performance.to_json(),
        }
        log(
            f"  {sid}: holdout net {dec_str(test.performance.returns['total_net_pnl'])} risk {test.result.risk_verdict}"
        )
    jdump(
        out / "final-holdout.json",
        {
            "holdout": holdout,
            "registry": reg.to_json(),
            "note": "evaluated exactly once; the holdout window is consumed (any reuse needs a new validation cycle)",
        },
    )

    # ------------------------------------------------------------ 4. sensitivity and stress
    log("sensitivity and stress")
    sens: dict[str, Any] = {}
    for sid in GRIDS:
        base = base_config(data, rules, sid, BASE_PARAMS[sid]).replace(warmup_bars=0)
        pts = []
        pts += fee_sensitivity(base, data)
        pts += slippage_sensitivity(base, data)
        pts += latency_sensitivity(base, data)
        if not args.quick:
            pts += missing_data_stress(base, data, (0.01, 0.05))
        ax = {k: v[:3] for k, v in GRIDS[sid].items()}
        psens = parameter_sensitivity(base, data, ax)
        base_ret = Decimal(str(full[f"{sid}_default"].performance.returns["net_return_pct"]))
        sens[sid] = {
            "scenarios": [p.to_json() for p in pts],
            "parameters": [p.to_json() for p in psens],
            "instability_flags": instability_flags(psens, base_ret),
            "unstable": adv.unstable_parameters(instability_flags(psens, base_ret)).text,
        }
        log(f"  {sid} sensitivity done")
    jdump(out / "sensitivity.json", sens)

    # ------------------------------------------------------------ 5. historical regimes (calendar quarters)
    log("quarterly windows")
    quarters = []
    q = START
    while q + timedelta(days=91) <= END:
        quarters.append(
            (f"{q.date()}..{(q + timedelta(days=91)).date()}", q, q + timedelta(days=91))
        )
        q += timedelta(days=91)
    qres = {}
    for sid in GRIDS:
        base = base_config(data, rules, sid, BASE_PARAMS[sid])
        qres[sid] = [p.to_json() for p in window_regimes(base, data, quarters)]
    jdump(out / "quarterly-windows.json", qres)

    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": round(time.time() - t0),
        "quick": args.quick,
        "git_note": "see commit history",
    }
    jdump(out / "run-meta.json", meta)
    log(f"done in {time.time() - t0:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
