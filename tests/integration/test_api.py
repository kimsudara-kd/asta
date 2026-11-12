import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from asta.api.app import AppContext, create_app
from asta.auth import service as auth
from asta.auth.rbac import mfa_required
from asta.core.events import Venue
from asta.core.settings import Settings

pytestmark = pytest.mark.db
PW = "correct-horse-battery"


@pytest.fixture
def client(migrated_engine, db):
    settings = Settings(database_url="unused")
    ctx = AppContext(settings, sessionmaker(migrated_engine, expire_on_commit=False))
    app = create_app(ctx, start_ingest=False, fixture_venues={Venue.OKX})
    with TestClient(app) as c:
        c.ctx = ctx
        yield c


def seed(db, name, role):
    u = auth.create_user(db, name, PW, role)
    secret = None
    if mfa_required(role):
        secret = pyotp.parse_uri(auth.enroll_mfa(db, u)).secret
    db.commit()
    return secret


def sign_in(client, db, name, role):
    secret = seed(db, name, role)
    r = client.post("/v1/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 200
    if secret:
        r = client.post("/v1/auth/mfa", json={"code": pyotp.TOTP(secret).now()})
        assert r.status_code == 200


def test_health_and_readiness_public(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/readiness").json()["database"] == "ok"


@pytest.mark.parametrize(
    "path",
    ["/v1/overview", "/v1/venues", "/v1/markets", "/v1/risk", "/v1/audit", "/v1/incidents"],
)
def test_unauthenticated_denied(client, path):
    assert client.get(path).status_code == 401


def test_password_only_session_for_mfa_role_is_denied(client, db):
    seed(db, "op1", "operator")
    client.post("/v1/auth/login", json={"username": "op1", "password": PW})
    assert client.get("/v1/overview").status_code == 401  # MFA not completed


def test_cookie_flags(client, db):
    seed(db, "v1", "viewer")
    r = client.post("/v1/auth/login", json={"username": "v1", "password": PW})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_viewer_sees_status_not_audit(client, db):
    sign_in(client, db, "v1", "viewer")
    assert client.get("/v1/overview").status_code == 200
    assert client.get("/v1/audit").status_code == 403


def test_reviewer_sees_audit_and_chain_is_ok(client, db):
    sign_in(client, db, "rev", "reviewer")
    r = client.get("/v1/audit")
    assert r.status_code == 200 and r.json()["chain_ok"] is True and r.json()["events"]


def test_overview_content_labels_fixture_and_denies_execution(client, db):
    sign_in(client, db, "v1", "viewer")
    o = client.get("/v1/overview").json()
    assert "NO TRADING" in o["banner"] and o["environment"] == "PAPER"
    assert o["risk"]["execution_enabled"] is False and o["risk"]["live_trading_enabled"] is False
    assert o["risk"]["gateway_probe"]["decision"] == "DENY"
    states = {v["venue"]: v["state"] for v in o["venues"]}
    assert states["OKX"] == "FIXTURE"
    # Binance not started in this test: must NOT claim to be live
    assert states["BINANCE"] in {"CONNECTING", "STALE", "UNREACHABLE"}
    okx = next(v for v in o["venues"] if v["venue"] == "OKX")
    assert okx["clock_ok"] is None and okx["clock_offset_ms"] is None  # n/a for fixtures
    assert next(v for v in o["venues"] if v["venue"] == "BINANCE")["clock_ok"] is False  # unknown


def test_markets_fixture_rows_are_labelled(client, db):
    import time

    sign_in(client, db, "v1", "viewer")
    time.sleep(0.3)
    rows = client.get("/v1/markets").json()
    okx = [r for r in rows if r["venue"] == "OKX"]
    assert okx and all(r["source"] == "FIXTURE" for r in okx if r["bid"])
    binance = [r for r in rows if r["venue"] == "BINANCE"]
    assert all(r["bid"] is None and r["stale"] for r in binance)  # unknown => stale


def test_risk_halts_when_data_missing(client, db):
    sign_in(client, db, "v1", "viewer")
    r = client.get("/v1/risk").json()
    assert r["halt_new_buys"] and "STALE_MARKET_DATA" in r["halt_reasons"]


def test_logout_requires_csrf_and_revokes(client, db):
    sign_in(client, db, "v1", "viewer")
    csrf = client.get("/v1/auth/me").json()["csrf_token"]
    assert client.post("/v1/auth/logout").status_code == 403
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.get("/v1/overview").status_code == 401


def test_security_headers(client):
    h = client.get("/health").headers
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["cache-control"] == "no-store"


# M1 (ADR-0008): the OFFLINE research surface is enumerated explicitly. `.../trades` lists SIMULATED
# fills of a backtest; it is the only path allowed to contain a banned word, and only this
# exact path.
RESEARCH_ALLOWED_PATHS = {"/v1/backtests/{run_id}/trades"}
RESEARCH_MUTATING = {
    ("POST", "/v1/datasets/import"),
    ("POST", "/v1/datasets/{version_id}/validate"),
    ("POST", "/v1/datasets/{version_id}/derive"),
    ("POST", "/v1/backtests"),
    ("POST", "/v1/backtests/{run_id}/cancel"),
    ("POST", "/v1/validations/import"),  # M1-R1: persists completed offline study files
}


# M2: the PAPER-TRADING surface is enumerated explicitly as well. `.../orders` lists SIMULATED paper
# orders stored in local tables; it is the only paper path allowed to contain a banned word. Every
# mutating paper route acts on LOCAL simulated state only (RBAC + CSRF + idempotency + audit).
PAPER_ALLOWED_PATHS = {"/v1/paper/sessions/{sid}/orders"}
PAPER_MUTATING = {
    ("POST", "/v1/paper/sessions"),
    ("POST", "/v1/paper/sessions/{sid}/start"),
    ("POST", "/v1/paper/sessions/{sid}/pause"),
    ("POST", "/v1/paper/sessions/{sid}/resume"),
    ("POST", "/v1/paper/sessions/{sid}/stop"),
    ("POST", "/v1/paper/sessions/{sid}/rearm"),
    ("POST", "/v1/paper/alerts/{alert_id}/ack"),
    ("POST", "/v1/reports/daily"),
}


def test_no_order_or_funds_routes_registered(client):
    """Live-lock proof: the route table has no trading/transfer surface beyond the enumerated
    offline-research paths (simulated results only)."""
    paths = {r.path.lower() for r in client.app.routes}
    banned = ("order", "trade", "withdraw", "transfer", "borrow", "margin", "live", "execute")
    offenders = [
        p
        for p in paths
        if p not in RESEARCH_ALLOWED_PATHS | PAPER_ALLOWED_PATHS and any(b in p for b in banned)
    ]
    assert not offenders, offenders
    assert (
        RESEARCH_ALLOWED_PATHS | PAPER_ALLOWED_PATHS <= paths
    )  # exceptions are real, not loopholes
    mutating = {
        (m, r.path)
        for r in client.app.routes
        for m in getattr(r, "methods", set())
        if m in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert (
        mutating
        == {
            ("POST", "/v1/auth/login"),
            ("POST", "/v1/auth/mfa"),
            ("POST", "/v1/auth/logout"),
        }
        | RESEARCH_MUTATING
        | PAPER_MUTATING
    )
    # no PUT/PATCH/DELETE route exists anywhere: nothing can edit or remove stored results via HTTP
    methods = {m for r in client.app.routes for m in getattr(r, "methods", set())}
    assert not (methods & {"PUT", "PATCH", "DELETE"}), methods
