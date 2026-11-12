"""Entry point: `python -m asta.main` (binds 127.0.0.1 only)."""

from __future__ import annotations

import os

import uvicorn

from asta.api.app import AppContext, create_app
from asta.core.events import Venue
from asta.core.settings import load_settings
from asta.db.session import make_engine, make_session_factory


def build_app() -> object:
    settings = load_settings()  # refuses to start if exchange credentials are in the environment
    ctx = AppContext(settings, make_session_factory(make_engine(settings.database_url)))
    fixtures = {Venue(v.strip().upper()) for v in os.environ.get("ASTA_FIXTURE_VENUES", "").split(",") if v.strip()}
    return create_app(ctx, start_ingest=settings.enable_market_ingest, fixture_venues=fixtures)


if __name__ == "__main__":
    uvicorn.run(build_app(), host="127.0.0.1", port=int(os.environ.get("ASTA_API_PORT", "4640")))
