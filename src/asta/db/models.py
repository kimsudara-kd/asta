"""SQLAlchemy models (M0 subset of handoff §6). Money = NUMERIC(38,18); times = timestamptz."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NUM = Numeric(38, 18)
TS = DateTime(timezone=True)
ENVIRONMENTS = "('BACKTEST','PAPER','EXCHANGE_DEMO')"


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (
        CheckConstraint("role IN ('viewer','operator','reviewer','admin')", name="ck_users_role"),
    )


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # SHA-256 of opaque token
    csrf_token: Mapped[str] = mapped_column(String(64))
    mfa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class ExchangeConnection(Base):
    """Metadata and a secret *reference name* only. Never credential values (ADR-0004)."""

    __tablename__ = "exchange_connections"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    venue: Mapped[str] = mapped_column(String(16))
    environment: Mapped[str] = mapped_column(String(16))
    secret_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PUBLIC_ONLY")
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (
        CheckConstraint(f"environment IN {ENVIRONMENTS}", name="ck_conn_env"),
        CheckConstraint("venue IN ('BINANCE','OKX')", name="ck_conn_venue"),
        UniqueConstraint("venue", "environment", name="uq_conn_venue_env"),
    )


class Market(Base):
    __tablename__ = "markets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    venue: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    base: Mapped[str] = mapped_column(String(16))
    quote: Mapped[str] = mapped_column(String(16))
    __table_args__ = (UniqueConstraint("venue", "symbol", name="uq_market_venue_symbol"),)


class MarketRulesVersion(Base):
    __tablename__ = "market_rules_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    venue: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    tick_size: Mapped[Decimal] = mapped_column(NUM)
    step_size: Mapped[Decimal] = mapped_column(NUM)
    min_qty: Mapped[Decimal] = mapped_column(NUM)
    min_notional: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    trading: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(8))
    retrieved_at: Mapped[datetime] = mapped_column(TS)
    __table_args__ = (UniqueConstraint("venue", "symbol", "retrieved_at", name="uq_rules_version"),)


class RiskPolicyRow(Base):
    __tablename__ = "risk_policies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (UniqueConstraint("name", "version", name="uq_policy_version"),)


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    severity: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(48))
    detail: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class AuditEvent(Base):
    """Append-only (DB trigger forbids UPDATE/DELETE/TRUNCATE) with SHA-256 hash chain."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(TS)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(16), default="PAPER")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(String(64))
    row_hash: Mapped[str] = mapped_column(String(64), unique=True)
    __table_args__ = (CheckConstraint(f"environment IN {ENVIRONMENTS}", name="ck_audit_env"),)


# register the M1 research tables on the same metadata (import at the end: they import Base above)
from asta.db import research_models as _research_models  # noqa: E402,F401
