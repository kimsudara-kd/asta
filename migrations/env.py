import os

os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

from alembic import context  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

from asta.core.settings import load_settings  # noqa: E402
from asta.db.models import Base  # noqa: E402

target_metadata = Base.metadata


def _url() -> str:
    return context.config.get_main_option("sqlalchemy.url") or load_settings().database_url


def run_migrations_online() -> None:
    connectable = context.config.attributes.get("connection")
    if connectable is None:
        engine = create_engine(_url())
        with engine.connect() as conn:
            context.configure(connection=conn, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    else:
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
