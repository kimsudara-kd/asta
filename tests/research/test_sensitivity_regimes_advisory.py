"""Sensitivity/stress analysis, causal regimes, comparison, and the AI-advisory boundary."""

import ast
import copy
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
from typing import Any

import pytest

from asta.research.advisory import DISCLAIMER, RuleBasedAdvisor, evidence_level
from asta.research.analytics.regimes import (
    RegimeParams,
    attribute_equity,
    classify,
    regime_counts,
)
from asta.research.data.fixtures import DOWNTREND, RANGE, UPTREND, generate_bars, seg
from asta.research.runner import run_backtest
from asta.research.validation.compare import compare_in_vs_out_of_sample, compare_runs
from asta.research.validation.sensitivity import (
    SensitivityPoint,
    fee_sensitivity,
    instability_flags,
    latency_sensitivity,
    missing_data_stress,
    parameter_sensitivity,
    slippage_sensitivity,
    window_regimes,
)
from asta.research.validation.walkforward import summarize

from .helpers import T0
from .run_helpers import SMA_PARAMS, START, make_config, make_datasets

DATA = make_datasets()
BASE = make_config("sma_cross", SMA_PARAMS, DATA)


# ------------------------------------------------------------------ sensitivity
def test_higher_fees_never_increase_net_pnl_and_zero_fee_is_best():
    pts = fee_sensitivity(BASE, DATA)
    pnl = {p.value: p.summary.net_pnl for p in pts}
    assert pnl["x0"] > pnl["x1"] > pnl["x2"] > pnl["x3"]  # worse-than-base assumptions hurt
    assert pts[0].summary.fees == 0  # the zero-fee scenario really has no fees
    assert len({p.summary.result_checksum for p in pts}) == 4
    assert all(p.summary.status in ("COMPLETED", "STOPPED_RISK_HALT") for p in pts)


def test_higher_slippage_never_increases_net_pnl():
    pts = slippage_sensitivity(BASE, DATA)
    pnl = [p.summary.net_pnl for p in pts]
    assert pnl == sorted(pnl, reverse=True) and pts[0].summary.slippage == 0
    assert pts[-1].summary.slippage > pts[1].summary.slippage


def test_latency_sensitivity_runs_each_extra_bar_and_changes_outcomes():
    pts = latency_sensitivity(BASE, DATA, (0, 1, 2))
    assert len({p.summary.result_checksum for p in pts}) == 3


def test_parameter_sensitivity_is_one_at_a_time_and_flags_instability():
    pts = parameter_sensitivity(BASE, DATA, {"fast": [3, 6, 9], "slow": [15, 20, 40]})
    assert [p.scenario for p in pts].count("param:fast") == 3
    assert all(p.summary.result_checksum for p in pts)
    flags = instability_flags(pts, D("0.01"))
    assert isinstance(flags, list)
    # crafted points: sign flip across neighbours is flagged
    s = pts[0].summary
    from dataclasses import replace

    mixed = [
        SensitivityPoint("param:x", "1", replace(s, net_return_pct=D("0.05"))),
        SensitivityPoint("param:x", "2", replace(s, net_return_pct=D("-0.04"))),
    ]
    assert any("changes sign" in f for f in instability_flags(mixed, D("0.01")))
    breach = [SensitivityPoint("param:y", "1", replace(s, risk_verdict="BREACHED"))]
    assert any("breach the risk budget" in f for f in instability_flags(breach, D("0.01")))


def test_missing_data_stress_is_degraded_labelled_and_seeded():
    a = missing_data_stress(BASE, DATA, (0.02,), seed=5)[0]
    b = missing_data_stress(BASE, DATA, (0.02,), seed=5)[0]
    c = missing_data_stress(BASE, DATA, (0.02,), seed=6)[0]
    assert a.summary.result_checksum == b.summary.result_checksum != c.summary.result_checksum
    assert a.summary.research_grade is False  # a stress run is never a research result
    assert a.value == "2%"


