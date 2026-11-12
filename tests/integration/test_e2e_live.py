"""Opt-in end-to-end: real Binance public feed -> ingest -> API -> authenticated viewer.

OKX is run as a clearly labelled FIXTURE venue when unreachable (never presented as live).
"""

import os
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from asta.api.app import AppContext, create_app
from asta.auth import service as auth
from asta.core.events import Venue
from asta.core.settings import Settings

pytestmark = [
    pytest.mark.db,
    pytest.mark.network,
    pytest.mark.skipif(
        os.environ.get("ASTA_RUN_NETWORK_TESTS") != "1", reason="set ASTA_RUN_NETWORK_TESTS=1"
    ),
]


def test_dashboard_api_shows_real_binance_and_labelled_fixture(migrated_engine, db):
    ctx = AppContext(
        Settings(database_url="unused"), sessionmaker(migrated_engine, expire_on_commit=False)
    )
    app = create_app(ctx, start_ingest=True, fixture_venues={Venue.OKX})
    auth.create_user(db, "viewer1", "correct-horse-battery", "viewer")
    db.commit()
    with TestClient(app) as c:
        assert (
            c.post(
                "/v1/auth/login", json={"username": "viewer1", "password": "correct-horse-battery"}
            ).status_code
            == 200
        )
        deadline = time.time() + 45
        rows: list[dict] = []
        while time.time() < deadline:
            rows = c.get("/v1/markets").json()
            if all(r["bid"] for r in rows if r["venue"] == "BINANCE"):
                break
            time.sleep(1)
        binance = [r for r in rows if r["venue"] == "BINANCE"]
        assert len(binance) == 3 and all(r["source"] == "LIVE" and not r["stale"] for r in binance)
        assert all(r["source"] == "FIXTURE" for r in rows if r["venue"] == "OKX" and r["bid"])
        o = c.get("/v1/overview").json()
        states = {v["venue"]: v for v in o["venues"]}
        assert states["BINANCE"]["state"] == "LIVE" and states["BINANCE"]["clock_ok"]
        assert states["OKX"]["state"] == "FIXTURE"
        assert o["risk"]["gateway_probe"]["decision"] == "DENY"
        assert (
            abs(states["BINANCE"]["clock_offset_ms"]) < timedelta(seconds=2).total_seconds() * 1000
        )
