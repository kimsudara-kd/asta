"""M3-B-R2: risk-halt bridge bookkeeping (schema only; nothing here sends or receives an order)

Revision ID: 0009
Revises: 0008

risk_halt_bridge_seen records which persistent paper risk-halt events the bridge has already turned
into a kill-switch trip, so a restart or a second bridge worker never trips twice for the same
event, and a halt that was released by an authorised operator is not silently re-latched by the same
old event (a NEW halt event always trips again). Rows are append-only. The bridge has no release
capability: only release_kill_switch() (authorised releasers, reason required) can clear the latch.
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_halt_bridge_seen",
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("event_key", sa.String(length=96), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("source", "event_key"),
    )
    op.execute(
        "CREATE TRIGGER rhbs_append_only BEFORE UPDATE OR DELETE ON risk_halt_bridge_seen FOR EACH ROW EXECUTE FUNCTION append_only_guard()"
    )
    op.execute(
        "CREATE TRIGGER rhbs_no_truncate BEFORE TRUNCATE ON risk_halt_bridge_seen FOR EACH STATEMENT EXECUTE FUNCTION append_only_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS rhbs_no_truncate ON risk_halt_bridge_seen")
    op.execute("DROP TRIGGER IF EXISTS rhbs_append_only ON risk_halt_bridge_seen")
    op.drop_table("risk_halt_bridge_seen")
