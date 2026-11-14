"""PostgreSQL implementation of the order-intent store port (docs/m3b/ORDER-PATH-DESIGN.md s.4).

Schema and guarantees live in migration 0006 (unique claim, DEMO-only environment, legal-transition
trigger, trigger-written append-only event log). This class only maps the port onto SQL.
Every method commits before it returns: the caller's write-ahead / claim guarantees depend on that
durability.
Nothing here talks to an exchange or holds a credential.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from asta.core.environment import Environment
from asta.orders.boundary import ExposureBlocked
from asta.orders.fills import Fill, FillBook
from asta.orders.lifecycle import Intent, State

_COLUMNS = (
    "decision_id, environment, venue, symbol, side, order_type, quote_notional, quantity, "
    "limit_price, state, attempts, misses, cancel_attempts, created_ts, note"
)

# constant text assembled once at import: no user input is ever formatted into SQL
_SELECT = "SELECT " + _COLUMNS + " FROM order_intents"  # noqa: S608


def _row_to_intent(r: tuple[object, ...]) -> Intent:
    def dec(v: object) -> Decimal | None:
        return None if v is None else Decimal(str(v))

    return Intent(
        decision_id=str(r[0]),
        environment=Environment(str(r[1])),
        venue=str(r[2]),
        symbol=str(r[3]),
        side=str(r[4]),
        order_type=str(r[5]),
        quote_notional=dec(r[6]),
        quantity=dec(r[7]),
        limit_price=dec(r[8]),
        state=State(str(r[9])),
        attempts=int(str(r[10])),
        misses=int(str(r[11])),
        cancel_attempts=int(str(r[12])),
        created_ts=int(str(r[13])),
        note=str(r[14]),
    )


class SqlIntentStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def insert_if_absent(self, intent: Intent) -> bool:
        """The atomic exact-once claim: True for the first insert of a decision id, False after."""
        with self._sessions() as db:
            res = db.execute(
                text(
                    "INSERT INTO order_intents (decision_id, environment, venue, symbol, side,"
                    " order_type, quote_notional, quantity, limit_price, state, attempts,"
                    " misses, cancel_attempts, created_ts, note) VALUES (:decision_id,"
                    " :environment, :venue,"
                    " :symbol, :side, :order_type, :quote_notional, :quantity, :limit_price,"
                    " :state, :attempts, :misses, :cancel_attempts, :created_ts, :note)"
                    " ON CONFLICT (decision_id) DO NOTHING"
                ),
                {
                    "decision_id": intent.decision_id,
                    "environment": intent.environment.value,
                    "venue": intent.venue,
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "order_type": intent.order_type,
                    "quote_notional": intent.quote_notional,
                    "quantity": intent.quantity,
                    "limit_price": intent.limit_price,
                    "state": intent.state.value,
                    "attempts": intent.attempts,
                    "misses": intent.misses,
                    "cancel_attempts": intent.cancel_attempts,
                    "created_ts": intent.created_ts,
                    "note": intent.note,
                },
            )
            db.commit()
            return bool(getattr(res, "rowcount", 0) == 1)

    def get(self, decision_id: str) -> Intent | None:
        with self._sessions() as db:
            r = db.execute(
                text(_SELECT + " WHERE decision_id = :d"),
                {"d": decision_id},
            ).first()
            return None if r is None else _row_to_intent(tuple(r))

    def save(self, intent: Intent) -> None:
        """Persist a state/counter/note change. The database refuses an illegal transition or any
        change of the identity/sizing columns (the error propagates: the caller must not go on)."""
        with self._sessions() as db:
            try:
                res = db.execute(
                    text(
                        "UPDATE order_intents SET state = :state, attempts = :attempts,"
                        " misses = :misses, cancel_attempts = :cancel_attempts, note = :note"
                        " WHERE decision_id = :d"
                    ),
                    {
                        "state": intent.state.value,
                        "attempts": intent.attempts,
                        "misses": intent.misses,
                        "cancel_attempts": intent.cancel_attempts,
                        "note": intent.note,
                        "d": intent.decision_id,
                    },
                )
            except DBAPIError as exc:
                db.rollback()
                if "kill switch latched" in str(exc):  # the latch holds across every worker
                    raise ExposureBlocked(str(exc)) from exc
                raise
            if getattr(res, "rowcount", 0) != 1:
                db.rollback()
                raise KeyError(f"unknown order intent {intent.decision_id}")
            db.commit()

    def by_state(self, states: frozenset[State]) -> list[Intent]:
        with self._sessions() as db:
            rows = db.execute(
                text(_SELECT + " WHERE state = ANY(:s) ORDER BY decision_id"),
                {"s": sorted(s.value for s in states)},
            ).all()
            return [_row_to_intent(tuple(r)) for r in rows]


class FillConflict(Exception):
    """A different trade was reported under an already-stored trade id. Recorded, never dropped."""


class FillRejected(Exception):
    """A fill that cannot be right (overfill, outside the limit, wrong order state). Recorded."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


