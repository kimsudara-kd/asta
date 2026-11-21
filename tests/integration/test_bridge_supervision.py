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
            c.execute(
                text(
                    "UPDATE execution_enablement SET enabled_until = 'epoch', enabled_by = 'none', reason = ''"
                )
            )

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
        for human in (
            "rel_alice",
            "rel_eve",
        ):  # one LOGIN-style role per human, member of asta_release
            c.execute(text(f"DROP ROLE IF EXISTS {human}"))
            c.execute(text(f"CREATE ROLE {human} NOLOGIN IN ROLE asta_release"))
    yield
    with migrated_engine.begin() as c:
        for r in (
            "rel_alice",
            "rel_eve",
            "asta_bridge",
            "asta_orders",
            "asta_monitor",
            "asta_release",
        ):
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


def as_login(engine, login: str, sql: str, **params):
    """Run as an authenticated session user (SET SESSION AUTHORIZATION changes session_user too)."""
    with engine.connect() as c:
        trans = c.begin()
        try:
            c.execute(text(f"SET SESSION AUTHORIZATION {login}"))
            r = c.execute(text(sql), params)
            out = r.all() if r.returns_rows else None
            c.execute(text("RESET SESSION AUTHORIZATION"))
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
        "SELECT release_kill_switch('a perfectly good reason here')",  # no EXECUTE
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
        "SELECT release_kill_switch('a perfectly good reason here')",
        "UPDATE kill_switch_state SET active = false",
        "INSERT INTO kill_switch_releasers (name) VALUES ('mallory')",
        "DELETE FROM order_fills",
        "UPDATE order_fills SET price = 1",
        "SELECT bridge_heartbeat()",  # only the bridge may vouch for itself
    ):
        with pytest.raises(DBAPIError):
            as_role(e, "asta_orders", sql)


def test_the_releaser_is_the_authenticated_session_user_and_cannot_be_claimed(
    roles, migrated_engine
):
    e = migrated_engine
    as_role(e, "asta_bridge", "SELECT trip_kill_switch('RISK_HALT', 'risk-bridge', 'role test')")
    reason = "reviewed the halt and releasing for the test"
    with pytest.raises(
        DBAPIError
    ):  # nobody is listed yet: even a member of asta_release is refused
        as_login(e, "rel_alice", f"SELECT release_kill_switch('{reason}')")
    with e.begin() as c:
        c.execute(text("INSERT INTO kill_switch_releasers (name) VALUES ('rel_alice')"))
    for sql in (
        "SELECT trip_kill_switch('MANUAL', 'x', 'y')",
        "UPDATE kill_switch_state SET active = false",
        "SELECT bridge_heartbeat()",
        "INSERT INTO kill_switch_releasers (name) VALUES ('eve')",
    ):
        with pytest.raises(DBAPIError):
            as_login(e, "rel_alice", sql)  # a releaser gets the release function and nothing else
    with pytest.raises(DBAPIError):  # a role that is not listed (an impersonator) is refused...
        as_login(e, "rel_eve", f"SELECT release_kill_switch('{reason}')")
    with pytest.raises(
        DBAPIError
    ):  # ...and there is no parameter to claim a name (2-arg form gone)
        as_login(
            e, "rel_eve", "SELECT release_kill_switch('rel_alice', 'a perfectly good reason here')"
        )
    assert as_role(e, "asta_bridge", "SELECT active FROM kill_switch_state")[0][0] is True
    with pytest.raises(DBAPIError):
        as_login(e, "rel_alice", "SELECT release_kill_switch('short')")  # reason still required
    assert as_login(e, "rel_alice", f"SELECT release_kill_switch('{reason}')")[0][0] is True
    last = as_role(
        e, "asta_orders", "SELECT action, actor FROM kill_switch_events ORDER BY id DESC LIMIT 1"
    )[0]
    assert tuple(last) == ("RELEASE", "rel_alice")  # recorded under the authenticated identity


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


