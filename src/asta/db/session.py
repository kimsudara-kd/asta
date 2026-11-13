from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str) -> Engine:
    eng = create_engine(url, pool_pre_ping=True, future=True)
    if url.startswith("postgresql"):

        @event.listens_for(eng, "connect")
        def _guard(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            """A killed process can leave a backend 'idle in transaction' holding row locks for
            hours (TCP keep-alive). Bound that blast radius: a lock wait fails after 45 s (the job
            is retried) and an abandoned transaction is dropped by the server after 10 min."""
            cur = dbapi_conn.cursor()
            cur.execute("SET lock_timeout = '45s'")
            cur.execute("SET idle_in_transaction_session_timeout = '10min'")
            cur.close()
            dbapi_conn.commit()

    return eng


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