class FillOutcome(StrEnum):
    STORED = "STORED"
    DUPLICATE = "DUPLICATE"  # exact repeat: an idempotent no-op


def _fill_repr(f: Fill) -> str:
    def n(d: Decimal) -> str:  # exact decimals, scale-independent, never float
        return format(d.normalize(), "f")

    return f"{f.trade_id}|{n(f.quantity)}|{n(f.price)}|{n(f.fee)}|{f.fee_asset}"


_FILLED_BY_SIZE = frozenset(
    {
        State.DISPATCHED,
        State.UNKNOWN,
        State.ACCEPTED,
        State.PARTIALLY_FILLED,
        State.CANCEL_REQUESTED,
        State.ABANDONED,
    }
)


class SqlFillStore:
    """Idempotent, append-only trade ledger with atomic fill + state accounting (0007/0008).

    ``record_fill`` runs in ONE transaction: it locks the order row (concurrent workers queue behind
    it), compares with any stored trade of the same id, inserts under the database overfill guard
    and advances the order state from the cumulative quantity. A conflicting or impossible fill
    is written
    to ``order_fill_conflicts`` (append-only) and raised; it is never absorbed or silently dropped.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record_fill(self, decision_id: str, fill: Fill) -> FillOutcome:
        if fill.client_id != decision_id:
            self._conflict(decision_id, fill, "CONFLICT", "trade belongs to another order", None)
            raise FillConflict(f"{fill.trade_id}: belongs to {fill.client_id}")
        with self._sessions() as db:
            row = db.execute(
                text(
                    "SELECT state, quantity, quote_notional FROM order_intents"
                    " WHERE decision_id = :d FOR UPDATE"
                ),
                {"d": decision_id},
            ).first()
            if row is None:
                db.rollback()
                raise KeyError(f"unknown order intent {decision_id}")
            state, qty, _quote = State(str(row[0])), row[1], row[2]
            prior = db.execute(
                text(
                    "SELECT trade_id, quantity, price, fee, fee_asset FROM order_fills"
                    " WHERE decision_id = :d AND trade_id = :t"
                ),
                {"d": decision_id, "t": fill.trade_id},
            ).first()
            if prior is not None:
                stored = Fill(
                    str(prior[0]), decision_id, Decimal(str(prior[1])), Decimal(str(prior[2])),
                    Decimal(str(prior[3])), str(prior[4]),
                )  # fmt: skip
                db.rollback()
                if stored == fill:
                    return FillOutcome.DUPLICATE
                self._conflict(
                    decision_id, fill, "CONFLICT", "same trade id, different content", stored
                )
                raise FillConflict(
                    f"{fill.trade_id}: stored {_fill_repr(stored)} vs {_fill_repr(fill)}"
                )
            try:
                db.execute(
                    text(
                        "INSERT INTO order_fills (decision_id, trade_id, quantity, price, fee,"
                        " fee_asset) VALUES (:d, :t, :q, :p, :f, :a)"
                    ),
                    {"d": decision_id, "t": fill.trade_id, "q": fill.quantity, "p": fill.price,
                     "f": fill.fee, "a": fill.fee_asset},
                )  # fmt: skip
            except DBAPIError as exc:
                db.rollback()
                msg = str(exc)
                for kind in ("OVERFILL", "BAD_STATE", "OUT_OF_LIMIT"):
                    if kind in msg:
                        detail = msg.split(kind + ":", 1)[1].split("\n")[0].strip(" '\"}")
                        self._conflict(decision_id, fill, kind, detail, None)
                        raise FillRejected(kind, detail) from exc
                raise
            total = db.execute(
                text("SELECT coalesce(sum(quantity), 0) FROM order_fills WHERE decision_id = :d"),
                {"d": decision_id},
            ).scalar_one()
            if state in _FILLED_BY_SIZE:
                new = (
                    State.FILLED
                    if qty is not None and Decimal(str(total)) == Decimal(str(qty))
                    else State.PARTIALLY_FILLED
                )
                if new != state or state == State.PARTIALLY_FILLED:
                    db.execute(
                        text("UPDATE order_intents SET state = :s WHERE decision_id = :d"),
                        {"s": new.value, "d": decision_id},
                    )
            db.commit()  # fill row and state advance land together or not at all
            return FillOutcome.STORED

    def conflicts(self, decision_id: str | None = None) -> list[tuple[str, str, str]]:
        with self._sessions() as db:
            q = "SELECT decision_id, trade_id, kind FROM order_fill_conflicts"
            rows = (
                db.execute(
                    text(q + " WHERE decision_id = :d ORDER BY id"), {"d": decision_id}
                ).all()
                if decision_id
                else db.execute(text(q + " ORDER BY id")).all()
            )
        return [(str(a), str(b), str(c)) for a, b, c in rows]

    def _conflict(
        self, decision_id: str, offered: Fill, kind: str, detail: str, existing: Fill | None
    ) -> None:
        with self._sessions() as db:  # separate transaction: survives the rollback above
            db.execute(
                text(
                    "INSERT INTO order_fill_conflicts (decision_id, trade_id, kind, detail,"
                    " existing, offered) VALUES (:d, :t, :k, :x, :e, :o)"
                ),
                {"d": decision_id, "t": offered.trade_id, "k": kind, "x": detail[:500],
                 "e": None if existing is None else _fill_repr(existing), "o": _fill_repr(offered)},
            )  # fmt: skip
            db.commit()

    def book(self, decision_id: str, max_quantity: Decimal | None = None) -> FillBook:
        """Rebuild the in-memory book from the ledger; the order's own size is read from its row."""
        book = FillBook(decision_id, max_quantity)
        with self._sessions() as db:
            if max_quantity is None:
                q = db.execute(
                    text("SELECT quantity FROM order_intents WHERE decision_id = :d"),
                    {"d": decision_id},
                ).scalar_one_or_none()
                book.max_quantity = None if q is None else Decimal(str(q))
            rows = db.execute(
                text(
                    "SELECT trade_id, quantity, price, fee, fee_asset FROM order_fills"
                    " WHERE decision_id = :d ORDER BY id"
                ),
                {"d": decision_id},
            ).all()
        for t, q2, p, f, a in rows:
            book.apply(
                Fill(
                    str(t), decision_id, Decimal(str(q2)), Decimal(str(p)), Decimal(str(f)), str(a)
                )
            )
        return book


