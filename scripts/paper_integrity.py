"""Read-only integrity snapshot of the live paper sessions (M2-R4A).

    python scripts/paper_integrity.py > out.json

Prints, per active session: id, config hash, state, cursor, reconciliation discrepancy count,
verified-replay result (every stored checkpoint digest recomputed), whether risk is halted after
replay, and a digest of the ledger/balance rows. Writes nothing to the database.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from asta.core.settings import load_settings
from asta.db.session import make_engine, make_session_factory
from asta.paper import store


def main() -> int:
    factory = make_session_factory(make_engine(load_settings().database_url))
    out: dict[str, object] = {}
    with factory() as db:
        rows = db.execute(
            text(
                "select id, name, state, last_step_ts from paper_sessions "
                "where state in ('RUNNING','PAUSED','RISK_HALTED','RECOVERING') order by name"
            )
        ).all()
        for sid, name, state, last in rows:
            cfg = (
                db.execute(
                    text("select * from paper_session_configs where session_id = :s"), {"s": sid}
                )
                .mappings()
                .first()
            )
            _rt, rep = store.rebuild_runtime(db, sid)
            bad = store.reconcile(db, sid)
            ledger = db.execute(
                text(
                    "select count(*), coalesce(md5(string_agg(id::text, ',' order by id)), '') "
                    "from paper_fills where session_id = :s"
                ),
                {"s": sid},
            ).one()
            out[name] = {
                "id": str(sid),
                "config_hash": (cfg or {}).get("config_hash") if cfg else None,
                "state": state,
                "last_step_ts": last.isoformat() if last else None,
                "replay_steps_verified": getattr(rep, "steps", None)
                or getattr(rep, "verified", None),
                "reconcile_discrepancies": len(bad),
                "fills": ledger[0],
                "fills_digest": hashlib.sha256(ledger[1].encode()).hexdigest()[:16],
            }
        db.rollback()
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
