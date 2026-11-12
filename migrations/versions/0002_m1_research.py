"""M1 offline research schema (datasets, strategies, backtest runs, simulated results)

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

NUM = sa.Numeric(38, 18)
TS = sa.DateTime(timezone=True)
NOW = sa.func.now()

# tables whose rows may never be updated or deleted (insert-only)
APPEND_ONLY = (
    "simulated_orders",
    "simulated_fills",
    "simulated_ledger_entries",
    "portfolio_equity_snapshots",
    "risk_events",
    "performance_reports",
    "backtest_run_configs",
    "historical_dataset_versions",
    "dataset_quality_reports",
    "research_validations",
    "research_holdout_windows",
)


def _id() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def _run_fk() -> sa.Column:
    return sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("backtest_runs.id"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "historical_datasets",
        _id(),
        sa.Column("venue", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
        sa.UniqueConstraint("venue", "symbol", "timeframe", name="uq_dataset_identity"),
    )
    op.create_table(
        "historical_dataset_versions",
        _id(),
        sa.Column("dataset_id", pg.UUID(as_uuid=True), sa.ForeignKey("historical_datasets.id"), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False, unique=True),
        sa.Column("source", sa.Text, nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("schema_version", sa.String(8), nullable=False),
        sa.Column("period_start", TS, nullable=False),
        sa.Column("period_end", TS, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("provenance", pg.JSONB, nullable=False),
        sa.Column("parent_checksums", pg.JSONB, nullable=False),
        sa.Column("validation_status", sa.String(10), nullable=False),
        sa.Column("accepted_for_research", sa.Boolean, nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("file_name", sa.String(200), nullable=False),
        sa.Column("imported_at", TS, nullable=False),
        sa.Column("imported_by", sa.String(64), nullable=False),
    )
    op.create_table(
        "dataset_quality_reports",
        _id(),
        sa.Column("version_id", pg.UUID(as_uuid=True), sa.ForeignKey("historical_dataset_versions.id"), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("report", pg.JSONB, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_table(
        "strategy_definitions",
        _id(),
        sa.Column("strategy_id", sa.String(48), nullable=False, unique=True),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
    )
    op.create_table(
        "strategy_versions",
        _id(),
        sa.Column("definition_id", pg.UUID(as_uuid=True), sa.ForeignKey("strategy_definitions.id"), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("code_fingerprint", sa.String(64), nullable=False),
        sa.Column("default_params", pg.JSONB, nullable=False),
        sa.UniqueConstraint("definition_id", "version", "code_fingerprint", name="uq_strategy_version"),
    )
    op.create_table(
        "backtest_run_configs",
        _id(),
        sa.Column("config_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("config", pg.JSONB, nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_table(
        "backtest_runs",
        _id(),
        sa.Column("config_id", pg.UUID(as_uuid=True), sa.ForeignKey("backtest_run_configs.id"), nullable=False),
        sa.Column("strategy_version_id", pg.UUID(as_uuid=True), sa.ForeignKey("strategy_versions.id")),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("result_status", sa.String(24)),
        sa.Column("risk_verdict", sa.String(10)),
        sa.Column("research_grade", sa.Boolean),
        sa.Column("git_commit", sa.String(64), nullable=False),
        sa.Column("dataset_version_ids", pg.JSONB, nullable=False),
        sa.Column("execution_model_version", sa.String(48), nullable=False),
        sa.Column("fee_schedule_version", sa.String(64), nullable=False),
        sa.Column("risk_policy_version", sa.String(32), nullable=False),
        sa.Column("seed", sa.Integer, nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("persist_detail", sa.Boolean, nullable=False),
        sa.Column("progress_pct", sa.Integer, nullable=False),
        sa.Column("result_checksum", sa.String(64)),
        sa.Column("error", sa.Text),
        sa.Column("notes", pg.JSONB, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("corrects_run_id", pg.UUID(as_uuid=True), sa.ForeignKey("backtest_runs.id")),
        sa.Column("queued_at", TS, server_default=NOW, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
    )
    op.create_table(
        "simulated_orders",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _run_fk(),
        sa.Column("order_id", sa.Integer, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("type", sa.String(12), nullable=False),
        sa.Column("quantity", NUM, nullable=False),
        sa.Column("limit_price", NUM),
        sa.Column("stop_price", NUM),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("filled_qty", NUM, nullable=False),
        sa.Column("tag", sa.String(32), nullable=False),
        sa.Column("client_id", sa.String(80)),
        sa.Column("reject_reason", sa.String(48), nullable=False),
        sa.Column("created_ts", TS, nullable=False),
        sa.Column("events", pg.JSONB, nullable=False),
    )
    op.create_index("ix_simulated_orders_run_id", "simulated_orders", ["run_id"])
    op.create_table(
        "simulated_fills",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _run_fk(),
        sa.Column("fill_id", sa.Integer, nullable=False),
        sa.Column("order_id", sa.Integer, nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("ts", TS, nullable=False),
        sa.Column("price", NUM, nullable=False),
        sa.Column("quantity", NUM, nullable=False),
        sa.Column("liquidity", sa.String(6), nullable=False),
        sa.Column("fee_asset", sa.String(16), nullable=False),
        sa.Column("fee_amount", NUM, nullable=False),
        sa.Column("notional", NUM, nullable=False),
        sa.Column("reference_price", NUM, nullable=False),
        sa.Column("slippage_cost", NUM, nullable=False),
        sa.Column("realized_pnl", NUM, nullable=False),
        sa.Column("tag", sa.String(32), nullable=False),
    )
    op.create_index("ix_simulated_fills_run_id", "simulated_fills", ["run_id"])
    op.create_table(
        "simulated_ledger_entries",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _run_fk(),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("ts", TS, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("asset", sa.String(16), nullable=False),
        sa.Column("amount", NUM, nullable=False),
        sa.Column("order_id", sa.Integer),
        sa.Column("fill_id", sa.Integer),
        sa.Column("note", sa.String(120), nullable=False),
    )
    op.create_index("ix_simulated_ledger_entries_run_id", "simulated_ledger_entries", ["run_id"])
    op.create_table(
        "portfolio_equity_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _run_fk(),
        sa.Column("ts", TS, nullable=False),
        sa.Column("equity", NUM, nullable=False),
        sa.Column("cash", NUM, nullable=False),
        sa.Column("positions_value", NUM, nullable=False),
        sa.Column("reserved_cash", NUM, nullable=False),
        sa.Column("exposure", NUM, nullable=False),
    )
    op.create_index("ix_portfolio_equity_snapshots_run_id", "portfolio_equity_snapshots", ["run_id"])
    op.create_table(
        "risk_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _run_fk(),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("ts", TS, nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column("equity", NUM, nullable=False),
    )
    op.create_index("ix_risk_events_run_id", "risk_events", ["run_id"])
    op.create_table(
        "performance_reports",
        _id(),
        sa.Column("run_id", pg.UUID(as_uuid=True), sa.ForeignKey("backtest_runs.id"), nullable=False, unique=True),
        sa.Column("metrics", pg.JSONB, nullable=False),
        sa.Column("summary", pg.JSONB, nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_table(
        "research_validations",
        _id(),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("strategy_id", sa.String(48), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("config", pg.JSONB, nullable=False),
        sa.Column("result", pg.JSONB, nullable=False),
        sa.Column("cycle", sa.Integer, nullable=False),
        sa.Column("oos_reused", sa.Boolean, nullable=False),
        sa.Column("research_grade", sa.Boolean, nullable=False),
        sa.Column("advisory", pg.JSONB, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_table(
        "research_holdout_windows",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("key", sa.String(160), nullable=False),
        sa.Column("window_start", TS, nullable=False),
        sa.Column("window_end", TS, nullable=False),
        sa.Column("cycle", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("endpoint", sa.String(80), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("response", pg.JSONB, nullable=False),
        sa.Column("created_at", TS, server_default=NOW, nullable=False),
    )

    # ---- immutability: completed research results can never be edited or deleted ----
    op.execute(
        """
        CREATE FUNCTION research_immutable() RETURNS trigger AS $$
        BEGIN
          IF TG_TABLE_NAME = 'backtest_runs' THEN
            IF TG_OP = 'DELETE' THEN
              RAISE EXCEPTION 'backtest_runs rows cannot be deleted';
            END IF;
            IF OLD.status IN ('COMPLETED', 'FAILED', 'CANCELLED') THEN
              RAISE EXCEPTION 'backtest run % is terminal and immutable; create a new run', OLD.id;
            END IF;
            RETURN NEW;
          END IF;
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER backtest_runs_immutable BEFORE UPDATE OR DELETE ON backtest_runs "
        "FOR EACH ROW EXECUTE FUNCTION research_immutable()"
    )
    for t in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {t}_append_only BEFORE UPDATE OR DELETE ON {t} "
            "FOR EACH ROW EXECUTE FUNCTION research_immutable()"
        )
        op.execute(
            f"CREATE TRIGGER {t}_no_truncate BEFORE TRUNCATE ON {t} "
            "FOR EACH STATEMENT EXECUTE FUNCTION research_immutable()"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS backtest_runs_immutable ON backtest_runs")
    for t in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t}")
    op.execute("DROP FUNCTION IF EXISTS research_immutable()")
    for t in (
        "idempotency_keys", "research_holdout_windows", "research_validations", "performance_reports",
        "risk_events", "portfolio_equity_snapshots", "simulated_ledger_entries", "simulated_fills",
        "simulated_orders", "backtest_runs", "backtest_run_configs", "strategy_versions",
        "strategy_definitions", "dataset_quality_reports", "historical_dataset_versions",
        "historical_datasets",
    ):
        op.drop_table(t)
