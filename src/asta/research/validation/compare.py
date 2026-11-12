"""Strategy-versus-baseline comparison and in-sample/out-of-sample comparison."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from asta.core.money import dec_str
from asta.research.validation.walkforward import RunSummary, WalkForwardReport

ZERO = Decimal(0)


def compare_runs(
    runs: Mapping[str, RunSummary], passive_key: str | None = None, cash_key: str | None = None
) -> dict[str, Any]:
    """Rows plus excess return vs passive hold (opportunity cost) and vs all-cash, with flags.
    All runs must share data/costs/capital for the comparison to be fair; that is asserted by
    comparing the stored period and checksums' presence, and by construction in the runner."""
    rows: list[dict[str, Any]] = []
    passive = runs.get(passive_key) if passive_key else None
    cash = runs.get(cash_key) if cash_key else None
    for name, s in runs.items():
        flags: list[str] = []
        if s.risk_verdict != "PASSED":
            flags.append("FAILED_RISK_BUDGET")
        if s.net_return_pct <= 0:
            flags.append("NEGATIVE_OR_ZERO_NET_RETURN")
        if not s.research_grade:
            flags.append("NOT_RESEARCH_GRADE_DATA")
        row: dict[str, Any] = {
            "name": name,
            "net_pnl": dec_str(s.net_pnl),
            "net_return_pct": dec_str(s.net_return_pct),
            "max_drawdown_pct": dec_str(s.max_drawdown_pct),
            "sharpe": None if s.sharpe is None else dec_str(s.sharpe),
            "round_trips": s.round_trips,
            "fees": dec_str(s.fees),
            "slippage": dec_str(s.slippage),
            "risk_verdict": s.risk_verdict,
            "status": s.status,
            "result_checksum": s.result_checksum,
        }
        if passive is not None and name != passive_key:
            row["excess_vs_passive_hold"] = dec_str(s.net_return_pct - passive.net_return_pct)
            if s.net_return_pct < passive.net_return_pct:
                flags.append("UNDERPERFORMS_PASSIVE_HOLD")
        if cash is not None and name != cash_key:
            row["excess_vs_all_cash"] = dec_str(s.net_return_pct - cash.net_return_pct)
        row["flags"] = flags
        rows.append(row)
    return {
        "rows": rows,
        "note": "SIMULATED, ESTIMATED FILLS; baselines use the identical engine, data and costs",
    }


def compare_in_vs_out_of_sample(rep: WalkForwardReport) -> dict[str, Any]:
    rows = []
    for w in rep.windows:
        if w.status != "OK" or not w.train or not w.test:
            rows.append({"window": w.window.index, "status": w.status})
            continue
        degr = None
        if w.train.net_return_pct > 0:
            degr = dec_str(w.test.net_return_pct / w.train.net_return_pct)
        rows.append(
            {
                "window": w.window.index,
                "in_sample_return": dec_str(w.train.net_return_pct),
                "out_of_sample_return": dec_str(w.test.net_return_pct),
                "oos_over_is_ratio": degr,
                "oos_risk_verdict": w.test.risk_verdict,
                "chosen_key": w.chosen_key,
            }
        )
    return {
        "rows": rows,
        "pooled": {
            k: (dec_str(v) if isinstance(v, Decimal) else str(v)) for k, v in rep.pooled.items()
        },
        "warnings": rep.warnings,
    }
