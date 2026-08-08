"""The proposal inbox.

Four verbs and no more: list, read, raise, dispose. There is deliberately no ``DELETE`` and
no ``PUT`` — a proposal is a record of what was suggested and what a human decided about
it, and both halves are append-only for the same reason simulation runs are (invariant 6).
Getting rid of an unwanted suggestion is a rejection with a reason, which is a decision the
ledger keeps, rather than a deletion, which is one it forgets.

``POST /proposals`` exists chiefly so the ledger is exercisable and reviewable before any
generator ships. When one does, it calls ``services/proposal_ledger.propose`` directly and
never crosses HTTP; the contract is the same either way, which is the point of writing this
route against the service rather than against the model.
"""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProposalTargetInvalid
from app.db.session import get_db
from app.models.proposal import (
    ACTIONS,
    PENDING,
    STATUSES,
    EvidenceRef,
    Proposal,
    ProposalRead,
)
from app.services import proposal_ledger
from app.services.scope import resolve_read_scope, resolve_write_scope

router = APIRouter(prefix="/proposals", tags=["proposals"])


class ProposalCreate(BaseModel):
    target_type: str = Field(..., max_length=40, examples=["risk"])
    target_id: int | None = None
    field_path: str = Field(..., max_length=120, examples=["consequences"])
    proposed_value: Any
    observed_value: Any = None
    rationale: str = Field(..., min_length=1)
    #: ``min_length=1`` mirrors the CHECK constraint rather than replacing it. The
    #: constraint is what holds for a generator writing through the service; this is what
    #: gives an HTTP caller a 422 with a field name instead of a 500 with a SQL error.
    evidence_refs: list[EvidenceRef] = Field(..., min_length=1)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    generator_model: str = Field(..., max_length=120)
    generator_prompt_version: str = Field(..., max_length=40)


class Disposition(BaseModel):
    action: str = Field(..., examples=list(ACTIONS))
    applied_value: Any = None
    note: str | None = None
    merge_into: int | None = None
    #: Set only after a reviewer has been shown what changed under the suggestion.
    confirm_stale: bool = False


class ParkWrite(BaseModel):
    parked: bool


@router.get("", response_model=list[ProposalRead])
async def list_proposals(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None, description="Rolls up. Omitted reads all."),
    status: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: int | None = Query(default=None),
    parked: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Proposal]:
    """Newest first, because an inbox is worked from the top."""
    if status is not None and status not in STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown status {status!r}. One of: {', '.join(STATUSES)}.",
        )

    stmt = select(Proposal).order_by(Proposal.created_at.desc(), Proposal.id.desc())
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        stmt = stmt.where(Proposal.scope_id.in_(scope_ids))
    if status is not None:
        stmt = stmt.where(Proposal.status == status)
    if target_type is not None:
        stmt = stmt.where(Proposal.target_type == target_type)
    if target_id is not None:
        stmt = stmt.where(Proposal.target_id == target_id)
    if parked is not None:
        stmt = stmt.where(Proposal.parked.is_(parked))

    rows = await db.scalars(stmt.limit(limit).offset(offset))
    return list(rows)


@router.get("/{proposal_id}", response_model=ProposalRead)
async def get_proposal(
    proposal_id: int, db: AsyncSession = Depends(get_db)
) -> Proposal:
    row = await db.get(Proposal, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return row


@router.post("", response_model=ProposalRead, status_code=201)
async def create_proposal(
    payload: ProposalCreate,
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(
        default=None, description="Project this is about. Omitted means the default."
    ),
) -> Proposal:
    scope = await resolve_write_scope(db, scope_id)
    row = await proposal_ledger.propose(
        db,
        scope_id=scope.id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        field_path=payload.field_path,
        proposed_value=payload.proposed_value,
        observed_value=payload.observed_value,
        rationale=payload.rationale,
        evidence_refs=[ref.model_dump() for ref in payload.evidence_refs],
        confidence=payload.confidence,
        generator_model=payload.generator_model,
        generator_prompt_version=payload.generator_prompt_version,
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/{proposal_id}/disposition", response_model=ProposalRead)
async def dispose_proposal(
    proposal_id: int,
    payload: Disposition,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> Proposal:
    """Accept, edit, reject or merge. One transaction with the write it causes.

    The commit is here and not in the service so that applying the value, writing the
    audit row and recording the disposition land together or not at all.
    """
    row = await db.get(Proposal, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    if payload.action == "edit" and payload.applied_value is None:
        raise ProposalTargetInvalid(
            "An edit needs the value the reviewer is applying. Use accept to take the "
            "suggestion as proposed."
        )

    await proposal_ledger.dispose(
        db,
        row,
        action=payload.action,
        actor=actor,
        applied_value=payload.applied_value,
        note=payload.note,
        merge_into=payload.merge_into,
        confirm_stale=payload.confirm_stale,
    )
    await db.commit()
    await db.refresh(row)
    return row


@router.post("/{proposal_id}/park", response_model=ProposalRead)
async def park_proposal(
    proposal_id: int,
    payload: ParkWrite,
    db: AsyncSession = Depends(get_db),
) -> Proposal:
    """Move a pending proposal out of the way without ruling on it.

    Still ``pending`` afterwards: parking is about this week's attention, not about the
    suggestion's fate, and a sixth status would put a non-terminal value into a vocabulary
    whose worth is that four of its five members are final.
    """
    row = await db.get(Proposal, proposal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    await proposal_ledger.set_parked(db, row, parked=payload.parked)
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/inbox/count", response_model=dict)
async def inbox_count(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None),
) -> dict:
    """How much is waiting, split by whether it has been parked.

    A single number would hide the difference between an untouched inbox and one a
    reviewer has already triaged, which is the difference that decides whether the badge
    means anything.
    """
    stmt = select(Proposal.parked, func.count()).where(Proposal.status == PENDING)
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        stmt = stmt.where(Proposal.scope_id.in_(scope_ids))
    rows = (await db.execute(stmt.group_by(Proposal.parked))).all()
    counts = {bool(parked): int(n) for parked, n in rows}
    return {
        "pending": counts.get(False, 0),
        "parked": counts.get(True, 0),
    }
