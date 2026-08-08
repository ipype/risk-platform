"""The corpus: upload, paste, browse, withdraw.

There is no ``DELETE``. A chunk id can appear in a proposal's ``evidence_refs``, and those
are JSON with no foreign key behind them, so deleting a document would leave the ledger
holding citations that resolve to nothing. Withdrawal takes a document out of retrieval and
leaves every row where it is, which keeps a citation made months ago openable. Same posture
as runs and proposals, for the same reason.
"""

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.ingest import SUPPORTED
from app.models.document import (
    ACTIVE,
    DOCUMENT_STATUSES,
    WITHDRAWN,
    Document,
    DocumentChunk,
    DocumentChunkRead,
    DocumentRead,
)
from app.services import document_ingest
from app.services.scope import resolve_read_scope, resolve_write_scope

router = APIRouter(prefix="/documents", tags=["documents"])

#: Refused before the bytes are read into memory where the client declares a length, and
#: after where it does not. Twenty-five megabytes is a large specification and a small
#: drawing set; the limit exists so one upload cannot take the API process down, not
#: because a bigger document is illegitimate.
MAX_BYTES = 25 * 1024 * 1024


class PasteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=400)
    text: str = Field(..., min_length=1)
    #: Recorded as typed. The platform did not fetch it and does not claim to have.
    source_url: str | None = Field(default=None, max_length=400)


class WithdrawWrite(BaseModel):
    reason: str | None = None


class DocumentCreated(BaseModel):
    document: DocumentRead
    created: bool


@router.get("/formats", response_model=dict)
async def formats() -> dict:
    """What an upload control should advertise.

    Schedules are absent on purpose: ``.xer`` is parsed into activities and relationships
    by the schedule pipeline, and the evidence service reads those relationally rather than
    as prose.
    """
    return {
        "suffixes": list(SUPPORTED),
        "max_bytes": MAX_BYTES,
        "note": "Anything else can be pasted as text.",
    }


@router.get("", response_model=list[DocumentRead])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None, description="Rolls up. Omitted reads all."),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Document]:
    if status is not None and status not in DOCUMENT_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status {status!r}. One of: {', '.join(DOCUMENT_STATUSES)}.",
        )
    stmt = select(Document).order_by(Document.created_at.desc(), Document.id.desc())
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        stmt = stmt.where(Document.scope_id.in_(scope_ids))
    if status is not None:
        stmt = stmt.where(Document.status == status)
    rows = await db.scalars(stmt.limit(limit).offset(offset))
    return list(rows)


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(document_id: int, db: AsyncSession = Depends(get_db)) -> Document:
    row = await db.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return row


@router.get("/{document_id}/chunks", response_model=list[DocumentChunkRead])
async def list_chunks(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    kind: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[DocumentChunk]:
    """In document order, which is the order a reviewer reading around a citation wants."""
    if await db.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.ordinal)
    )
    if kind is not None:
        stmt = stmt.where(DocumentChunk.kind == kind)
    rows = await db.scalars(stmt.limit(limit).offset(offset))
    return list(rows)


@router.post("", response_model=DocumentCreated)
async def upload_document(
    response: Response,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
    scope_id: int | None = Query(default=None),
) -> DocumentCreated:
    """Extract first, store second — so a file that yields nothing is never stored.

    Returns 201 for a new document and 200 when identical bytes were already in this
    scope, so a client can tell an ingest from a no-op without comparing ids.
    """
    scope = await resolve_write_scope(db, scope_id)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    if len(data) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{len(data)} bytes exceeds the {MAX_BYTES}-byte limit.",
        )

    result = await document_ingest.ingest_bytes(
        db,
        scope_id=scope.id,
        filename=file.filename or "upload",
        data=data,
        uploaded_by=actor,
    )
    await db.commit()
    await db.refresh(result.document)
    response.status_code = 201 if result.created else 200
    return DocumentCreated(
        document=DocumentRead.model_validate(result.document), created=result.created
    )


@router.post("/paste", response_model=DocumentCreated)
async def paste_document(
    payload: PasteCreate,
    response: Response,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
    scope_id: int | None = Query(default=None),
) -> DocumentCreated:
    """Text pasted from anywhere — a web page, an email, a PDF that would not open."""
    scope = await resolve_write_scope(db, scope_id)
    result = await document_ingest.ingest_text(
        db,
        scope_id=scope.id,
        title=payload.title,
        text=payload.text,
        uploaded_by=actor,
        source_url=payload.source_url,
    )
    await db.commit()
    await db.refresh(result.document)
    response.status_code = 201 if result.created else 200
    return DocumentCreated(
        document=DocumentRead.model_validate(result.document), created=result.created
    )


@router.post("/{document_id}/withdraw", response_model=DocumentRead)
async def withdraw_document(
    document_id: int,
    payload: WithdrawWrite,
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Out of retrieval, still citable. There is no delete; see the module docstring."""
    row = await db.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    row.status = WITHDRAWN
    row.withdrawn_reason = payload.reason
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/{document_id}/restore", response_model=DocumentRead)
async def restore_document(
    document_id: int, db: AsyncSession = Depends(get_db)
) -> Document:
    row = await db.get(Document, document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found")
    row.status = ACTIVE
    row.withdrawn_reason = None
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/corpus/summary", response_model=dict)
async def corpus_summary(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None),
) -> dict:
    """What retrieval will actually see, and what it will not.

    Split by status rather than totalled, because "forty documents" that is really
    thirty-one active and nine withdrawn is a number that misleads whoever reads it next.
    """
    stmt = select(
        Document.status, func.count(Document.id), func.coalesce(func.sum(Document.chunk_count), 0)
    )
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        stmt = stmt.where(Document.scope_id.in_(scope_ids))
    rows = (await db.execute(stmt.group_by(Document.status))).all()
    by_status = {status: {"documents": int(n), "chunks": int(c)} for status, n, c in rows}
    return {
        "active": by_status.get(ACTIVE, {"documents": 0, "chunks": 0}),
        "withdrawn": by_status.get(WITHDRAWN, {"documents": 0, "chunks": 0}),
    }
