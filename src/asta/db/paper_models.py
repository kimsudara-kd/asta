"""M2 paper-trading and market-observation tables (migration 0004).

Insert-only (DB triggers forbid UPDATE/DELETE/TRUNCATE): observations, gaps' history is kept by
status rows, session configs and state transitions, step inputs, checkpoints, order events,
fills, ledger entries, equity snapshots, risk events, signals, daily reports.
Mutable projections (documented): paper_sessions (state cursor), paper_orders (current order
state), paper_balances, durable_jobs, worker_leases, market_feed_connections, market_data_gaps
(status), incidents/alerts (status).
Nothing here can talk to an exchange: these are plain tables of SIMULATED state.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asta.db.models import NUM, TS, Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


# ------------------------------------------------------------------ public market data (M2-A)
class MarketFeedConnection(Base):
    __tablename__ = "market_feed_connections"
    venue: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))  # CONNECTED | RECONNECTING | DISABLED | ERROR
    source: Mapped[str] = mapped_column(String(8))  # LIVE | DISABLED
    reason: Mapped[str] = mapped_column(Text, default="")
    connection_id: Mapped[str] = mapped_column(String(64), default="")
    since: Mapped[datetime] = mapped_column(TS)
    last_event_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    reconnects: Mapped[int] = mapped_column(Integer, default=0)
    clock_offset_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class MarketObservation(Base):
    __tablename__ = "market_observations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    native_symbol: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(12))  # CANDLE_1M | QUOTE
    event_ts: Mapped[datetime] = mapped_column(TS)  # candle open time / quote minute
    exchange_ts: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    ingested_ts: Mapped[datetime] = mapped_column(TS)
    source: Mapped[str] = mapped_column(String(10))  # LIVE | BACKFILL
    connection_id: Mapped[str] = mapped_column(String(64), default="")
    open: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    high: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    low: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    close: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    volume: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    bid: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    ask: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    complete: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("venue", "symbol", "kind", "event_ts", name="uq_observation"),
    )


class MarketDataGap(Base):
    __tablename__ = "market_data_gaps"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    gap_start: Mapped[datetime] = mapped_column(TS)  # first missing 1m open time
    gap_end: Mapped[datetime] = mapped_column(TS)  # last missing 1m open time
    missing: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(14))  # OPEN | BACKFILLED | UNRECOVERABLE
    detail: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(TS)
    closed_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    __table_args__ = (UniqueConstraint("venue", "symbol", "gap_start", name="uq_gap"),)


# ------------------------------------------------------------------ sessions (M2-B)
class PaperSession(Base):
    __tablename__ = "paper_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(14))
    state_reason: Mapped[str] = mapped_column(Text, default="")
    config_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_step_seq: Mapped[int] = mapped_column(Integer, default=-1)
    last_step_ts: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    last_reconcile_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=0)  # optimistic concurrency


class PaperSessionConfig(Base):
    __tablename__ = "paper_session_configs"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), primary_key=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class PaperSessionState(Base):
    __tablename__ = "paper_session_states"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    from_state: Mapped[str | None] = mapped_column(String(14), nullable=True)
    to_state: Mapped[str] = mapped_column(String(14))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class PaperStepInput(Base):
    __tablename__ = "paper_step_inputs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    step_seq: Mapped[int] = mapped_column(Integer)
    step_ts: Mapped[datetime] = mapped_column(TS)  # bar OPEN time of the bars consumed
    warm: Mapped[bool] = mapped_column(Boolean)
    bars: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {"BINANCE:BTC-USDT": {ohlcv...}}
    blocked: Mapped[dict[str, Any]] = mapped_column(JSONB)  # {"BINANCE:BTC-USDT": "STALE_DATA"}
    strategy_active: Mapped[bool] = mapped_column(Boolean)
    actions: Mapped[list[Any]] = mapped_column(JSONB)  # operator actions applied before this step
    inputs_hash: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("session_id", "step_seq", name="uq_step_seq"),
        UniqueConstraint("session_id", "step_ts", name="uq_step_ts"),
    )


class PaperSessionCheckpoint(Base):
    __tablename__ = "paper_session_checkpoints"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    step_seq: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (UniqueConstraint("session_id", "step_seq", name="uq_checkpoint_step"),)


# ------------------------------------------------------------------ simulated orders / money
class StrategySignal(Base):
    __tablename__ = "strategy_signals"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    account: Mapped[str] = mapped_column(String(64))
    signal_id: Mapped[int] = mapped_column(Integer)  # == the simulated order id it produced
    step_ts: Mapped[datetime] = mapped_column(TS)
    strategy_id: Mapped[str] = mapped_column(String(48))
    strategy_version: Mapped[str] = mapped_column(String(32))
    venue: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(16))
    tag: Mapped[str] = mapped_column(String(32), default="")
    accepted: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("session_id", "account", "signal_id", name="uq_signal"),)


class PaperOrder(Base):
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    account: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[int] = mapped_column(Integer)
    venue: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    order_type: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[Decimal] = mapped_column(NUM)
    filled_qty: Mapped[Decimal] = mapped_column(NUM)
    limit_price: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(64), default="")
    tag: Mapped[str] = mapped_column(String(32), default="")
    created_ts: Mapped[datetime] = mapped_column(TS)
    updated_ts: Mapped[datetime] = mapped_column(TS)
    __table_args__ = (UniqueConstraint("session_id", "account", "order_id", name="uq_paper_order"),)


class PaperOrderEvent(Base):
    __tablename__ = "paper_order_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    account: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(TS)
    status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(64), default="")
    __table_args__ = (UniqueConstraint("session_id", "account", "seq", name="uq_order_event"),)


class PaperFill(Base):
    __tablename__ = "paper_fills"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    account: Mapped[str] = mapped_column(String(64))
    fill_id: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[int] = mapped_column(Integer)
    venue: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    ts: Mapped[datetime] = mapped_column(TS)
    price: Mapped[Decimal] = mapped_column(NUM)
    quantity: Mapped[Decimal] = mapped_column(NUM)
    liquidity: Mapped[str] = mapped_column(String(6))
    fee_asset: Mapped[str] = mapped_column(String(16))
    fee_amount: Mapped[Decimal] = mapped_column(NUM)
    notional: Mapped[Decimal] = mapped_column(NUM)
    reference_price: Mapped[Decimal] = mapped_column(NUM)
    slippage_cost: Mapped[Decimal] = mapped_column(NUM)
    realized_pnl: Mapped[Decimal] = mapped_column(NUM)
    tag: Mapped[str] = mapped_column(String(32), default="")
    __table_args__ = (UniqueConstraint("session_id", "account", "fill_id", name="uq_paper_fill"),)


class PaperLedgerEntry(Base):
    __tablename__ = "paper_ledger_entries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    account: Mapped[str] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(TS)
    kind: Mapped[str] = mapped_column(String(16))
    asset: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(NUM)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(120), default="")
    __table_args__ = (UniqueConstraint("session_id", "account", "seq", name="uq_paper_ledger"),)


class PaperBalance(Base):
    """Projection of the ledger after the latest step (reconciled against the entries)."""

    __tablename__ = "paper_balances"
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), primary_key=True)
    account: Mapped[str] = mapped_column(String(64), primary_key=True)
    asset: Mapped[str] = mapped_column(String(16), primary_key=True)
    balance: Mapped[Decimal] = mapped_column(NUM)
    reserved: Mapped[Decimal] = mapped_column(NUM)
    cost_total: Mapped[Decimal] = mapped_column(NUM)
    mark: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    updated_step_seq: Mapped[int] = mapped_column(Integer)


class PaperEquitySnapshot(Base):
    __tablename__ = "paper_equity_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    step_seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(TS)  # bar CLOSE time
    equity: Mapped[Decimal] = mapped_column(NUM)
    cash: Mapped[Decimal] = mapped_column(NUM)
    exposure: Mapped[Decimal] = mapped_column(NUM)
    accounts: Mapped[dict[str, Any]] = mapped_column(JSONB)  # per account/venue breakdown
    benchmarks: Mapped[dict[str, Any]] = mapped_column(JSONB)  # shadow benchmark equity
    __table_args__ = (UniqueConstraint("session_id", "step_seq", name="uq_paper_equity"),)


class PaperRiskEvent(Base):
    __tablename__ = "paper_risk_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(TS)
    kind: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(Text)
    equity: Mapped[Decimal] = mapped_column(NUM)
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_paper_risk"),)


# ------------------------------------------------------------------ durable work
class DurableJob(Base):
    __tablename__ = "durable_jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_key: Mapped[str] = mapped_column(String(160), unique=True)  # idempotent creation
    kind: Mapped[str] = mapped_column(String(24))
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_sessions.id"), nullable=True, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    failure_class: Mapped[str | None] = mapped_column(String(12), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


Index("ix_jobs_claim", DurableJob.status, DurableJob.run_after)


class WorkerLease(Base):
    __tablename__ = "worker_leases"
    worker_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(24))
    started_at: Mapped[datetime] = mapped_column(TS)
    heartbeat_at: Mapped[datetime] = mapped_column(TS)
    info: Mapped[dict[str, Any]] = mapped_column(JSONB)


# ------------------------------------------------------------------ operations
class OperationalAlert(Base):
    __tablename__ = "operational_alerts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("paper_sessions.id"), nullable=True, index=True
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DailyPaperReport(Base):
    __tablename__ = "daily_paper_reports"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_sessions.id"), index=True)
    report_date: Mapped[datetime] = mapped_column(TS)  # UTC day start
    window_end: Mapped[datetime] = mapped_column(TS)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    __table_args__ = (
        UniqueConstraint("session_id", "report_date", "revision", name="uq_daily_report_rev"),
    )


# partial unique index: at most one OPEN incident per dedupe key (M0 `incidents` table reused)
OPEN_INCIDENT_INDEX = text("dedupe_key IS NOT NULL AND resolved_at IS NULL")
