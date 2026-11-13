"""Read-only final verification report for the live paper sessions (ASTA-DECISION-005).

    python scripts/paper_final_report.py > out.json

Per active session: portfolio (cash/inventory/equity), orders/fills/fees/slippage/P&L, max drawdown,
negative-balance check, risk events, checkpoint replay verification, ledger reconciliation, daily
report verification (every revision), and external-order evidence (exchange_connections empty;
outbound HTTP methods in the service log). Writes nothing to the database.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from asta.core.settings import load_settings
from asta.db.paper_models import DailyPaperReport
from asta.db.session import make_engine, make_session_factory
from asta.paper import reports, store, views


def _sessions(db) -> list[tuple]:  # type: ignore[no-untyped-def]
    return list(
        db.execute(
            text(
                "select id, name, state, started_at, last_step_ts from paper_sessions "
                "where state in ('RUNNING','PAUSED','RISK_HALTED','RECOVERING') order by name"
            )
        ).all()
    )


def main() -> int:
    factory = make_session_factory(make_engine(load_settings().database_url))
    out: dict[str, object] = {}
    with factory() as db:
        for sid, name, state, started, last in _sessions(db):
            p = views.portfolio(db, sid)
            perf = views.performance(db, sid, max_points=100000)
            dd = max((Decimal(pt["drawdown"] or "0") for pt in perf["points"]), default=Decimal(0))
            orders = {
                str(k): int(v)
                for k, v in db.execute(
                    text(
                        "select status, count(*) from paper_orders where session_id=:s group by 1"
                    ),
                    {"s": str(sid)},
                ).all()
            }
            fills = db.execute(
                text(
                    "select count(*), coalesce(sum(notional),0), coalesce(sum(slippage_cost),0), "
                    "coalesce(sum(realized_pnl),0) from paper_fills where session_id=:s"
                ),
                {"s": str(sid)},
            ).one()
            negative = db.execute(
                text(
                    "select count(*) from paper_balances where session_id=:s "
                    "and (balance < 0 or reserved < 0)"
                ),
                {"s": str(sid)},
            ).scalar()
            risk = {
                str(k): int(v)
                for k, v in db.execute(
                    text(
                        "select kind, count(*) from paper_risk_events where session_id=:s group by 1"
                    ),
                    {"s": str(sid)},
                ).all()
            }
            _rt, rep = store.rebuild_runtime(db, sid)  # raises ReplayMismatch on any bad digest
            bad = store.reconcile(db, sid)
            ncheck = db.execute(
                text("select count(*) from paper_session_checkpoints where session_id=:s"),
                {"s": str(sid)},
            ).scalar()
            rows = db.execute(
                text(
                    "select id from daily_paper_reports where session_id=:s order by report_date, revision"
                ),
                {"s": str(sid)},
            ).scalars()
            rep_rows = []
            for rid in list(rows):
                r = db.get(DailyPaperReport, rid)
                assert r is not None
                rep_rows.append(
                    {
                        "date": r.report_date.date().isoformat(),
                        "revision": r.revision,
                        "reproducible": reports.verify_report(db, r),
                    }
                )
            out[name] = {
                "id": str(sid),
                "state": state,
                "started_at": started.isoformat() if started else None,
                "last_step_ts": last.isoformat() if last else None,
                "totals": p["totals"],
                "accounts": {
                    k: {
                        "cash_total": a["cash_total"],
                        "assets": [(i["asset"], i["quantity"]) for i in a["assets"]],
                        "realized_pnl": a["realized_pnl"],
                        "unrealized_pnl": a["unrealized_pnl"],
                        "equity": a["equity"],
                    }
                    for k, a in p["accounts"].items()
                },
                "equity": perf["equity"],
                "net_pnl": perf["net_pnl"],
                "max_drawdown_fraction": str(dd),
                "strategies": perf["strategies"],
                "data_coverage": perf["data_coverage"],
                "orders_by_status": orders,
                "fills": {
                    "count": fills[0],
                    "notional": str(fills[1]),
                    "estimated_slippage": str(fills[2]),
                    "realized_pnl": str(fills[3]),
                },
                "negative_balance_rows": negative,
                "risk_events": risk,
                "checkpoints_stored": ncheck,
                "replay_report": repr(rep)[:200],
                "replay_verified": True,
                "reconcile_discrepancies": len(bad),
                "daily_reports": rep_rows,
            }
        out["_global"] = {
            "exchange_connections_rows": db.execute(
                text("select count(*) from exchange_connections")
            ).scalar(),
            "unresolved_critical_incidents": db.execute(
                text(
                    "select count(*) from incidents where resolved_at is null and severity='CRITICAL'"
                )
            ).scalar(),
            "dead_letter_jobs": db.execute(
                text("select count(*) from durable_jobs where status = 'DEAD'")
            ).scalar(),
        }
        db.rollback()
    log = Path("data/paper/service.log")
    methods: Counter[str] = Counter()
    if log.exists():
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"HTTP Request: ([A-Z]+) ", line)
            if m:
                methods[m.group(1)] += 1
    out["_outbound_http_methods_in_service_log"] = dict(methods)
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
