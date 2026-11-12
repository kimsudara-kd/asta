"""M1 research tables (migration 0002). Completed results are insert-only: DB triggers forbid
UPDATE/DELETE on result tables, and on `backtest_runs` once a run is terminal."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from asta.db.models import NUM, TS, Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class HistoricalDataset(Base):
    __tablename__ = "historical_datasets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    venue: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    __table_args__ = (UniqueConstraint("venue", "symbol", "timeframe", name="uq_dataset_identity"),)


class HistoricalDatasetVersion(Base):
    """One immutable version = one content checksum."""

    __tablename__ = "historical_dataset_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("historical_datasets.id"))
    checksum: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(24))
    schema_version: Mapped[str] = mapped_column(String(8))
    period_start: Mapped[datetime] = mapped_column(TS)
    period_end: Mapped[datetime] = mapped_column(TS)
    row_count: Mapped[int] = mapped_column(Integer)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    parent_checksums: Mapped[list[str]] = mapped_column(JSONB)
    validation_status: Mapped[str] = mapped_column(String(10))
    accepted_for_research: Mapped[bool] = mapped_column(Boolean)
    label: Mapped[str] = mapped_column(String(80))
    file_name: Mapped[str] = mapped_column(String(200))
    imported_at: Mapped[datetime] = mapped_column(TS)
    imported_by: Mapped[str] = mapped_column(String(64))


class DatasetQualityReport(Base):
    __tablename__ = "dataset_quality_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("historical_dataset_versions.id"))
    status: Mapped[str] = mapped_column(String(10))
    report: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class StrategyDefinition(Base):
    __tablename__ = "strategy_definitions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    strategy_id: Mapped[str] = mapped_column(String(48), unique=True)
    kind: Mapped[str] = mapped_column(String(12))
    display_name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)


class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    definition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("strategy_definitions.id"))
    version: Mapped[str] = mapped_column(String(16))
    code_fingerprint: Mapped[str] = mapped_column(String(64))
    default_params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint(
            "definition_id", "version", "code_fingerprint", name="uq_strategy_version"
        ),
    )


class BacktestRunConfig(Base):
    __tablename__ = "backtest_run_configs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    config_hash: Mapped[str] = mapped_column(String(64), unique=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_run_configs.id"))
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("strategy_versions.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(12))  # QUEUED RUNNING COMPLETED FAILED CANCELLED
    result_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    risk_verdict: Mapped[str | None] = mapped_column(String(10), nullable=True)
    research_grade: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    git_commit: Mapped[str] = mapped_column(String(64))
    dataset_version_ids: Mapped[dict[str, Any]] = mapped_column(JSONB)
    execution_model_version: Mapped[str] = mapped_column(String(48))
    fee_schedule_version: Mapped[str] = mapped_column(String(64))
    risk_policy_version: Mapped[str] = mapped_column(String(32))
    seed: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(120))
    persist_detail: Mapped[bool] = mapped_column(Boolean)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    result_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[list[str]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(64))
    corrects_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("backtest_runs.id"), nullable=True
    )
    queued_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class SimulatedOrder(Base):
    __tablename__ = "simulated_orders"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    order_id: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    type: Mapped[str] = mapped_column(String(12))
    quantity: Mapped[Decimal] = mapped_column(NUM)
    limit_price: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(NUM, nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    filled_qty: Mapped[Decimal] = mapped_column(NUM)
    tag: Mapped[str] = mapped_column(String(32))
    client_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reject_reason: Mapped[str] = mapped_column(String(48))
    created_ts: Mapped[datetime] = mapped_column(TS)
    events: Mapped[list[Any]] = mapped_column(JSONB)


class SimulatedFill(Base):
    __tablename__ = "simulated_fills"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    fill_id: Mapped[int] = mapped_column(Integer)
    order_id: Mapped[int] = mapped_column(Integer)
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
    tag: Mapped[str] = mapped_column(String(32))


class SimulatedLedgerEntry(Base):
    __tablename__ = "simulated_ledger_entries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(TS)
    kind: Mapped[str] = mapped_column(String(16))
    asset: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal] = mapped_column(NUM)
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fill_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(String(120))


class PortfolioEquitySnapshot(Base):
    __tablename__ = "portfolio_equity_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    ts: Mapped[datetime] = mapped_column(TS)
    equity: Mapped[Decimal] = mapped_column(NUM)
    cash: Mapped[Decimal] = mapped_column(NUM)
    positions_value: Mapped[Decimal] = mapped_column(NUM)
    reserved_cash: Mapped[Decimal] = mapped_column(NUM)
    exposure: Mapped[Decimal] = mapped_column(NUM)


class RiskEventRow(Base):
    __tablename__ = "risk_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(TS)
    kind: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(Text)
    equity: Mapped[Decimal] = mapped_column(NUM)


class PerformanceReportRow(Base):
    __tablename__ = "performance_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("backtest_runs.id"), unique=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ResearchValidation(Base):
    __tablename__ = "research_validations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(24))  # WALK_FORWARD | SENSITIVITY | HOLDOUT
    strategy_id: Mapped[str] = mapped_column(String(48))
    title: Mapped[str] = mapped_column(String(160))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    cycle: Mapped[int] = mapped_column(Integer)
    oos_reused: Mapped[bool] = mapped_column(Boolean)
    research_grade: Mapped[bool] = mapped_column(Boolean)
    advisory: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class ResearchHoldoutWindow(Base):
    __tablename__ = "research_holdout_windows"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(160))
    window_start: Mapped[datetime] = mapped_column(TS)
    window_end: Mapped[datetime] = mapped_column(TS)
    cycle: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS, server_default=func.now())


__all__ = [
    "BacktestRun",
    "BacktestRunConfig",
    "DatasetQualityReport",
    "HistoricalDataset",
    "HistoricalDatasetVersion",
    "IdempotencyKey",
    "Numeric",
    "PerformanceReportRow",
    "PortfolioEquitySnapshot",
    "ResearchHoldoutWindow",
    "ResearchValidation",
    "RiskEventRow",
    "SimulatedFill",
    "SimulatedLedgerEntry",
    "SimulatedOrder",
    "StrategyDefinition",
    "StrategyVersion",
]
