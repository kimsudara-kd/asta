"""Fail-closed bridge: persistent paper risk halts -> the centralized kill switch (M3-B-R2).

The paper/risk engines deliberately do not import ``asta.orders``; the bridge is a separate,
supervised reader that observes the durable paper tables (read-only SQL) and latches the durable
kill switch. Properties:

* LATCH ONLY. The bridge has no release capability (no code path calls ``release``); a halt that is
  released by an authorised operator through ``release_kill_switch()`` stays released until a NEW
  risk-halt event appears.
* AT-MOST-ONCE PER EVENT. Each halt event (a ``paper_session_states`` row that moved a session to
  RISK_HALTED, or a session found in that state with no such row) is recorded in
  ``risk_halt_bridge_seen`` in the SAME transaction as the trip: a restart or any number of
  concurrent bridge workers produce exactly one trip per event.
* FAIL CLOSED. Not being able to read the paper risk state ``max_read_failures`` times in a row, or
  meeting a session state this bridge does not know, trips the switch (cause RISK_HALT).
Nothing here talks to an exchange or holds a credential.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from asta.core.environment import Environment
from asta.db.audit_store import append_audit

#: mirror of asta.paper.states.SessionState (not imported: the paper package stays independent;
#: a test fails if the two ever differ)
KNOWN_SESSION_STATES = frozenset(
    {
        "CREATED",
        "INITIALIZING",
        "READY",
        "RUNNING",
        "PAUSED",
        "RECOVERING",
        "RISK_HALTED",
        "STOPPED",
        "FAILED",
    }  # fmt: skip
)
SOURCE_TRANSITION = "paper_session_states"
SOURCE_SESSION = "paper_sessions"
SOURCE_UNREADABLE = "bridge_unreadable"
SOURCE_UNKNOWN_STATE = "unknown_state"


@dataclass
class BridgeReport:
    tripped: list[str] = field(default_factory=list)  # event keys that caused a trip now
    already_seen: int = 0
    read_failed: bool = False
    unknown_states: list[str] = field(default_factory=list)


class RiskHaltBridge:
    def __init__(self, sessions: sessionmaker[Session], max_read_failures: int = 2) -> None:
        if max_read_failures < 1:
            raise ValueError("max_read_failures must be >= 1")
        self._sessions = sessions
        self._max_failures = max_read_failures
        self._failures = 0

    # ------------------------------------------------------------------ one trip, atomically
    def _trip_once(self, source: str, key: str, detail: str) -> bool:
        """Record the event and trip in ONE transaction. True if this call tripped."""
        with self._sessions() as db:
            res = db.execute(
                text(
                    "INSERT INTO risk_halt_bridge_seen (source, event_key, detail)"
                    " VALUES (:s, :k, :d) ON CONFLICT DO NOTHING"
                ),
                {"s": source, "k": key, "d": detail[:300]},
            )
            if getattr(res, "rowcount", 0) != 1:
                db.rollback()
                return False
            db.execute(
                text("SELECT trip_kill_switch('RISK_HALT', 'risk-bridge', :r)"),
                {"r": f"{source}:{key} {detail}"[:300]},
            )
            payload = {"cause": "RISK_HALT", "source": source, "key": key}
            append_audit(db, "risk-bridge", "killswitch.trip", payload, Environment.EXCHANGE_DEMO)
            db.commit()
            return True

    def _beat(self) -> None:
        try:
            with self._sessions() as db:
                db.execute(text("SELECT bridge_heartbeat()"))
                db.commit()
        except Exception:  # noqa: BLE001, S110 - a missed beat only makes the gate stricter
            pass

    def _latched(self) -> bool:
        try:
            with self._sessions() as db:
                return bool(
                    db.execute(
                        text("SELECT active FROM kill_switch_state WHERE id = 1")
                    ).scalar_one()
                )
        except Exception:  # noqa: BLE001
            return False  # cannot even read the latch: try to trip (which will fail loudly)

    def poll(self) -> BridgeReport:
        rep = BridgeReport()
        try:
            with self._sessions() as db:
                transitions = db.execute(
                    text(
                        "SELECT s.id, s.session_id, s.reason FROM paper_session_states s"
                        " WHERE s.to_state = 'RISK_HALTED' ORDER BY s.id"
                    )
                ).all()
                halted = db.execute(
                    text("SELECT id FROM paper_sessions WHERE state = 'RISK_HALTED' ORDER BY id")
                ).all()
                states = db.execute(text("SELECT DISTINCT state FROM paper_sessions")).all()
        except Exception:  # noqa: BLE001 - any failure to read is "cannot tell": fail closed
            rep.read_failed = True
            self._failures += 1
            if self._failures >= self._max_failures and not self._latched():
                key = f"unreadable-{int(time.time()) // 60}"
                if self._trip_once(SOURCE_UNREADABLE, key, "paper risk state unreadable"):
                    rep.tripped.append(key)
            return rep
        self._failures = 0
        self._beat()  # alive and able to read: a stale heartbeat closes the gate (BridgedKillState)
        for tid, sid, reason in transitions:
            if self._trip_once(SOURCE_TRANSITION, str(tid), f"session {sid}: {reason or 'halt'}"):
                rep.tripped.append(f"{SOURCE_TRANSITION}:{tid}")
            else:
                rep.already_seen += 1
        for (sid,) in halted:  # halted with no transition row: still a halt
            if self._trip_once(SOURCE_SESSION, str(sid), "session in RISK_HALTED"):
                rep.tripped.append(f"{SOURCE_SESSION}:{sid}")
            else:
                rep.already_seen += 1
        for (st,) in states:
            if str(st) not in KNOWN_SESSION_STATES:
                rep.unknown_states.append(str(st))
                if self._trip_once(
                    SOURCE_UNKNOWN_STATE, str(st)[:60], "unknown paper session state"
                ):
                    rep.tripped.append(f"{SOURCE_UNKNOWN_STATE}:{st}")
        return rep


def main() -> None:  # pragma: no cover - supervised process entry point
    """``python -m asta.orders.risk_bridge``: poll every 5 s until stopped."""
    from asta.core.settings import load_settings
    from asta.db.session import make_engine, make_session_factory

    bridge = RiskHaltBridge(make_session_factory(make_engine(load_settings().database_url)))
    while True:
        bridge.poll()
        time.sleep(5)


if __name__ == "__main__":  # pragma: no cover
    main()
