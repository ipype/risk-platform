"""the document corpus

Revision ID: 0022
Revises: 0021
Create Date: 2026-08-08

Two new tables, nothing existing touched, so there is no backfill to review and the
downgrade is a clean drop.

**``uq_document_scope_sha256`` is what makes this a corpus rather than a pile.** A second
upload of the same bytes into the same project returns the document already there instead
of duplicating every chunk in it, and a duplicated chunk is worse than a wasted row: it
doubles a source's weight in any retrieval that later runs over the text. Scoped rather
than global, because two projects legitimately hold the same standard and each needs its
own citations to resolve within its own scope.

**``uq_chunk_document_ordinal``** pins that a document's chunks are a sequence with no
gaps or repeats at any given moment. Re-extraction replaces the whole set rather than
patching it, so this holds across a re-ingest as well as within one.

**No embedding column.** ``pgvector`` is installed and 0001 creates the extension, but
``pgvector.sqlalchemy.Vector`` does not compile against the SQLite the suite runs on, so
declaring it here would break the test engine in exchange for storage nothing writes.
Adding it later is one nullable column and a backfill over rows that already exist, which
keeps the provider decision off the critical path instead of on it.

``ondelete="CASCADE"`` on ``document_chunk.document_id`` is a Postgres promise only —
SQLite ignores it without ``PRAGMA foreign_keys``. The re-ingest path deletes chunks
explicitly for that reason; the cascade is the safety net.
"""

import sqlalchemy as sa

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("suffix", sa.String(length=20), nullable=False),
        sa.Column(
            "source_kind", sa.String(length=20), nullable=False, server_default="upload"
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="active"
        ),
        sa.Column("withdrawn_reason", sa.Text(), nullable=True),
        sa.Column(
            "uploaded_by", sa.String(length=120), nullable=False, server_default="Unknown"
        ),
        # ``sa.func.now()`` and not ``sa.text("now()")``: the latter is Postgres-only and
        # this migration is executed against SQLite by its own test.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["scope_node.id"],
            name="fk_document_scope_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("scope_id", "sha256", name="uq_document_scope_sha256"),
    )
    op.create_index("ix_document_scope_id", "document", ["scope_id"])
    op.create_index("ix_document_status", "document", ["status"])
    op.create_index("ix_document_created_at", "document", ["created_at"])

    op.create_table(
        "document_chunk",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=True),
        sa.Column("section", sa.String(length=500), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["document.id"],
            name="fk_document_chunk_document_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),
    )
    op.create_index("ix_document_chunk_document_id", "document_chunk", ["document_id"])
    op.create_index("ix_document_chunk_kind", "document_chunk", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_document_chunk_kind", table_name="document_chunk")
    op.drop_index("ix_document_chunk_document_id", table_name="document_chunk")
    op.drop_table("document_chunk")
    op.drop_index("ix_document_created_at", table_name="document")
    op.drop_index("ix_document_status", table_name="document")
    op.drop_index("ix_document_scope_id", table_name="document")
    op.drop_table("document")
