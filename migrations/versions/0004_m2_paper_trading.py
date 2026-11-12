"""M2 paper trading + public market observation schema

Revision ID: 0004
Revises: 0003

Reuses the M0 `incidents` table for operational incidents (adds session/venue/symbol/dedupe
columns and a partial unique index: one OPEN incident per dedupe key). All ledger-relevant paper
tables are insert-only (the research_immutable() trigger from 0002 also blocks TRUNCATE).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APPEND_ONLY = (
    "market_observations",
    "paper_session_configs",
    "paper_session_states",
    "paper_step_inputs",
    "paper_session_checkpoints",
    "strategy_signals",
    "paper_order_events",
    "paper_fills",
    "paper_ledger_entries",
    "paper_equity_snapshots",
    "paper_risk_events",
    "daily_paper_reports",
)


def upgrade() -> None:
    op.create_table(
        "market_data_gaps",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("venue", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("gap_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gap_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("missing", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=14), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("venue", "symbol", "gap_start", name="uq_gap"),
    )
    op.create_table(
        "market_feed_connections",
        sa.Column("venue", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("since", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconnects", sa.Integer(), nullable=False),
        sa.Column("clock_offset_ms", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("venue"),
    )
    op.create_table(
        "market_observations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("venue", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("native_symbol", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("event_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchange_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("open", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("high", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("low", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("close", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("volume", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("bid", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("ask", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("venue", "symbol", "kind", "event_ts", name="uq_observation"),
    )
    op.create_table(
        "paper_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=14), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_step_seq", sa.Integer(), nullable=False),
        sa.Column("last_step_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reconcile_ok", sa.Boolean(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "worker_leases",
        sa.Column("worker_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("info", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_table(
        "daily_paper_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("report_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "report_date", name="uq_daily_report"),
    )
    op.create_index(
        op.f("ix_daily_paper_reports_session_id"),
        "daily_paper_reports",
        ["session_id"],
        unique=False,
    )
    op.create_table(
        "durable_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_key", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_class", sa.String(length=12), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_key"),
    )
    op.create_index(
        op.f("ix_durable_jobs_session_id"), "durable_jobs", ["session_id"], unique=False
    )
    op.create_index(op.f("ix_durable_jobs_status"), "durable_jobs", ["status"], unique=False)
    op.create_index("ix_jobs_claim", "durable_jobs", ["status", "run_after"], unique=False)
    op.create_table(
        "operational_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("incident_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_operational_alerts_session_id"), "operational_alerts", ["session_id"], unique=False
    )
    op.create_table(
        "paper_balances",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("asset", sa.String(length=16), nullable=False),
        sa.Column("balance", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("reserved", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("cost_total", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("mark", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("updated_step_seq", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("session_id", "account", "asset"),
    )
    op.create_table(
        "paper_equity_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("step_seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("equity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("cash", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("exposure", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("accounts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("benchmarks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "step_seq", name="uq_paper_equity"),
    )
    op.create_index(
        op.f("ix_paper_equity_snapshots_session_id"),
        "paper_equity_snapshots",
        ["session_id"],
        unique=False,
    )
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("fill_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("venue", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("liquidity", sa.String(length=6), nullable=False),
        sa.Column("fee_asset", sa.String(length=16), nullable=False),
        sa.Column("fee_amount", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("notional", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("reference_price", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("slippage_cost", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("realized_pnl", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("tag", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "account", "fill_id", name="uq_paper_fill"),
    )
    op.create_index(op.f("ix_paper_fills_session_id"), "paper_fills", ["session_id"], unique=False)
    op.create_table(
        "paper_ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("asset", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("fill_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=120), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "account", "seq", name="uq_paper_ledger"),
    )
    op.create_index(
        op.f("ix_paper_ledger_entries_session_id"),
        "paper_ledger_entries",
        ["session_id"],
        unique=False,
    )
    op.create_table(
        "paper_order_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "account", "seq", name="uq_order_event"),
    )
    op.create_index(
        op.f("ix_paper_order_events_session_id"), "paper_order_events", ["session_id"], unique=False
    )
    op.create_table(
        "paper_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("venue", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("filled_qty", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("limit_price", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("tag", sa.String(length=32), nullable=False),
        sa.Column("created_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_ts", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "account", "order_id", name="uq_paper_order"),
    )
    op.create_index(
        op.f("ix_paper_orders_session_id"), "paper_orders", ["session_id"], unique=False
    )
    op.create_table(
        "paper_risk_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("equity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "seq", name="uq_paper_risk"),
    )
    op.create_index(
        op.f("ix_paper_risk_events_session_id"), "paper_risk_events", ["session_id"], unique=False
    )
    op.create_table(
        "paper_session_checkpoints",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("step_seq", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "step_seq", name="uq_checkpoint_step"),
    )
    op.create_index(
        op.f("ix_paper_session_checkpoints_session_id"),
        "paper_session_checkpoints",
        ["session_id"],
        unique=False,
    )
    op.create_table(
        "paper_session_configs",
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_table(
        "paper_session_states",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("from_state", sa.String(length=14), nullable=True),
        sa.Column("to_state", sa.String(length=14), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_paper_session_states_session_id"),
        "paper_session_states",
        ["session_id"],
        unique=False,
    )
    op.create_table(
        "paper_step_inputs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("step_seq", sa.Integer(), nullable=False),
        sa.Column("step_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warm", sa.Boolean(), nullable=False),
        sa.Column("bars", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("blocked", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("strategy_active", sa.Boolean(), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "step_seq", name="uq_step_seq"),
        sa.UniqueConstraint("session_id", "step_ts", name="uq_step_ts"),
    )
    op.create_index(
        op.f("ix_paper_step_inputs_session_id"), "paper_step_inputs", ["session_id"], unique=False
    )
    op.create_table(
        "strategy_signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("account", sa.String(length=64), nullable=False),
        sa.Column("signal_id", sa.Integer(), nullable=False),
        sa.Column("step_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("strategy_id", sa.String(length=48), nullable=False),
        sa.Column("strategy_version", sa.String(length=32), nullable=False),
        sa.Column("venue", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=4), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("tag", sa.String(length=32), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["paper_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "account", "signal_id", name="uq_signal"),
    )
    op.create_index(
        op.f("ix_strategy_signals_session_id"), "strategy_signals", ["session_id"], unique=False
    )
    op.add_column("incidents", sa.Column("session_id", sa.UUID(), nullable=True))
    op.add_column("incidents", sa.Column("venue", sa.String(length=16), nullable=True))
    op.add_column("incidents", sa.Column("symbol", sa.String(length=32), nullable=True))
    op.add_column("incidents", sa.Column("dedupe_key", sa.String(length=160), nullable=True))
    op.add_column("incidents", sa.Column("resolved_by", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_incident_open_dedupe",
        "incidents",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL AND resolved_at IS NULL"),
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
    for t in APPEND_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS {t}_append_only ON {t}")
        op.execute(f"DROP TRIGGER IF EXISTS {t}_no_truncate ON {t}")
    op.drop_index(
        "uq_incident_open_dedupe",
        table_name="incidents",
        postgresql_where=sa.text("dedupe_key IS NOT NULL AND resolved_at IS NULL"),
    )
    op.drop_column("incidents", "resolved_by")
    op.drop_column("incidents", "dedupe_key")
    op.drop_column("incidents", "symbol")
    op.drop_column("incidents", "venue")
    op.drop_column("incidents", "session_id")
    op.drop_index(op.f("ix_strategy_signals_session_id"), table_name="strategy_signals")
    op.drop_table("strategy_signals")
    op.drop_index(op.f("ix_paper_step_inputs_session_id"), table_name="paper_step_inputs")
    op.drop_table("paper_step_inputs")
    op.drop_index(op.f("ix_paper_session_states_session_id"), table_name="paper_session_states")
    op.drop_table("paper_session_states")
    op.drop_table("paper_session_configs")
    op.drop_index(
        op.f("ix_paper_session_checkpoints_session_id"), table_name="paper_session_checkpoints"
    )
    op.drop_table("paper_session_checkpoints")
    op.drop_index(op.f("ix_paper_risk_events_session_id"), table_name="paper_risk_events")
    op.drop_table("paper_risk_events")
    op.drop_index(op.f("ix_paper_orders_session_id"), table_name="paper_orders")
    op.drop_table("paper_orders")
    op.drop_index(op.f("ix_paper_order_events_session_id"), table_name="paper_order_events")
    op.drop_table("paper_order_events")
    op.drop_index(op.f("ix_paper_ledger_entries_session_id"), table_name="paper_ledger_entries")
    op.drop_table("paper_ledger_entries")
    op.drop_index(op.f("ix_paper_fills_session_id"), table_name="paper_fills")
    op.drop_table("paper_fills")
    op.drop_index(op.f("ix_paper_equity_snapshots_session_id"), table_name="paper_equity_snapshots")
    op.drop_table("paper_equity_snapshots")
    op.drop_table("paper_balances")
    op.drop_index(op.f("ix_operational_alerts_session_id"), table_name="operational_alerts")
    op.drop_table("operational_alerts")
    op.drop_index("ix_jobs_claim", table_name="durable_jobs")
    op.drop_index(op.f("ix_durable_jobs_status"), table_name="durable_jobs")
    op.drop_index(op.f("ix_durable_jobs_session_id"), table_name="durable_jobs")
    op.drop_table("durable_jobs")
    op.drop_index(op.f("ix_daily_paper_reports_session_id"), table_name="daily_paper_reports")
    op.drop_table("daily_paper_reports")
    op.drop_table("worker_leases")
    op.drop_table("paper_sessions")
    op.drop_table("market_observations")
    op.drop_table("market_feed_connections")
    op.drop_table("market_data_gaps")
