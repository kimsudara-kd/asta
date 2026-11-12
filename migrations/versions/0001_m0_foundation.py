"""M0 foundation schema

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NUM = sa.Numeric(38, 18)
TS = sa.DateTime(timezone=True)
ENVS = "('BACKTEST','PAPER','EXCHANGE_DEMO')"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("totp_secret_enc", sa.Text),
        sa.Column("mfa_enrolled", sa.Boolean, nullable=False),
        sa.Column("failed_logins", sa.Integer, nullable=False),
        sa.Column("locked_until", TS),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "role IN ('viewer','operator','reviewer','admin')", name="ck_users_role"
        ),
    )
    op.create_table(
        "sessions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("mfa_passed", sa.Boolean, nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False),
    )
    op.create_table(
        "exchange_connections",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("venue", sa.String(16), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("secret_ref", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"environment IN {ENVS}", name="ck_conn_env"),
        sa.CheckConstraint("venue IN ('BINANCE','OKX')", name="ck_conn_venue"),
        sa.UniqueConstraint("venue", "environment", name="uq_conn_venue_env"),
    )
    op.create_table(
        "markets",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("venue", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("base", sa.String(16), nullable=False),
        sa.Column("quote", sa.String(16), nullable=False),
        sa.UniqueConstraint("venue", "symbol", name="uq_market_venue_symbol"),
    )
    op.create_table(
        "market_rules_versions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("venue", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("tick_size", NUM, nullable=False),
        sa.Column("step_size", NUM, nullable=False),
        sa.Column("min_qty", NUM, nullable=False),
        sa.Column("min_notional", NUM),
        sa.Column("trading", sa.Boolean, nullable=False),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("retrieved_at", TS, nullable=False),
        sa.UniqueConstraint("venue", "symbol", "retrieved_at", name="uq_rules_version"),
    )
    op.create_table(
        "risk_policies",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("params", pg.JSONB, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", TS, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_policy_version"),
    )
    op.create_table(
        "incidents",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("severity", sa.String(8), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column("opened_at", TS, server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", TS),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", TS, nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False, unique=True),
        sa.CheckConstraint(f"environment IN {ENVS}", name="ck_audit_env"),
    )
    op.execute(
        """
        CREATE FUNCTION audit_events_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_immutable()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_immutable()")
    for t in (
        "audit_events",
        "incidents",
        "risk_policies",
        "market_rules_versions",
        "markets",
        "exchange_connections",
        "sessions",
        "users",
    ):
        op.drop_table(t)