def test_alert_state_changes_go_to_the_hash_chained_audit_log_once_per_change(
    sessions, migrated_engine
):
    from asta.db.audit_store import verify_audit_chain

    def n_events() -> int:
        with sessions() as db:
            return int(
                db.execute(
                    text("SELECT count(*) FROM audit_events WHERE action = 'security.alert_state'")
                ).scalar_one()
            )

    def last_codes() -> list[str]:
        with sessions() as db:
            return sorted(
                db.execute(
                    text(
                        "SELECT payload FROM audit_events WHERE action = 'security.alert_state' ORDER BY id DESC LIMIT 1"
                    )
                ).scalar_one()["codes"]
            )

    RiskHaltBridge(sessions).poll()  # healthy
    monitor.record_changes(sessions, monitor.check(sessions, 30))  # normalise to the current state
    base = n_events()
    set_beat_age(migrated_engine, 300)  # the bridge dies
    a = monitor.check(sessions, 30)
    assert monitor.record_changes(sessions, a) is True and n_events() == base + 1
    assert "BRIDGE_HEARTBEAT_STALE" in last_codes()
    for _ in range(3):  # the 30 s timer fires again and again: no new events while nothing changes
        assert monitor.record_changes(sessions, monitor.check(sessions, 30)) is False
    assert n_events() == base + 1
    RiskHaltBridge(sessions).poll()  # restarted: the alert clears
    assert monitor.record_changes(sessions, monitor.check(sessions, 30)) is True
    assert "BRIDGE_HEARTBEAT_STALE" not in last_codes() and n_events() == base + 2
    with sessions() as db:
        ok, bad = verify_audit_chain(db)
    assert ok and bad is None


def test_the_monitor_role_can_append_audit_events_but_still_cannot_touch_the_halt(
    roles, migrated_engine
):
    e = migrated_engine
    as_role(e, "asta_monitor", "SELECT active FROM kill_switch_state")
    for sql in (
        "UPDATE kill_switch_state SET active = true",
        "SELECT trip_kill_switch('MANUAL', 'x', 'yyyyyyyyyyyy')",
        "SELECT bridge_heartbeat()",
        "DELETE FROM audit_events",
    ):
        with pytest.raises(DBAPIError):
            as_role(e, "asta_monitor", sql)


def test_the_monitor_role_can_run_every_query_of_the_extended_checks(roles, migrated_engine):
    """R-02: asta_monitor needs SELECT on incidents and market_feed_connections or the stamp is always ok=0."""
    e = migrated_engine
    as_role(
        e, "asta_monitor", "SELECT count(*) FROM incidents WHERE kind = 'RECONCILIATION_MISMATCH'"
    )
    as_role(
        e,
        "asta_monitor",
        "SELECT extract(epoch FROM now() - coalesce(last_event_at, since)) "
        "FROM market_feed_connections WHERE source = 'LIVE'",
    )


def test_setting_the_release_guc_is_not_enough_to_release_the_kill_switch(roles, migrated_engine):
    """R-01: a direct UPDATE with the GUC set is refused unless a releaser logged a RELEASE in the same transaction."""
    import pytest
    from sqlalchemy.exc import DBAPIError

    e = migrated_engine
    as_role(e, "asta_bridge", "SELECT trip_kill_switch('RISK_HALT', 'risk-bridge', 'guard test')")
    with e.connect() as c:
        trans = c.begin()
        c.execute(text("SET LOCAL asta.kill_release_ok = 'on'"))
        with pytest.raises(DBAPIError, match="not an authorised releaser"):
            c.execute(text("UPDATE kill_switch_state SET active = false WHERE id = 1"))
        trans.rollback()
    with e.connect() as c:
        assert c.execute(text("SELECT active FROM kill_switch_state")).scalar() is True


# ------------------------------------------------------------------ M3-B step C: runtime enablement of testnet execution
ENABLED = "SELECT execution_enabled('BINANCE_TESTNET')"
WHY = "owner approved a testnet session for today"


