"""M3-B-R3: bridge heartbeat (fail-closed), failure monitor, least-privilege roles (PostgreSQL)."""

from __future__ import annotations

import random
import uuid
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from asta.orders import monitor
from asta.orders.boundary import GatedExchange, InternalGate, PortKind
from asta.orders.dispatch import Dispatcher
from asta.orders.lifecycle import State
from asta.orders.pretrade import DayState, PreTradeLimits
from asta.orders.risk_bridge import RiskHaltBridge
from asta.orders.sql_store import BridgedKillState, SqlIntentStore, SqlKillSwitch
from tests.orders.fakes import FakeExchange, make_intent

pytestmark = pytest.mark.db
ROLES = Path(__file__).parents[2] / "infra/security-host/mac/app-vm/pg_roles_m3b.sql"
LIM = PreTradeLimits(
    frozenset({"BINANCE_TESTNET"}), frozenset({"BTC-USDT"}), D("50"), 99, D("5000"), D("100")
)


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
            c.execute(text("UPDATE risk_halt_bridge_heartbeat SET beat_at = 'epoch'"))

    reset()
    yield
    reset()


def set_beat_age(engine, seconds: float) -> None:
    with engine.begin() as c:
        c.execute(
            text(
                "UPDATE risk_halt_bridge_heartbeat SET beat_at = now() - make_interval(secs => :s)"
            ),
            {"s": seconds},
        )


def test_a_dead_or_missing_bridge_closes_the_order_path(sessions, migrated_engine):
    kill = SqlKillSwitch(sessions)
    state = BridgedKillState(kill, sessions, max_heartbeat_age_s=30)
    assert state.active() is True  # never beaten: the bridge is not proven alive -> closed
    RiskHaltBridge(sessions).poll()  # one successful poll beats
    assert state.active() is False and not kill.active()
    set_beat_age(migrated_engine, 120)  # the bridge process died two minutes ago
    assert state.active() is True and not kill.active()  # closed although nothing is latched
    RiskHaltBridge(sessions).poll()
    assert (
        state.active() is False
    )  # a restarted bridge reopens it; no human needed for a mere outage


def test_dispatch_is_refused_while_the_bridge_is_dead_and_resumes_when_it_beats(
    sessions, migrated_engine
):
    kill = SqlKillSwitch(sessions)
    ex = FakeExchange(random.Random(1))
    gate = GatedExchange(ex, PortKind.SIMULATED, InternalGate(BridgedKillState(kill, sessions, 30)))
    d = Dispatcher(
        gate,
        SqlIntentStore(sessions),
        LIM,
        lambda: DayState(0, D(0), False),
        lambda s: D("100"),
        lambda: 1_000,
    )
    salt = uuid.uuid4().hex * 2  # a fresh decision-id space per run (claims are permanent)
    r = d.handle(make_intent(1, session=salt))
    assert "KILL_SWITCH" in r.reasons and ex.send_calls == 0  # no heartbeat yet
    RiskHaltBridge(sessions).poll()
    assert d.handle(make_intent(2, session=salt)).state == State.ACCEPTED
    set_beat_age(migrated_engine, 300)
    assert "KILL_SWITCH" in d.handle(make_intent(3, session=salt)).reasons
    assert ex.send_calls == 1


def test_an_unreadable_database_also_closes_the_gate(sessions):
    class Broken:
        def __call__(self):
            raise RuntimeError("down")

    assert BridgedKillState(SqlKillSwitch(sessions), Broken(), 30).active() is True  # type: ignore[arg-type]


def test_monitor_reports_dead_bridge_latch_and_unreadable_database(sessions, migrated_engine):
    codes = lambda: {a.code for a in monitor.check(sessions, 30)}  # noqa: E731
    assert "BRIDGE_HEARTBEAT_STALE" in codes()
    assert monitor.exit_code(monitor.check(sessions, 30)) == 2
    RiskHaltBridge(sessions).poll()
    assert codes() == set() and monitor.exit_code(monitor.check(sessions, 30)) == 0
    SqlKillSwitch(sessions).trip("MANUAL", "alice", "test the monitor")
    a = monitor.check(sessions, 30)
    assert {x.code for x in a} == {"KILL_SWITCH_LATCHED"} and monitor.exit_code(a) == 1
    set_beat_age(migrated_engine, 90)
    assert monitor.exit_code(monitor.check(sessions, 30)) == 2

    class Broken:
        def __call__(self):
            raise RuntimeError("down")

    bad = monitor.check(Broken(), 30)  # type: ignore[arg-type]
    assert [x.code for x in bad] == ["DATABASE_UNREADABLE"] and monitor.exit_code(bad) == 2


