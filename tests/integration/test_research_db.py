"""M1 persistence: immutability triggers, full persist/reload/reproduce round trip, idempotency."""

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from asta.db import research_store as rs
from asta.db.research_models import (
    BacktestRun,
    HistoricalDatasetVersion,
    PortfolioEquitySnapshot,
    SimulatedFill,
)
from asta.research.data.quality import validate_bars
from asta.research.data.store import DatasetFileStore
from asta.research.runner import BacktestConfig, run_backtest
from asta.research.validation.holdout import HoldoutRegistry
from tests.research.run_helpers import START, make_config, make_datasets

pytestmark = pytest.mark.db

DATA = make_datasets(symbols=("BTC-USDT", "ETH-USDT"))
SMA = {"symbols": ["BTC-USDT", "ETH-USDT"], "fast": 6, "slow": 20}


def persist_datasets(db, tmp_path):
    store = DatasetFileStore(tmp_path)
    ids = {}
    for sym, ds in DATA.items():
        store.save(ds)
        rep = validate_bars(ds.bars, ds.meta.timeframe, ds.meta.venue, ds.meta.symbol)
        ids[sym] = str(rs.register_dataset(db, ds, rep, "tester", f"{sym}.csv").id)
    db.commit()
    return store, ids


def completed_run(db, tmp_path):
    store, ids = persist_datasets(db, tmp_path)
    cfg = make_config("sma_cross", SMA, DATA, label="db-test")
    out = run_backtest(cfg, DATA, git_commit="abc123")
    row = rs.create_run(db, cfg, ids, "tester", "abc123")
    db.commit()
    rs.set_running(db, row.id)
    rs.save_result(db, row.id, out)
    return row.id, cfg, out, store


def test_dataset_registration_is_idempotent_and_versions_are_immutable(db, tmp_path):
    store, ids = persist_datasets(db, tmp_path)
    again = rs.register_dataset(
        db,
        DATA["BTC-USDT"],
        validate_bars(
            DATA["BTC-USDT"].bars, DATA["BTC-USDT"].meta.timeframe, "BINANCE", "BTC-USDT"
        ),
        "tester",
        "x.csv",
    )
    assert str(again.id) == ids["BTC-USDT"]
    rows = rs.list_datasets(db)
    assert len(rows) == 2 and rows[0]["validation_status"] == "PASS" and rows[0]["checksum"]
    assert rows[0]["label"].startswith("EXCHANGE_HISTORICAL")
    qr = rs.quality_reports(db, uuid.UUID(ids["BTC-USDT"]))
    assert len(qr) == 1 and qr[0]["status"] == "PASS"
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE historical_dataset_versions SET label = 'tampered'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM dataset_quality_reports"))
    db.rollback()


def test_full_run_round_trips_through_postgres_and_reproduces_exactly(db, tmp_path):
    run_id, cfg, out, store = completed_run(db, tmp_path)
    row = db.get(BacktestRun, run_id)
    assert row.status == "COMPLETED" and row.result_checksum == out.result_checksum
    assert row.git_commit == "abc123" and row.execution_model_version == cfg.execution.version
    assert (
        row.fee_schedule_version == cfg.fees.schedule_id
        and row.risk_policy_version == cfg.risk.version
    )
    assert row.research_grade is True and row.strategy_version_id is not None
    # every simulated artefact was persisted
    n_eq = (
        db.execute(select(PortfolioEquitySnapshot).where(PortfolioEquitySnapshot.run_id == run_id))
        .scalars()
        .all()
    )
    n_fill = db.execute(select(SimulatedFill).where(SimulatedFill.run_id == run_id)).scalars().all()
    assert len(n_eq) == len(out.result.equity) and len(n_fill) == len(out.result.fills) > 0
    assert rs.performance_json(db, run_id)["returns"]["total_net_pnl"] == format(
        out.performance.returns["total_net_pnl"], "f"
    )
    # reproduce from ONLY what is in the database + the content-addressed files
    detail = rs.run_json(db, row)
    reloaded = BacktestConfig.from_json(json.loads(json.dumps(detail["config"])))
    data = {s: store.load(cs) for s, cs in reloaded.dataset_checksums.items()}
    again = run_backtest(reloaded, data)
    assert again.result_checksum == row.result_checksum
    assert (
        detail["config_hash"] == cfg.config_hash() and detail["summary"]["research_grade"] is True
    )


def test_equity_endpoint_downsamples_but_keeps_true_drawdown_and_last_point(db, tmp_path):
    run_id, _, out, _ = completed_run(db, tmp_path)
    pts = rs.equity_points(db, run_id, max_points=50)
    assert len(pts) <= 52 and pts[-1]["ts"] == out.result.equity[-1].ts.isoformat()
    worst = max(float(p["drawdown"]) for p in pts)
    assert worst <= float(out.performance.risk["max_drawdown_pct"]) + 1e-8


def test_completed_run_and_all_result_tables_are_immutable(db, tmp_path):
    run_id, _, _, _ = completed_run(db, tmp_path)
    for stmt, msg in (
        ("UPDATE backtest_runs SET label = 'x'", "terminal and immutable"),
        ("UPDATE backtest_runs SET status = 'RUNNING'", "terminal and immutable"),
        ("DELETE FROM backtest_runs", "cannot be deleted"),
        ("UPDATE simulated_fills SET price = 1", "append-only"),
        ("DELETE FROM simulated_fills", "append-only"),
        ("UPDATE simulated_orders SET status = 'FILLED'", "append-only"),
        ("UPDATE simulated_ledger_entries SET amount = 1", "append-only"),
        ("DELETE FROM simulated_ledger_entries", "append-only"),
        ("UPDATE portfolio_equity_snapshots SET equity = 1", "append-only"),
        ("DELETE FROM portfolio_equity_snapshots", "append-only"),
        ("UPDATE performance_reports SET metrics = '{}'", "append-only"),
        ("UPDATE backtest_run_configs SET config = '{}'", "append-only"),
        ("TRUNCATE simulated_fills", "append-only"),
        ("TRUNCATE portfolio_equity_snapshots", "append-only"),
    ):
        with pytest.raises(DBAPIError, match=msg):
            db.execute(text(stmt))
        db.rollback()
    assert db.get(BacktestRun, run_id).status == "COMPLETED"  # nothing changed


