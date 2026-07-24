"""rbs category and subcategory tables

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-24

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rbs_category",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_rbs_category_code", "rbs_category", ["code"], unique=True)

    op.create_table(
        "rbs_subcategory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=3), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["category_id"], ["rbs_category.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("category_id", "code", name="uq_subcategory_code"),
    )
    op.create_index(
        "ix_rbs_subcategory_category_id", "rbs_subcategory", ["category_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_rbs_subcategory_category_id", table_name="rbs_subcategory")
    op.drop_table("rbs_subcategory")
    op.drop_index("ix_rbs_category_code", table_name="rbs_category")
    op.drop_table("rbs_category")
