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

FROZEN_DIGESTS = {
    "f9d8e770d765ee84",
    "be76eb9ef1a917a5",
}  # docs/evidence/m3a/mac-xarch-integrity-*.json
# session name -> sha256[:16] of md5 over the full fill rows; measured on the accepted M2 ledgers (see PIN below)
CONTENT_DIGESTS: dict[
    str, str
] = {  # PIN 2026-11-21: ids already matched the frozen digests, so these are the M2 rows
    "demo-synthetic-default": "970891b4aef19133",
    "demo-synthetic-repeat-guard-on": "4a7f40147ac8df6d",
}


def verify(out: dict[str, dict[str, object]]) -> list[str]:
    """Compare a snapshot with the frozen M2 expectations; returns the list of failures (empty = pass)."""
    bad = []
    if len(out) != 2:
        bad.append(f"expected 2 active paper sessions, found {len(out)}")
    for name, r in out.items():
        if r.get("replay_steps_verified") != 900:
            bad.append(f"{name}: replay_steps_verified {r.get('replay_steps_verified')} != 900")
        if r.get("reconcile_discrepancies") != 0:
            bad.append(f"{name}: unexplained reconciliation differences")
        if r.get("fills") != 35:
            bad.append(f"{name}: fills {r.get('fills')} != 35")
    if {str(r.get("fills_digest")) for r in out.values()} != FROZEN_DIGESTS:
        bad.append("fill digests differ from the frozen M2 evidence")
    # the id digest above cannot see a changed price/quantity/fee; the content digest covers those, per session
    if CONTENT_DIGESTS:
        got = {n: str(r.get("content_digest")) for n, r in out.items()}
        if got != CONTENT_DIGESTS:
            bad.append(
                "fill CONTENT digests (id, side, symbol, price, quantity, fee, notional, pnl) differ"
            )
    return bad


def main() -> int:
    import sys

    from sqlalchemy.exc import InterfaceError, OperationalError

    try:
        return _run("--verify" in sys.argv[1:])
    except (OperationalError, InterfaceError) as exc:
        # classified failure: the dev PostgreSQL is not reachable (scripts/dev_pg_tcp.sh start)
        print(
            f"DATABASE UNAVAILABLE ({type(exc).__name__}): start it with scripts/dev_pg_tcp.sh start",
            file=sys.stderr,
        )
        return 3


def _run(check: bool) -> int:
    factory = make_session_factory(make_engine(load_settings().database_url))
    out: dict[str, dict[str, object]] = {}
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
                    "select count(*), coalesce(md5(string_agg(id::text, ',' order by id)), ''), "
                    "coalesce(md5(string_agg(concat_ws('|', id, fill_id, side, symbol, price::text, "
                    "quantity::text, fee_amount::text, notional::text, realized_pnl::text), ';' "
                    "order by id)), '') from paper_fills where session_id = :s"
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
                "content_digest": hashlib.sha256(ledger[2].encode()).hexdigest()[:16],
            }
        db.rollback()
    print(json.dumps(out, indent=1, default=str))
    if check:
        problems = verify(out)
        for p in problems:
            print("FAIL:", p)
        print("PAPER INTEGRITY:", "FAIL" if problems else "PASS")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
