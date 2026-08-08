"""Persisting an extraction, and the decisions the pure extractors are not allowed to make.

Everything here is a policy question rather than a parsing one: whether a re-upload is a
new document, what happens to the chunks of a document being re-extracted, and whether a
document that yielded nothing gets stored. The extractors stay pure and answer none of it.

**A re-upload of identical bytes is the same document.** Not a new row and not an error —
the existing record is returned and the caller is told nothing was created. Two copies of
one source double its weight in any retrieval that later runs over the text, which quietly
biases every suggestion drawn from it.

**Re-extraction replaces the chunk set, it does not patch it.** Chunk ordinals are a
sequence with no gaps, and a partial update would have to reconcile a new sequence against
an old one with no stable identity to match on — text moves when a paragraph above it is
edited. Replacing is also honest about what happened: the citations made against the old
ordinals no longer point where they did, which is a fact worth surfacing rather than
papering over. It is why re-extraction is a separate, explicit call and not something an
upload does silently.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest import registry
from app.ingest.plain import extract_text
from app.ingest.types import Extraction
from app.models.document import ACTIVE, PASTE, UPLOAD, Document, DocumentChunk

__all__ = ["ingest_bytes", "ingest_text", "reextract", "IngestResult"]


@dataclass(slots=True)
class IngestResult:
    document: Document
    #: ``False`` when identical bytes were already in this scope. The route turns this
    #: into 200 rather than 201, so a client can tell a no-op from an ingest.
    created: bool


async def ingest_bytes(
    db: AsyncSession,
    *,
    scope_id: int,
    filename: str,
    data: bytes,
    uploaded_by: str,
    title: str | None = None,
) -> IngestResult:
    """Extract, then store. In that order, so a file that yields nothing is never stored.

    The extractor runs before any row is written. A document with zero chunks looks
    successful in a list, retrieves nothing forever, and gives nobody a reason to suspect
    the file — so :class:`DocumentHasNoText` propagates and the transaction never starts.
    """
    digest = hashlib.sha256(data).hexdigest()
    existing = await db.scalar(
        select(Document).where(
            Document.scope_id == scope_id, Document.sha256 == digest
        )
    )
    if existing is not None:
        return IngestResult(document=existing, created=False)

    extraction = registry.extract(data, filename=filename)
    document = await _store(
        db,
        scope_id=scope_id,
        filename=filename,
        suffix=PurePosixPath(filename).suffix.lower(),
        source_kind=UPLOAD,
        digest=digest,
        byte_size=len(data),
        extraction=extraction,
        uploaded_by=uploaded_by,
        title=title,
    )
    return IngestResult(document=document, created=True)


async def ingest_text(
    db: AsyncSession,
    *,
    scope_id: int,
    title: str,
    text: str,
    uploaded_by: str,
    source_url: str | None = None,
) -> IngestResult:
    """The paste path. Stands in for web ingestion, deliberately.

    ``source_url`` is recorded in the filename rather than in a column of its own: it is
    provenance a human typed and cannot be verified, and giving it a dedicated field would
    imply the platform fetched and checked it.
    """
    data = text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    existing = await db.scalar(
        select(Document).where(
            Document.scope_id == scope_id, Document.sha256 == digest
        )
    )
    if existing is not None:
        return IngestResult(document=existing, created=False)

    extraction = extract_text(text, filename=title)
    label = f"{title} ({source_url})" if source_url else title
    document = await _store(
        db,
        scope_id=scope_id,
        filename=label[:500],
        suffix=".txt",
        source_kind=PASTE,
        digest=digest,
        byte_size=len(data),
        extraction=extraction,
        uploaded_by=uploaded_by,
        title=title,
    )
    return IngestResult(document=document, created=True)


async def reextract(
    db: AsyncSession, document: Document, *, data: bytes
) -> Document:
    """Run the extractor again over the same bytes and replace the chunk set.

    For when an extractor improves, not for when a file changes — the sha is not
    recomputed and a caller passing different bytes would leave the record claiming a
    digest it no longer has. Changed content is a new document.
    """
    extraction = registry.extract(data, filename=document.filename)
    await db.execute(
        delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
    )
    _attach(db, document, extraction)
    await db.flush()
    return document


async def _store(
    db: AsyncSession,
    *,
    scope_id: int,
    filename: str,
    suffix: str,
    source_kind: str,
    digest: str,
    byte_size: int,
    extraction: Extraction,
    uploaded_by: str,
    title: str | None,
) -> Document:
    document = Document(
        scope_id=scope_id,
        filename=filename,
        suffix=suffix,
        source_kind=source_kind,
        sha256=digest,
        byte_size=byte_size,
        page_count=extraction.page_count,
        title=title,
        status=ACTIVE,
        uploaded_by=uploaded_by,
    )
    db.add(document)
    await db.flush()
    _attach(db, document, extraction)
    await db.flush()
    return document


def _attach(db: AsyncSession, document: Document, extraction: Extraction) -> None:
    for chunk in extraction.chunks:
        db.add(
            DocumentChunk(
                document_id=document.id,
                ordinal=chunk.ordinal,
                kind=chunk.kind,
                text=chunk.text,
                locator=chunk.locator or None,
                section=chunk.section,
                char_count=len(chunk.text),
            )
        )
    document.chunk_count = len(extraction.chunks)
    document.page_count = extraction.page_count
    # Empty list rather than NULL would claim the extractor checked and found nothing to
    # report, which is true — but NULL reads the same and costs no bytes.
    document.warnings = extraction.warnings or None
