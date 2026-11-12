"""M2 durable worker: leases, retries, dead-letter, crash recovery, concurrency, fail-closed paths.

Deterministic fault injection: the worker exposes named fault points; tests raise a BaseException
(`ProcessKilled`, which the worker deliberately does NOT catch) to emulate abrupt process death
at that exact point, then start a brand-new worker with an empty cache and verify the outcome."""

import threading
from datetime import timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from asta.core.timeutil import utcnow
from asta.db.models import Incident
from asta.db.paper_models import (
    DurableJob,
    PaperFill,
    PaperSessionCheckpoint,
    PaperStepInput,
)
from asta.paper import jobs, store
from asta.paper.config import StrategySpec
from asta.paper.runtime import PaperRuntime
from asta.paper.states import SessionState
from asta.paper.store import bar_to_json
from asta.paper.worker import StepWorker
from tests.paper.helpers import T0, make_cfg, make_series

pytestmark = pytest.mark.db
WARM = 300
N = 420
CFG = make_cfg(enabled_venues=("BINANCE",), strategies=(StrategySpec("regime_grid"),))
SERIES = make_series(N, venues=("BINANCE",))


class ProcessKilled(BaseException):
    """Abrupt process death (not an Exception: the worker must not 'handle' it)."""


@pytest.fixture
def factory(migrated_engine, db):  # db fixture = clean tables
    return sessionmaker(migrated_engine, expire_on_commit=False)


def reference_digests(n=N):
    rt = PaperRuntime(CFG)
    out = []
    for k in range(n):
        ts, bars = SERIES[k]
        cp = rt.step(k, ts, bars, {}, True, k < WARM).checkpoint
        assert cp is not None
        out.append(cp["digest"])
    return out, rt


REF_DIGESTS, REF_RT = reference_digests()


def new_session(factory, cfg=CFG, state=SessionState.RUNNING):
    with factory() as db:
        s = store.create_session(db, cfg, "tester")
        for st in (SessionState.INITIALIZING, SessionState.READY, SessionState.RUNNING):
            store.transition(db, s.id, st, "tester")
            if st == state:
                break
        db.commit()
        return s.id


def enqueue_steps(factory, sid, lo=0, hi=N, clock_now=None):
    with factory() as db:
        for k in range(lo, hi):
            ts, bars = SERIES[k]
            jobs.enqueue(
                db,
                f"STEP:{sid}:{ts.isoformat()}",
                jobs.KIND_STEP,
                sid,
                {
                    "step_ts": ts.isoformat(),
                    "warm": k < WARM,
                    "bars": {key: bar_to_json(b) for key, b in bars.items()},
                },
                run_after=T0,
            )
        db.commit()


def clock_at(k):
    """A clock that makes step k look 'on time' (no late-step muting)."""
    return lambda: SERIES[k][0] + timedelta(hours=1, seconds=5)


def cursor(factory, sid):
    with factory() as db:
        return store.get_session(db, sid).last_step_seq


def checkpoints(factory, sid):
    with factory() as db:
        rows = db.execute(
            select(PaperSessionCheckpoint.step_seq, PaperSessionCheckpoint.digest)
            .where(PaperSessionCheckpoint.session_id == sid)
            .order_by(PaperSessionCheckpoint.step_seq)
        ).all()
    return [d for _, d in rows]


def worker(factory, **kw):
    w = StepWorker(factory, clock=kw.pop("clock", lambda: T0 + timedelta(hours=N + 5)), **kw)
    return w


def late_ok_worker(factory, **kw):
    """Clock far in the past of 'now' relative to steps would mute strategies as 'late'; the tests
    that compare with the reference (always active) pin the clock right after each step."""
    state = {"now": T0}

    def clk():
        return state["now"]

    w = StepWorker(factory, clock=clk, **kw)
    w._state = state  # type: ignore[attr-defined]
    return w


def drain_steps(w, factory, sid, hi=N):
    """Process jobs one at a time, advancing the worker clock so steps are never 'late'."""
    while True:
        c = cursor(factory, sid)
        if c >= hi - 1:
            return
        # 'on time' for the step (well inside the late threshold) but past any 60 s lease/backoff
        w._state["now"] = SERIES[min(c + 1, N - 1)][0] + timedelta(hours=1, seconds=125)
        if not w.run_once():
            return


