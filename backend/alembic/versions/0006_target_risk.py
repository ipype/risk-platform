"""target (residual) probability and impact

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-28

"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("risk", sa.Column("target_probability", sa.Integer(), nullable=True))
    op.add_column("risk", sa.Column("target_impact", sa.Integer(), nullable=True))
    op.add_column("risk", sa.Column("target_impact_scores", sa.JSON(), nullable=True))
    op.add_column("risk", sa.Column("target_risk_level", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("risk", "target_risk_level")
    op.drop_column("risk", "target_impact_scores")
    op.drop_column("risk", "target_impact")
    op.drop_column("risk", "target_probability")