def _list(engine, *names: str) -> None:
    with engine.begin() as c:
        for n in names:
            c.execute(text("INSERT INTO kill_switch_releasers (name) VALUES (:n)"), {"n": n})


def _on(engine) -> bool:
    return bool(as_role(engine, "asta_orders", ENABLED)[0][0])


def _events(engine) -> list[tuple[str, str]]:
    with engine.connect() as c:
        return [
            (a, b)
            for a, b in c.execute(
                text("SELECT action, actor FROM execution_enablement_events ORDER BY id")
            ).all()
        ]


def test_execution_is_off_by_default_and_only_a_listed_releaser_can_turn_it_on(
    roles, migrated_engine
):
    e = migrated_engine
    assert _on(e) is False
    for who in ("asta_orders", "asta_bridge", "asta_monitor"):  # no EXECUTE on the enable function
        with pytest.raises(DBAPIError):
            as_role(e, who, f"SELECT enable_testnet_execution(2, '{WHY}')")
    with pytest.raises(DBAPIError):  # a member of asta_release who is not listed
        as_login(e, "rel_alice", f"SELECT enable_testnet_execution(2, '{WHY}')")
    _list(e, "rel_alice")
    before = len(_events(e))
    until = as_login(e, "rel_alice", f"SELECT enable_testnet_execution(2, '{WHY}')")[0][0]
    assert until is not None and _on(e) is True
    assert _events(e)[before:] == [("ENABLE", "rel_alice")]
    with pytest.raises(DBAPIError):  # rel_eve is a releaser-role member but is not listed
        as_login(e, "rel_eve", f"SELECT enable_testnet_execution(2, '{WHY}')")


def test_enablement_arguments_are_validated_and_a_latched_kill_switch_refuses(
    roles, migrated_engine
):
    e = migrated_engine
    _list(e, "rel_alice")
    for hours, reason in ((0, WHY), (25, WHY), (-3, WHY), (2, "short"), (2, "         ")):
        with pytest.raises(DBAPIError):
            as_login(e, "rel_alice", f"SELECT enable_testnet_execution({hours}, '{reason}')")
    with pytest.raises(DBAPIError):
        as_login(e, "rel_alice", f"SELECT enable_testnet_execution(NULL, '{WHY}')")
    assert _on(e) is False
    as_role(e, "asta_bridge", "SELECT trip_kill_switch('RISK_HALT', 'risk-bridge', 'guard test')")
    with pytest.raises(DBAPIError, match="kill switch is latched"):
        as_login(e, "rel_alice", f"SELECT enable_testnet_execution(2, '{WHY}')")
    assert _on(e) is False


def test_a_direct_update_cannot_turn_execution_on_not_even_with_the_guc_or_as_the_owner(
    roles, migrated_engine
):
    e = migrated_engine
    on = "UPDATE execution_enablement SET enabled_until = now() + interval '1 hour'"
    for who in ("asta_orders", "asta_monitor", "asta_release"):
        with pytest.raises(DBAPIError):
            as_role(e, who, on)  # no UPDATE privilege
    for pre in (
        "",
        "SET LOCAL asta.kill_release_ok = 'on'",
    ):  # the owner login, not a listed releaser
        with e.connect() as c:
            trans = c.begin()
            if pre:
                c.execute(text(pre))
            with pytest.raises(DBAPIError, match="not an authorised releaser"):
                c.execute(text(on))
            trans.rollback()
    owner = _session_user(e)
    _list(e, owner)  # even a LISTED releaser needs an ENABLE event in the same transaction
    with e.connect() as c:
        trans = c.begin()
        with pytest.raises(DBAPIError, match="no ENABLE event"):
            c.execute(text(on))
        trans.rollback()
    with e.connect() as c:
        trans = c.begin()
        with pytest.raises(DBAPIError, match="24 hours"):
            c.execute(
                text("UPDATE execution_enablement SET enabled_until = now() + interval '3 days'")
            )
        trans.rollback()
    assert _on(e) is False


def _session_user(engine) -> str:
    with engine.connect() as c:
        return str(c.execute(text("SELECT session_user")).scalar_one())