# ------------------------------------------------------------------ happy path
def test_worker_applies_every_step_exactly_like_the_in_memory_reference(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid)
    w = late_ok_worker(factory)
    drain_steps(w, factory, sid)
    assert cursor(factory, sid) == N - 1
    assert checkpoints(factory, sid) == REF_DIGESTS
    with factory() as db:
        assert store.reconcile(db, sid) == []
        n = db.execute(
            select(func.count()).select_from(DurableJob).where(DurableJob.status == "DONE")
        ).scalar_one()
        assert n == N


def test_job_creation_is_idempotent_and_redelivery_is_a_noop(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 5)
    enqueue_steps(factory, sid, 0, 5)  # same keys again
    with factory() as db:
        assert db.execute(select(func.count()).select_from(DurableJob)).scalar_one() == 5
    w = late_ok_worker(factory)
    drain_steps(w, factory, sid, 5)
    before = checkpoints(factory, sid)
    # a duplicate job for an already-posted step (different key) must not post again
    with factory() as db:
        ts = SERIES[2][0]
        jobs.enqueue(
            db,
            "dup-of-step-2",
            jobs.KIND_STEP,
            sid,
            {
                "step_ts": ts.isoformat(),
                "warm": True,
                "bars": {k: bar_to_json(b) for k, b in SERIES[2][1].items()},
            },
            run_after=T0,
        )
        db.commit()
    # the duplicate sorts after the others, but cursor-based idempotency catches it regardless
    w._state["now"] = T0 + timedelta(days=30)
    while w.run_once():
        pass
    assert checkpoints(factory, sid) == before and cursor(factory, sid) == 4


# ------------------------------------------------------------------ crash recovery (fault injection)
FAULT_POINTS = [
    "before_handle",
    "before_step",
    "after_step_before_persist",
    "after_persist",
    "before_commit",
]


@pytest.mark.parametrize("point", FAULT_POINTS)
def test_abrupt_death_at_any_point_then_restart_reaches_the_identical_state(factory, point):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 340)
    w1 = late_ok_worker(factory)
    drain_steps(w1, factory, sid, 330)  # run well into live trading (warm-up is 300)
    assert cursor(factory, sid) == 329

    killed = {"n": 0}

    def die(p):
        if p == point and killed["n"] == 0:
            killed["n"] += 1
            raise ProcessKilled(p)

    w2 = late_ok_worker(factory, fault=die)
    w2._state["now"] = SERIES[330][0] + timedelta(hours=1, seconds=5)
    with pytest.raises(ProcessKilled):
        w2.run_once()
    # the job stays LEASED by the dead worker until its lease expires
    with factory() as db:
        leased = db.execute(
            select(func.count()).select_from(DurableJob).where(DurableJob.status == "LEASED")
        ).scalar_one()
    assert leased == 1
    assert cursor(factory, sid) == 329  # nothing half-posted

    # brand-new process: empty cache, clock past the lease
    w3 = late_ok_worker(factory)
    w3._state["now"] = SERIES[330][0] + timedelta(hours=1, minutes=5)  # lease (60s) long expired
    assert not w3.runtimes
    drain_steps(w3, factory, sid, 340)
    assert cursor(factory, sid) == 339
    assert checkpoints(factory, sid) == REF_DIGESTS[:340]
    with factory() as db:
        assert store.reconcile(db, sid) == []
        fills = db.execute(
            select(func.count()).select_from(PaperFill).where(PaperFill.session_id == sid)
        ).scalar_one()
        steps = db.execute(
            select(func.count()).select_from(PaperStepInput).where(PaperStepInput.session_id == sid)
        ).scalar_one()
    assert steps == 340
    ref = PaperRuntime(CFG)
    for k in range(340):
        ref.step(k, *SERIES[k], {}, True, k < WARM)
    assert fills == sum(len(a.engine.fills) for a in ref.active.values())  # no duplicate postings


