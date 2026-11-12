"""M1-R1: provenance + de-duplication columns for stored research studies

Revision ID: 0003
Revises: 0002

Adds nullable columns (existing rows, if any, are untouched; the append-only trigger is not
involved because no UPDATE is issued) and a unique content hash so repeated imports are no-ops.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("research_validations", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("research_validations", sa.Column("sample_type", sa.String(32), nullable=True))
    op.add_column("research_validations", sa.Column("strategy_version", sa.String(32), nullable=True))
    op.add_column("research_validations", sa.Column("source_datasets", pg.JSONB, nullable=True))
    op.add_column("research_validations", sa.Column("source_artifact", sa.String(200), nullable=True))
    op.add_column("research_validations", sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_research_validations_content_hash", "research_validations", ["content_hash"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_research_validations_content_hash", table_name="research_validations")
    for c in (
        "generated_at",
        "source_artifact",
        "source_datasets",
        "strategy_version",
        "sample_type",
        "content_hash",
    ):
        op.drop_column("research_validations", c)