class SqlReplayStore:
    """Durable replay/expiry state of the intent channel (migration 0008). One transaction per
    decision: concurrent receivers and restarts cannot accept the same message twice."""

    durable = True

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def accept(self, sender: str, seq: int, nonce: str, expires_at: int, now: int) -> str:
        """ "" if newly accepted (state advanced), else the refusal reason (state unchanged)."""
        with self._sessions() as db:
            db.execute(
                text("INSERT INTO channel_senders (sender) VALUES (:s) ON CONFLICT DO NOTHING"),
                {"s": sender},
            )
            high = db.execute(
                text("SELECT high_seq FROM channel_senders WHERE sender = :s FOR UPDATE"),
                {"s": sender},
            ).scalar_one()
            if seq <= int(high):
                db.rollback()
                return "REPLAYED_OR_OUT_OF_ORDER"
            seen = db.execute(
                text("SELECT 1 FROM channel_nonces WHERE sender = :s AND nonce = :n"),
                {"s": sender, "n": nonce},
            ).first()
            if seen is not None:
                db.rollback()
                return "NONCE_REUSED"
            db.execute(
                text("INSERT INTO channel_nonces (sender, nonce, expires_at) VALUES (:s, :n, :e)"),
                {"s": sender, "n": nonce, "e": expires_at},
            )
            db.execute(
                text("UPDATE channel_senders SET high_seq = :q WHERE sender = :s"),
                {"q": seq, "s": sender},
            )
            db.execute(
                text("DELETE FROM channel_nonces WHERE sender = :s AND expires_at < :n"),
                {"s": sender, "n": now - 3600},
            )  # expiry state is pruned an hour after expiry; refused as EXPIRED long before
            db.commit()
            return ""