def test_two_workers_never_process_the_same_job_and_results_are_identical(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 380)
    stop = {"flag": False}
    errors = []

    def run(name):
        w = StepWorker(factory, worker_id=name, clock=lambda: T0 + timedelta(days=40))
        try:
            while not stop["flag"]:
                if not w.run_once():
                    break
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=300)
    assert not errors
    # late clock => strategies muted: the invariant under test is exactly-once posting + order
    with factory() as db:
        assert (
            db.execute(
                select(func.count())
                .select_from(PaperStepInput)
                .where(PaperStepInput.session_id == sid)
            ).scalar_one()
            == 380
        )
        done = db.execute(
            select(func.count()).select_from(DurableJob).where(DurableJob.status == "DONE")
        ).scalar_one()
        assert done == 380
        assert store.reconcile(db, sid) == []
    assert cursor(factory, sid) == 379
    # every step input is unique and consecutive (STEP jobs of one session are serialised)
    with factory() as db:
        seqs = (
            db.execute(
                select(PaperStepInput.step_seq)
                .where(PaperStepInput.session_id == sid)
                .order_by(PaperStepInput.step_seq)
            )
            .scalars()
            .all()
        )
    assert seqs == list(range(380))


# ------------------------------------------------------------------ retries, backoff, dead letter
def test_transient_failures_back_off_then_dead_letter_with_an_incident(factory):
    sid = new_session(factory)
    with factory() as db:
        jobs.enqueue(
            db, "poison", jobs.KIND_STEP, sid, {"step_ts": "garbage", "warm": True}, max_attempts=3
        )
        db.commit()
    now = {"t": utcnow()}
    w = StepWorker(factory, clock=lambda: now["t"])
    for attempt in range(1, 4):
        assert w.run_once()
        with factory() as db:
            j = db.execute(select(DurableJob).where(DurableJob.job_key == "poison")).scalar_one()
        if attempt < 3:
            assert j.status == "PENDING" and j.attempts == attempt
            assert (j.run_after - now["t"]).total_seconds() == jobs.backoff_seconds(attempt)
            assert not w.run_once()  # backing off: not runnable yet
            now["t"] = j.run_after + timedelta(seconds=1)
    assert j.status == "DEAD" and j.failure_class == "TRANSIENT"
    with factory() as db:
        inc = db.execute(
            select(Incident).where(Incident.dedupe_key == f"job-dead:{j.id}")
        ).scalar_one()
        assert inc.severity == "CRITICAL" and inc.resolved_at is None


def test_permanent_failure_is_not_retried(factory):
    sid = new_session(factory)
    with factory() as db:
        jobs.enqueue(db, "bad-kind", "NOPE", sid, {})
        db.commit()
    w = StepWorker(factory)
    # claim() only hands out known kinds; emulate a corrupt row by claiming explicitly
    with factory() as db:
        j = jobs.claim(db, "x", kinds=("NOPE",))
        db.commit()
    assert j is not None
    with factory() as db:
        assert jobs.fail(db, j.id, "x", "boom", permanent=True) == jobs.DEAD
        db.commit()
    assert w.run_once() is False


def test_poison_job_with_spent_retries_is_dead_lettered_not_re_leased(factory):
    sid = new_session(factory)
    with factory() as db:
        jobs.enqueue(db, "p2", jobs.KIND_RECONCILE, sid, {}, max_attempts=1)
        db.commit()
        t0 = utcnow()
        assert jobs.claim(db, "a", lease_seconds=1, now=t0) is not None
        db.commit()
        # lease expired; the budget (1 attempt) is spent -> dead-letter instead of re-leasing
        assert jobs.claim(db, "b", now=t0 + timedelta(minutes=5)) is None
        db.commit()
        assert (
            db.execute(select(DurableJob.status).where(DurableJob.job_key == "p2")).scalar_one()
            == "DEAD"
        )


