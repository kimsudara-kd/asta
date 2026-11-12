"""Paper tests reuse the PostgreSQL fixtures of the integration suite (compose DB or pgserver)."""

from tests.integration.conftest import database_url, db, migrated_engine  # noqa: F401
