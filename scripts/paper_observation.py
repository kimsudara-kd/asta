"""Supervised paper-observation helper (local operator tool; no network, no exchange access).

  create <name> <timeframe>   create a session from the stored public rules + live feed status
                              (same builder as the API; actor 'observation-operator')
  start <session-id>          READY -> RUNNING
  status                      one-line state of every session + feed/worker health
  gate <session-id> [--hours N]   evaluate the continuous-observation gate (engineering stability)

State changes here are the same audited transitions the API performs; they only touch LOCAL
simulated state.
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import timedelta

from sqlalchemy import func, select, text

from asta.api.paper import CreateBody, build_config
from asta.core.environment import Environment
from asta.core.settings import load_settings
from asta.core.timeutil import utcnow
from asta.db.audit_store import append_audit
from asta.db.paper_models import (
    MarketDataGap,
    PaperFill,
    PaperSession,
    PaperStepInput,
)
from asta.db.session import make_engine, make_session_factory
from asta.paper import store, views
from asta.paper.states import SessionState

ACTOR = "observation-operator"


def main() -> int:
    factory = make_session_factory(make_engine(load_settings().database_url))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    with factory() as db:
        if cmd == "create":
            body = CreateBody(name=sys.argv[2], timeframe=sys.argv[3])  # type: ignore[arg-type]
            cfg = build_config(db, body)
            s = store.create_session(db, cfg, ACTOR)
            store.transition(db, s.id, SessionState.INITIALIZING, ACTOR, "warm-up requested")
            append_audit(
                db, ACTOR, "paper.session.create", {"session": str(s.id)}, Environment.PAPER
            )
            db.commit()
            print(s.id)
        elif cmd == "start":
            sid = uuid.UUID(sys.argv[2])
            store.transition(db, sid, SessionState.RUNNING, ACTOR, "supervised observation start")
            append_audit(db, ACTOR, "paper.session.start", {"session": str(sid)}, Environment.PAPER)
            db.commit()
            print("RUNNING")
        elif cmd == "status":
            for s in db.execute(select(PaperSession).order_by(PaperSession.created_at)).scalars():
                print(
                    f"{s.id} {s.name:<24} {s.state:<12} step={s.last_step_seq:<5} "
                    f"last={s.last_step_ts} reconcile_ok={s.last_reconcile_ok}"
                )
            h = views.health(db)
            print(
                json.dumps(
                    {
                        "worker_online": h["worker_online"],
                        "queue": h["queue"]["counts"],
                        "open_incidents": h["open_incidents"],
                        "feeds": [(f["venue"], f["state"], f["reconnects"]) for f in h["feeds"]],
                    }
                )
            )
        elif cmd == "measure":
            print(
                json.dumps(
                    measure(db, since=sys.argv[2] if len(sys.argv) > 2 else None),
                    indent=1,
                    default=str,
                )
            )
        elif cmd == "gate":
            sid = uuid.UUID(sys.argv[2])
            hours = (
                float(sys.argv[sys.argv.index("--hours") + 1]) if "--hours" in sys.argv else 72.0
            )
            return gate(db, sid, hours)
    return 0


def measure(db, since: str | None = None):  # type: ignore[no-untyped-def]
    """Operational measurements for the evidence document (all derived from durable rows)."""
    from datetime import datetime

    from asta.paper import reports

    now = utcnow()
    t0 = datetime.fromisoformat(since) if since else now - timedelta(hours=24)
    out: dict = {
        "measured_at": now.isoformat(),
        "since": t0.isoformat(),
        "sessions": [],
        "feeds": {},
    }
    for s in db.execute(select(PaperSession).order_by(PaperSession.created_at)).scalars():
        cfg = store.load_config(db, s.id)
        live = (
            db.execute(
                select(PaperStepInput)
                .where(PaperStepInput.session_id == s.id, PaperStepInput.warm.is_(False))
                .order_by(PaperStepInput.step_seq)
            )
            .scalars()
            .all()
        )
        muted = sum(1 for x in live if not x.strategy_active)
        blocked = sum(1 for x in live if x.blocked)
        elapsed = (now - s.started_at).total_seconds() if s.started_at else 0.0
        expected = int(elapsed // cfg.timeframe.seconds)
        bad = store.reconcile(db, s.id)
        fills = db.execute(
            select(func.count()).select_from(PaperFill).where(PaperFill.session_id == s.id)
        ).scalar_one()
        out["sessions"].append(
            {
                "id": str(s.id),
                "name": s.name,
                "state": s.state,
                "timeframe": cfg.timeframe.value,
                "started_at": s.started_at,
                "elapsed_hours": round(elapsed / 3600, 3),
                "live_steps": len(live),
                "expected_steps_approx": expected,
                "muted_late_or_paused_steps": muted,
                "steps_with_blocked_markets": blocked,
                "fills": fills,
                "reconciliation_discrepancies": len(bad),
                "last_reconcile_ok": s.last_reconcile_ok,
                "equity": views.session_json(db, s)["equity"],
                "partial_day_report": reports.build_report(
                    db, s.id, now.replace(hour=0, minute=0, second=0, microsecond=0), until=now
                )
                if live
                else None,
            }
        )
    rows = db.execute(
        text(
            "select venue, symbol, count(*), min(event_ts), max(event_ts), "
            "sum(case when source='BACKFILL' then 1 else 0 end) from market_observations "
            "where kind='CANDLE_1M' and event_ts >= :t group by venue, symbol order by 1,2"
        ),
        {"t": t0},
    ).all()
    for v, sym, n, lo, hi, bf in rows:
        span = int((hi - lo).total_seconds() // 60) + 1
        out["feeds"][f"{v}:{sym}"] = {
            "candles": n,
            "span_minutes": span,
            "present_fraction": round(n / span, 5),
            "from_backfill": int(bf),
            "first": lo,
            "last": hi,
        }
    out["gaps"] = [
        {
            "venue": g.venue,
            "symbol": g.symbol,
            "start": g.gap_start,
            "missing": g.missing,
            "status": g.status,
        }
        for g in db.execute(select(MarketDataGap).order_by(MarketDataGap.gap_start)).scalars()
    ]
    out["incidents"] = [
        {
            "kind": i["kind"],
            "severity": i["severity"],
            "opened": i["opened_at"],
            "resolved": i["resolved_at"],
            "detail": i["detail"][:160],
        }
        for s in db.execute(select(PaperSession)).scalars()
        for i in views.incidents(db, s.id, 500)
    ]
    out["queue"] = views.health(db)["queue"]
    out["workers_seen"] = [
        {"id": w, "started": a, "last_heartbeat": b}
        for w, a, b in db.execute(
            text(
                "select worker_id, started_at, heartbeat_at from worker_leases order by started_at"
            )
        )
    ]
    out["feed_status"] = views.feeds_status(db)["feeds"]
    return out


def gate(db, sid: uuid.UUID, hours: float) -> int:  # type: ignore[no-untyped-def]
    s = store.get_session(db, sid)
    started = s.started_at
    now = utcnow()
    elapsed = (now - started).total_seconds() / 3600 if started else 0.0
    bad = store.reconcile(db, sid)
    steps = db.execute(
        select(func.count())
        .select_from(PaperStepInput)
        .where(PaperStepInput.session_id == sid, PaperStepInput.warm.is_(False))
    ).scalar_one()
    cfg = store.load_config(db, sid)
    expected = int(elapsed * 3600 // cfg.timeframe.seconds)
    open_gaps = db.execute(
        select(func.count()).select_from(MarketDataGap).where(MarketDataGap.status == "OPEN")
    ).scalar_one()
    unrec = db.execute(
        select(func.count())
        .select_from(MarketDataGap)
        .where(MarketDataGap.status == "UNRECOVERABLE")
    ).scalar_one()
    crit = db.execute(
        text(
            "select count(*) from incidents where severity='CRITICAL' and "
            "(session_id=:s or session_id is null) and kind not in ('RECOVERY')"
        ),
        {"s": str(sid)},
    ).scalar_one()
    fills = db.execute(
        select(func.count()).select_from(PaperFill).where(PaperFill.session_id == sid)
    ).scalar_one()
    dup = db.execute(
        text(
            "select count(*) from (select account, fill_id from paper_fills where session_id=:s "
            "group by 1,2 having count(*)>1) x"
        ),
        {"s": str(sid)},
    ).scalar_one()
    checks = {
        "elapsed_hours": round(elapsed, 2),
        "required_hours": hours,
        "duration_met": elapsed >= hours,
        "session_state": s.state,
        "state_is_operating": s.state in ("RUNNING", "PAUSED", "RISK_HALTED"),
        "steps_processed": steps,
        "steps_expected_approx": expected,
        "reconciliation_discrepancies": len(bad),
        "duplicate_fill_postings": dup,
        "open_data_gaps": open_gaps,
        "unrecoverable_data_gaps": unrec,
        "critical_incidents": crit,
        "fills": fills,
        "real_orders_possible": False,
    }
    ok = (
        checks["duration_met"]
        and checks["state_is_operating"]
        and not bad
        and not dup
        and not open_gaps
        and not crit
    )
    print(json.dumps({"gate_pass": ok, **checks}, indent=1, default=str))
    return 0 if ok else 1


_ = timedelta

if __name__ == "__main__":
    raise SystemExit(main())