def test_lease_heartbeat_completion_and_ownership(factory):
    sid = new_session(factory)
    t0 = utcnow()
    with factory() as db:
        jobs.enqueue(db, "own", jobs.KIND_RECONCILE, sid, {})
        db.commit()
        j = jobs.claim(db, "A", lease_seconds=10, now=t0)
        db.commit()
        assert j is not None and jobs.claim(db, "B", now=t0 + timedelta(seconds=5)) is None
        assert jobs.heartbeat(db, j.id, "A", 10, now=t0 + timedelta(seconds=8))
        assert not jobs.heartbeat(db, j.id, "B", 10)
        assert jobs.claim(db, "B", now=t0 + timedelta(seconds=12)) is None  # extended to t0+18
        j2 = jobs.claim(db, "B", now=t0 + timedelta(seconds=30))  # expired -> reclaimed
        db.commit()
        assert j2 is not None and j2.id == j.id and j2.attempts == 2
        assert not jobs.complete(db, j.id, "A")  # the old owner lost the lease
        assert jobs.complete(db, j.id, "B")


def test_cancelled_session_work_is_dropped(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 10)
    with factory() as db:
        assert jobs.cancel_session_jobs(db, sid) == 10
        db.commit()
        assert jobs.claim(db, "w") is None


# ------------------------------------------------------------------ fail-closed paths
def test_reconciliation_mismatch_suspends_the_session_fail_closed(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 340)
    w = late_ok_worker(factory)
    drain_steps(w, factory, sid, 335)
    with factory() as db:  # corrupt a projection behind the system's back (test-only bypass)
        db.execute(text("SET LOCAL session_replication_role = replica"))
        db.execute(
            text(
                "update paper_ledger_entries set amount = amount + 3 where session_id=:s and kind='INITIAL_CASH'"
            ),
            {"s": str(sid)},
        )
        db.commit()
    drain_steps(w, factory, sid, 340)
    with factory() as db:
        sess = store.get_session(db, sid)
        assert sess.state == "FAILED" and "reconciliation mismatch" in sess.state_reason
        inc = db.execute(
            select(Incident).where(Incident.dedupe_key == f"reconcile:{sid}")
        ).scalar_one()
        assert inc.severity == "CRITICAL"


def test_replay_mismatch_on_restart_fails_the_session_instead_of_guessing(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 330)
    w = late_ok_worker(factory)
    drain_steps(w, factory, sid, 325)
    with factory() as db:
        db.execute(text("SET LOCAL session_replication_role = replica"))
        db.execute(
            text("delete from paper_session_checkpoints where session_id=:s and step_seq=310"),
            {"s": str(sid)},
        )
        db.commit()
    w2 = late_ok_worker(factory)  # restart: must replay + verify, and refuses
    drain_steps(w2, factory, sid, 330)
    with factory() as db:
        dead = db.execute(
            select(func.count()).select_from(DurableJob).where(DurableJob.status == "DEAD")
        ).scalar_one()
        assert dead >= 1 and store.get_session(db, sid).last_step_seq == 324
        sess = store.get_session(db, sid)
        assert sess.state == "FAILED" and "replay verification failed" in sess.state_reason
        assert (
            db.execute(
                select(func.count()).select_from(Incident).where(Incident.kind == "REPLAY_MISMATCH")
            ).scalar_one()
            == 1
        )
        assert (
            db.execute(
                select(func.count())
                .select_from(Incident)
                .where(Incident.kind == "UNEXPECTED_ERROR")
            ).scalar_one()
            >= 1
        )


def test_late_steps_after_downtime_are_observed_but_never_acted_on(factory):
    """Catching up after an outage: recorded bars are absorbed (marks, resting orders) but strategies
    are muted, so no new simulated order is created from a stale decision."""
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 360)
    w = StepWorker(factory, clock=lambda: T0 + timedelta(days=60))  # hours late for every step
    while w.run_once():
        pass
    with factory() as db:
        live = (
            db.execute(
                select(PaperStepInput.strategy_active).where(
                    PaperStepInput.session_id == sid, PaperStepInput.warm.is_(False)
                )
            )
            .scalars()
            .all()
        )
        n_orders = db.execute(
            text("select count(*) from strategy_signals where session_id=:s"), {"s": str(sid)}
        ).scalar_one()
    assert len(live) == 60 and not any(live) and n_orders == 0


