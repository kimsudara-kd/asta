"""Failure monitor for the security-critical order-path services (M3-B-R3). Read-only.

``python -m asta.orders.monitor`` prints a JSON status and exits 0 (ok), 1 (warning) or 2
(critical); a timer runs it every 30 s and a failing exit is the alert (systemd OnFailure, journal).
It reports: bridge heartbeat missing/stale (critical: the order path is closed by BridgedKillState),
kill switch latched (warning: a human release is pending), database unreadable (critical).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class Alert:
    code: str
    severity: str  # "warning" | "critical"
    detail: str


def check(sessions: sessionmaker[Session], max_heartbeat_age_s: float = 30.0) -> list[Alert]:
    out: list[Alert] = []
    try:
        with sessions() as db:
            age = db.execute(
                text(
                    "SELECT extract(epoch FROM now() - beat_at)"
                    " FROM risk_halt_bridge_heartbeat WHERE id = 1"
                )
            ).scalar_one_or_none()
            latched = bool(
                db.execute(text("SELECT active FROM kill_switch_state WHERE id = 1")).scalar_one()
            )
    except Exception as exc:  # noqa: BLE001
        return [Alert("DATABASE_UNREADABLE", "critical", type(exc).__name__)]
    if age is None:
        out.append(Alert("BRIDGE_HEARTBEAT_MISSING", "critical", "no heartbeat row"))
    elif float(age) > max_heartbeat_age_s:
        out.append(
            Alert(
                "BRIDGE_HEARTBEAT_STALE",
                "critical",
                f"{float(age):.0f}s old (max {max_heartbeat_age_s:.0f}s)",
            )
        )
    if latched:
        out.append(Alert("KILL_SWITCH_LATCHED", "warning", "a human release is required"))
    return out


def record_changes(sessions: sessionmaker[Session], alerts: list[Alert]) -> bool:
    """Append ``security.alert_state`` to the hash-chained audit log when the SET of active alert
    codes changed since the last such event (entering or leaving an alert state), never on every
    run. Returns True if an event was written. Payload: codes and severities only, no secrets."""
    from asta.core.environment import Environment
    from asta.db.audit_store import append_audit

    codes = sorted(a.code for a in alerts)
    with sessions() as db:
        row = db.execute(
            text(
                "SELECT payload FROM audit_events WHERE action = 'security.alert_state'"
                " ORDER BY id DESC LIMIT 1"
            )
        ).first()
        last = sorted(row[0].get("codes", [])) if row else []
        if row is None and not codes:
            return False  # nothing was ever wrong and nothing is wrong now
        if codes == last:
            return False
        sev = {a.code: a.severity for a in alerts}
        append_audit(
            db,
            "security-monitor",
            "security.alert_state",
            {"codes": codes, "severity": sev, "cleared": sorted(set(last) - set(codes))},
            Environment.EXCHANGE_DEMO,
        )
        db.commit()
        return True


def exit_code(alerts: list[Alert]) -> int:
    return 2 if any(a.severity == "critical" for a in alerts) else 1 if alerts else 0


def main() -> int:  # pragma: no cover - supervised entry point
    import os
    from pathlib import Path

    from asta.core.settings import load_settings
    from asta.db.session import make_engine, make_session_factory
    from asta.orders import alerting

    factory = make_session_factory(make_engine(load_settings().database_url))
    alerts = check(factory)
    backup = os.environ.get("ASTA_BACKUP_DIR")
    alerts += [
        a
        for a in alerting.extended_checks(factory, Path(backup) if backup else None)
        if a not in alerts
    ]
    try:
        record_changes(factory, alerts)
    except Exception as exc:  # noqa: BLE001 - the alert exit code must still be returned
        print(json.dumps({"audit_write_failed": type(exc).__name__}), file=sys.stderr)
    keyfile, stamp = os.environ.get("ASTA_STATUS_KEY_FILE"), os.environ.get("ASTA_STATUS_FILE")
    if keyfile and stamp:  # the signed status feed read by the firewall VM's dead-man job
        alerting.write_stamp(Path(stamp), Path(keyfile).read_bytes().strip(), alerts)
    print(json.dumps({"ok": not alerts, "alerts": [a.__dict__ for a in alerts]}))
    return exit_code(alerts)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
