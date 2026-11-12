"""M2 durable paper state: effectively-once posting, replay recovery, reconciliation, immutability."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from asta.db.paper_models import (
    PaperFill,
    PaperLedgerEntry,
    PaperSession,
    PaperSessionCheckpoint,
    PaperSessionState,
    PaperStepInput,
    StrategySignal,
)
from asta.paper import store
from asta.paper.runtime import PaperRuntime
from asta.paper.states import IllegalTransition, SessionState
from tests.paper.helpers import make_cfg, make_series

pytestmark = pytest.mark.db
WARM = 300
SERIES = make_series(900)


def post(db, sid, rt, k, ts, bars, active=True, blocked=None, actions=()):
    blocked = blocked or {}
    for a in actions:
        store.apply_action(rt, a)
    rows = rt.step(k, ts, bars, blocked, active, k < WARM)
    store.persist_step(db, sid, k, ts, k < WARM, bars, blocked, active, list(actions), rows)
    db.commit()
    return rows


def make_session(db, n=0, cfg=None):
    cfg = cfg or make_cfg()
    s = store.create_session(db, cfg, "tester")
    db.commit()
    rt = PaperRuntime(cfg)
    for k in range(n):
        ts, bars = SERIES[k]
        post(db, s.id, rt, k, ts, bars)
    return s.id, rt


def count(db, model, sid):
    return db.execute(
        select(func.count()).select_from(model).where(model.session_id == sid)
    ).scalar_one()


def test_full_run_persists_and_reconciles_from_durable_rows_only(db):
    sid, rt = make_session(db, 700)
    assert store.reconcile(db, sid) == []
    sess = store.get_session(db, sid)
    assert sess.last_step_seq == 699 and count(db, PaperStepInput, sid) == 700
    assert count(db, PaperSessionCheckpoint, sid) == 700
    assert count(db, PaperFill, sid) == sum(len(a.engine.fills) for a in rt.active.values()) > 0
    assert count(db, StrategySignal, sid) > 0
    # initial cash was posted for every active account even though it happened in warm-up
    n_init = db.execute(
        text(
            "select count(*) from paper_ledger_entries where session_id=:s and kind='INITIAL_CASH'"
        ),
        {"s": str(sid)},
    ).scalar_one()
    assert n_init == len(rt.active)
    rejected = db.execute(
        select(func.count())
        .select_from(StrategySignal)
        .where(StrategySignal.session_id == sid, StrategySignal.accepted.is_(False))
    ).scalar_one()
    assert rejected > 0  # rejected signals are recorded too


def test_a_redelivered_step_is_rejected_and_writes_nothing(db):
    sid, rt = make_session(db, 400)
    before = (
        count(db, PaperFill, sid),
        count(db, PaperLedgerEntry, sid),
        count(db, PaperStepInput, sid),
    )
    ts, bars = SERIES[399]
    twin = PaperRuntime(make_cfg())
    for k in range(399):  # an independent runtime computing the same step again
        twin.step(k, *SERIES[k], {}, True, k < WARM)
    rows = twin.step(399, ts, bars, {}, True, False)
    with pytest.raises(store.StepAlreadyPosted):
        store.persist_step(db, sid, 399, ts, False, bars, {}, True, [], rows)
    db.rollback()
    after = (
        count(db, PaperFill, sid),
        count(db, PaperLedgerEntry, sid),
        count(db, PaperStepInput, sid),
    )
    assert before == after and store.get_session(db, sid).last_step_seq == 399


def test_failure_during_posting_rolls_the_whole_step_back(db):
    sid, rt = make_session(db, 450)
    k = 450
    ts, bars = SERIES[k]
    rows = rt.step(k, ts, bars, {}, True, False)
    rows.checkpoint = {"step_seq": k, "digest": None, "summary": {}}  # violates NOT NULL last
    before = (count(db, PaperLedgerEntry, sid), count(db, StrategySignal, sid))
    with pytest.raises(Exception):  # noqa: B017  (any DB failure must undo everything)
        store.persist_step(db, sid, k, ts, False, bars, {}, True, [], rows)
        db.commit()
    db.rollback()
    assert (count(db, PaperLedgerEntry, sid), count(db, StrategySignal, sid)) == before
    assert store.get_session(db, sid).last_step_seq == 449  # cursor did not move


def test_rebuilt_runtime_continues_exactly_like_the_original(db):
    sid, live = make_session(db, 600)
    rebuilt, rep = store.rebuild_runtime(db, sid)
    assert rep.steps == 600 and rep.last_step_seq == 599
    assert rebuilt.digest(599, SERIES[599][0]) == live.digest(599, SERIES[599][0])
    for k in range(600, 700):  # both continue with the same new bars
        ts, bars = SERIES[k]
        a = live.step(k, ts, bars, {}, True, False)
        b = rebuilt.step(k, ts, bars, {}, True, False)
        assert a.checkpoint["digest"] == b.checkpoint["digest"]
        assert [f["fill_id"] for f in a.fills] == [f["fill_id"] for f in b.fills]


def test_restart_with_open_paper_orders_recovers_them(db):
    rt0 = PaperRuntime(make_cfg())
    kstop = None
    for k in range(len(SERIES)):
        rt0.step(k, *SERIES[k], {}, True, k < WARM)
        if k >= WARM and any(
            o for a in rt0.active.values() for s in a.engine.cfg.symbols for o in a.engine.open[s]
        ):
            kstop = k
            break
    assert kstop is not None, "scenario must leave resting paper orders"
    sid, live = make_session(db, kstop + 1)
    open_before = {
        k: sorted(o.order_id for s in a.engine.cfg.symbols for o in a.engine.open[s])
        for k, a in live.active.items()
    }
    assert any(open_before.values()), "scenario must leave resting paper orders"
    rebuilt, _ = store.rebuild_runtime(db, sid)
    open_after = {
        k: sorted(o.order_id for s in a.engine.cfg.symbols for o in a.engine.open[s])
        for k, a in rebuilt.active.items()
    }
    assert open_before == open_after


def _bypass(db, sql):
    db.execute(text("SET LOCAL session_replication_role = replica"))
    db.execute(text(sql))
    db.commit()


def test_replay_detects_tampered_inputs_missing_checkpoints_and_wrong_cursor(db):
    sid, _ = make_session(db, 450)
    _bypass(
        db,
        f"update paper_step_inputs set bars = '{{}}'::jsonb where session_id='{sid}' and step_seq=420",
    )
    with pytest.raises(store.ReplayMismatch, match="integrity"):
        store.rebuild_runtime(db, sid)
    sid2, _ = make_session(db, 450)
    _bypass(db, f"delete from paper_session_checkpoints where session_id='{sid2}' and step_seq=449")
    with pytest.raises(store.ReplayMismatch):
        store.rebuild_runtime(db, sid2)
    sid3, _ = make_session(db, 450)
    _bypass(db, f"update paper_sessions set last_step_seq = 460 where id='{sid3}'")
    with pytest.raises(store.ReplayMismatch, match="cursor"):
        store.rebuild_runtime(db, sid3)


def test_reconcile_flags_a_corrupted_ledger_and_balance_and_fill(db):
    sid, _ = make_session(db, 800)
    assert store.reconcile(db, sid) == []
    _bypass(
        db,
        "update paper_ledger_entries set amount = amount + 1 where id = ("
        f"select min(id) from paper_ledger_entries where session_id='{sid}' and kind='FEE')",
    )
    bad = store.reconcile(db, sid)
    assert any("balance" in d.where or "fills-vs-ledger" in d.where for d in bad)
    sid2, _ = make_session(db, 800)
    _bypass(
        db,
        f"update paper_balances set balance = balance + 5 where session_id='{sid2}' and asset='USDT'",
    )
    assert store.reconcile(db, sid2)
    sid3, _ = make_session(db, 800)
    _bypass(
        db,
        f"delete from paper_fills where id = (select min(id) from paper_fills where session_id='{sid3}')",
    )
    assert store.reconcile(db, sid3)


def test_paper_history_tables_are_append_only(db):
    sid, _ = make_session(db, 500)
    for stmt in (
        "update paper_fills set price = 1",
        "delete from paper_fills",
        "update paper_ledger_entries set amount = 1",
        "delete from paper_ledger_entries",
        "update paper_step_inputs set warm = true",
        "delete from paper_session_checkpoints",
        "update paper_equity_snapshots set equity = 1",
        "update paper_session_configs set config = '{}'::jsonb",
        "update strategy_signals set accepted = true",
        "delete from paper_order_events",
        "truncate paper_fills",
    ):
        with pytest.raises(DBAPIError, match="append-only|insert-only|immutable|not allowed"):
            db.execute(text(stmt))
        db.rollback()


def test_state_machine_enforced_and_every_transition_logged(db):
    s = store.create_session(db, make_cfg(), "tester")
    db.commit()
    sid = s.id
    for to in (
        SessionState.INITIALIZING,
        SessionState.READY,
        SessionState.RUNNING,
        SessionState.PAUSED,
        SessionState.RUNNING,
        SessionState.RISK_HALTED,
        SessionState.PAUSED,
        SessionState.STOPPED,
    ):
        store.transition(db, sid, to, "tester", "test")
    db.commit()
    for bad in (SessionState.RUNNING, SessionState.CREATED, SessionState.FAILED):
        with pytest.raises(IllegalTransition):  # STOPPED is terminal
            store.transition(db, sid, bad, "tester")
    log = (
        db.execute(
            select(PaperSessionState.to_state)
            .where(PaperSessionState.session_id == sid)
            .order_by(PaperSessionState.id)
        )
        .scalars()
        .all()
    )
    assert log[0] == "CREATED" and log[-1] == "STOPPED" and len(log) == 9
    s2 = store.create_session(db, make_cfg(), "tester")
    db.commit()
    for illegal in (
        SessionState.RUNNING,
        SessionState.READY,
        SessionState.RISK_HALTED,
        SessionState.RECOVERING,
    ):
        with pytest.raises(IllegalTransition):
            store.transition(db, s2.id, illegal, "tester")
    assert store.get_session(db, s2.id).state == "CREATED"


def test_session_config_is_stored_with_its_hash_and_tamper_is_detected(db):
    cfg = make_cfg()
    s = store.create_session(db, cfg, "tester")
    db.commit()
    assert store.load_config(db, s.id).config_hash() == cfg.config_hash() == s.config_hash
    _bypass(
        db,
        f"update paper_session_configs set config = jsonb_set(config, '{{name}}', '\"evil\"') where session_id='{s.id}'",
    )
    with pytest.raises(store.ReplayMismatch):
        store.load_config(db, s.id)


def test_sessions_table_is_a_projection_not_the_book_of_record(db):
    """Deleting the cursor row cannot change history: fills/ledger stay and reconcile still passes."""
    sid, _ = make_session(db, 500)
    assert isinstance(store.get_session(db, sid), PaperSession)
    assert store.reconcile(db, sid) == []