def test_database_outage_is_transient_and_the_work_is_retried(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 3)
    boom = {"n": 0}

    def outage(p):
        if p == "after_persist" and boom["n"] == 0:
            boom["n"] += 1
            raise RuntimeError("connection reset by peer")  # a handled (non-fatal) exception

    w = late_ok_worker(factory, fault=outage)
    w._state["now"] = SERIES[0][0] + timedelta(hours=1, seconds=5)
    assert w.run_once()  # fails, gets rescheduled with backoff
    with factory() as db:
        j = db.execute(select(DurableJob).order_by(DurableJob.id)).scalars().first()
        assert j.status == "PENDING" and "connection reset" in j.last_error
        assert store.get_session(db, sid).last_step_seq == -1  # the failed attempt left nothing
    w._state["now"] = SERIES[0][0] + timedelta(hours=1, minutes=10)
    drain_steps(w, factory, sid, 3)
    assert cursor(factory, sid) == 2


def test_graceful_shutdown_drains_then_resumes_from_the_durable_cursor(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 50)
    w = late_ok_worker(factory)
    for _ in range(20):
        w._state["now"] = SERIES[min(cursor(factory, sid) + 1, N - 1)][0] + timedelta(
            hours=1, seconds=5
        )
        w.run_once()
    w.stopping = True  # SIGTERM-equivalent: finish the current job, take no more
    assert w.drain() == 0
    assert cursor(factory, sid) == 19
    w2 = late_ok_worker(factory)
    drain_steps(w2, factory, sid, 50)
    assert cursor(factory, sid) == 49 and checkpoints(factory, sid) == REF_DIGESTS[:50]


def test_a_restart_is_logged_as_recovering_then_back_to_the_previous_state(factory):
    sid = new_session(factory)
    enqueue_steps(factory, sid, 0, 320)
    w = late_ok_worker(factory)
    drain_steps(w, factory, sid, 310)
    w2 = late_ok_worker(factory)  # "restart": empty cache
    drain_steps(w2, factory, sid, 320)
    with factory() as db:
        log = db.execute(
            text(
                "select from_state, to_state, reason from paper_session_states where session_id=:s order by id"
            ),
            {"s": str(sid)},
        ).all()
        assert (
            db.execute(
                select(func.count()).select_from(Incident).where(Incident.kind == "RECOVERY")
            ).scalar_one()
            >= 1
        )
    pairs = [(a, b) for a, b, _ in log]
    assert ("RUNNING", "RECOVERING") in pairs and ("RECOVERING", "RUNNING") in pairs
    assert any("recovered:" in r for _, _, r in log)


def test_a_risk_trip_halts_the_session_opens_incidents_and_keeps_history(factory):
    """Tiny limits (0.01 % drawdown, 0.01 % daily) trip on the first marked loss: the worker moves
    RUNNING -> RISK_HALTED, records the risk events and opens the incidents; nothing is deleted."""
    from asta.research.sim.risk import SimRiskLimits

    tight = SimRiskLimits(
        D("1000"), D("0.10"), D("0.30"), D("0.0001"), D("0.0001"), "RISK_COMPLIANT", "test-tight"
    )
    cfg = CFG.__class__(**{**CFG.__dict__, "risk": tight})
    sid = new_session(factory, cfg)
    enqueue_steps(factory, sid)
    w = late_ok_worker(factory)
    drain_steps(w, factory, sid)
    with factory() as db:
        sess = store.get_session(db, sid)
        kinds = {
            k
            for (k,) in db.execute(
                text("select distinct kind from paper_risk_events where session_id=:s"),
                {"s": str(sid)},
            )
        }
        inc = {k for (k,) in db.execute(select(Incident.kind).where(Incident.session_id == sid))}
    assert sess.state == "RISK_HALTED" and "risk halt" in sess.state_reason
    assert kinds & {"DAILY_LOSS_TRIP", "DRAWDOWN_HALT"} and inc & {
        "DAILY_LOSS_LIMIT",
        "MAX_DRAWDOWN",
    }
    assert cursor(factory, sid) == N - 1  # observation continues while halted (bars still absorbed)
