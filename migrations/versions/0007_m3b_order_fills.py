"""M3-B preparation: durable, append-only fill store (schema only; nothing sends or receives orders)

Revision ID: 0007
Revises: 0006

Trades of an order as an idempotent, append-only ledger: (decision_id, trade_id) is UNIQUE so the
same trade delivered twice (stream + REST after a reconnect) is stored once; a fill belongs to an
existing intent (FK); sizes/prices must be positive and the fee non-negative; rows can never be
updated, deleted or truncated (a wrong record is corrected by a reviewed migration, not silently).
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_fills",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "decision_id",
            sa.String(length=32),
            sa.ForeignKey("order_intents.decision_id"),
            nullable=False,
        ),
        sa.Column("trade_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee_asset", sa.String(length=16), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("decision_id", "trade_id", name="uq_order_fills_trade"),
        sa.CheckConstraint("quantity > 0 AND price > 0 AND fee >= 0", name="ck_of_positive"),
        sa.CheckConstraint("length(trade_id) > 0 AND length(fee_asset) > 0", name="ck_of_ids"),
    )
    op.create_index("ix_order_fills_decision", "order_fills", ["decision_id"])
    op.execute(
        """
        CREATE FUNCTION order_fills_immutable() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'order_fills is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER order_fills_append_only BEFORE UPDATE OR DELETE ON order_fills "
        "FOR EACH ROW EXECUTE FUNCTION order_fills_immutable()"
    )
    op.execute(
        "CREATE TRIGGER order_fills_no_truncate BEFORE TRUNCATE ON order_fills "
        "FOR EACH STATEMENT EXECUTE FUNCTION order_fills_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS order_fills_no_truncate ON order_fills")
    op.execute("DROP TRIGGER IF EXISTS order_fills_append_only ON order_fills")
    op.execute("DROP FUNCTION IF EXISTS order_fills_immutable()")
    op.drop_table("order_fills")
