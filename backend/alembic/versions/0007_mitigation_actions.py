"""structured mitigation actions

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mitigation_action",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("risk_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("budget", sa.Float(), nullable=True),
        sa.Column("completion_pct", sa.Integer(), nullable=True),
        sa.Column("effectiveness", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="Proposed"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["risk_id"], ["risk.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_mitigation_action_risk_id", "mitigation_action", ["risk_id"])


def downgrade() -> None:
    op.drop_index("ix_mitigation_action_risk_id", table_name="mitigation_action")
    op.drop_table("mitigation_action")
