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
    Boolean,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
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


class KillSwitchState(Base):
    __tablename__ = "kill_switch_state"
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class KillSwitchEvent(Base):
    __tablename__ = "kill_switch_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    action: Mapped[str] = mapped_column(String(8))
    cause: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)


class KillSwitchReleaser(Base):
    __tablename__ = "kill_switch_releasers"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class OrderFillConflict(Base):
    __tablename__ = "order_fill_conflicts"
    __table_args__ = (Index("ix_order_fill_conflicts_decision", "decision_id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("order_intents.decision_id"))
    trade_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text)
    existing: Mapped[str | None] = mapped_column(Text, nullable=True)
    offered: Mapped[str] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ChannelSender(Base):
    __tablename__ = "channel_senders"
    sender: Mapped[str] = mapped_column(String(64), primary_key=True)
    high_seq: Mapped[int] = mapped_column(BigInteger, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ChannelNonce(Base):
    __tablename__ = "channel_nonces"
    __table_args__ = (PrimaryKeyConstraint("sender", "nonce"),)
    sender: Mapped[str] = mapped_column(ForeignKey("channel_senders.sender"))
    nonce: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[int] = mapped_column(BigInteger)
    accepted_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class RiskHaltBridgeSeen(Base):
    __tablename__ = "risk_halt_bridge_seen"
    __table_args__ = (PrimaryKeyConstraint("source", "event_key"),)
    source: Mapped[str] = mapped_column(String(32))
    event_key: Mapped[str] = mapped_column(String(96))
    detail: Mapped[str] = mapped_column(Text)
    seen_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
