"""Walk-forward: windows, train/test separation, holdout bookkeeping, overfitting diagnostics."""

from datetime import timedelta
from decimal import Decimal as D

import pytest

from asta.research.data.models import Bar
from asta.research.runner import run_backtest
from asta.research.validation.holdout import KIND_FINAL, HoldoutRegistry, HoldoutViolation
from asta.research.validation.splits import (
    SplitError,
    Window,
    anchored_windows,
    chronological_split,
    rolling_windows,
)
from asta.research.validation.walkforward import (
    RunSummary,
    WalkForwardReport,
    WindowResult,
    _finalise,
    default_objective,
    expand_grid,
    params_key,
    walk_forward,
)

from .run_helpers import SMA_PARAMS, START, make_config, make_datasets

DATA = make_datasets()
END = START + timedelta(days=DATA["BTC-USDT"].meta.row_count // 24)
AXES = {"fast": [4, 6, 8], "slow": [18, 30]}
GRID = expand_grid({"symbols": SMA_PARAMS["symbols"]}, AXES)


def windows():
    return rolling_windows(START, END, train_days=20, test_days=7, step_days=7)


# ------------------------------------------------------------------ splits
def test_windows_never_let_training_touch_the_test_period():
    ws = windows()
    assert len(ws) >= 3
    for w in ws:
        assert w.train_start < w.train_end <= w.test_start < w.test_end <= END
    assert [w.test_start for w in ws] == sorted(w.test_start for w in ws)
    emb = rolling_windows(START, END, 20, 7, 7, embargo_days=2)
    assert all((w.test_start - w.train_end).days == 2 for w in emb)  # purge gap respected


def test_invalid_windows_and_splits_are_refused():
    with pytest.raises(SplitError):
        Window(
            0,
            START,
            START + timedelta(days=5),
            START + timedelta(days=4),
            START + timedelta(days=9),
        )
    with pytest.raises(SplitError):
        rolling_windows(START, START + timedelta(days=3), 20, 7)
    with pytest.raises(SplitError):
        chronological_split(START, END, 1.2)
    (a, b), (c, d) = chronological_split(START, END, 0.7)
    assert a == START and b == c and d == END and a < b < d


def test_anchored_windows_expand_from_the_same_start():
    ws = anchored_windows(START, END, 20, 7)
    assert all(w.train_start == START for w in ws)
    assert [(w.train_end - START).days for w in ws] == sorted(
        (w.train_end - START).days for w in ws
    )
    assert all(w.train_end <= w.test_start for w in ws)


def test_grid_expansion_is_deterministic():
    g = expand_grid({"x": 1}, {"b": [1, 2], "a": [3, 4]})
    assert g == [
        {"x": 1, "a": 3, "b": 1},
        {"x": 1, "a": 3, "b": 2},
        {"x": 1, "a": 4, "b": 1},
        {"x": 1, "a": 4, "b": 2},
    ]
    assert (
        params_key({"a": 1, "b": 2}) == params_key({"b": 2, "a": 1}) != params_key({"a": 1, "b": 3})
    )


# ------------------------------------------------------------------ separation
def test_training_runs_only_see_bars_before_train_end_and_test_runs_start_after_it():
    ws = windows()[:2]
    calls = []

    def spy(cfg, data):
        calls.append((cfg.period_start, cfg.period_end))
        return run_backtest(cfg, data)

    rep = walk_forward(
        make_config("sma_cross", SMA_PARAMS, DATA, warmup_bars=0), DATA, GRID, ws, runner=spy
    )
    for w in ws:
        train_calls = [c for c in calls if c == (w.train_start, w.train_end)]
        test_calls = [c for c in calls if c == (w.test_start, w.test_end)]
        assert (
            len(train_calls) == len(GRID) and len(test_calls) == 1
        )  # OOS evaluated once per window
    assert all(r.selection_data_end <= r.window.test_start for r in rep.windows)
    assert all(
        end <= w.train_end
        for w in ws
        for (s, end) in calls
        if (s, end) == (w.train_start, w.train_end)
    )


def test_parameter_selection_is_independent_of_everything_after_the_training_end():
    """Corrupt ALL data after window 0's train_end: window 0's chosen parameters cannot change."""
    ws = windows()[:1]
    base = make_config("sma_cross", SMA_PARAMS, DATA)
    clean = walk_forward(base, DATA, GRID, ws)
    cut = ws[0].train_end
    bad = {}
    for s, ds in DATA.items():
        bad[s] = type(ds)(
            ds.meta,
            tuple(
                b
                if b.open_time < cut
                else Bar(b.open_time, b.open * 3, b.high * 4, b.low * 2, b.close * 5, b.volume)
                for b in ds.bars
            ),
        )
    dirty = walk_forward(base, bad, GRID, ws)
    assert clean.windows[0].chosen_key == dirty.windows[0].chosen_key
    assert clean.windows[0].trials == dirty.windows[0].trials  # every training score identical
    ctr, dtr = clean.windows[0].train, dirty.windows[0].train
    cte, dte = clean.windows[0].test, dirty.windows[0].test
    assert ctr is not None and dtr is not None and cte is not None and dte is not None
    assert ctr.result_checksum == dtr.result_checksum
    assert cte.result_checksum != dte.result_checksum  # OOS did change


def test_out_of_sample_results_never_feed_selection_within_a_window():
    ws = windows()[:1]
    rep = walk_forward(make_config("sma_cross", SMA_PARAMS, DATA), DATA, GRID, ws)
    w = rep.windows[0]
    assert len(w.trials) == len(GRID)  # one training score per candidate; no test score among them
    assert w.test is not None and w.test.period_start == ws[0].test_start


def test_oos_windows_are_registered_and_forward_chaining_overlap_is_recorded_not_hidden():
    reg = HoldoutRegistry()
    rep = walk_forward(
        make_config("sma_cross", SMA_PARAMS, DATA), DATA, GRID, windows()[:3], registry=reg
    )
    assert len(reg.consumed) == 3 and all(
        c.kind == "WF_TEST" and c.cycle == 1 for c in reg.consumed
    )
    assert rep.cycle == 1 and not rep.oos_reused
    assert any("forward-chaining" in w for w in rep.warnings) or not any(
        r.train_overlaps_prior_oos for r in rep.windows
    )


def test_final_holdout_cannot_be_used_for_selection_without_a_new_cycle():
    reg = HoldoutRegistry()
    ws = windows()
    key = "sma_cross:" + ",".join(
        f"{s}={c[:12]}" for s, c in sorted({s: d.meta.checksum for s, d in DATA.items()}.items())
    )
    reg.consume(key, ws[0].train_start, ws[0].train_end, KIND_FINAL)
    base = make_config("sma_cross", SMA_PARAMS, DATA)
    with pytest.raises(HoldoutViolation, match="FINAL holdout"):
        walk_forward(base, DATA, GRID[:1], ws[:1], registry=reg)
    reg.open_new_cycle()  # explicitly record a new validation cycle
    rep = walk_forward(base, DATA, GRID[:1], ws[:1], registry=reg)
    assert rep.oos_reused and rep.cycle == 2
    assert any("OUT-OF-SAMPLE REUSED" in w for w in rep.warnings)


def test_default_objective_excludes_risk_breaches_and_unreconciled_runs():
    breached = run_backtest(
        make_config(
            "passive_hold",
            {"symbols": list(DATA)},
            DATA,
            period_start=START + timedelta(days=30),
            period_end=START + timedelta(days=60),
        ),
        DATA,
    )
    safe = run_backtest(
        make_config("all_cash", {}, DATA, period_end=START + timedelta(days=10)), DATA
    )
    assert default_objective(safe) == D("0.00000000") or default_objective(safe) == 0
    if breached.result.risk_verdict != "PASSED":
        assert default_objective(breached) is None  # attractive P&L cannot rescue a breach


def test_window_with_no_admissible_parameters_is_reported_and_not_traded():
    # a grid of one configuration that always trips the 5 % drawdown halt inside the training window
    crash = make_datasets()
    from asta.research.data.fixtures import DOWNTREND, seg

    crash = make_datasets(segments=[seg(DOWNTREND, 24 * 40)])
    hold = {"symbols": list(crash)}
    base = make_config("passive_hold", hold, crash)
    ws = rolling_windows(START, START + timedelta(days=40), 15, 5, 5)
    rep = walk_forward(base, crash, [hold], ws, objective=lambda r: None)
    assert all(w.status == "NO_ADMISSIBLE_PARAMETERS" and w.test is None for w in rep.windows)
    assert any("no risk-admissible" in w for w in rep.warnings)


# ------------------------------------------------------------------ overfitting diagnostics
def summ(ret, risk="PASSED"):
    return RunSummary(
        "x",
        "h",
        "c",
        "COMPLETED",
        risk,
        True,
        None,
        None,
        D(1000),
        D(1000),
        D(ret) * 1000,
        D(ret),
        D("0.01"),
        None,
        None,
        3,
        6,
        D(1),
        D(1),
    )


def fake_report(pairs, keys=None, grid=3, params=None):
    rep = WalkForwardReport("s", 1, False, grid)
    for i, (is_r, oos_r) in enumerate(pairs):
        w = Window(
            i,
            START,
            START + timedelta(days=10),
            START + timedelta(days=10),
            START + timedelta(days=15),
        )
        rep.windows.append(
            WindowResult(
                w,
                "OK",
                {"fast": (params or list(range(len(pairs))))[i]},
                (keys or [str(i) for i in range(len(pairs))])[i],
                summ(is_r),
                summ(oos_r),
                [],
                w.train_end,
            )
        )
    return rep


def test_overfitting_warnings_fire_for_is_positive_oos_negative_and_degradation():
    rep = fake_report([("0.05", "-0.02"), ("0.06", "0.00"), ("0.04", "0.01"), ("0.05", "-0.01")])
    _finalise(rep, [{}] * 3, None)
    assert any("in-sample positive but out-of-sample non-positive" in w for w in rep.warnings)
    assert any("less than half" in w for w in rep.warnings)
    assert rep.pooled["oos_positive_windows"] == 1 and rep.pooled["windows"] == 4


def test_unstable_optimum_and_search_edge_and_multiple_comparison_warnings():
    rep = fake_report([("0.02", "0.02")] * 5, keys=list("abcde"), params=[0, 4, 0, 4, 2])
    _finalise(rep, [{}] * 25, {"fast": [0, 1, 2, 3, 4]})
    assert any("unstable optimum" in w for w in rep.warnings)
    assert any("edge of the search range" in w for w in rep.warnings)
    assert any("multiple-comparison" in w for w in rep.warnings)


def test_risk_breach_in_oos_is_reported_as_failure():
    rep = fake_report([("0.02", "0.03"), ("0.02", "0.03")])
    rep.windows[1].test = summ("0.03", risk="BREACHED")
    _finalise(rep, [{}], None)
    assert rep.pooled["oos_risk_breached_windows"] == 1
    assert any("breached the risk budget" in w for w in rep.warnings)


def test_report_json_is_serialisable_and_carries_cycle_and_cutoffs():
    import json

    rep = walk_forward(make_config("sma_cross", SMA_PARAMS, DATA), DATA, GRID[:2], windows()[:2])
    j = json.loads(json.dumps(rep.to_json()))
    assert j["cycle"] == 1 and j["windows"][0]["selection_data_end"] == j["windows"][0]["train"][1]