# ------------------------------------------------------------------ least-privilege roles
@pytest.fixture
def roles(migrated_engine):
    body = "\n".join(ln for ln in ROLES.read_text().splitlines() if not ln.strip().startswith("--"))
    do_end = body.index("END $$;") + len("END $$;")
    stmts = [body[:do_end].rstrip(";")] + [x.strip() for x in body[do_end:].split(";") if x.strip()]
    with migrated_engine.begin() as c:
        for stmt in stmts:
            c.exec_driver_sql(stmt)
    yield
    with migrated_engine.begin() as c:
        for r in ("asta_bridge", "asta_orders", "asta_release"):
            c.execute(text(f"DROP OWNED BY {r}"))
            c.execute(text(f"DROP ROLE IF EXISTS {r}"))


def as_role(engine, role: str, sql: str, **params):
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text(f"SET LOCAL ROLE {role}"))
            r = c.execute(text(sql), params)
            out = r.all() if r.returns_rows else None
            trans.commit()
            return out
        except Exception:
            trans.rollback()
            raise


def test_the_bridge_role_can_latch_and_beat_but_nothing_else(roles, migrated_engine):
    e = migrated_engine
    as_role(e, "asta_bridge", "SELECT bridge_heartbeat()")
    as_role(e, "asta_bridge", "SELECT trip_kill_switch('RISK_HALT', 'risk-bridge', 'role test')")
    assert as_role(e, "asta_bridge", "SELECT active FROM kill_switch_state")[0][0] is True
    for sql in (
        "SELECT release_kill_switch('alice', 'a perfectly good reason here')",  # no EXECUTE
        "UPDATE kill_switch_state SET active = false",  # no table write
        "UPDATE kill_switch_state SET active = true",
        "INSERT INTO kill_switch_releasers (name) VALUES ('mallory')",
        "INSERT INTO kill_switch_events (action, actor, reason) VALUES ('RELEASE', 'x', 'y')",
        "DELETE FROM risk_halt_bridge_seen",
        "UPDATE order_intents SET state = 'FILLED'",
        "INSERT INTO order_fills (decision_id, trade_id, quantity, price, fee, fee_asset) VALUES ('a', 'b', 1, 1, 0, 'X')",
    ):
        with pytest.raises(DBAPIError):
            as_role(e, "asta_bridge", sql)
    assert (
        as_role(e, "asta_bridge", "SELECT active FROM kill_switch_state")[0][0] is True
    )  # still latched


def test_the_orders_role_cannot_release_or_edit_the_halt_tables(roles, migrated_engine):
    e = migrated_engine
    as_role(
        e, "asta_orders", "SELECT trip_kill_switch('FILL_CONFLICT', 'fill-ingest', 'role test')"
    )
    for sql in (
        "SELECT release_kill_switch('alice', 'a perfectly good reason here')",
        "UPDATE kill_switch_state SET active = false",
        "INSERT INTO kill_switch_releasers (name) VALUES ('mallory')",
        "DELETE FROM order_fills",
        "UPDATE order_fills SET price = 1",
        "SELECT bridge_heartbeat()",  # only the bridge may vouch for itself
    ):
        with pytest.raises(DBAPIError):
            as_role(e, "asta_orders", sql)


def test_the_release_role_can_only_release_and_only_as_a_listed_actor(roles, migrated_engine):
    e = migrated_engine
    as_role(e, "asta_bridge", "SELECT trip_kill_switch('RISK_HALT', 'risk-bridge', 'role test')")
    with pytest.raises(DBAPIError):  # nobody is listed yet
        as_role(
            e, "asta_release", "SELECT release_kill_switch('alice', 'a perfectly good reason here')"
        )
    with e.begin() as c:
        c.execute(text("INSERT INTO kill_switch_releasers (name) VALUES ('alice')"))
    for sql in (
        "SELECT trip_kill_switch('MANUAL', 'x', 'y')",
        "UPDATE kill_switch_state SET active = false",
        "SELECT bridge_heartbeat()",
        "INSERT INTO kill_switch_releasers (name) VALUES ('eve')",
    ):
        with pytest.raises(DBAPIError):
            as_role(e, "asta_release", sql)
    with pytest.raises(DBAPIError):  # an unlisted actor name is still refused
        as_role(
            e,
            "asta_release",
            "SELECT release_kill_switch('mallory', 'a perfectly good reason here')",
        )
    assert (
        as_role(
            e,
            "asta_release",
            "SELECT release_kill_switch('alice', 'reviewed and releasing for the test')",
        )[0][0]
        is True
    )


def test_public_has_no_execute_on_the_halt_functions(migrated_engine):
    with migrated_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT p.proname, p.prosecdef, coalesce(array_to_string(p.proacl, ','), '') FROM pg_proc p"
                " WHERE p.proname IN ('trip_kill_switch', 'release_kill_switch', 'bridge_heartbeat')"
            )
        ).all()
    assert {r[0] for r in rows} == {"trip_kill_switch", "release_kill_switch", "bridge_heartbeat"}
    for name, secdef, acl in rows:
        assert secdef, name  # SECURITY DEFINER
        assert not any(entry.startswith("=") for entry in acl.split(",") if entry), (
            name,
            acl,
        )  # no PUBLIC grant
