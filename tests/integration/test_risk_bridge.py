"""M3-B-R2: persistent paper risk halts -> the centralized kill switch (PostgreSQL)."""

from __future__ import annotations

import ast
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from asta.orders import risk_bridge
from asta.orders.risk_bridge import KNOWN_SESSION_STATES, BridgeReport, RiskHaltBridge
from asta.orders.sql_store import SqlKillSwitch
from asta.paper.states import SessionState

pytestmark = pytest.mark.db


@pytest.fixture
def sessions(migrated_engine) -> sessionmaker[Session]:
    return sessionmaker(migrated_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def clean(migrated_engine):
    def reset() -> None:
        with migrated_engine.begin() as c:
            c.execute(text("SET LOCAL session_replication_role = replica"))
            c.execute(text("UPDATE kill_switch_state SET active = false"))
            c.execute(text("DELETE FROM kill_switch_releasers"))
            c.execute(text("DELETE FROM risk_halt_bridge_seen"))
            c.execute(text("DELETE FROM paper_session_states"))
            c.execute(text("DELETE FROM paper_sessions"))

    reset()
    yield
    reset()


def new_session(sessions, state: str = "RUNNING") -> str:
    sid = str(uuid.uuid4())
    with sessions() as db:
        db.execute(
            text(
                "INSERT INTO paper_sessions (id, name, state, state_reason, last_step_seq, config_hash, created_by, version)"
                " VALUES (:i, :n, :s, '', -1, :h, 'test', 0)"
            ),
            {"i": sid, "n": "s-" + sid[:6], "s": state, "h": "h" * 64},
        )
        db.commit()
    return sid


def transition(sessions, sid: str, frm: str | None, to: str, reason: str = "") -> None:
    with sessions() as db:
        db.execute(
            text(
                "INSERT INTO paper_session_states (session_id, from_state, to_state, reason, actor)"
                " VALUES (:i, :f, :t, :r, 'engine')"
            ),
            {"i": sid, "f": frm, "t": to, "r": reason},
        )
        db.execute(text("UPDATE paper_sessions SET state = :t WHERE id = :i"), {"t": to, "i": sid})
        db.commit()


def halt(sessions, why: str = "drawdown halt") -> str:
    sid = new_session(sessions)
    transition(sessions, sid, "RUNNING", "RISK_HALTED", why)
    return sid


def authorize(sessions) -> None:
    who = SqlKillSwitch(sessions).session_actor()
    with sessions() as db:
        db.execute(
            text("INSERT INTO kill_switch_releasers (name) VALUES (:n) ON CONFLICT DO NOTHING"),
            {"n": who},
        )
        db.commit()


def trips(sessions) -> int:
    return sum(
        1 for a, c, _ in SqlKillSwitch(sessions).history() if a == "TRIP" and c == "RISK_HALT"
    )


def test_the_states_the_bridge_knows_are_exactly_the_paper_session_states():
    assert KNOWN_SESSION_STATES == {s.value for s in SessionState}


def test_a_persistent_risk_halt_latches_the_central_kill_switch(sessions):
    k = SqlKillSwitch(sessions)
    b = RiskHaltBridge(sessions)
    assert not b.poll().tripped and not k.active()  # nothing halted: nothing happens
    halt(sessions)
    rep = b.poll()
    assert len(rep.tripped) >= 1 and k.active()
    assert ("TRIP", "RISK_HALT", "risk-bridge") in k.history()


def test_restart_does_not_trip_twice_for_the_same_event_but_a_new_halt_trips_again(sessions):
    k = SqlKillSwitch(sessions)
    halt(sessions)
    RiskHaltBridge(sessions).poll()
    first = trips(sessions)
    assert first >= 1
    for _ in range(3):  # "restarts": brand-new bridge objects, same database
        assert RiskHaltBridge(sessions).poll().tripped == []
    assert trips(sessions) == first
    authorize(sessions)
    assert k.release("operator reviewed the halt and re-armed the session") is True
    assert (
        RiskHaltBridge(sessions).poll().tripped == [] and not k.active()
    )  # the old event does not re-latch
    halt(sessions, "daily loss halt")  # a NEW halt event
    assert RiskHaltBridge(sessions).poll().tripped != [] and k.active()


def test_the_bridge_never_releases_even_when_the_session_is_re_armed(sessions):
    k = SqlKillSwitch(sessions)
    base = len(k.history())  # the event log is append-only and shared: look only at new entries
    sid = halt(sessions)
    b = RiskHaltBridge(sessions)
    b.poll()
    assert k.active()
    transition(sessions, sid, "RISK_HALTED", "PAUSED", "operator re-arm")
    transition(sessions, sid, "PAUSED", "RUNNING", "resume")
    for _ in range(3):
        b.poll()
    assert k.active()  # the paper session is healthy again; only a human release clears the latch
    assert not any(a == "RELEASE" for a, _, _ in k.history()[base:])
    src = Path(risk_bridge.__file__).read_text()
    tree = ast.parse(src)
    calls = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert (
        "release" not in calls
        and "release_kill_switch"
        not in src.replace("release_kill_switch() ", "").split('"""', 2)[2]
    )
    assert not hasattr(RiskHaltBridge, "release")


def test_concurrent_bridge_workers_trip_exactly_once_per_event(sessions):
    base = len(
        [
            1
            for a, _, who in SqlKillSwitch(sessions).history()
            if a == "TRIP" and who == "risk-bridge"
        ]
    )
    halt(sessions)
    gate = threading.Barrier(8)
    out: list[object] = [None] * 8

    def work(i: int) -> None:
        gate.wait()
        out[i] = RiskHaltBridge(sessions).poll()

    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    total = sum(len(r.tripped) for r in out if isinstance(r, BridgeReport))
    with sessions() as db:
        events = int(
            db.execute(
                text(
                    "SELECT count(*) FROM kill_switch_events WHERE action='TRIP' AND actor='risk-bridge'"
                )
            ).scalar_one()
        )
        seen = int(db.execute(text("SELECT count(*) FROM risk_halt_bridge_seen")).scalar_one())
    assert events - base == total == seen  # one trip per recorded event, never more
    assert SqlKillSwitch(sessions).active()


class Flaky:
    """Session factory whose next ``fail`` openings raise (a database that cannot be read)."""

    def __init__(self, real) -> None:
        self.real, self.fail = real, 0

    def __call__(self):
        if self.fail > 0:
            self.fail -= 1
            raise RuntimeError("database says no")
        return self.real()


def test_unreadable_risk_state_fails_closed_after_the_configured_failures(sessions):
    k = SqlKillSwitch(sessions)
    base = trips(sessions)
    f = Flaky(sessions)
    b = RiskHaltBridge(f, max_read_failures=2)  # type: ignore[arg-type]
    f.fail = 1
    r1 = b.poll()
    assert r1.read_failed and r1.tripped == [] and not k.active()  # one failure is tolerated
    assert not b.poll().read_failed  # a clean read resets the count
    f.fail = 1
    assert b.poll().read_failed and not k.active()  # still only one in a row
    f.fail = 1
    r3 = b.poll()
    assert r3.read_failed and r3.tripped and k.active()  # two in a row: fail closed
    assert ("TRIP", "RISK_HALT", "risk-bridge") in k.history()
    f.fail = 1
    b.poll()
    f.fail = 1
    b.poll()
    assert trips(sessions) - base == 1  # while latched, further outages do not spam trips


def test_an_unknown_session_state_fails_closed(sessions, migrated_engine):
    sid = new_session(sessions)
    with migrated_engine.begin() as c:  # a state this bridge was never taught
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(text("UPDATE paper_sessions SET state = 'MYSTERY' WHERE id = :i"), {"i": sid})
    rep = RiskHaltBridge(sessions).poll()
    assert rep.unknown_states == ["MYSTERY"] and rep.tripped and SqlKillSwitch(sessions).active()


def test_a_session_found_halted_without_a_transition_row_still_trips(sessions, migrated_engine):
    sid = new_session(sessions, "RISK_HALTED")  # state set directly: no paper_session_states row
    rep = RiskHaltBridge(sessions).poll()
    assert rep.tripped == [f"paper_sessions:{sid}"] and SqlKillSwitch(sessions).active()
    assert RiskHaltBridge(sessions).poll().tripped == []  # and only once


def test_bridge_trips_are_in_the_audit_chain(sessions):
    from asta.db.audit_store import verify_audit_chain

    halt(sessions)
    RiskHaltBridge(sessions).poll()
    with sessions() as db:
        n = int(
            db.execute(
                text(
                    "SELECT count(*) FROM audit_events WHERE actor='risk-bridge' AND action='killswitch.trip'"
                )
            ).scalar_one()
        )
        ok, bad = verify_audit_chain(db)
    assert n >= 1 and ok and bad is None