def test_window_regimes_report_each_named_window():
    wins = [
        ("quarter-1", START, START + timedelta(days=30)),
        ("quarter-2", START + timedelta(days=30), START + timedelta(days=60)),
    ]
    pts = window_regimes(BASE, DATA, wins)
    assert [p.value for p in pts] == ["quarter-1", "quarter-2"]
    assert pts[0].summary.period_end == wins[0][2]


# ------------------------------------------------------------------ causal regimes
BARS = generate_bars(
    T0,
    __import__("asta.research.types", fromlist=["Timeframe"]).Timeframe.H1,
    [seg(RANGE, 120), seg(UPTREND, 120), seg(DOWNTREND, 120)],
    4,
    D("100"),
)


def test_regime_labels_are_causal_prefix_invariant():
    full = classify(BARS)
    for cut in (130, 200, 300):
        assert classify(BARS[:cut]) == full[:cut]


def test_regime_classifier_sees_trend_and_ignores_future_garbage():
    labels = classify(BARS)
    assert labels[0] is None and labels[-1] is not None  # warm-up is explicit, not guessed
    counts = regime_counts(labels)
    assert any(k.startswith("UP/") for k in counts) and any(k.startswith("DOWN/") for k in counts)
    assert sum(counts.values()) == len(BARS) and counts["WARMUP"] >= 95


def test_equity_attribution_uses_the_label_known_before_each_bar():
    run = run_backtest(
        make_config("passive_hold", {"symbols": ["BTC-USDT"]}, {"BTC-USDT": DATA["BTC-USDT"]}),
        {"BTC-USDT": DATA["BTC-USDT"]},
    )
    bars = list(DATA["BTC-USDT"].bars)
    labels = classify(bars, RegimeParams())
    attr = attribute_equity(run.result.equity, bars, labels, 3600)
    total = sum((v["pnl"] for v in attr.values()), D(0))
    assert (
        total == run.result.equity[-1].equity - run.result.equity[0].equity
    )  # attribution is exact
    assert sum(v["bars"] for v in attr.values()) == len(run.result.equity) - 1


# ------------------------------------------------------------------ comparison
def test_comparison_flags_underperformance_failed_risk_and_negative_returns():
    pts = {
        "strategy": summarize(
            run_backtest(make_config("sma_cross", SMA_PARAMS, DATA), DATA), "strategy"
        ),
        "passive": summarize(
            run_backtest(
                make_config(
                    "passive_hold", {"symbols": list(DATA), "notional_per_symbol": "100"}, DATA
                ),
                DATA,
            ),
            "passive",
        ),
        "cash": summarize(run_backtest(make_config("all_cash", {}, DATA), DATA), "cash"),
    }
    out = compare_runs(pts, "passive", "cash")
    rows = {r["name"]: r for r in out["rows"]}
    assert (
        rows["cash"]["net_pnl"] == "0.00000000"
        and rows["cash"]["max_drawdown_pct"] == "0E-8"
        or rows["cash"]["net_return_pct"] == "0.00000000"
    )
    assert "excess_vs_passive_hold" in rows["strategy"] and "excess_vs_all_cash" in rows["strategy"]
    assert "NEGATIVE_OR_ZERO_NET_RETURN" in rows["cash"]["flags"]  # flat is not a success
    assert "SIMULATED" in out["note"] and "identical engine" in out["note"]


def test_in_vs_out_of_sample_comparison_lists_every_window():
    from .test_walkforward import fake_report

    rep = fake_report([("0.05", "-0.02"), ("0.04", "0.01")])
    rep.windows[1].status = "NO_ADMISSIBLE_PARAMETERS"
    out = compare_in_vs_out_of_sample(rep)
    assert (
        out["rows"][0]["oos_over_is_ratio"] == "-0.4"
        and out["rows"][1]["status"] == "NO_ADMISSIBLE_PARAMETERS"
    )


