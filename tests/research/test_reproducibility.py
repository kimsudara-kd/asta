"""A completed run must be reproducible from its persisted configuration and immutable inputs."""

import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from asta.research.data.models import Bar
from asta.research.runner import BacktestConfig, RunRefused, run_backtest
from asta.research.types import Severity, SourceType

from .helpers import dataset, flat_bars
from .run_helpers import GRID_PARAMS, SMA_PARAMS, START, make_config, make_datasets

DATA = make_datasets()


def test_same_config_same_data_gives_identical_checksum_twice():
    cfg = make_config("sma_cross", SMA_PARAMS, DATA)
    a, b = run_backtest(cfg, DATA), run_backtest(cfg, DATA)
    assert a.result_checksum == b.result_checksum and a.config_hash == b.config_hash
    assert [c.digest for c in a.result.checkpoints] == [c.digest for c in b.result.checkpoints]
    assert len(a.result.checkpoints) > 3  # deterministic checkpoints are produced


def test_run_reproduces_from_the_persisted_json_config():
    cfg = make_config("regime_grid", GRID_PARAMS, DATA, period_start=START + timedelta(days=5))
    stored = json.dumps(cfg.to_json(), sort_keys=True)  # what would be persisted
    reloaded = BacktestConfig.from_json(json.loads(stored))
    assert reloaded.config_hash() == cfg.config_hash()
    assert run_backtest(reloaded, DATA).result_checksum == run_backtest(cfg, DATA).result_checksum


def test_any_change_to_any_input_changes_the_result_checksum():
    base = make_config("sma_cross", SMA_PARAMS, DATA)
    ref = run_backtest(base, DATA).result_checksum
    variants = {
        "param": base.replace(strategy_params={**SMA_PARAMS, "fast": 7}),
        "fees": base.replace(fees=base.fees.scaled(D(2), "x2")),
        "slippage": base.replace(execution=base.execution.scaled_slippage(D(2))),
        "latency": base.replace(latency_bars=1),
        "risk": base.replace(risk=replace(base.risk, max_total_pct=D("0.1"))),  # binds: fewer buys
        "cash": base.replace(starting_cash=D(2000)),
    }
    for name, cfg in variants.items():
        assert run_backtest(cfg, DATA).result_checksum != ref, name
        assert cfg.config_hash() != base.config_hash(), name


def test_wrong_dataset_version_is_refused_not_silently_used():
    cfg = make_config("sma_cross", SMA_PARAMS, DATA)
    other = make_datasets(seed=99)
    with pytest.raises(RunRefused, match="checksum"):
        run_backtest(cfg, other)
    with pytest.raises(RunRefused, match="not provided"):
        run_backtest(cfg, {k: v for k, v in DATA.items() if k != "ETH-USDT"})


def test_rejected_dataset_is_kept_but_cannot_produce_results():
    from .helpers import bar

    # 10 % of bars missing -> REJECTED by the gap policy (never forward-filled)
    gapped = dataset([bar(i, "100", "101", "99", "100") for i in range(200) if i % 10 != 5]).dataset
    assert gapped.meta.validation_status == Severity.REJECTED and gapped.meta.row_count == 180
    cfg = make_config("all_cash", {}, {"BTC-USDT": gapped})
    with pytest.raises(RunRefused, match="REJECTED"):
        run_backtest(cfg, {"BTC-USDT": gapped})
    run = run_backtest(cfg, {"BTC-USDT": gapped}, allow_degraded=True)  # explicit stress mode only
    assert not run.research_grade and any("STRESS TEST" in n for n in run.notes)


def test_engine_refuses_duplicate_timestamps_even_in_stress_mode():
    from asta.research.sim.engine import EngineError

    dup = dataset([flat_bars(2)[0], flat_bars(2)[0]] + flat_bars(10)[2:]).dataset
    assert dup.meta.validation_status == Severity.REJECTED
    with pytest.raises(EngineError, match="strictly increasing"):
        run_backtest(
            make_config("all_cash", {}, {"BTC-USDT": dup}), {"BTC-USDT": dup}, allow_degraded=True
        )


def test_synthetic_and_fixture_data_run_but_are_never_research_grade():
    for st in (SourceType.SYNTHETIC, SourceType.FIXTURE):
        d = make_datasets(source_type=st)
        run = run_backtest(make_config("all_cash", {}, d), d)
        assert not run.research_grade and any("NOT research-grade" in n for n in run.notes)
        assert all("not real exchange data" in label for label in run.data_labels.values())
    real_like = run_backtest(make_config("all_cash", {}, DATA), DATA)
    assert real_like.research_grade
    assert "SIMULATED" in real_like.result_labels and "NOT LIVE TRADING" in real_like.result_labels


def test_unverified_rules_and_fees_are_disclosed_in_every_run():
    run = run_backtest(make_config("all_cash", {}, DATA), DATA)
    assert any("UNVERIFIED" in n for n in run.notes)


def test_future_data_beyond_period_end_cannot_change_the_result():
    cut = START + timedelta(days=30)
    cfg = make_config("regime_grid", GRID_PARAMS, DATA, period_end=cut)
    a = run_backtest(cfg, DATA)
    corrupted = {}
    for s, ds in DATA.items():
        bars = [
            b
            if b.open_time < cut
            else Bar(b.open_time, b.open * 9, b.high * 9, b.low * 9, b.close * 9, b.volume)
            for b in ds.bars
        ]
        corrupted[s] = type(ds)(ds.meta, tuple(bars))
    assert run_backtest(cfg, corrupted).result_checksum == a.result_checksum


def test_warmup_feeds_indicators_but_not_trading():
    start = START + timedelta(days=20)
    cfg = make_config("sma_cross", SMA_PARAMS, DATA, period_start=start, warmup_bars=60)
    run = run_backtest(cfg, DATA)
    assert run.result.warmup_bars == 60 and run.result.counters["warmup_dropped"] == 0
    assert all(f.ts >= start for f in run.result.fills)  # nothing traded during warm-up
    assert run.result.equity[0].ts > start  # equity observations start at trade_start
    no_warm = run_backtest(make_config("sma_cross", SMA_PARAMS, DATA, period_start=start), DATA)
    assert no_warm.result_checksum != run.result_checksum  # primed indicators trade earlier


def test_result_is_stable_across_repeated_runs_with_progress_and_cancel_hooks():
    cfg = make_config("sma_cross", SMA_PARAMS, DATA)
    ticks: list[int] = []
    a = run_backtest(cfg, DATA, on_progress=lambda k, n: ticks.append(k))
    assert ticks and ticks == sorted(ticks)
    assert (
        a.result_checksum == run_backtest(cfg, DATA).result_checksum
    )  # hooks do not alter results


def test_cancellation_stops_the_run():
    from asta.research.sim.engine import RunCancelled

    with pytest.raises(RunCancelled):
        run_backtest(make_config("sma_cross", SMA_PARAMS, DATA), DATA, should_cancel=lambda: True)
