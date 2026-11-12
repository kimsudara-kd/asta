"""Paper-trading persistence (M2-B/C). One step = one transaction (effectively-once posting).

* ``persist_step`` takes a row lock on the session, advances the step cursor with a compare-and-set
  and only then inserts the step's rows; a re-delivered step finds the cursor already advanced and
  raises ``StepAlreadyPosted`` with nothing written. Unique keys are the second line of defence.
* ``rebuild_runtime`` restores a runtime after any interruption by deterministically replaying the
  stored step inputs and comparing every rebuilt checkpoint digest with the durable one. A mismatch
  raises ``ReplayMismatch`` (callers fail closed). Replay never invents inputs.
* ``reconcile`` recomputes balances, fills and equity from DURABLE rows only.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from asta.core.money import dec_str
from asta.core.timeutil import utcnow
from asta.db.paper_models import (
    PaperBalance,
    PaperEquitySnapshot,
    PaperFill,
    PaperLedgerEntry,
    PaperOrder,
    PaperOrderEvent,
    PaperRiskEvent,
    PaperSession,
    PaperSessionCheckpoint,
    PaperSessionConfig,
    PaperSessionState,
    PaperStepInput,
    StrategySignal,
)
from asta.paper.config import PaperSessionConfig as Config
from asta.paper.runtime import PaperRuntime, StepRows
from asta.paper.states import SessionState, check_transition
from asta.research.data.models import Bar
from asta.research.runner import canonical

ZERO = Decimal(0)


class StepAlreadyPosted(Exception):
    pass


class ReplayMismatch(Exception):
    pass


class SessionNotFound(Exception):
    pass


# ------------------------------------------------------------------ bar (de)serialisation
def bar_to_json(b: Bar) -> dict[str, str]:
    return {
        "t": b.open_time.isoformat(),
        "o": dec_str(b.open),
        "h": dec_str(b.high),
        "l": dec_str(b.low),
        "c": dec_str(b.close),
        "v": dec_str(b.volume),
    }


def bar_from_json(d: Mapping[str, str]) -> Bar:
    return Bar(
        datetime.fromisoformat(d["t"]),
        Decimal(d["o"]),
        Decimal(d["h"]),
        Decimal(d["l"]),
        Decimal(d["c"]),
        Decimal(d["v"]),
    )


def inputs_hash(
    bars: Mapping[str, Any],
    blocked: Mapping[str, str],
    active: bool,
    warm: bool,
    actions: Sequence[Any],
) -> str:
    body = {"bars": bars, "blocked": blocked, "active": active, "warm": warm, "actions": actions}
    return hashlib.sha256(canonical(body).encode()).hexdigest()


# ------------------------------------------------------------------ sessions
def create_session(db: Session, cfg: Config, user: str) -> PaperSession:
    cfg.validate()
    h = cfg.config_hash()
    row = PaperSession(
        name=cfg.name, state=SessionState.CREATED.value, config_hash=h, created_by=user
    )
    db.add(row)
    db.flush()
    db.add(PaperSessionConfig(session_id=row.id, config=cfg.to_json(), config_hash=h))
    db.add(PaperSessionState(session_id=row.id, from_state=None, to_state="CREATED", actor=user))
    db.flush()
    return row


def get_session(db: Session, session_id: uuid.UUID, lock: bool = False) -> PaperSession:
    q = select(PaperSession).where(PaperSession.id == session_id)
    if lock:
        q = q.with_for_update()
    row = db.execute(q).scalar_one_or_none()
    if row is None:
        raise SessionNotFound(str(session_id))
    return row


def load_config(db: Session, session_id: uuid.UUID) -> Config:
    row = db.get(PaperSessionConfig, session_id)
    if row is None:
        raise SessionNotFound(str(session_id))
    cfg = Config.from_json(row.config)
    if cfg.config_hash() != row.config_hash:
        raise ReplayMismatch("stored session configuration does not match its hash")
    return cfg


def transition(
    db: Session, session_id: uuid.UUID, to: SessionState, actor: str, reason: str = ""
) -> PaperSession:
    """Transactional, lock-protected state change; the permitted-transition table is enforced."""
    row = get_session(db, session_id, lock=True)
    src = SessionState(row.state)
    check_transition(src, to)
    row.state = to.value
    row.state_reason = reason
    row.version += 1
    if to == SessionState.RUNNING and row.started_at is None:
        row.started_at = utcnow()
    if to in (SessionState.STOPPED, SessionState.FAILED):
        row.stopped_at = utcnow()
    db.add(
        PaperSessionState(
            session_id=session_id,
            from_state=src.value,
            to_state=to.value,
            reason=reason,
            actor=actor,
        )
    )
    db.flush()
    return row


# ------------------------------------------------------------------ step posting
def persist_step(
    db: Session,
    session_id: uuid.UUID,
    step_seq: int,
    ts: datetime,
    warm: bool,
    bars: Mapping[str, Bar],
    blocked: Mapping[str, str],
    strategy_active: bool,
    actions: Sequence[Any],
    rows: StepRows,
) -> None:
    """Insert one step atomically. The caller commits (or rolls back everything)."""
    sess = get_session(db, session_id, lock=True)
    res = db.execute(
        update(PaperSession)
        .where(PaperSession.id == session_id, PaperSession.last_step_seq == step_seq - 1)
        .values(last_step_seq=step_seq, last_step_ts=ts)
    )
    if getattr(res, "rowcount", 0) != 1:
        raise StepAlreadyPosted(f"step {step_seq} (cursor at {sess.last_step_seq})")
    bj = {k: bar_to_json(v) for k, v in sorted(bars.items())}
    db.add(
        PaperStepInput(
            session_id=session_id,
            step_seq=step_seq,
            step_ts=ts,
            warm=warm,
            bars=bj,
            blocked=dict(blocked),
            strategy_active=strategy_active,
            actions=list(actions),
            inputs_hash=inputs_hash(bj, blocked, strategy_active, warm, actions),
        )
    )

    def bulk(model: Any, items: Iterable[dict[str, Any]]) -> None:
        batch = [{"session_id": session_id, **i} for i in items]
        if not batch:
            return
        per = max(1, 30000 // len(batch[0]))
        for i in range(0, len(batch), per):
            db.execute(insert(model).values(batch[i : i + per]))

    bulk(StrategySignal, rows.signals)
    bulk(PaperOrderEvent, rows.order_events)
    bulk(PaperFill, rows.fills)
    bulk(PaperLedgerEntry, rows.ledger)
    bulk(PaperRiskEvent, rows.risk_events)
    if rows.orders:
        stmt = pg_insert(PaperOrder).values([{"session_id": session_id, **o} for o in rows.orders])
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["session_id", "account", "order_id"],
                set_={
                    k: stmt.excluded[k]
                    for k in ("filled_qty", "status", "reason", "updated_ts", "quantity")
                },
            )
        )
    if rows.balances:
        bstmt = pg_insert(PaperBalance).values(
            [{"session_id": session_id, "updated_step_seq": step_seq, **b} for b in rows.balances]
        )
        db.execute(
            bstmt.on_conflict_do_update(
                index_elements=["session_id", "account", "asset"],
                set_={
                    k: bstmt.excluded[k]
                    for k in ("balance", "reserved", "cost_total", "mark", "updated_step_seq")
                },
            )
        )
    if rows.equity is not None:
        db.add(PaperEquitySnapshot(session_id=session_id, **rows.equity))
    if rows.checkpoint is not None:
        db.add(PaperSessionCheckpoint(session_id=session_id, **rows.checkpoint))
    db.flush()


# ------------------------------------------------------------------ recovery by replay
@dataclass
class ReplayReport:
    steps: int = 0
    last_step_seq: int = -1
    last_digest: str = ""
    problems: list[str] = field(default_factory=list)


def rebuild_runtime(db: Session, session_id: uuid.UUID) -> tuple[PaperRuntime, ReplayReport]:
    """Deterministically re-derive the full runtime from the durable inputs and VERIFY it."""
    cfg = load_config(db, session_id)
    rt = PaperRuntime(cfg)
    rep = ReplayReport()
    sess = get_session(db, session_id)
    checkpoints = {
        c.step_seq: c.digest
        for c in db.execute(
            select(PaperSessionCheckpoint).where(PaperSessionCheckpoint.session_id == session_id)
        ).scalars()
    }
    inputs = db.execute(
        select(PaperStepInput)
        .where(PaperStepInput.session_id == session_id)
        .order_by(PaperStepInput.step_seq)
    ).scalars()
    for si in inputs:
        if si.step_seq != rep.last_step_seq + 1:
            raise ReplayMismatch(f"step inputs not consecutive at {si.step_seq}")
        bj = {k: dict(v) for k, v in si.bars.items()}
        if inputs_hash(bj, si.blocked, si.strategy_active, si.warm, si.actions) != si.inputs_hash:
            raise ReplayMismatch(f"step {si.step_seq}: stored inputs fail their integrity hash")
        for act in si.actions:
            apply_action(rt, act)
        bars = {k: bar_from_json(v) for k, v in bj.items()}
        out = rt.step(si.step_seq, si.step_ts, bars, si.blocked, si.strategy_active, si.warm)
        want = checkpoints.get(si.step_seq)
        got = out.checkpoint["digest"] if out.checkpoint else None
        if want is None or got != want:
            raise ReplayMismatch(
                f"step {si.step_seq}: rebuilt state digest {str(got)[:12]} != durable {str(want)[:12]}"
            )
        rep.steps += 1
        rep.last_step_seq = si.step_seq
        rep.last_digest = got or ""
    if rep.last_step_seq != sess.last_step_seq:
        raise ReplayMismatch(
            f"session cursor {sess.last_step_seq} != last stored step {rep.last_step_seq}"
        )
    return rt, rep


def apply_action(rt: PaperRuntime, action: Mapping[str, Any]) -> str | None:
    """Operator actions that change risk state; recorded in the step inputs so replay agrees."""
    if action.get("action") == "REARM_DAILY":
        return rt.rearm_daily(datetime.fromisoformat(str(action["ts"])))
    raise ValueError(f"unknown action {action!r}")


# ------------------------------------------------------------------ reconciliation (durable rows only)
@dataclass
class Discrepancy:
    where: str
    detail: str


def reconcile(db: Session, session_id: uuid.UUID) -> list[Discrepancy]:
    """Recompute cash, assets, fees and equity from durable ledger events and compare with the
    stored projections. Any entry in the result must fail the session closed."""
    out: list[Discrepancy] = []
    ledger: dict[tuple[str, str], Decimal] = {}
    for acct, asset, amt in db.execute(
        text(
            "select account, asset, sum(amount) from paper_ledger_entries "
            "where session_id=:s and kind not in ('RESERVE','RELEASE') group by account, asset"
        ),
        {"s": str(session_id)},
    ):
        ledger[(acct, asset)] = Decimal(amt)
    bals = {
        (b.account, b.asset): b
        for b in db.execute(
            select(PaperBalance).where(PaperBalance.session_id == session_id)
        ).scalars()
    }
    for key, b in bals.items():
        if ledger.get(key, ZERO) != b.balance:
            out.append(
                Discrepancy(
                    f"balance {key}", f"ledger {ledger.get(key, ZERO)} != projection {b.balance}"
                )
            )
        if b.balance < 0 or b.reserved < 0 or b.reserved > b.balance:
            out.append(Discrepancy(f"balance {key}", "negative or over-reserved"))
    for key in ledger:
        if key not in bals and ledger[key] != 0:
            out.append(Discrepancy(f"balance {key}", "ledger balance without projection"))
    # fills -> balances (independent path): quote and base movements
    fills = db.execute(
        select(PaperFill).where(PaperFill.session_id == session_id).order_by(PaperFill.id)
    ).scalars()
    start_cash: dict[str, Decimal] = {}
    for acct, amt in db.execute(
        text(
            "select account, amount from paper_ledger_entries "
            "where session_id=:s and kind='INITIAL_CASH'"
        ),
        {"s": str(session_id)},
    ):
        start_cash[acct] = Decimal(amt)
    derived: dict[tuple[str, str], Decimal] = {}
    for f in fills:
        base = f.symbol.split("-")[0]
        sign = 1 if f.side == "BUY" else -1
        k_q, k_b, k_f = (f.account, "USDT"), (f.account, base), (f.account, f.fee_asset)
        derived[k_q] = derived.get(k_q, ZERO) - sign * f.notional
        derived[k_b] = derived.get(k_b, ZERO) + sign * f.quantity
        derived[k_f] = derived.get(k_f, ZERO) - f.fee_amount
    for acct, c in start_cash.items():
        derived[(acct, "USDT")] = derived.get((acct, "USDT"), ZERO) + c
    for key in set(derived) | set(ledger):
        if derived.get(key, ZERO) != ledger.get(key, ZERO):
            out.append(
                Discrepancy(
                    f"fills-vs-ledger {key}",
                    f"fills imply {derived.get(key, ZERO)}, ledger {ledger.get(key, ZERO)}",
                )
            )
    # last equity snapshot re-derived from balances x marks
    snap = db.execute(
        select(PaperEquitySnapshot)
        .where(PaperEquitySnapshot.session_id == session_id)
        .order_by(PaperEquitySnapshot.step_seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if snap is not None:
        for acct, rec in snap.accounts.items():
            eq = bals[(acct, "USDT")].balance if (acct, "USDT") in bals else ZERO
            for (a, asset), b in bals.items():
                if a == acct and asset != "USDT" and b.balance and b.mark is not None:
                    eq += b.balance * b.mark
            if abs(eq - Decimal(rec["equity"])) > Decimal("0.00000001"):
                out.append(
                    Discrepancy(f"equity {acct}", f"derived {eq} != snapshot {rec['equity']}")
                )
    n_fills = db.execute(
        select(func.count()).select_from(PaperFill).where(PaperFill.session_id == session_id)
    ).scalar_one()
    cp = db.execute(
        select(PaperSessionCheckpoint)
        .where(PaperSessionCheckpoint.session_id == session_id)
        .order_by(PaperSessionCheckpoint.step_seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if cp is not None:
        persisted = sum(
            v for k, v in cp.summary.get("fills", {}).items() if not k.startswith("BENCH:")
        )
        if persisted != n_fills:
            out.append(
                Discrepancy("fill count", f"checkpoint {persisted} != stored fills {n_fills}")
            )
    return out


def record_reconcile(db: Session, session_id: uuid.UUID, ok: bool) -> None:
    db.execute(
        update(PaperSession)
        .where(PaperSession.id == session_id)
        .values(last_reconciled_at=utcnow(), last_reconcile_ok=ok)
    )