def test_run_lifecycle_allows_progress_while_open_then_locks(db, tmp_path):
    store, ids = persist_datasets(db, tmp_path)
    cfg = make_config("all_cash", {}, DATA)
    row = rs.create_run(db, cfg, ids, "tester", "abc")
    db.commit()
    assert db.get(BacktestRun, row.id).status == "QUEUED"
    rs.set_running(db, row.id)
    rs.set_progress(db, row.id, 40)
    assert db.get(BacktestRun, row.id).progress_pct == 40
    rs.finish_failed(db, row.id, "CANCELLED", "cancelled by user")
    cur = db.get(BacktestRun, row.id)
    db.refresh(cur)
    assert cur.status == "CANCELLED" and cur.finished_at is not None
    with pytest.raises(DBAPIError, match="immutable"):
        db.execute(text("UPDATE backtest_runs SET progress_pct = 1"))
    db.rollback()


def test_identical_configurations_share_one_immutable_config_row(db, tmp_path):
    store, ids = persist_datasets(db, tmp_path)
    cfg = make_config("all_cash", {}, DATA)
    a = rs.create_run(db, cfg, ids, "tester", "abc")
    b = rs.create_run(db, cfg, ids, "tester", "abc")
    assert a.config_id == b.config_id and a.id != b.id


def test_recover_interrupted_marks_open_runs_failed(db, tmp_path):
    store, ids = persist_datasets(db, tmp_path)
    cfg = make_config("all_cash", {}, DATA)
    q = rs.create_run(db, cfg, ids, "tester", "abc")
    r = rs.create_run(db, cfg.replace(label="2"), ids, "tester", "abc")
    db.commit()
    rs.set_running(db, r.id)
    assert rs.recover_interrupted(db) == 2
    for rid in (q.id, r.id):
        row = db.get(BacktestRun, rid)
        db.refresh(row)
        assert row.status == "FAILED" and "restart" in row.error


def test_strategy_sync_is_idempotent_and_fingerprints_the_source(db):
    a = rs.sync_strategies(db)
    b = rs.sync_strategies(db)
    assert a == b and set(a) >= {
        "regime_grid",
        "trend_pullback",
        "passive_hold",
        "all_cash",
        "sma_cross",
    }
    from asta.db.research_models import StrategyVersion

    fps = db.execute(select(StrategyVersion.code_fingerprint)).scalars().all()
    assert len(fps) == 5 and all(len(f) == 64 for f in fps)


def test_idempotency_replay_conflict_and_scoping(db):
    h = rs.request_hash({"a": 1})
    assert rs.idempotent_lookup(db, "k1", "u1", "POST /x", h) is None
    rs.idempotent_store(db, "k1", "u1", "POST /x", h, 202, {"id": "run-1"})
    db.commit()
    assert rs.idempotent_lookup(db, "k1", "u1", "POST /x", h) == (202, {"id": "run-1"})
    with pytest.raises(rs.IdempotencyConflict):
        rs.idempotent_lookup(db, "k1", "u1", "POST /x", rs.request_hash({"a": 2}))
    assert rs.idempotent_lookup(db, "k1", "u2", "POST /x", h) is None  # keys are per user
    assert rs.idempotent_lookup(db, "k1", "u1", "POST /y", h) is None  # and per endpoint


def test_holdout_registry_and_validation_records_persist(db):
    reg = HoldoutRegistry()
    reg.consume("k", START, START + timedelta(days=5), "WF_TEST")
    reg.open_new_cycle()
    reg.consume("k", START, START + timedelta(days=9), "FINAL_HOLDOUT")
    rs.persist_registry(db, reg, 0)
    v = rs.save_validation(
        db,
        "WALK_FORWARD",
        "sma_cross",
        "t",
        {"grid": 1},
        {"pooled": {"x": 1}, "warnings": ["w"]},
        2,
        True,
        False,
        {"summary": "s"},
        "tester",
    )
    db.commit()
    back = rs.load_registry(db)
    assert back.cycle == 2 and [c.kind for c in back.consumed] == ["WF_TEST", "FINAL_HOLDOUT"]
    listed = rs.list_validations(db)
    assert (
        listed[0]["id"] == str(v.id)
        and listed[0]["warnings"] == ["w"]
        and listed[0]["oos_reused"] is True
    )
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("UPDATE research_validations SET title = 'x'"))
    db.rollback()


def test_query_helpers_cover_list_fills_risk(db, tmp_path):
    run_id, _, out, _ = completed_run(db, tmp_path)
    runs = rs.list_runs(db)
    assert runs[0]["id"] == str(run_id) and runs[0]["summary"]["net_pnl"] is not None
    assert "NOT LIVE TRADING" in runs[0]["labels"]
    f = rs.fills_json(db, run_id, limit=5)
    assert (
        f["total"] == len(out.result.fills)
        and len(f["fills"]) == min(5, f["total"])
        and "SIMULATED" in f["note"]
    )
    r = rs.risk_json(db, run_id)
    assert r["verdict"] == out.result.risk_verdict and r["limits"]["label"] == "RISK_COMPLIANT"
    assert db.query(HistoricalDatasetVersion).count() == 2
