"""initial schema: pgvector extension + system_meta

Revision ID: 0001
Revises:
Create Date: 2026-07-17

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "system_meta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_system_meta_key", "system_meta", ["key"], unique=True)
    op.execute(
        "INSERT INTO system_meta (key, value) VALUES ('schema_version', '0001')"
    )


def downgrade() -> None:
    op.drop_index("ix_system_meta_key", table_name="system_meta")
    op.drop_table("system_meta")
