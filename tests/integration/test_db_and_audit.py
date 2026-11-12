from decimal import Decimal

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from asta.core.timeutil import utcnow
from asta.db.audit_store import append_audit, recent_audit, verify_audit_chain
from asta.db.models import Base, ExchangeConnection, MarketRulesVersion
from tests.integration.conftest import alembic_config

pytestmark = pytest.mark.db


def test_models_match_migrations(migrated_engine):
    with migrated_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff


def test_migration_down_and_up(database_url, migrated_engine):
    cfg = alembic_config(database_url)
    # audit_events trigger functions must drop cleanly too
    command.downgrade(cfg, "base")
    with migrated_engine.connect() as c:
        assert c.execute(text("select to_regclass('public.users')")).scalar() is None
    command.upgrade(cfg, "head")
    with migrated_engine.connect() as c:
        assert c.execute(text("select to_regclass('public.users')")).scalar() is not None


def test_audit_append_only_and_chain(db):
    for i in range(3):
        append_audit(db, "tester", "unit.test", {"i": i, "amount": Decimal("1.50")})
    db.commit()
    assert verify_audit_chain(db) == (True, None)
    with pytest.raises((DBAPIError, Exception)) as e1:
        db.execute(text("UPDATE audit_events SET actor='evil'"))
    assert "append-only" in str(e1.value)
    db.rollback()
    with pytest.raises(Exception) as e2:
        db.execute(text("DELETE FROM audit_events"))
    assert "append-only" in str(e2.value)
    db.rollback()
    with pytest.raises(Exception) as e3:
        db.execute(text("TRUNCATE audit_events"))
    assert "append-only" in str(e3.value)
    db.rollback()
    assert len(recent_audit(db, 10)) >= 3


def test_numeric_roundtrip_exact(db):
    db.add(
        MarketRulesVersion(
            venue="BINANCE",
            symbol="BTC-USDT",
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.00001"),
            min_qty=Decimal("0.00001"),
            min_notional=Decimal("5"),
            trading=True,
            source="FIXTURE",
            retrieved_at=utcnow(),
        )
    )
    db.commit()
    row = db.query(MarketRulesVersion).one()
    assert row.step_size == Decimal("0.00001") and isinstance(row.min_notional, Decimal)


def test_environment_check_constraint_rejects_live(db):
    db.add(ExchangeConnection(venue="BINANCE", environment="LIVE", status="X"))
    with pytest.raises(DBAPIError) as e1:
        db.commit()
    assert "ck_conn_env" in str(e1.value)
    db.rollback()
    with pytest.raises(DBAPIError) as e2:
        db.execute(
            text(
                "INSERT INTO audit_events(at,actor,action,environment,payload,prev_hash,row_hash) "
                "VALUES (now(),'a','b','LIVE','{}','x','y')"
            )
        )
    assert "ck_audit_env" in str(e2.value)
    db.rollback()


def test_unique_exchange_connection(db):
    db.add(ExchangeConnection(venue="OKX", environment="PAPER", status="PUBLIC_ONLY"))
    db.commit()
    db.add(ExchangeConnection(venue="OKX", environment="PAPER", status="PUBLIC_ONLY"))
    with pytest.raises(DBAPIError) as e:
        db.commit()
    assert "uq_conn_venue_env" in str(e.value)
    db.rollback()
