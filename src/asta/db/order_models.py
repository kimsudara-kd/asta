"""M3-B preparation tables (migration 0006): durable order intents, their audit events, and the
legal-transition table. Plain tables of intent STATE: nothing here can talk to an exchange.

Constraints and triggers (DEMO-only environment, unique claim, legal transitions, append-only event
log) are defined in the migration; the CHECKs are not mirrored here because the metadata comparison
does not model them. See docs/m3b/ORDER-PATH-DESIGN.md.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from asta.db.models import NUM, TS, Base


class OrderIntentTransition(Base):
    __tablename__ = "order_intent_transitions"
    __table_args__ = (PrimaryKeyConstraint("src", "dst"),)
    src: Mapped[str] = mapped_column(Text)
    dst: Mapped[str] = mapped_column(Text)


class OrderIntentRow(Base):
    __tablename__ = "order_intents"
    __table_args__ = (Index("ix_order_intents_state", "state"),)
    decision_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    environment: Mapped[str] = mapped_column(String(16))
    venue: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(8))
    order_type: Mapped[str] = mapped_column(String(8))
    quote_notional: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    limit_price: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    state: Mapped[str] = mapped_column(String(24))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    misses: Mapped[int] = mapped_column(Integer, server_default="0")
    cancel_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    created_ts: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str] = mapped_column(Text, server_default="")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class OrderIntentEvent(Base):
    __tablename__ = "order_intent_events"
    __table_args__ = (Index("ix_order_intent_events_decision", "decision_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("order_intents.decision_id"))
    from_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_state: Mapped[str] = mapped_column(String(24))
    attempts: Mapped[int] = mapped_column(Integer)
    cancel_attempts: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class OrderFill(Base):
    __tablename__ = "order_fills"
    __table_args__ = (
        UniqueConstraint("decision_id", "trade_id", name="uq_order_fills_trade"),
        Index("ix_order_fills_decision", "decision_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("order_intents.decision_id"))
    trade_id: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[Decimal] = mapped_column(NUM)
    price: Mapped[Decimal] = mapped_column(NUM)
    fee: Mapped[Decimal] = mapped_column(NUM)
    fee_asset: Mapped[str] = mapped_column(String(16))
    recorded_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
