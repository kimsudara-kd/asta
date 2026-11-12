"""Local process supervisor for the paper service (M2-R1). External to the trading engine.

    python scripts/paper_supervisor.py            # supervises `python -m asta.paper_service`

* starts the service only after a PRE-RESUME check passes: database reachable and every active
  session reconciles from durable rows (otherwise a CRITICAL SUPERVISOR_BLOCKED incident is opened
  and the service is NOT started - fail closed);
* samples health every 10 s into data/paper/supervisor.jsonl (process alive, DB reachable, worker
  heartbeat fresh, feed freshness) - the raw input of the availability measurement;
* on unexpected exit or a hung worker (heartbeat stale > 90 s) records a SERVICE_DOWN incident,
  restarts with bounded backoff, and after the restart waits for fresh feeds, then records a
  resolved SERVICE_RECOVERED incident with the measured downtime;
* never changes a session's state: a RISK_HALTED / PAUSED session stays that way across restarts
  (state lives in the database and is restored by verified replay). Nothing here can resume or
  create trading activity; it only restarts the observer/worker process.
Windows note: wrap this script in a Task Scheduler "at log on / on failure restart" entry to
supervise the supervisor (documented, not installed automatically).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from asta.core.settings import load_settings
from asta.core.timeutil import utcnow
from asta.db.paper_models import MarketFeedConnection
from asta.db.session import make_engine, make_session_factory
from asta.paper import jobs, ops, store
from asta.paper.supervision import Sample

LOG_DIR = Path("data/paper")
SAMPLES = LOG_DIR / "supervisor.jsonl"
INTERVAL_S = 10
HUNG_WORKER_S = 90
FEED_FRESH_S = 60
ACTIVE = ("RUNNING", "PAUSED", "RISK_HALTED", "RECOVERING")


def health(factory, child: subprocess.Popen | None, since: datetime | None = None) -> Sample:  # type: ignore[no-untyped-def]
    """``since``: evidence older than the current child's start (a dead process's last heartbeat /
    feed event) must not count as health of the NEW process."""
    now = utcnow()
    since = since or datetime(2000, 1, 1, tzinfo=now.tzinfo)
    alive = child is not None and child.poll() is None
    try:
        with factory() as db:
            db.execute(text("select 1"))
            workers = [
                w
                for w in jobs.workers_alive(db, HUNG_WORKER_S, "paper-worker", now)
                if w.heartbeat_at >= since
            ]
            feeds = {}
            for v in ("BINANCE", "OKX"):
                f = db.get(MarketFeedConnection, v)
                feeds[v] = bool(
                    f is not None
                    and f.source == "LIVE"
                    and f.state == "CONNECTED"
                    and f.last_event_at is not None
                    and f.last_event_at >= since
                    and (now - f.last_event_at) <= timedelta(seconds=FEED_FRESH_S)
                )
        return Sample(now, alive, True, bool(workers), feeds)
    except Exception:  # noqa: BLE001 - database unreachable
        return Sample(now, alive, False, False, {})


def preflight(factory) -> tuple[bool, str]:  # type: ignore[no-untyped-def]
    """Pre-resume gate: reconciliation of every active session from durable rows."""
    try:
        with factory() as db:
            rows = db.execute(
                text("select id, name from paper_sessions where state = any(:s)"),
                {"s": list(ACTIVE)},
            ).all()
            for sid, name in rows:
                bad = store.reconcile(db, sid)
                store.record_reconcile(db, sid, not bad)
                if bad:
                    db.commit()
                    return False, f"session {name} fails reconciliation: {bad[0].detail}"
            db.commit()
        return True, f"{len(rows)} active session(s) reconcile"
    except Exception as exc:  # noqa: BLE001
        return False, f"database not reachable: {type(exc).__name__}"


def incident(factory, kind, sev, detail, key, resolved=False):  # type: ignore[no-untyped-def]
    for _ in range(30):  # the database may be the thing that is down: retry
        try:
            with factory() as db:
                ops.open_incident(db, kind, sev, detail, key)
                if resolved:
                    ops.resolve_incident(db, key, "supervisor")
                db.commit()
            return
        except Exception:  # noqa: BLE001
            time.sleep(5)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    factory = make_session_factory(make_engine(load_settings().database_url))
    out = SAMPLES.open("a", encoding="utf-8")
    child: subprocess.Popen | None = None  # type: ignore[type-arg]
    restarts = 0
    down_since: datetime | None = None
    down_key: str | None = None
    backoff = 1.0
    unhealthy_since: datetime | None = None
    child_started: datetime | None = None
    try:
        while True:
            if child is None or child.poll() is not None:
                code = None if child is None else child.returncode
                if child is not None and down_since is None:
                    down_since = utcnow()
                    down_key = f"svc-down:{int(down_since.timestamp())}"
                    incident(
                        factory,
                        "SERVICE_DOWN",
                        "WARNING",
                        f"paper service exited unexpectedly (code {code}); supervisor restarting",
                        down_key,
                    )
                ok, why = preflight(factory)
                if not ok:
                    incident(
                        factory,
                        "SUPERVISOR_BLOCKED",
                        "CRITICAL",
                        f"restart refused (fail-closed): {why}",
                        f"blocked:{int(time.time() // 300)}",
                    )
                    out.write(json.dumps(health(factory, None).to_json()) + "\n")
                    out.flush()
                    time.sleep(30)
                    continue
                log = (LOG_DIR / "service.log").open("a", encoding="utf-8")
                log.write(
                    f"\n--- supervisor start {utcnow().isoformat()} (restart #{restarts}) ---\n"
                )
                log.flush()
                child = subprocess.Popen(  # noqa: S603 - fixed argv: this interpreter + our module
                    [sys.executable, "-m", "asta.paper_service"],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                restarts += 1
                child_started = utcnow()
                time.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)
            s = health(factory, child, child_started)
            out.write(json.dumps(s.to_json()) + "\n")
            out.flush()
            if s.available and all(s.feeds.get(v, False) for v in ("BINANCE", "OKX")):
                backoff = 1.0
                unhealthy_since = None
                if down_since is not None and down_key is not None:
                    secs = int((utcnow() - down_since).total_seconds())
                    incident(
                        factory,
                        "SERVICE_RECOVERED",
                        "INFO",
                        f"service healthy again after {secs} s downtime (fresh feeds verified)",
                        f"svc-up:{down_key}",
                        resolved=True,
                    )
                    incident(factory, "SERVICE_DOWN", "WARNING", "closed", down_key, resolved=True)
                    down_since = down_key = None
            elif child.poll() is None:
                unhealthy_since = unhealthy_since or utcnow()
                if (
                    not s.worker_ok
                    and (utcnow() - unhealthy_since).total_seconds() > 3 * HUNG_WORKER_S
                ):
                    incident(
                        factory,
                        "SERVICE_DOWN",
                        "WARNING",
                        "worker heartbeat stale; supervisor terminating the service for a safe restart",
                        f"svc-hung:{int(time.time())}",
                    )
                    child.terminate()
                    down_since = utcnow()
                    down_key = f"svc-down:{int(down_since.timestamp())}"
            time.sleep(INTERVAL_S)
    except KeyboardInterrupt:
        pass
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
