"""PostgreSQL for DB tests.

Uses ASTA_TEST_DATABASE_URL if set (e.g. the docker-compose test database); otherwise starts a
throw-away embedded PostgreSQL 16 via the dev-only `pgserver` package in a temp directory.
Never touches any other project's database.
"""

import os
import tempfile
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# OWASP alternative profile (7 MiB, t=5): this dev PC has almost no free commit memory
os.environ.setdefault("ASTA_ARGON2_MEMORY_KIB", "7168")
os.environ.setdefault("ASTA_ARGON2_TIME_COST", "5")
os.environ.setdefault("ASTA_SECRET_KEY", "test-only-secret-key-not-a-real-secret")


def _to_pg8000(uri: str) -> str:
    return uri.replace("postgresql://", "postgresql+pg8000://", 1)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("ASTA_TEST_DATABASE_URL")
    if url:
        yield url
        return
    import pgserver

    with tempfile.TemporaryDirectory(prefix="asta-pg-") as d:
        server = pgserver.get_server(d, cleanup_mode="stop")  # type: ignore[attr-defined]
        try:
            yield _to_pg8000(server.get_uri())
        finally:
            server.cleanup()


def alembic_config(url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def migrated_engine(database_url: str) -> Engine:
    engine = create_engine(database_url)
    command.upgrade(alembic_config(database_url), "head")
    return engine


@pytest.fixture
def db(migrated_engine: Engine) -> Iterator[Session]:
    """Each test gets a clean slate (TRUNCATE all tables except the append-only audit log,
    which the DB forbids truncating; tests must not depend on audit emptiness)."""
    with migrated_engine.begin() as c:
        c.execute(
            text(
                "TRUNCATE sessions, users, exchange_connections, markets, "
                "market_rules_versions, risk_policies, incidents CASCADE"
            )
        )
        # research result tables are append-only (triggers also block TRUNCATE). A test database may
        # reset them only by bypassing ORIGIN triggers (superuser): production code never can.
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text(
                "TRUNCATE idempotency_keys, research_holdout_windows, research_validations, "
                "performance_reports, risk_events, portfolio_equity_snapshots, "
                "simulated_ledger_entries, "
                "simulated_fills, simulated_orders, backtest_runs, backtest_run_configs, "
                "strategy_versions, strategy_definitions, dataset_quality_reports, "
                "historical_dataset_versions, historical_datasets CASCADE"
            )
        )
        c.execute(
            text(
                "TRUNCATE paper_sessions, market_observations, market_data_gaps, "
                "market_feed_connections, durable_jobs, worker_leases, operational_alerts CASCADE"
            )
        )
    factory = sessionmaker(migrated_engine, expire_on_commit=False)
    with factory() as s:
        yield s
