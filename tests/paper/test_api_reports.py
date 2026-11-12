"""M2-D/E: paper API (RBAC, CSRF, idempotency, lifecycle, read models) and the daily CEO report."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select, text

from asta.core.timeutil import utcnow
from asta.db.models import MarketRulesVersion
from asta.db.paper_models import DailyPaperReport
from asta.paper import ops, reports, store
from asta.paper.collector import set_feed_state
from asta.paper.runtime import PaperRuntime
from asta.paper.states import SessionState
from tests.integration.test_api import sign_in
from tests.integration.test_research_api import client, post  # noqa: F401, F811
from tests.paper.helpers import SYMS, T0, make_cfg, make_series

# ruff: noqa: F811
pytestmark = pytest.mark.db
WARM = 300
SERIES = make_series(460)


def build_session(db, n=460, state=SessionState.RUNNING):
    cfg = make_cfg()
    s = store.create_session(db, cfg, "tester")
    for st in (SessionState.INITIALIZING, SessionState.READY, SessionState.RUNNING):
        store.transition(db, s.id, st, "tester")
    if state != SessionState.RUNNING:
        store.transition(db, s.id, state, "tester")
    db.commit()
    rt = PaperRuntime(cfg)
    for k in range(n):
        ts, bars = SERIES[k]
        rows = rt.step(k, ts, bars, {}, True, k < WARM)
        store.persist_step(db, s.id, k, ts, k < WARM, bars, {}, True, [], rows)
        if k % 50 == 49:
            db.commit()
    db.commit()
    return s.id


def seed_feeds(db, venues=("BINANCE", "OKX")):
    now = utcnow()
    for v in venues:
        set_feed_state(db, v, "CONNECTED", "LIVE", "", "c1", now=now)
        for sym in SYMS:
            db.add(
                MarketRulesVersion(
                    venue=v,
                    symbol=sym,
                    tick_size=D("0.01"),
                    step_size=D("0.001"),
                    min_qty=D("0.001"),
                    min_notional=D("5"),
                    trading=True,
                    source="LIVE",
                    retrieved_at=now,
                )
            )
    db.execute(text("update market_feed_connections set last_event_at = now()"))
    db.commit()


def login(client, db, name, role):
    client.cookies.clear()
    sign_in(client, db, name, role)


# ------------------------------------------------------------------ RBAC / CSRF / idempotency
PAPER_GETS = [
    "/v1/paper/sessions",
    "/v1/market-feeds/status",
    "/v1/paper/health",
    "/v1/reports/daily",
]


def test_every_paper_read_requires_authentication_and_viewers_may_read(client, db):
    for path in PAPER_GETS:
        assert client.get(path).status_code == 401
    login(client, db, "v1", "viewer")
    for path in PAPER_GETS:
        r = client.get(path)
        assert r.status_code == 200, path
    assert "PAPER TRADING" in client.get("/v1/paper/sessions").json()["labels"][0]


def test_state_changing_routes_enforce_rbac_csrf_and_idempotency_independently_of_the_ui(
    client, db
):
    seed_feeds(db)
    sid = uuid.uuid4()
    login(client, db, "v1", "viewer")
    for path in (
        "/v1/paper/sessions",
        f"/v1/paper/sessions/{sid}/start",
        f"/v1/paper/sessions/{sid}/pause",
        f"/v1/paper/sessions/{sid}/resume",
        f"/v1/paper/sessions/{sid}/stop",
        f"/v1/paper/sessions/{sid}/rearm",
    ):
        body = {"name": "x"} if path.endswith("sessions") else {}
        assert post(client, path, body).status_code == 403, path  # viewer: RBAC
    login(client, db, "r1", "reviewer")
    assert post(client, "/v1/paper/sessions", {"name": "x"}).status_code == 403
    login(client, db, "op1", "operator")
    nocsrf = post(client, "/v1/paper/sessions", {"name": "x"}, token=False)
    assert nocsrf.status_code in (401, 403)
    no_key = client.post(
        "/v1/paper/sessions",
        json={"name": "x"},
        headers={"X-CSRF-Token": client.get("/v1/auth/me").json()["csrf_token"]},
    )
    assert no_key.status_code == 400  # Idempotency-Key is mandatory


def test_create_validates_wallets_and_is_idempotent_and_audited(client, db):
    seed_feeds(db)
    login(client, db, "op1", "operator")
    assert (
        post(
            client,
            "/v1/paper/sessions",
            {"name": "s", "binance_wallet": "600", "okx_wallet": "600"},
        ).status_code
        == 422
    )
    assert (
        post(
            client, "/v1/paper/sessions", {"name": "s", "strategies": ["passive_hold"]}
        ).status_code
        == 422
    )
    assert post(client, "/v1/paper/sessions", {"name": "bad/name?"}).status_code == 422
    r = post(client, "/v1/paper/sessions", {"name": "s1"}, key="create-key-1")
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["state"] == "INITIALIZING" and j["wallets"] == {"BINANCE": "500", "OKX": "500"}
    assert set(j["enabled_venues"]) == {"BINANCE", "OKX"} and j["starting_equity"] == "1000"
    again = post(client, "/v1/paper/sessions", {"name": "s1"}, key="create-key-1")
    assert again.headers.get("Idempotent-Replay") == "true" and again.json()["id"] == j["id"]
    assert len(client.get("/v1/paper/sessions").json()["sessions"]) == 1
    audit = (
        db.execute(text("select action from audit_events where action like 'paper.%'"))
        .scalars()
        .all()
    )
    assert "paper.session.create" in audit
    mismatch = post(client, "/v1/paper/sessions", {"name": "other"}, key="create-key-1")
    assert mismatch.status_code == 409  # same key, different body


def test_no_session_without_a_verified_live_feed_and_a_dead_venue_keeps_cash_idle(client, db):
    login(client, db, "op1", "operator")
    r = post(client, "/v1/paper/sessions", {"name": "s"})
    assert r.status_code == 409 and "verified live public market-data feed" in r.json()["detail"]
    seed_feeds(db, ("BINANCE",))  # OKX never connected
    set_feed_state(db, "OKX", "DISABLED", "DISABLED", "ConnectError: blocked", "c")
    db.commit()
    r = post(client, "/v1/paper/sessions", {"name": "s"})
    assert r.status_code == 201
    j = r.json()
    assert j["enabled_venues"] == ["BINANCE"] and j["idle_cash_venues"] == ["OKX"]
    assert j["wallets"]["OKX"] == "500"  # not reallocated to the other venue


def test_lifecycle_endpoints_follow_the_state_machine(client, db):
    seed_feeds(db)
    login(client, db, "op1", "operator")
    sid = post(client, "/v1/paper/sessions", {"name": "s"}).json()["id"]
    base = f"/v1/paper/sessions/{sid}"
    assert post(client, f"{base}/start").status_code == 409  # INITIALIZING cannot start
    assert post(client, f"{base}/pause").status_code == 409
    store.transition(db, uuid.UUID(sid), SessionState.READY, "system")
    db.commit()
    assert post(client, f"{base}/start").json()["state"] == "RUNNING"
    assert post(client, f"{base}/pause").json()["state"] == "PAUSED"
    assert post(client, f"{base}/rearm").status_code == 409  # not risk-halted
    assert post(client, f"{base}/stop", {"reason": "done"}).json()["state"] == "STOPPED"
    assert post(client, f"{base}/start").status_code == 409  # terminal
    assert post(client, f"{base}/resume").status_code == 409
    states = (
        db.execute(text("select to_state from paper_session_states order by id")).scalars().all()
    )
    assert states == ["CREATED", "INITIALIZING", "READY", "RUNNING", "PAUSED", "STOPPED"]
    assert client.get("/v1/paper/sessions/" + str(uuid.uuid4())).status_code == 404


def test_resume_is_validated_and_refused_when_data_is_stale_or_incidents_are_open(client, db):
    seed_feeds(db)
    sid = build_session(db, 320, SessionState.PAUSED)
    login(client, db, "op1", "operator")
    ops.open_incident(db, ops.RECONCILIATION_MISMATCH, ops.SEV_CRIT, "manual test", f"x:{sid}", sid)
    db.commit()
    r = post(client, f"/v1/paper/sessions/{sid}/resume")
    assert r.status_code == 409 and "critical incident" in r.json()["detail"]
    ops.resolve_incident(db, f"x:{sid}")
    db.execute(text("update market_feed_connections set last_event_at = now() - interval '1 hour'"))
    db.commit()
    r = post(client, f"/v1/paper/sessions/{sid}/resume")
    assert r.status_code == 409 and "not fresh" in r.json()["detail"]
    db.execute(text("update market_feed_connections set last_event_at = now()"))
    db.commit()
    assert post(client, f"/v1/paper/sessions/{sid}/resume").json()["state"] == "RUNNING"


# ------------------------------------------------------------------ read models + report
@pytest.fixture
def populated(client, db):
    seed_feeds(db)
    sid = build_session(db)
    return sid


def test_read_models_are_labelled_consistent_and_never_expose_execution(client, db, populated):
    sid = populated
    login(client, db, "v1", "viewer")
    base = f"/v1/paper/sessions/{sid}"
    s = client.get(base).json()
    pf = client.get(f"{base}/portfolio").json()
    assert pf["labels"][0].startswith("PAPER TRADING")
    assert D(pf["totals"]["equity"]) == D(s["equity"])  # portfolio == last equity snapshot
    assert set(pf["accounts"]) >= {"BINANCE:regime_grid", "OKX:sma_cross"}
    assert not any(
        k.startswith("BENCH") for k in pf["accounts"]
    )  # shadow accounts are not portfolio
    orders = client.get(f"{base}/orders?limit=50").json()
    assert orders["orders"] and all("SIMULATED" in o["labels"] for o in orders["orders"])
    sig = client.get(f"{base}/signals?accepted=false&limit=20").json()["signals"]
    assert sig and all(not x["accepted"] and x["reason"] for x in sig)
    risk = client.get(f"{base}/risk").json()
    assert D(risk["policy"]["max_asset_pct"]) == D("0.1") and risk["daily_auto_reset"] is False
    assert set(risk["market_health"]) == {f"{v}:{x}" for v in ("BINANCE", "OKX") for x in SYMS}
    perf = client.get(f"{base}/performance").json()
    assert perf["points"] and {"constrained_hold", "unconstrained_hold", "all_cash"} <= set(
        perf["points"][0]
    )
    assert all(x["deployment_candidate"] is False for x in perf["strategies"])
    assert client.get(f"{base}/incidents").status_code == 200
    h = client.get("/v1/paper/health").json()
    assert (
        h["worker_online"] is False and h["queue"]["backlog"] == 0
    )  # no worker heartbeat in this test
    assert client.get("/v1/market-feeds/status").json()["labels"][0] == "PUBLIC MARKET DATA ONLY"


def test_daily_report_is_reproducible_immutable_and_reconciles_with_the_ledger(
    client, db, populated
):
    sid = populated
    day = datetime(2026, 1, 15, tzinfo=UTC)
    row, created = reports.store_report(db, sid, day)
    db.commit()
    assert created and reports.verify_report(db, row)
    b = row.body
    f = b["financial"]
    assert D(f["closing_equity"]) - D(f["opening_equity"]) == D(f["net_pnl"])
    assert D(f["net_pnl"]) == D(f["realized_pnl"]) + D(f["unrealized_pnl_movement"])
    assert (
        b["window"]["start"] == "2026-01-15T00:00:00+00:00"
        and b["kind"] == "OBSERVED_PAPER_SIMULATION"
    )
    assert b["trading"]["strategies_are_deployment_candidates"] is False
    assert b["research_conclusion"]["ai_commentary"] is None
    assert b["research_conclusion"]["market_data"].startswith(
        "NO_STORED_OBSERVATIONS"
    )  # fixtures say so
    assert b["operations"]["steps_processed"] == 24 and b["operations"]["uptime_fraction"] == "1"
    assert set(b["trading"]["benchmarks"]) == {
        "BENCH:all_cash",
        "BENCH:hold_constrained",
        "BENCH:hold_unconstrained",
    }
    assert b["trading"]["benchmarks"]["BENCH:all_cash"]["pnl"] == "0"
    # the strategy / venue contributions add up to the net P&L
    assert sum(D(v) for v in f["venue_contribution"].values()) == D(f["net_pnl"])
    # a second request creates nothing new; the stored body is immutable
    again, created2 = reports.store_report(db, sid, day)
    assert not created2 and again.content_hash == row.content_hash
    assert db.execute(select(DailyPaperReport)).scalars().all().__len__() == 1
    with pytest.raises(Exception):  # noqa: B017
        db.execute(text("update daily_paper_reports set body = '{}'::jsonb"))
    db.rollback()
    # corrupting the underlying ledger makes the recomputed report differ: tamper is detectable
    db.execute(text("SET LOCAL session_replication_role = replica"))
    db.execute(
        text(
            "update paper_equity_snapshots set equity = equity + 1 where ts > '2026-01-15' and ts <= '2026-01-16'"
        )
    )
    db.commit()
    assert not reports.verify_report(db, db.execute(select(DailyPaperReport)).scalar_one())


def test_report_api_requires_a_complete_day_and_a_clean_reconciliation(client, db, populated):
    sid = populated
    login(client, db, "op1", "operator")
    today = utcnow().date().isoformat()
    assert (
        post(client, "/v1/reports/daily", {"session_id": str(sid), "day": today}).status_code == 409
    )
    r = post(client, "/v1/reports/daily", {"session_id": str(sid), "day": "2026-01-15"})
    assert r.status_code == 201 and r.json()["created"] is True
    r2 = post(client, "/v1/reports/daily", {"session_id": str(sid), "day": "2026-01-15"})
    assert r2.json()["created"] is False and r2.json()["content_hash"] == r.json()["content_hash"]
    got = client.get(f"/v1/reports/daily?session_id={sid}&day=2026-01-15").json()
    assert got["reports"][0]["reproducible"] is True
    # a reconciliation failure blocks reporting
    db.execute(text("SET LOCAL session_replication_role = replica"))
    db.execute(
        text(
            "update paper_ledger_entries set amount = amount + 1 where kind='INITIAL_CASH' and id = (select min(id) from paper_ledger_entries)"
        )
    )
    db.commit()
    bad = post(client, "/v1/reports/daily", {"session_id": str(sid), "day": "2026-01-16"})
    assert bad.status_code == 409 and "reconciliation failed" in bad.json()["detail"]


def test_rearm_refuses_a_latched_drawdown_and_a_same_day_daily_trip(client, db, populated):
    sid = populated
    login(client, db, "op1", "operator")
    store.transition(db, sid, SessionState.RISK_HALTED, "system", "test")
    db.execute(
        text(
            "insert into paper_risk_events (session_id, seq, ts, kind, detail, equity) values (:s, 9001, now(), 'DAILY_LOSS_TRIP', 'x', 970)"
        ),
        {"s": str(sid)},
    )
    db.commit()
    r = post(client, f"/v1/paper/sessions/{sid}/rearm")
    assert r.status_code == 409 and "later UTC day" in r.json()["detail"]
    db.execute(
        text(
            "insert into paper_risk_events (session_id, seq, ts, kind, detail, equity) values (:s, 9002, now(), 'DRAWDOWN_HALT', 'x', 900)"
        ),
        {"s": str(sid)},
    )
    db.commit()
    r = post(client, f"/v1/paper/sessions/{sid}/rearm")
    assert r.status_code == 409 and "drawdown halt" in r.json()["detail"]


_ = (timedelta, T0, MarketRulesVersion)
