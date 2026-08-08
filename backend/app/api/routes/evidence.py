"""Retrieval, exposed.

Two endpoints and they are two halves of one contract. ``/evidence/search`` is what a
generator calls before it proposes anything; ``/evidence/resolve`` is what a review panel
calls to turn a stored ``evidence_refs`` entry back into readable text months later. Ship
the first without the second and the ledger accumulates citations nobody can open.

The search response is deliberately not just a list. It carries what was searched, how big
each corpus was, whether any was truncated, and — when nothing came back — why. "No
evidence found" over forty chunks and over four thousand are different statements, and a
generator deciding whether to abstain needs to be able to tell them apart.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import evidence as evidence_service
from app.services.evidence import SOURCES

router = APIRouter(prefix="/evidence", tags=["evidence"])


class EvidenceRead(BaseModel):
    kind: str
    ref: str
    excerpt: str
    score: float
    source_label: str
    scope_id: int
    locator: dict[str, Any] | None = None
    section: str | None = None
    #: The query terms that caused this hit. A citation nobody can interrogate is a
    #: citation nobody should accept.
    matched: list[str] = []
    idf_share: float = 0.0
    from_other_scope: bool = False


class EvidenceSetRead(BaseModel):
    results: list[EvidenceRead]
    #: The generator's cue to say nothing rather than to say something weakly.
    abstained: bool
    reason: str | None
    searched: list[str]
    corpus_sizes: dict[str, int]
    truncated: list[str]


def _read(item) -> EvidenceRead:
    return EvidenceRead(
        kind=item.kind,
        ref=item.ref,
        excerpt=item.excerpt,
        score=item.score,
        source_label=item.source_label,
        scope_id=item.scope_id,
        locator=item.locator,
        section=item.section,
        matched=list(item.matched),
        idf_share=item.idf_share,
        from_other_scope=item.from_other_scope,
    )


@router.get("/sources", response_model=dict)
async def sources() -> dict:
    """What can be searched, and what deliberately cannot.

    ``cost_model`` is named in its absence: there is no CBS in this platform yet, and a
    list that simply omitted it would read as though the substrate had been forgotten.
    """
    return {
        "available": list(SOURCES),
        "not_built": {
            "cost_model": "No CBS table exists yet. Percentage-basis estimates fall back "
            "to the run's base cost.",
        },
        "not_a_document_source": {
            "schedule_file": "Parsed into activities and relationships and searched "
            "relationally, not extracted as prose.",
        },
    }


@router.get("/search", response_model=EvidenceSetRead)
async def search(
    q: str = Query(..., min_length=1, description="Free text. Tokenised, not parsed."),
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None, description="Rolls up. Omitted searches all."),
    source: list[str] | None = Query(default=None, description="Repeat to select several."),
    limit: int = Query(default=10, ge=1, le=50),
    history_across_scopes: bool = Query(
        default=True,
        description=(
            "A reference class limited to this project is empty exactly when it matters "
            "most. Results from elsewhere are flagged, never silently merged."
        ),
    ),
) -> EvidenceSetRead:
    found = await evidence_service.search(
        db,
        query=q,
        scope_id=scope_id,
        sources=source,
        limit=limit,
        history_across_scopes=history_across_scopes,
    )
    return EvidenceSetRead(
        results=[_read(item) for item in found.results],
        abstained=found.abstained,
        reason=found.reason,
        searched=found.searched,
        corpus_sizes=found.corpus_sizes,
        truncated=found.truncated,
    )


@router.get("/resolve", response_model=EvidenceRead)
async def resolve(
    ref: str = Query(..., description="As stored in a proposal's evidence_refs."),
    db: AsyncSession = Depends(get_db),
) -> EvidenceRead:
    """Full text, not an excerpt: this is the call a reviewer makes to read the source."""
    return _read(await evidence_service.resolve(db, ref))
