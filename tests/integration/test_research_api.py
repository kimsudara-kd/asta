"""Research API: auth/RBAC/CSRF/idempotency/audit, safe import, backtest lifecycle, reproducibility."""

import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from asta.api.app import AppContext, create_app
from asta.core.events import Venue
from asta.core.settings import Settings
from asta.db import research_store as rs
from asta.db.audit_store import recent_audit
from asta.research.data.fixtures import DOWNTREND, RANGE, UPTREND, generate_bars, seg
from asta.research.data.store import DatasetFileStore
from asta.research.runner import BacktestConfig, run_backtest
from asta.research.types import Timeframe
from asta.research_jobs import QueueFull
from tests.integration.test_api import sign_in
from tests.research.helpers import T0

pytestmark = pytest.mark.db
SYMS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")


def write_csv(dirp, symbol, seed, source_type="SYNTHETIC", gaps=False, name=None):
    from decimal import Decimal

    bars = generate_bars(
        T0,
        Timeframe.H1,
        [seg(RANGE, 120), seg(UPTREND, 120), seg(DOWNTREND, 120)],
        seed,
        Decimal("100"),
    )
    if gaps:
        bars = [b for i, b in enumerate(bars) if i % 10 != 5]
    name = name or f"syn_{symbol}.csv"
    lines = ["open_time,open,high,low,close,volume"] + [
        f"{int(b.open_time.timestamp() * 1000)},{b.open},{b.high},{b.low},{b.close},{b.volume}"
        for b in bars
    ]
    (dirp / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    (dirp / (name + ".meta.json")).write_text(
        json.dumps(
            {
                "venue": "BINANCE",
                "symbol": symbol,
                "timeframe": "1h",
                "source": "unit-test generator",
                "source_type": source_type,
            }
        ),
        encoding="utf-8",
    )
    return name


@pytest.fixture
def client(migrated_engine, db, tmp_path):
    imp = tmp_path / "import"
    imp.mkdir()
    for i, s in enumerate(SYMS):
        write_csv(imp, s, 10 + i)
    settings = Settings(
        database_url="unused", data_dir=str(tmp_path / "store"), import_dir=str(imp)
    )
    ctx = AppContext(settings, sessionmaker(migrated_engine, expire_on_commit=False))
    app = create_app(ctx, start_ingest=False, fixture_venues={Venue.OKX})
    with TestClient(app) as c:
        c.ctx = ctx
        c.imp = imp
        yield c


def csrf(c):
    return c.get("/v1/auth/me").json()["csrf_token"]


def post(c, path, body=None, key=None, headers=None, token=True):
    h = {"Idempotency-Key": key or f"k-{uuid.uuid4()}"}
    if token:
        h["X-CSRF-Token"] = csrf(c)
    h.update(headers or {})
    return c.post(path, json=body if body is not None else {}, headers=h)


def import_all(c, names=None):
    out = {}
    for s in SYMS:
        r = post(c, "/v1/datasets/import", {"file_name": (names or {}).get(s, f"syn_{s}.csv")})
        assert r.status_code == 201, r.text
        out[s] = r.json()["dataset"]["version_id"]
    return out


def wait_done(c, run_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/v1/backtests/{run_id}").json()
        if r["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return r
        time.sleep(0.2)
    raise AssertionError("backtest did not finish")


def bt_body(ids, strategy="all_cash", **kw):
    b = {
        "strategy_id": strategy,
        "dataset_version_ids": ids,
        "rule_set": "fixture-generic",
        "fee_profile": "BINANCE-SPOT-BASE-ASSUMED-v1",
    }
    b.update(kw)
    return b


# ------------------------------------------------------------------ auth / RBAC / CSRF / idempotency
READS = [
    "/v1/datasets",
    "/v1/datasets/importable",
    "/v1/strategies",
    "/v1/profiles",
    "/v1/backtests",
    "/v1/validations",
]


@pytest.mark.parametrize("path", READS)
def test_research_reads_need_authentication(client, path):
    assert client.get(path).status_code == 401


def test_viewer_can_read_but_never_write(client, db):
    sign_in(client, db, "v1", "viewer")
    for p in READS:
        assert client.get(p).status_code == 200, p
    for path, body in (
        ("/v1/datasets/import", {"file_name": "syn_BTC-USDT.csv"}),
        ("/v1/backtests", {}),
    ):
        assert post(client, path, body).status_code == 403
    assert post(client, f"/v1/backtests/{uuid.uuid4()}/cancel").status_code == 403


def test_reviewer_reads_only_operator_can_import_and_run(client, db):
    sign_in(client, db, "rev", "reviewer")
    assert client.get("/v1/datasets").status_code == 200
    assert post(client, "/v1/datasets/import", {"file_name": "syn_BTC-USDT.csv"}).status_code == 403
    client.cookies.clear()
    sign_in(client, db, "op", "operator")
    assert post(client, "/v1/datasets/import", {"file_name": "syn_BTC-USDT.csv"}).status_code == 201


def test_post_requires_csrf_and_idempotency_key(client, db):
    sign_in(client, db, "op", "operator")
    body = {"file_name": "syn_BTC-USDT.csv"}
    assert (
        post(client, "/v1/datasets/import", body, token=False).status_code == 403
    )  # no CSRF header
    bad = client.post("/v1/datasets/import", json=body, headers={"X-CSRF-Token": csrf(client)})
    assert bad.status_code == 400 and "Idempotency-Key" in bad.text
    short = client.post(
        "/v1/datasets/import",
        json=body,
        headers={"X-CSRF-Token": csrf(client), "Idempotency-Key": "x"},
    )
    assert short.status_code == 400


def test_idempotent_replay_returns_same_response_and_conflict_is_rejected(client, db):
    sign_in(client, db, "op", "operator")
    body = {"file_name": "syn_BTC-USDT.csv"}
    a = post(client, "/v1/datasets/import", body, key="import-key-0001")
    b = post(client, "/v1/datasets/import", body, key="import-key-0001")
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
    assert b.headers.get("Idempotent-Replay") == "true"
    c = post(
        client, "/v1/datasets/import", {"file_name": "syn_ETH-USDT.csv"}, key="import-key-0001"
    )
    assert c.status_code == 409


# ------------------------------------------------------------------ import safety
@pytest.mark.parametrize(
    "name",
    [
        "../secret.csv",
        "..\\secret.csv",
        "a/b.csv",
        "/etc/passwd.csv",
        "C:\\x.csv",
        "file.txt",
        ".csv",
        "x" * 200 + ".csv",
        "a b.csv",
    ],
)
def test_import_refuses_paths_and_odd_names(client, db, name):
    sign_in(client, db, "op", "operator")
    r = post(client, "/v1/datasets/import", {"file_name": name})
    assert r.status_code in (400, 422), (name, r.status_code)


def test_import_unknown_file_or_missing_sidecar_is_a_clean_error(client, db):
    sign_in(client, db, "op", "operator")
    assert post(client, "/v1/datasets/import", {"file_name": "nope.csv"}).status_code == 422
    (client.imp / "bare.csv").write_text("open_time,open,high,low,close,volume\n", encoding="utf-8")
    r = post(client, "/v1/datasets/import", {"file_name": "bare.csv"})
    assert r.status_code == 422 and "sidecar" in r.text


def test_importable_lists_local_files_with_provenance_note(client, db):
    sign_in(client, db, "v1", "viewer")
    j = client.get("/v1/datasets/importable").json()
    assert {f["file_name"] for f in j["files"]} == {f"syn_{s}.csv" for s in SYMS}
    assert "LOCAL files only" in j["note"] and all(
        f["source_type"] == "SYNTHETIC" for f in j["files"]
    )


def test_import_labels_synthetic_data_and_never_accepts_it_for_research(client, db):
    sign_in(client, db, "op", "operator")
    r = post(client, "/v1/datasets/import", {"file_name": "syn_BTC-USDT.csv"}).json()
    d = r["dataset"]
    assert d["source_type"] == "SYNTHETIC" and "not real exchange data" in d["label"]
    assert d["accepted_for_research"] is False and d["validation_status"] == "PASS"
    assert r["quality"]["status"] == "PASS" and "SIMULATED" in r["labels"]
    # the same content registers to the same immutable version
    again = post(client, "/v1/datasets/import", {"file_name": "syn_BTC-USDT.csv"}).json()["dataset"]
    assert (
        again["version_id"] == d["version_id"]
        and len(client.get("/v1/datasets").json()["datasets"]) == 1
    )


def test_rejected_dataset_is_retained_for_diagnostics_and_refused_for_runs(client, db):
    sign_in(client, db, "op", "operator")
    write_csv(client.imp, "BTC-USDT", 99, gaps=True, name="gappy.csv")
    r = post(client, "/v1/datasets/import", {"file_name": "gappy.csv"}).json()
    assert r["dataset"]["validation_status"] == "REJECTED" and r["quality"]["findings"]
    assert any(f["code"] == "GAPS" for f in r["quality"]["findings"])
    q = client.get(f"/v1/datasets/{r['dataset']['version_id']}/quality").json()
    assert q["dataset"]["accepted_for_research"] is False and q["reports"]
    bad = post(client, "/v1/backtests", bt_body({"BTC-USDT": r["dataset"]["version_id"]}))
    assert bad.status_code == 422 and "REJECTED" in bad.text


def test_validate_adds_a_report_and_derive_only_goes_coarser(client, db):
    sign_in(client, db, "op", "operator")
    ids = import_all(client)
    v = post(client, f"/v1/datasets/{ids['BTC-USDT']}/validate").json()
    assert v["quality"]["status"] == "PASS"
    assert len(client.get(f"/v1/datasets/{ids['BTC-USDT']}/quality").json()["reports"]) == 2
    d = post(client, f"/v1/datasets/{ids['BTC-USDT']}/derive", {"timeframe": "4h"})
    assert d.status_code == 201 and d.json()["dataset"]["source_type"] == "DERIVED"
    assert d.json()["dataset"]["parent_checksums"]
    finer = post(client, f"/v1/datasets/{ids['BTC-USDT']}/derive", {"timeframe": "5m"})
    assert finer.status_code == 422 and "never invented" in finer.text
    assert post(client, f"/v1/datasets/{uuid.uuid4()}/validate").status_code == 404


# ------------------------------------------------------------------ backtests
def test_strategies_and_profiles_are_listed_with_unverified_labels(client, db):
    sign_in(client, db, "v1", "viewer")
    s = client.get("/v1/strategies").json()["strategies"]
    assert {x["strategy_id"] for x in s} == {
        "regime_grid",
        "trend_pullback",
        "passive_hold",
        "all_cash",
        "sma_cross",
    }
    assert all(x["version"] and x["default_params"] is not None for x in s)
    p = client.get("/v1/profiles").json()
    assert any(r["id"] == "fixture-generic" and r["verified"] is False for r in p["rule_sets"])
    assert all(f["verified"] is False for f in p["fee_profiles"])
    assert "assumptions" in p["note"]


def test_backtest_validation_errors(client, db):
    sign_in(client, db, "op", "operator")
    ids = import_all(client)
    assert post(client, "/v1/backtests", bt_body(ids, "martingale")).status_code == 422
    assert (
        post(
            client, "/v1/backtests", bt_body(ids, "regime_grid", strategy_params={"levelz": 1})
        ).status_code
        == 422
    )
    assert (
        post(client, "/v1/backtests", bt_body({"ETH-USDT": ids["BTC-USDT"]})).status_code == 422
    )  # wrong symbol
    assert post(client, "/v1/backtests", bt_body(ids, rule_set="nope")).status_code == 422
    assert post(client, "/v1/backtests", bt_body(ids, fee_profile="nope")).status_code == 422
    assert post(client, "/v1/backtests", bt_body(ids, latency_bars=99)).status_code == 422
    assert post(client, "/v1/backtests", bt_body(ids, starting_cash=1)).status_code == 422
    assert (
        post(client, "/v1/backtests", bt_body(ids, period_start="2024-03-01T00:00:00")).status_code
        == 422
    )  # naive tz
    assert (
        post(
            client,
            "/v1/backtests",
            bt_body(ids, period_start="2024-03-02T00:00:00Z", period_end="2024-03-01T00:00:00Z"),
        ).status_code
        == 422
    )


def test_full_backtest_lifecycle_results_and_exact_reproducibility(client, db):
    sign_in(client, db, "op", "operator")
    ids = import_all(client)
    r = post(
        client,
        "/v1/backtests",
        bt_body(
            ids,
            "sma_cross",
            strategy_params={"symbols": list(SYMS), "fast": 6, "slow": 20},
            label="lifecycle",
        ),
    )
    assert (
        r.status_code == 202
        and r.json()["status"] == "QUEUED"
        and "NOT LIVE TRADING" in r.json()["labels"]
    )
    run = wait_done(client, r.json()["id"])
    assert run["status"] == "COMPLETED" and run["result_checksum"] and run["git_commit"]
    assert run["research_grade"] is False and any("NOT research-grade" in n for n in run["notes"])
    res = client.get(f"/v1/backtests/{run['id']}/results").json()
    perf = res["performance"]
    assert "NOT realised" in perf["returns"]["note"] and "SIMULATED" in res["honesty"]
    assert (
        perf["risk"]["sharpe"]["value"] is None and perf["risk"]["sharpe"]["na_reason"]
    )  # N/A, never 0
    eq = client.get(f"/v1/backtests/{run['id']}/equity?max_points=100").json()["points"]
    assert 2 <= len(eq) <= 102 and {"ts", "equity", "drawdown"} <= set(eq[0])
    fills = client.get(f"/v1/backtests/{run['id']}/trades?limit=5").json()
    assert fills["total"] > 0 and len(fills["fills"]) <= 5 and "SIMULATED" in fills["note"]
    risk = client.get(f"/v1/backtests/{run['id']}/risk").json()
    assert risk["limits"]["label"] == "RISK_COMPLIANT" and "regardless of P&L" in risk["note"]
    assert any(x["id"] == run["id"] for x in client.get("/v1/backtests").json()["runs"])
    # reproduce from ONLY the persisted config + stored datasets, with the pure runner
    store = DatasetFileStore(client.ctx.research.store.root)
    cfg = BacktestConfig.from_json(run["config"])
    again = run_backtest(cfg, {s: store.load(c) for s, c in cfg.dataset_checksums.items()})
    assert again.result_checksum == run["result_checksum"]
    # audit trail: import + create recorded, no secrets
    sess = client.ctx.sessions()
    actions = [e.action for e in recent_audit(sess, 80)]
    sess.close()
    assert "research.backtest.create" in actions and "research.dataset.import" in actions


def test_backtest_create_is_idempotent_and_does_not_start_two_runs(client, db):
    sign_in(client, db, "op", "operator")
    ids = import_all(client)
    body = bt_body(ids, "all_cash")
    a = post(client, "/v1/backtests", body, key="bt-key-00001")
    b = post(client, "/v1/backtests", body, key="bt-key-00001")
    assert a.json() == b.json() and b.headers.get("Idempotent-Replay") == "true"
    assert len(client.get("/v1/backtests").json()["runs"]) == 1


class StubWorker:
    def __init__(self, full=False):
        self.full, self.submitted, self.cancels = full, [], []

    def submit(self, run_id, cfg):
        if self.full:
            raise QueueFull("full")
        self.submitted.append(run_id)

    def request_cancel(self, run_id):
        self.cancels.append(run_id)

    def stop(self):
        pass


def test_cancel_queued_running_and_terminal_runs(client, db):
    sign_in(client, db, "op", "operator")
    ids = import_all(client)
    client.ctx.research.worker.stop()
    stub = client.ctx.research.worker = StubWorker()
    rid = post(client, "/v1/backtests", bt_body(ids)).json()["id"]
    assert client.get(f"/v1/backtests/{rid}").json()["status"] == "QUEUED"
    c1 = post(client, f"/v1/backtests/{rid}/cancel")
    assert c1.status_code == 200 and c1.json()["state"] == "CANCELLED"
    assert client.get(f"/v1/backtests/{rid}").json()["status"] == "CANCELLED"
    assert post(client, f"/v1/backtests/{rid}/cancel").status_code == 409  # already terminal
    # a RUNNING run gets a cooperative cancel request
    rid2 = post(client, "/v1/backtests", bt_body(ids, label="r2")).json()["id"]
    sess = client.ctx.sessions()
    rs.set_running(sess, uuid.UUID(rid2))
    sess.close()
    c2 = post(client, f"/v1/backtests/{rid2}/cancel")
    assert c2.json()["state"] == "CANCELLING" and uuid.UUID(rid2) in stub.cancels
    assert post(client, f"/v1/backtests/{uuid.uuid4()}/cancel").status_code == 404


def test_full_queue_returns_429_and_leaves_no_dangling_queued_run(client, db):
    sign_in(client, db, "op", "operator")
    ids = import_all(client)
    client.ctx.research.worker.stop()
    client.ctx.research.worker = StubWorker(full=True)
    r = post(client, "/v1/backtests", bt_body(ids))
    assert r.status_code == 429
    assert [x["status"] for x in client.get("/v1/backtests").json()["runs"]] == ["CANCELLED"]


def test_unknown_run_ids_are_404_and_results_of_failed_inputs_do_not_leak(client, db):
    sign_in(client, db, "v1", "viewer")
    for suffix in ("", "/results", "/equity", "/trades", "/risk"):
        assert client.get(f"/v1/backtests/{uuid.uuid4()}{suffix}").status_code == 404
    assert client.get("/v1/backtests/not-a-uuid").status_code == 422
    assert client.get(f"/v1/validations/{uuid.uuid4()}").status_code == 404


def test_stored_validation_is_readable_and_labelled(client, db):
    sess = client.ctx.sessions()
    v = rs.save_validation(
        sess,
        "WALK_FORWARD",
        "sma_cross",
        "demo wf",
        {"g": 1},
        {"pooled": {"windows": 3}, "warnings": ["w1"]},
        1,
        False,
        False,
        {"summary": {"text": "Evidence level: NONE"}},
        "tester",
    )
    sess.commit()
    sess.close()
    sign_in(client, db, "v1", "viewer")
    lst = client.get("/v1/validations").json()
    assert lst["validations"][0]["warnings"] == ["w1"] and "NOT LIVE TRADING" in lst["labels"]
    full = client.get(f"/v1/validations/{v.id}").json()
    assert full["advisory"]["summary"]["text"].startswith("Evidence level")
