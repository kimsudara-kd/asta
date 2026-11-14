"""PostgreSQL implementation of the order-intent store port (docs/m3b/ORDER-PATH-DESIGN.md s.4).

Schema and guarantees live in migration 0006 (unique claim, DEMO-only environment, legal-transition
trigger, trigger-written append-only event log). This class only maps the port onto SQL.
Every method commits before it returns: the caller's write-ahead / claim guarantees depend on that
durability.
Nothing here talks to an exchange or holds a credential.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from asta.core.environment import Environment
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


class SqlFillStore:
    """Idempotent, append-only trade ledger (migration 0007). Commits before returning."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record(self, decision_id: str, fill: Fill) -> bool:
        """True when stored now; False when this (decision, trade) was already stored."""
        with self._sessions() as db:
            res = db.execute(
                text(
                    "INSERT INTO order_fills (decision_id, trade_id, quantity, price, fee,"
                    " fee_asset) VALUES (:d, :t, :q, :p, :f, :a)"
                    " ON CONFLICT (decision_id, trade_id) DO NOTHING"
                ),
                {
                    "d": decision_id,
                    "t": fill.trade_id,
                    "q": fill.quantity,
                    "p": fill.price,
                    "f": fill.fee,
                    "a": fill.fee_asset,
                },
            )
            db.commit()
            return bool(getattr(res, "rowcount", 0) == 1)

    def book(self, decision_id: str, max_quantity: Decimal | None = None) -> FillBook:
        """Rebuild the in-memory book from the ledger (e.g. after a restart)."""
        book = FillBook(decision_id, max_quantity)
        with self._sessions() as db:
            rows = db.execute(
                text(
                    "SELECT trade_id, quantity, price, fee, fee_asset FROM order_fills"
                    " WHERE decision_id = :d ORDER BY id"
                ),
                {"d": decision_id},
            ).all()
        for t, q, p, f, a in rows:
            book.apply(
                Fill(str(t), decision_id, Decimal(str(q)), Decimal(str(p)), Decimal(str(f)), str(a))
            )
        return book