def test_the_kill_switch_switches_execution_off_and_releasing_it_does_not_switch_it_back_on(
    roles, migrated_engine
):
    e = migrated_engine
    _list(e, "rel_alice")
    as_login(e, "rel_alice", f"SELECT enable_testnet_execution(4, '{WHY}')")
    assert _on(e) is True
    as_role(e, "asta_bridge", "SELECT trip_kill_switch('RISK_HALT', 'risk-bridge', 'guard test')")
    assert _on(e) is False
    assert ("AUTO_OFF", "kill-switch") in _events(e)
    as_login(
        e, "rel_alice", "SELECT release_kill_switch('reviewed the halt and releasing for the test')"
    )
    assert _on(e) is False  # still off: a person must enable it again


def test_a_releaser_can_disable_and_the_enablement_expires_by_itself(roles, migrated_engine):
    e = migrated_engine
    _list(e, "rel_alice")
    as_login(e, "rel_alice", f"SELECT enable_testnet_execution(1, '{WHY}')")
    assert (
        as_login(
            e, "rel_alice", "SELECT disable_testnet_execution('stopping the testnet session now')"
        )[0][0]
        is True
    )
    assert _on(e) is False and ("DISABLE", "rel_alice") in _events(e)
    with pytest.raises(DBAPIError):
        as_login(e, "rel_alice", "SELECT disable_testnet_execution('x')")  # reason required
    as_login(e, "rel_alice", f"SELECT enable_testnet_execution(1, '{WHY}')")
    with e.begin() as c:  # time passes: the window ends without anyone doing anything
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text("UPDATE execution_enablement SET enabled_until = now() - interval '1 minute'")
        )
    assert _on(e) is False


def test_the_event_log_is_append_only_and_the_orders_role_can_only_read(roles, migrated_engine):
    e = migrated_engine
    _list(e, "rel_alice")
    as_login(e, "rel_alice", f"SELECT enable_testnet_execution(1, '{WHY}')")
    for sql in (
        "UPDATE execution_enablement_events SET actor = 'x'",
        "DELETE FROM execution_enablement_events",
        "TRUNCATE execution_enablement_events",
        "DELETE FROM execution_enablement",
        "TRUNCATE execution_enablement",
    ):
        with pytest.raises(DBAPIError):
            with e.begin() as c:
                c.execute(text(sql))
    for who in ("asta_orders", "asta_monitor"):
        with pytest.raises(DBAPIError):
            as_role(
                e,
                who,
                "INSERT INTO execution_enablement_events (action, actor, reason) VALUES ('ENABLE','x','y')",
            )
        assert as_role(e, who, "SELECT count(*) FROM execution_enablement_events")[0][0] >= 1


def test_the_application_view_fails_closed_and_the_monitor_announces_enablement(
    roles, migrated_engine
):
    from asta.orders import alerting
    from asta.orders.enablement import SqlEnablement

    e = migrated_engine
    sessions = sessionmaker(e, expire_on_commit=False)
    view = SqlEnablement(sessions)
    assert view.active("BINANCE_TESTNET") is False and view.active("OKX") is False
    _list(e, "rel_alice")
    as_login(e, "rel_alice", f"SELECT enable_testnet_execution(1, '{WHY}')")
    assert view.active("BINANCE_TESTNET") is True and view.active("OKX") is False
    assert "EXECUTION_ENABLED" in [a.code for a in alerting.extended_checks(sessions, None)]
    as_role(e, "asta_monitor", ENABLED)  # the monitor role can run the new query (R-02 lesson)

    class Broken:
        def __call__(self):
            raise RuntimeError("database down")

    assert SqlEnablement(Broken()).active("BINANCE_TESTNET") is False  # type: ignore[arg-type]


