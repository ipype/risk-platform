"""risk change history

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-21

"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("risk_id", sa.Integer(), nullable=False),
        sa.Column("risk_code", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False, server_default="Unknown"),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_risk_history_risk_id", "risk_history", ["risk_id"])
    op.create_index("ix_risk_history_risk_code", "risk_history", ["risk_code"])
    op.create_index("ix_risk_history_created_at", "risk_history", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_risk_history_created_at", table_name="risk_history")
    op.drop_index("ix_risk_history_risk_code", table_name="risk_history")
    op.drop_index("ix_risk_history_risk_id", table_name="risk_history")
    op.drop_table("risk_history")