# ------------------------------------------------------------------ advisory boundary
def good_wf():
    return {
        "pooled": {
            "windows_with_params": 8,
            "oos_positive_windows": 6,
            "oos_net_pnl_sum": "42.5",
            "oos_risk_breached_windows": 0,
        },
        "warnings": [],
        "oos_reused": False,
    }


def test_evidence_levels_are_mechanical_and_conservative():
    assert evidence_level({"pooled": {}})[0] == "NONE"
    assert evidence_level(good_wf())[0] == "CANDIDATE"
    reused = good_wf() | {"oos_reused": True}
    assert evidence_level(reused)[0] == "NONE"
    losing = good_wf()
    losing["pooled"] = losing["pooled"] | {"oos_net_pnl_sum": "-3"}
    assert evidence_level(losing)[0] == "NONE"
    weak = good_wf()
    weak["pooled"] = weak["pooled"] | {"oos_positive_windows": 3}
    assert evidence_level(weak)[0] == "WEAK"
    breached = good_wf()
    breached["pooled"] = breached["pooled"] | {"oos_risk_breached_windows": 1}
    assert evidence_level(breached)[0] == "NONE"
    warned = good_wf() | {"warnings": ["x"]}
    assert evidence_level(warned)[0] == "WEAK"


def test_advisor_never_declares_profitability_and_always_carries_the_disclaimer():
    adv = RuleBasedAdvisor()
    wfs: list[dict[str, Any]] = [good_wf(), {"pooled": {}}, good_wf() | {"warnings": ["x"]}]
    for wf in wfs:
        a = adv.summarize(wf)
        text = a.text.lower()
        assert a.disclaimer == DISCLAIMER and "not investment advice" in a.disclaimer.lower()
        assert (
            "is profitable" not in text
            and "are profitable" not in text
            and "will be profitable" not in text
        )
        assert "not establish" in text or "not established" in text
    assert "candidate evidence only" in adv.summarize(good_wf()).text.lower()


def test_advisor_cannot_change_its_inputs_or_the_recorded_results():
    adv = RuleBasedAdvisor()
    wf = good_wf() | {"warnings": ["selected parameters change in most windows (unstable optimum)"]}
    before = copy.deepcopy(wf)
    out = adv.suggest_hypotheses(wf)
    assert wf == before  # inputs untouched
    out.data["hypotheses"].append("tamper")
    assert wf == before  # returned data is a copy, not a view onto internals
    with pytest.raises(AttributeError):
        out.text = "changed"  # type: ignore[misc]  # Advice is frozen
    assert "HYPOTHESIS" in out.text and "new validation cycle" in out.text


def test_advisor_summaries_cover_regimes_drawdown_and_instability():
    adv = RuleBasedAdvisor()
    assert "dominant" in adv.classify_regimes(BARS).text
    run = run_backtest(make_config("passive_hold", {"symbols": list(DATA)}, DATA), DATA)
    dd = adv.explain_drawdown(run.performance.to_json())
    assert "Maximum drawdown" in dd.text
    assert "none flagged" in adv.unstable_parameters([]).text
    assert "fast" in adv.unstable_parameters(["param:fast: net return changes sign"]).text


def test_advisor_works_with_no_model_and_no_network_or_keys(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "HTTPS_PROXY", "HTTP_PROXY"):
        monkeypatch.delenv(k, raising=False)
    assert RuleBasedAdvisor().summarize(good_wf()).generated_by == "rule-based@1"


def test_advisory_module_has_no_path_to_the_engine_ledger_orders_or_network():
    src = Path(__file__).parents[2] / "src" / "asta" / "research" / "advisory.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module)
        elif isinstance(n, ast.Import):
            imported.update(a.name for a in n.names)
    forbidden = {
        "asta.research.sim.engine",
        "asta.research.sim.ledger",
        "asta.research.sim.orders",
        "asta.research.sim.risk",
        "asta.research.runner",
        "httpx",
        "requests",
        "socket",
        "asta.exchanges",
    }
    assert not (imported & forbidden), imported & forbidden