class SqlKillSwitch:
    """Latched, durable kill switch (migration 0008). ``active`` is read from the database on every
    call, so every worker and every restart sees the same latch. The functions in the database
    enforce who may release and that a reason is given; each change also goes to the audit chain."""

    durable = True

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def active(self) -> bool:
        with self._sessions() as db:
            return bool(
                db.execute(text("SELECT active FROM kill_switch_state WHERE id = 1")).scalar_one()
            )

    def trip(self, cause: str, actor: str = "system", reason: str = "") -> None:
        from asta.core.environment import Environment
        from asta.db.audit_store import append_audit

        with self._sessions() as db:
            db.execute(
                text("SELECT trip_kill_switch(:c, :a, :r)"),
                {"c": cause, "a": actor, "r": reason or cause},
            )
            append_audit(
                db,
                actor,
                "killswitch.trip",
                {"cause": cause, "reason": reason[:200]},
                Environment.EXCHANGE_DEMO,
            )
            db.commit()

    def session_actor(self) -> str:
        """The authenticated database login: the ONLY identity a release can use (G-10)."""
        with self._sessions() as db:
            return str(db.execute(text("SELECT session_user")).scalar_one())

    def release(self, reason: str) -> bool:
        """True if released, False if not latched. The releaser is the authenticated session user
        (it must be listed in kill_switch_releasers); a refusal (unlisted user, short reason) raises
        and is recorded in the audit chain with that same identity."""
        from asta.core.environment import Environment
        from asta.db.audit_store import append_audit

        actor = self.session_actor()
        with self._sessions() as db:
            try:
                done = bool(
                    db.execute(text("SELECT release_kill_switch(:r)"), {"r": reason}).scalar_one()
                )
            except DBAPIError:
                db.rollback()
                with self._sessions() as db2:
                    append_audit(
                        db2,
                        actor,
                        "killswitch.release_refused",
                        {"reason": (reason or "")[:200]},
                        Environment.EXCHANGE_DEMO,
                    )
                    db2.commit()
                raise
            if done:
                append_audit(
                    db,
                    actor,
                    "killswitch.release",
                    {"reason": reason[:200]},
                    Environment.EXCHANGE_DEMO,
                )
            db.commit()
            return done

    def history(self) -> list[tuple[str, str | None, str]]:
        with self._sessions() as db:
            rows = db.execute(
                text("SELECT action, cause, actor FROM kill_switch_events ORDER BY id")
            ).all()
        return [(str(a), None if c is None else str(c), str(b)) for a, c, b in rows]


class BridgedKillState:
    """The kill state the order path must be built with in any real deployment: latched OR the
    risk-halt bridge is not alive. A missing, stale or unreadable heartbeat counts as a halt, so the
    death of the bridge can never leave the order path open (fail closed)."""

    durable = True

    def __init__(
        self,
        kill: SqlKillSwitch,
        sessions: sessionmaker[Session],
        max_heartbeat_age_s: float = 30.0,
    ) -> None:
        self._kill, self._sessions, self._max_age = kill, sessions, max_heartbeat_age_s

    def heartbeat_age(self) -> float | None:
        try:
            with self._sessions() as db:
                v = db.execute(
                    text(
                        "SELECT extract(epoch FROM now() - beat_at)"
                        " FROM risk_halt_bridge_heartbeat WHERE id = 1"
                    )
                ).scalar_one_or_none()
            return None if v is None else float(v)
        except Exception:  # noqa: BLE001
            return None

    def active(self) -> bool:
        age = self.heartbeat_age()
        if age is None or age > self._max_age or age < -5:  # unreadable / stale / clock nonsense
            return True
        return self._kill.active()
