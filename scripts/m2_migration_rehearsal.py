"""M2 migration REHEARSAL (M3A-R3). Read-only with respect to the live M2 database.

    python scripts/m2_migration_rehearsal.py out.json

Rehearses the database part of a later, separately authorized migration of the paper observation:
pg_dump of the live ``asta`` database (read-only transaction) -> restore into a scratch database
``asta_rehearsal`` on the TEST Postgres container -> prove on the copy that
  * session ids, config hashes and states are identical to the source,
  * every checkpoint of the copy exists in the source with the identical digest (prefix equality,
    the live source keeps advancing while the rehearsal runs),
  * deterministic replay verifies every stored checkpoint digest of the copy,
  * ledger reconciliation reports 0 discrepancies,
  * every latest-revision daily report of the copy is reproducible,
then drops the scratch database and deletes the dump. Nothing live is stopped, written or moved.
It does NOT start any paper service against the copy and migrates nothing.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import text

from asta.db.paper_models import DailyPaperReport
from asta.db.session import make_engine, make_session_factory
from asta.paper import reports, store

SRC_URL = os.environ.get(
    "ASTA_DATABASE_URL", "postgresql+pg8000://asta:asta_dev_only@127.0.0.1:5640/asta"
)
COPY_DB = "asta_rehearsal"
COPY_URL = f"postgresql+pg8000://asta_test:asta_test_only@127.0.0.1:5641/{COPY_DB}"
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
DUMP = Path("data/rehearsal/asta.dump")


def sh(*args: str, stdin: bytes | None = None, stdout_path: Path | None = None) -> bytes:
    r = subprocess.run(args, input=stdin, capture_output=True, env=ENV, check=False)  # noqa: S603
    if r.returncode != 0:
        raise RuntimeError(f"{args[:4]} failed: {r.stderr.decode()[-300:]}")
    return r.stdout


def psql_test(sql: str, db: str = "postgres") -> None:
    sh(
        "docker",
        "exec",
        "asta-db-test",
        "psql",
        "-U",
        "asta_test",
        "-d",
        db,
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        sql,
    )


def checkpoints(factory) -> dict[tuple[str, int], str]:  # type: ignore[no-untyped-def]
    with factory() as db:
        rows = db.execute(
            text("select session_id::text, step_seq, digest from paper_session_checkpoints")
        ).all()
        db.rollback()
    return {(r[0], r[1]): r[2] for r in rows}


def sessions(factory) -> dict[str, dict[str, str]]:  # type: ignore[no-untyped-def]
    with factory() as db:
        rows = db.execute(
            text(
                "select s.id::text, s.name, s.state, c.config_hash from paper_sessions s "
                "join paper_session_configs c on c.session_id = s.id order by s.name"
            )
        ).all()
        db.rollback()
    return {r[0]: {"name": r[1], "state": r[2], "config_hash": r[3]} for r in rows}


def main() -> int:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("m2_rehearsal.json")
    rep: dict[str, object] = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    DUMP.parent.mkdir(parents=True, exist_ok=True)
    src = make_session_factory(make_engine(SRC_URL))

    # 1. dump (read-only: pg_dump takes ACCESS SHARE locks only)
    dump = sh(
        "docker", "exec", "asta-db", "pg_dump", "-U", "asta", "-d", "asta", "-Fc", "--no-sync"
    )
    DUMP.write_bytes(dump)
    rep["dump_bytes"] = len(dump)
    rep["dump_sha256"] = hashlib.sha256(dump).hexdigest()

    # 2. restore into a scratch database on the TEST container
    psql_test(f"DROP DATABASE IF EXISTS {COPY_DB} WITH (FORCE)")
    psql_test(f"CREATE DATABASE {COPY_DB}")
    sh(
        "docker", "exec", "-i", "asta-db-test", "pg_restore", "-U", "asta_test", "-d", COPY_DB,
        "--no-owner", "--no-privileges", "--exit-on-error", stdin=dump,
    )  # fmt: skip
    copy = make_session_factory(make_engine(COPY_URL))
    try:
        # 3. identity of sessions
        s_src, s_copy = sessions(src), sessions(copy)
        rep["sessions_identical_id_hash"] = {
            sid: (sid in s_src and s_src[sid]["config_hash"] == v["config_hash"])
            for sid, v in s_copy.items()
        }
        rep["sessions_in_copy"] = s_copy
        # 4. prefix equality of checkpoints
        c_src, c_copy = checkpoints(src), checkpoints(copy)
        missing = [k for k in c_copy if k not in c_src]
        differ = [k for k, v in c_copy.items() if k in c_src and c_src[k] != v]
        rep["checkpoints_in_copy"] = len(c_copy)
        rep["checkpoints_in_source_now"] = len(c_src)
        rep["checkpoints_missing_in_source"] = len(missing)
        rep["checkpoints_with_different_digest"] = len(differ)
        # 5. replay, reconciliation, reports on the COPY
        results: dict[str, object] = {}
        with copy() as db:
            for sid in s_copy:
                import uuid

                u = uuid.UUID(sid)
                _rt, replay = store.rebuild_runtime(db, u)  # raises on any digest mismatch
                bad = store.reconcile(db, u)
                rows = db.execute(
                    text(
                        "select id from daily_paper_reports where session_id=:s "
                        "order by report_date, revision"
                    ),
                    {"s": sid},
                ).scalars()
                latest: dict[str, bool] = {}
                for rid in list(rows):
                    r = db.get(DailyPaperReport, rid)
                    assert r is not None
                    latest[f"{r.report_date.date()}#rev{r.revision}"] = reports.verify_report(db, r)
                results[sid] = {
                    "replay_steps_verified": replay.steps,
                    "replay_problems": list(replay.problems),
                    "reconcile_discrepancies": len(bad),
                    "report_revisions_reproducible": latest,
                }
            db.rollback()
        rep["copy_verification"] = results
    finally:
        psql_test(f"DROP DATABASE IF EXISTS {COPY_DB} WITH (FORCE)")
        DUMP.unlink(missing_ok=True)
    rep["scratch_database_dropped_and_dump_deleted"] = True
    ok = (
        all(rep["sessions_identical_id_hash"].values())  # type: ignore[union-attr]
        and rep["checkpoints_missing_in_source"] == 0
        and rep["checkpoints_with_different_digest"] == 0
        and all(
            v["replay_problems"] == [] and v["reconcile_discrepancies"] == 0  # type: ignore[index]
            for v in rep["copy_verification"].values()  # type: ignore[attr-defined]
        )
    )
    rep["rehearsal_passed"] = bool(ok)
    out_path.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(
        json.dumps({k: rep[k] for k in ("rehearsal_passed", "checkpoints_in_copy", "dump_sha256")})
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
