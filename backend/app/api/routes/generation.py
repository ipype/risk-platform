"""Starting a generator and reading what it did.

One start verb per generator, then list and read shared by both. There is no delete, no re-run in place and no
edit, for the reason simulation runs give — the record of what a model produced, and of how
much of it was refused before anyone saw it, is the evidence that the review process is
real, and evidence that can be tidied up is not evidence.

Cancel is missing and is the one gap worth naming out loud. A twenty-window pass is
twenty paid calls, and there is currently no way to stop one halfway. It is deliberately
not smuggled in here: cancelling means a status the CHECK constraint does not hold, a
revoke path, and a decision about a worker that is mid-call. That is its own delivery, the
way ``simulation_run`` took its cancel in migration 0018 rather than at birth.

**The provider is resolved in the request, before a run row exists.** A deployment with no
``LLM_PROVIDER`` gets one 503 naming the setting rather than a growing list of failed runs
all reporting the same missing environment variable.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.llm import get_provider
from app.models.generation import (
    KINDS,
    QUALITATIVE_EVALUATION,
    QUEUED,
    RISK_IDENTIFICATION,
    STATUSES,
    GenerationRun,
    GenerationRunDetail,
    GenerationRunSummary,
)
from app.models.proposal import Proposal, ProposalRead
from app.services import generation_dispatch
from app.services.qual_generate import load_scale, load_subjects
from app.services.risk_generate import load_pack
from app.services.scope import resolve_read_scope, resolve_write_scope
from app.agents.qual_eval import PROMPT_VERSION as QUAL_PROMPT_VERSION
from app.agents.risk_id import PROMPT_VERSION

router = APIRouter(prefix="/generation", tags=["generation"])


class RiskIdentificationRequest(BaseModel):
    #: Narrow the pass to particular documents. Empty means the whole active corpus for
    #: the scope. Offered because the first useful thing an analyst does with a generator
    #: is point it at the one document they just uploaded, not at everything again.
    document_ids: list[int] = Field(default_factory=list)


@router.post("/risk-identification", response_model=GenerationRunDetail, status_code=201)
async def start_risk_identification(
    payload: RiskIdentificationRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
    scope_id: int | None = Query(
        default=None,
        description="Project whose corpus to read. Omitted means the default project.",
    ),
) -> GenerationRun:
    """Queue a pass over the project's documents.

    Returns as soon as the run is queued. Everything that happens afterwards lands on the
    run row and in the proposal inbox — nothing is returned here that the polling endpoint
    below does not also serve, so a client that loses the response has lost nothing.
    """
    scope = await resolve_write_scope(db, scope_id)
    config = get_settings()

    # Raises ``LlmNotConfigured`` (503) before anything is written. Deliberately not
    # caught: a run recording "no provider configured" would be a row about the
    # deployment rather than about a generation.
    provider = get_provider(config)

    chunks, _, document_ids = await load_pack(
        db, scope.id, only_documents=payload.document_ids or None
    )
    if not chunks:
        # Refused here rather than by the worker, because the answer never changes on a
        # retry and a queued run that will certainly fail is worse than a 422 that says
        # why now.
        raise HTTPException(
            status_code=422,
            detail=(
                "There is nothing in this project's corpus to read"
                + (
                    " in the documents you named."
                    if payload.document_ids
                    else ". Upload or paste a document first."
                )
            ),
        )

    run = GenerationRun(
        scope_id=scope.id,
        kind=RISK_IDENTIFICATION,
        status=QUEUED,
        prompt_version=PROMPT_VERSION,
        provider=provider.name,
        model=config.llm_model or "",
        temperature=config.llm_temperature,
        document_ids=sorted(document_ids),
        chunk_count=len(chunks),
        requested_by=actor,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await generation_dispatch.dispatch(db, run)
    await db.refresh(run)
    return run


class QualitativeEvaluationRequest(BaseModel):
    #: Narrow the pass to particular risks. Empty means every risk in the project that
    #: still has something left to score. Offered for the same reason identification
    #: takes document ids: the first useful thing an analyst does with a generator is
    #: point it at the handful of rows they just wrote.
    risk_ids: list[int] = Field(default_factory=list)


@router.post(
    "/qualitative-evaluation", response_model=GenerationRunDetail, status_code=201
)
async def start_qualitative_evaluation(
    payload: QualitativeEvaluationRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
    scope_id: int | None = Query(
        default=None,
        description="Project whose register to score. Omitted means the default project.",
    ),
) -> GenerationRun:
    """Queue a pass that scores the project's unassessed risks.

    The subject list is resolved here rather than in the worker and written onto the run,
    so the row says exactly which risks it was about. A pass whose subjects were computed
    at execution time would silently cover a different set from the one the analyst was
    looking at when they started it — the register moves.

    A field a person has already scored is never re-scored, and a fully assessed risk named
    explicitly comes back as a skip on the run rather than as a silent no-op.
    """
    scope = await resolve_write_scope(db, scope_id)
    config = get_settings()

    provider = get_provider(config)

    scale = await load_scale(db)
    if not scale.areas or not scale.probability:
        raise HTTPException(
            status_code=422,
            detail=(
                "The active risk matrix has no probability levels or no impact areas, "
                "so there is no scale to score against."
            ),
        )

    subjects, skipped = await load_subjects(
        db, scope.id, scale, only_risks=payload.risk_ids or None
    )
    if not subjects:
        raise HTTPException(
            status_code=422,
            detail=(
                "Every risk you named has already been scored on every field."
                if skipped and payload.risk_ids
                else (
                    "Every risk in this project has already been scored. Clear a field "
                    "to ask for a second opinion on it."
                    if skipped
                    else "There is nothing in this project's register to evaluate."
                )
            ),
        )

    run = GenerationRun(
        scope_id=scope.id,
        kind=QUALITATIVE_EVALUATION,
        status=QUEUED,
        prompt_version=QUAL_PROMPT_VERSION,
        provider=provider.name,
        model=config.llm_model or "",
        temperature=config.llm_temperature,
        subject_ids=[s.risk_id for s in subjects],
        skipped=[s.as_dict() for s in skipped] or None,
        requested_by=actor,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await generation_dispatch.dispatch(db, run)
    await db.refresh(run)
    return run


@router.get("/runs", response_model=list[GenerationRunSummary])
async def list_runs(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None, description="Rolls up. Omitted reads all."),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[GenerationRun]:
    """Newest first. Without transcripts, which are the bulk of the row."""
    if status is not None and status not in STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status {status!r}. One of: {', '.join(STATUSES)}.",
        )
    if kind is not None and kind not in KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown kind {kind!r}. One of: {', '.join(KINDS)}.",
        )
    stmt = select(GenerationRun).order_by(
        GenerationRun.created_at.desc(), GenerationRun.id.desc()
    )
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        stmt = stmt.where(GenerationRun.scope_id.in_(scope_ids))
    if status is not None:
        stmt = stmt.where(GenerationRun.status == status)
    if kind is not None:
        stmt = stmt.where(GenerationRun.kind == kind)
    return list(await db.scalars(stmt.limit(limit).offset(offset)))


@router.get("/runs/{run_id}", response_model=GenerationRunDetail)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)) -> GenerationRun:
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Generation run not found")
    return run


@router.get("/runs/{run_id}/proposals", response_model=list[ProposalRead])
async def run_proposals(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Proposal]:
    """What this pass raised, oldest first.

    Oldest first here and newest first in the inbox, which is not an inconsistency: the
    inbox is a queue worked from the top, and this is a batch read in the order the
    generator found things, which is document order.
    """
    run = await db.get(GenerationRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Generation run not found")
    rows = await db.scalars(
        select(Proposal)
        .where(Proposal.generation_run_id == run_id)
        .order_by(Proposal.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows)
