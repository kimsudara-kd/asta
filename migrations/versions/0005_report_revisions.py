"""M2-R2: daily report revisions (append-only correction of a defective report)

Revision ID: 0005
Revises: 0004

A report row is never edited. A report found incomplete or not reproducible is superseded by a NEW
revision row (explicit operator/CLI action); readers use the highest revision per (session, day).
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "daily_paper_reports",
        sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.drop_constraint("uq_daily_report", "daily_paper_reports", type_="unique")
    op.create_unique_constraint(
        "uq_daily_report_rev", "daily_paper_reports", ["session_id", "report_date", "revision"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_daily_report_rev", "daily_paper_reports", type_="unique")
    op.create_unique_constraint(
        "uq_daily_report", "daily_paper_reports", ["session_id", "report_date"]
    )
    op.drop_column("daily_paper_reports", "revision")