def test_releasing_the_kill_switch_never_re_enables_and_the_enable_function_locks_the_state_row(
    roles, migrated_engine
):
    """Review S-02: an enablement can not survive a latch/release cycle (even a future-dated one that slipped in)."""
    e = migrated_engine
    _list(e, "rel_alice")
    as_role(e, "asta_bridge", "SELECT trip_kill_switch('RISK_HALT', 'risk-bridge', 'guard test')")
    with (
        e.begin() as c
    ):  # simulate the race outcome: a future-dated enablement behind a latched switch
        c.execute(text("SET LOCAL session_replication_role = replica"))
        c.execute(
            text("UPDATE execution_enablement SET enabled_until = now() + interval '2 hours'")
        )
    assert _on(e) is False  # inert while latched
    as_login(
        e, "rel_alice", "SELECT release_kill_switch('reviewed the halt and releasing for the test')"
    )
    assert (
        _on(e) is False
    )  # the release cleared it: nothing is enabled until a person enables it again
    assert ("AUTO_OFF", "kill-switch") in _events(e)
    body = (
        Path(__file__).parents[2] / "migrations/versions/0014_m3b_enablement_race_and_release.py"
    ).read_text()
    assert "FOR SHARE" in body


# ------------------------------------------------------------------ venue sync against the real SQL fill store
def test_sync_order_uses_the_sql_fill_store_idempotently_and_records_conflicts(
    roles, migrated_engine
):
    from decimal import Decimal as Dec

    from asta.orders.boundary import GatedExchange, InternalGate, PortKind
    from asta.orders.fills import Fill
    from asta.orders.lifecycle import State
    from asta.orders.ports import QueryKind
    from asta.orders.sql_store import SqlFillStore, SqlIntentStore, SqlKillSwitch
    from asta.orders.venue_sync import ledger_fills, sync_order
    from tests.orders.fakes import make_intent

    e = migrated_engine
    sessions = sessionmaker(e, expire_on_commit=False)
    intents, fills = SqlIntentStore(sessions), SqlFillStore(sessions)
    i = make_intent(
        1,
        session=__import__("uuid").uuid4().hex * 2,
        order_type="LIMIT",
        quote_notional=None,
        quantity=Dec("0.0002"),
        limit_price=Dec("50000"),
    )
    assert intents.insert_if_absent(i)
    intents.save(i.moved(State.DISPATCHED))
    intents.save(intents.get(i.decision_id).moved(State.ACCEPTED))  # type: ignore[union-attr]

    class Port:
        rows = [
            Fill("1", i.decision_id, Dec("0.0001"), Dec("50000"), Dec("0.0000001"), "BTC"),
            Fill("2", i.decision_id, Dec("0.0001"), Dec("50000"), Dec("0.0000001"), "BTC"),
        ]

        def trades(self, cid, skip=0):
            return (
                QueryKind.FOUND,
                self.rows[skip : skip + 1],
                skip + 1 < len(self.rows),
            )  # one trade per page

    port = Port()
    gx = GatedExchange(port, PortKind.SIMULATED, InternalGate(SqlKillSwitch(sessions)))  # type: ignore[arg-type]
    rep = sync_order(gx, fills, i)
    assert (rep.new, rep.duplicates, rep.conflicts) == (2, 0, [])
    assert intents.get(i.decision_id).state is State.FILLED  # type: ignore[union-attr]
    assert [(f.trade_id, f.quantity) for f in ledger_fills(sessions, i.decision_id)] == [
        ("1", Dec("0.0001")),
        ("2", Dec("0.0001")),
    ]
    again = sync_order(gx, fills, i)
    assert (again.new, again.duplicates) == (0, 2)
    port.rows[0] = Fill(
        "1", i.decision_id, Dec("0.0001"), Dec("49999"), Dec("0.0000001"), "BTC"
    )  # same id, other price
    bad = sync_order(gx, fills, i)
    assert bad.conflicts and "FillConflict" in bad.conflicts[0]
    with e.connect() as c:
        n = c.execute(
            text("SELECT count(*) FROM order_fill_conflicts WHERE decision_id = :d"),
            {"d": i.decision_id},
        ).scalar()
    assert n >= 1  # the append-only conflict log has it
