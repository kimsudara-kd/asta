"""M1-R1 workstream B: artefact-level reproducibility, independent ledger audit, risk reporting."""

import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from asta.research.data.fixtures import HIGHVOL, UPTREND, seg
from asta.research.runner import run_backtest
from asta.research.sim.risk import SimRiskLimits
from asta.research.validation.audit import digests, independent_audit

from .run_helpers import GRID_PARAMS, SMA_PARAMS, START, SYMS, make_config, make_datasets

DATA = make_datasets()
CASES = [
    ("sma_cross", SMA_PARAMS),
    ("regime_grid", GRID_PARAMS),
    ("trend_pullback", {}),
    ("passive_hold", {"symbols": ["BTC-USDT", "ETH-USDT"], "notional_per_symbol": "100"}),
    ("all_cash", {}),
]


@pytest.mark.parametrize(("sid", "params"), CASES)
def test_every_artefact_is_identical_across_identical_runs(sid, params):
    cfg = make_config(sid, params, DATA)
    a, b = run_backtest(cfg, DATA), run_backtest(cfg, DATA)
    da, db_ = digests(a.result), digests(b.result)
    assert da == db_, {k: (da[k], db_[k]) for k in da if da[k] != db_[k]}
    assert a.result_checksum == b.result_checksum
    assert a.performance.to_json() == b.performance.to_json()


def test_artefacts_are_identical_in_a_fresh_process_with_a_different_hash_seed():
    code = (
        "from tests.research.run_helpers import *;"
        "from asta.research.runner import run_backtest;"
        "from asta.research.validation.audit import digests;"
        "import json;"
        "d=make_datasets();"
        "r=run_backtest(make_config('sma_cross',SMA_PARAMS,d),d);"
        "print(json.dumps([r.result_checksum,digests(r.result)]))"
    )
    outs = []
    for seed in ("1", "98765"):
        env = {**__import__("os").environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
        p = subprocess.run(  # noqa: S603  (fixed argv: this interpreter, trusted literal code)
            [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
        )
        outs.append(p.stdout.strip().splitlines()[-1])
    assert outs[0] == outs[1]
    here = run_backtest(make_config("sma_cross", SMA_PARAMS, DATA), DATA)
    import json

    assert json.loads(outs[0])[0] == here.result_checksum


@pytest.mark.parametrize(("sid", "params"), CASES)
def test_reported_pnl_reconciles_to_fills_and_ledger_exactly(sid, params):
    run = run_backtest(make_config(sid, params, DATA), DATA)
    checks = independent_audit(
        run.result, run.config.starting_cash, "USDT", run.performance.returns
    )
    failed = [c for c in checks if not c.ok]
    assert not failed, failed
    assert len(checks) >= 7


def test_audit_detects_a_tampered_ledger_and_a_wrong_reported_figure():
    run = run_backtest(make_config("sma_cross", SMA_PARAMS, DATA), DATA)
    assert run.result.fills, "scenario must trade"
    wrong = dict(run.performance.returns) | {"total_net_pnl": D("12345")}
    names = {
        c.name
        for c in independent_audit(run.result, run.config.starting_cash, "USDT", wrong)
        if not c.ok
    }
    assert "reported total net P&L = last equity - starting cash" in names
    # a corrupted fill (as if someone edited the record) no longer matches the ledger
    bad = replace(run.result.fills[0], notional=run.result.fills[0].notional + D("1"))
    res = replace(run.result, fills=[bad, *run.result.fills[1:]])
    broken = [
        c
        for c in independent_audit(res, run.config.starting_cash, "USDT", run.performance.returns)
        if not c.ok
    ]
    assert any("balances equal ledger" in c.name for c in broken)


def test_a_positive_return_with_a_breached_risk_budget_is_reported_as_breached():
    """Buy-and-hold across a rally, a volatile drop and a bigger rally ends far in profit, but the
    drawdown/daily-loss limits were hit on the way: it is reported as BREACHED regardless."""
    rising = make_datasets([seg(UPTREND, 800), seg(HIGHVOL, 300), seg(UPTREND, 1800)])
    cfg = make_config(
        "passive_hold",
        {"symbols": list(SYMS), "notional_per_symbol": "100"},
        rising,
        risk=SimRiskLimits.default(),
    )
    run = run_backtest(cfg, rising)
    assert D(str(run.performance.returns["total_net_pnl"])) > 0  # profitable ...
    assert run.result.risk_verdict == "BREACHED"  # ... and still reported as a risk failure
    kinds = {e.kind for e in run.result.risk_events}
    assert kinds & {"DRAWDOWN_HALT", "DAILY_LOSS_TRIP"}
    assert run.performance.meta["risk_verdict"] == "BREACHED"


def test_no_future_information_changes_earlier_decisions_end_to_end():
    """Truncating the data after T must not alter any order or fill decided/filled before T."""
    full = run_backtest(make_config("regime_grid", GRID_PARAMS, DATA), DATA)
    cut = START + timedelta(hours=900)
    short = run_backtest(make_config("regime_grid", GRID_PARAMS, DATA, period_end=cut), DATA)
    key = lambda f: (f.order_id, f.symbol, f.side, f.ts, f.price, f.quantity)  # noqa: E731
    before = [key(f) for f in full.result.fills if f.ts < cut]
    assert before == [key(f) for f in short.result.fills if f.ts < cut]
    assert before, "scenario must trade before the cut"
