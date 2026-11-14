"""Cross-architecture replay drill, step 1: persist two deterministic paper sessions.

    ASTA_DATABASE_URL=... ASTA_SECRET_KEY=... PYTHONPATH=. uv run python scripts/xarch_seed.py

Run on machine A (e.g. macOS arm64) against an EMPTY database. It migrates the schema, then writes two
900-step sessions from the deterministic synthetic series of the test helpers: one with the default
behaviour and one with the opt-in repeat guard. Then:

    pg_dump -Fc --no-owner -d <db> | age -r <public key> > dump.age      # encrypted, like db_backup.sh
    # on machine B: age -d -i <identity> dump.age | pg_restore --no-owner -d <scratch db>
    uv run python scripts/paper_integrity.py   # on BOTH machines; the JSON must be identical

paper_integrity recomputes every stored checkpoint digest from the stored inputs, so an identical
result proves the simulation and ledger arithmetic are architecture/OS independent for these sessions.
Synthetic data only; development use (it imports the test helpers).
"""

from dataclasses import replace

from alembic import command
from alembic.config import Config
from tests.paper.helpers import make_cfg, make_series

from asta.core.settings import load_settings
from asta.db.session import make_engine, make_session_factory
from asta.paper import store
from asta.paper.runtime import PaperRuntime
from asta.paper.states import SessionState

WARM, N = 300, 900


def main() -> None:
    url = load_settings().database_url
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic_cfg, "head")
    factory = make_session_factory(make_engine(url))
    series = make_series(N)

    def run(cfg) -> None:  # type: ignore[no-untyped-def]
        with factory() as db:
            sess = store.create_session(db, cfg, "xarch")
            db.commit()
            for to in (SessionState.INITIALIZING, SessionState.READY, SessionState.RUNNING):
                store.transition(db, sess.id, to, "xarch")
                db.commit()
            rt = PaperRuntime(cfg)
            for k in range(N):
                ts, bars = series[k]
                rows = rt.step(k, ts, bars, {}, True, k < WARM)
                store.persist_step(db, sess.id, k, ts, k < WARM, bars, {}, True, [], rows)
                db.commit()
            bad = store.reconcile(db, sess.id)
            print(cfg.name, sess.id, "steps", N, "reconcile", bad, "equity", rt.global_equity())

    run(make_cfg(name="xarch-default"))
    run(replace(make_cfg(name="xarch-guard-on"), suppress_repeated_rejections=True))


if __name__ == "__main__":
    main()
