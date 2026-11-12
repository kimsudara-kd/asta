"""Research advisory interface (ADR-0008). Deterministic by default; no model or network needed.

An `Advisor` may classify regimes, summarise, compare, flag unstable parameters, explain drawdowns and
suggest hypotheses. It receives READ-ONLY copies, returns text/data only, and has no handle to the
engine, ledger, fills, risk limits or any exchange. It can never declare a strategy profitable: the
only claim it may make is the evidence level computed by `evidence_level`, and even the strongest level
is phrased as "candidate evidence", never proof.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from asta.research.analytics.regimes import RegimeParams, classify, regime_counts
from asta.research.data.models import Bar

DISCLAIMER = (
    "Advisory output: SIMULATED results on historical data with ESTIMATED fills. It is not a "
    "prediction, not investment advice, and does not establish profitability."
)


@dataclass(frozen=True)
class Advice:
    generated_by: str
    kind: str
    text: str
    data: Mapping[str, Any]
    disclaimer: str = DISCLAIMER


class Advisor(Protocol):
    name: str

    def classify_regimes(self, bars: Sequence[Bar]) -> Advice: ...
    def summarize(self, walk_forward: Mapping[str, Any]) -> Advice: ...
    def explain_drawdown(self, performance: Mapping[str, Any]) -> Advice: ...
    def unstable_parameters(self, flags: Sequence[str]) -> Advice: ...
    def suggest_hypotheses(self, walk_forward: Mapping[str, Any]) -> Advice: ...


def evidence_level(wf: Mapping[str, Any]) -> tuple[str, list[str]]:
    """NONE | WEAK | CANDIDATE. Conservative, mechanical, and never 'proven'."""
    reasons: list[str] = []
    pooled = wf.get("pooled") or {}
    windows = int(pooled.get("windows_with_params", 0) or 0)
    if windows < 4:
        return "NONE", ["fewer than 4 out-of-sample windows with parameters"]
    pos = int(pooled.get("oos_positive_windows", 0))
    pnl = Decimal(str(pooled.get("oos_net_pnl_sum", "0")))
    breached = int(pooled.get("oos_risk_breached_windows", 0))
    if wf.get("oos_reused"):
        return "NONE", ["out-of-sample data was reused for selection"]
    if breached:
        reasons.append(f"{breached} out-of-sample window(s) breached the risk budget")
    if pnl <= 0:
        reasons.append("pooled out-of-sample net P&L is not positive")
    if pos * 100 < windows * 60:
        reasons.append(f"only {pos}/{windows} out-of-sample windows are positive (< 60 %)")
    if wf.get("warnings"):
        reasons.append(f"{len(wf['warnings'])} overfitting/robustness warning(s) outstanding")
    if not reasons:
        return "CANDIDATE", [
            "positive pooled out-of-sample result, >= 60 % positive windows, no risk breach"
        ]
    return ("WEAK" if pnl > 0 and not breached else "NONE"), reasons


class RuleBasedAdvisor:
    name = "rule-based@1"

    def _adv(self, kind: str, text: str, data: Mapping[str, Any]) -> Advice:
        return Advice(self.name, kind, text, copy.deepcopy(dict(data)))

    def classify_regimes(self, bars: Sequence[Bar]) -> Advice:
        labels = classify(bars, RegimeParams())
        counts = regime_counts(labels)
        total = sum(counts.values()) or 1
        top = max(counts, key=lambda k: counts[k])
        return self._adv(
            "REGIMES",
            f"Historical regime mix (causal labels): dominant '{top}' at {counts[top] * 100 // total}% of bars.",
            {"counts": counts},
        )

    def summarize(self, walk_forward: Mapping[str, Any]) -> Advice:
        level, why = evidence_level(walk_forward)
        pooled = walk_forward.get("pooled") or {}
        text = (
            f"Evidence level: {level}. "
            + (
                f"Out-of-sample windows: {pooled.get('windows_with_params', 0)} with parameters, "
                f"{pooled.get('oos_positive_windows', 0)} positive; pooled net P&L {pooled.get('oos_net_pnl_sum', 'N/A')}. "
                if pooled
                else "No out-of-sample result is available. "
            )
            + "Reasons: "
            + "; ".join(why)
            + ". "
            + (
                "Profitability is NOT established."
                if level != "CANDIDATE"
                else "Candidate evidence only - it requires an independent holdout and forward paper validation; "
                "it does not establish profitability."
            )
        )
        return self._adv("SUMMARY", text, {"evidence_level": level, "reasons": why})

    def explain_drawdown(self, performance: Mapping[str, Any]) -> Advice:
        risk = performance.get("risk", {})
        port = performance.get("portfolio", {})
        by_asset = port.get("by_asset", {})
        worst = None
        if by_asset:
            worst = min(by_asset.items(), key=lambda kv: Decimal(str(kv[1]["contribution"])))
        parts = [
            f"Maximum drawdown {risk.get('max_drawdown_pct', 'N/A')} (abs {risk.get('max_drawdown_abs', 'N/A')})."
        ]
        if worst:
            parts.append(f"Weakest contributor: {worst[0]} ({worst[1]['contribution']}).")
        tae = risk.get("time_at_exposure")
        if tae:
            parts.append(f"Time at exposure: {tae}.")
        return self._adv("DRAWDOWN", " ".join(parts), {"worst_asset": worst[0] if worst else None})

    def unstable_parameters(self, flags: Sequence[str]) -> Advice:
        text = "Unstable configurations: " + (
            "; ".join(flags) if flags else "none flagged by the mechanical checks."
        )
        return self._adv("UNSTABLE", text, {"flags": list(flags)})

    def suggest_hypotheses(self, walk_forward: Mapping[str, Any]) -> Advice:
        ideas = []
        for w in walk_forward.get("warnings", []):
            if "edge of the search range" in w:
                ideas.append(
                    "HYPOTHESIS: widen the search range for the edge parameter, then re-validate in a NEW cycle."
                )
            if "unstable optimum" in w:
                ideas.append(
                    "HYPOTHESIS: constrain the parameter space (fewer free parameters) and re-validate."
                )
            if "in-sample positive" in w:
                ideas.append(
                    "HYPOTHESIS: add a regime filter or a higher minimum-edge threshold; re-validate."
                )
        ideas = sorted(set(ideas)) or ["No hypothesis suggested by the mechanical checks."]
        return self._adv(
            "HYPOTHESES",
            " ".join(ideas) + " All hypotheses need a new validation cycle on fresh data.",
            {"hypotheses": ideas},
        )


__all__ = ["Advice", "Advisor", "RuleBasedAdvisor", "evidence_level"]
