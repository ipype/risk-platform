"""risk register table

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-31

"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subcategory_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("risk_code", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("causes", sa.Text(), nullable=True),
        sa.Column("consequences", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="Open"),
        sa.Column("probability", sa.Integer(), nullable=True),
        sa.Column("impact", sa.Integer(), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=True),
        sa.Column("mitigation_actions", sa.Text(), nullable=True),
        sa.Column("owner", sa.String(length=200), nullable=True),
        sa.Column("last_review_date", sa.Date(), nullable=True),
        sa.Column("comments", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["subcategory_id"], ["rbs_subcategory.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("subcategory_id", "seq", name="uq_risk_subcategory_seq"),
    )
    op.create_index("ix_risk_subcategory_id", "risk", ["subcategory_id"])
    op.create_index("ix_risk_risk_code", "risk", ["risk_code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_risk_risk_code", table_name="risk")
    op.drop_index("ix_risk_subcategory_id", table_name="risk")
    op.drop_table("risk")