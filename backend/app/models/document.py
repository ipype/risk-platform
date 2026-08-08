"""The corpus: what was ingested, and the citable pieces it was broken into.

**Documents are withdrawn, never deleted.** A chunk id can appear in a proposal's
``evidence_refs``, and those are JSON with no foreign key behind them — deleting the
document a citation points at would leave the ledger holding references that resolve to
nothing, which is the one thing the ledger must never do. Withdrawal takes a document out
of retrieval while leaving every row where it is, so a citation made eight months ago still
opens. This is the same posture simulation runs and proposals already take, for the same
reason: the record of what a decision rested on has to outlive the decision to stop using
it.

**Chunks carry no ``scope_id``.** Every chunk's scope is its document's, and denormalising
it would create two places for one fact to be wrong. Retrieval filters by scope through a
join, which is one join against an indexed foreign key.

**There is no embedding column yet.** ``pgvector`` is installed and its extension is
created by migration 0001, but ``pgvector.sqlalchemy.Vector`` does not compile under the
SQLite the whole suite runs against, so declaring the column now would break the test
engine in exchange for storage nothing writes to. Adding it later is one nullable
``ALTER TABLE ADD COLUMN`` and a backfill job over rows that already exist — cheap, and not
on the critical path of choosing a provider. Lexical retrieval works against ``text``
meanwhile.

**Extraction warnings live on the document.** A sheet skipped for being empty, a table that
held no rows, a header row that could not be found: these are declared on the face of the
record rather than logged, because the reviewer judging a suggestion needs to know the
corpus behind it had a hole in it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

#: In retrieval.
ACTIVE = "active"

#: Out of retrieval, still resolvable by every citation ever made against it.
WITHDRAWN = "withdrawn"

DOCUMENT_STATUSES: tuple[str, ...] = (ACTIVE, WITHDRAWN)

#: How the bytes arrived. Kept because a pasted extract and an uploaded original deserve
#: different trust, and a reviewer can only apply that judgement if the record says which.
UPLOAD = "upload"
PASTE = "paste"


class Document(Base):
    """One ingested file or pasted extract."""

    __tablename__ = "document"

    id: Mapped[int] = mapped_column(primary_key=True)

    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Lowercased, with the dot. Stored rather than derived on read so a rename cannot
    #: change how an already-extracted document reports itself.
    suffix: Mapped[str] = mapped_column(String(20), nullable=False)
    source_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UPLOAD, server_default=UPLOAD
    )

    #: Of the bytes, not of the text. Unique per scope: re-uploading the same file returns
    #: the document already there rather than doubling every chunk in it, which is the
    #: difference between a corpus and a pile. Per scope and not globally, because two
    #: projects legitimately hold the same standard and each needs its own citations.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)

    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)

    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ACTIVE, server_default=ACTIVE, index=True
    )
    withdrawn_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    uploaded_by: Mapped[str] = mapped_column(String(120), nullable=False, default="Unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint("scope_id", "sha256", name="uq_document_scope_sha256"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Document {self.id} {self.filename!r} {self.status}>"


class DocumentChunk(Base):
    """One citable piece of a document."""

    __tablename__ = "document_chunk"

    id: Mapped[int] = mapped_column(primary_key=True)

    #: ``CASCADE`` in the DDL, and the service deletes explicitly anyway. SQLite ignores
    #: ``ondelete`` without ``PRAGMA foreign_keys`` (see ``REFERENCE.md``), so relying on
    #: it would mean the test engine and the production engine disagree about whether a
    #: re-ingest left orphans behind. The cascade is the safety net, not the mechanism.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("document.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Position in the document. What "next chunk" means when a reviewer wants context
    #: around a citation, and the only ordering that survives re-extraction unchanged.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    #: Enough to render one highlight in the source. Shape varies by format; see
    #: ``app/ingest/types.py`` for why it is not normalised.
    locator: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    section: Mapped[str | None] = mapped_column(String(500), nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),
        Index("ix_document_chunk_kind", "kind"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DocumentChunk {self.id} doc={self.document_id} #{self.ordinal}>"


class DocumentChunkRead(BaseModel):
    id: int
    document_id: int
    ordinal: int
    kind: str
    text: str
    locator: dict[str, Any] | None
    section: str | None
    char_count: int

    model_config = {"from_attributes": True}


class DocumentRead(BaseModel):
    id: int
    scope_id: int
    filename: str
    suffix: str
    source_kind: str
    sha256: str
    byte_size: int
    page_count: int | None
    chunk_count: int
    warnings: list[str] | None
    title: str | None
    status: str
    withdrawn_reason: str | None
    uploaded_by: str
    created_at: datetime

    model_config = {"from_attributes": True}
