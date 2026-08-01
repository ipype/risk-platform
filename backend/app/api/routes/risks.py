from datetime import date, datetime
from enum import Enum

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.history import (
    RiskHistory,
    RiskHistoryRead,
    creation_changes,
    diff_snapshots,
    snapshot,
)
from app.models.matrix import band_for, get_active_config, overall_impact
from app.models.mitigation import MitigationAction
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.services.scope import resolve_write_scope

router = APIRouter(prefix="/risks", tags=["risks"])


class RiskStatus(str, Enum):
    open = "Open"
    analyzing = "Analyzing"
    mitigating = "Mitigating"
    closed = "Closed"


class RiskCreate(BaseModel):
    subcategory_prefix: str = Field(..., examples=["ENV-030"])
    title: str
    description: str | None = None
    causes: str | None = None
    consequences: str | None = None
    status: RiskStatus = RiskStatus.open
    probability: int | None = Field(default=None, ge=1, le=9)
    impact: int | None = Field(default=None, ge=1, le=9)
    impact_scores: dict[str, int] | None = None
    target_probability: int | None = Field(default=None, ge=1, le=9)
    target_impact: int | None = Field(default=None, ge=1, le=9)
    target_impact_scores: dict[str, int] | None = None
    mitigation_actions: str | None = None
    owner: str | None = None
    last_review_date: date | None = None
    comments: str | None = None
    custom_fields: dict | None = None


class RiskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    causes: str | None = None
    consequences: str | None = None
    status: RiskStatus | None = None
    probability: int | None = Field(default=None, ge=1, le=9)
    impact: int | None = Field(default=None, ge=1, le=9)
    impact_scores: dict[str, int] | None = None
    target_probability: int | None = Field(default=None, ge=1, le=9)
    target_impact: int | None = Field(default=None, ge=1, le=9)
    target_impact_scores: dict[str, int] | None = None
    mitigation_actions: str | None = None
    owner: str | None = None
    last_review_date: date | None = None
    comments: str | None = None
    custom_fields: dict | None = None


class RiskRead(BaseModel):
    id: int
    risk_code: str
    title: str
    description: str | None
    causes: str | None
    consequences: str | None
    status: str
    probability: int | None
    impact: int | None
    impact_scores: dict[str, int] | None
    risk_level: str | None
    target_probability: int | None
    target_impact: int | None
    target_impact_scores: dict[str, int] | None
    target_risk_level: str | None
    mitigation_actions: str | None
    owner: str | None
    last_review_date: date | None
    comments: str | None
    custom_fields: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


async def _resolve_subcategory(
    db: AsyncSession, prefix: str
) -> tuple[RbsCategory, RbsSubcategory]:
    parts = prefix.strip().upper().split("-")
    if len(parts) != 2:
        raise HTTPException(status_code=422, detail="prefix must look like 'ENV-030'")
    cat_code, sub_code = parts
    result = await db.execute(
        select(RbsCategory, RbsSubcategory)
        .join(RbsSubcategory, RbsSubcategory.category_id == RbsCategory.id)
        .where(RbsCategory.code == cat_code, RbsSubcategory.code == sub_code)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"No subcategory '{prefix}'")
    return row[0], row[1]


def _rescore(risk: Risk, config: dict) -> None:
    oi = overall_impact(risk.impact_scores, risk.impact)
    risk.impact = oi
    risk.risk_level = band_for(risk.probability, oi, config)
    toi = overall_impact(risk.target_impact_scores, risk.target_impact)
    risk.target_impact = toi
    risk.target_risk_level = band_for(risk.target_probability, toi, config)


@router.post("", response_model=RiskRead, status_code=201)
async def create_risk(
    payload: RiskCreate,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
    scope_id: int | None = Query(
        default=None,
        description="Project this risk belongs to. Omitted means the default project.",
    ),
) -> Risk:
    scope = await resolve_write_scope(db, scope_id)
    category, subcategory = await _resolve_subcategory(db, payload.subcategory_prefix)
    config = await get_active_config(db)

    # Sequenced within the scope, so every project's register starts at 0001. A global
    # sequence would hand the second project ENV-030-0007 as its first environmental risk
    # because another project got there first, and that is not a register anyone signs.
    max_seq = await db.execute(
        select(func.coalesce(func.max(Risk.seq), 0)).where(
            Risk.scope_id == scope.id, Risk.subcategory_id == subcategory.id
        )
    )
    seq = max_seq.scalar_one() + 1
    risk_code = f"{category.code}-{subcategory.code}-{seq:04d}"

    risk = Risk(
        scope_id=scope.id,
        subcategory_id=subcategory.id,
        seq=seq,
        risk_code=risk_code,
        title=payload.title,
        description=payload.description,
        causes=payload.causes,
        consequences=payload.consequences,
        status=payload.status.value,
        probability=payload.probability,
        impact=payload.impact,
        impact_scores=payload.impact_scores,
        target_probability=payload.target_probability,
        target_impact=payload.target_impact,
        target_impact_scores=payload.target_impact_scores,
        mitigation_actions=payload.mitigation_actions,
        owner=payload.owner,
        last_review_date=payload.last_review_date,
        comments=payload.comments,
        custom_fields=payload.custom_fields,
    )
    _rescore(risk, config)
    db.add(risk)
    await db.flush()

    db.add(
        RiskHistory(
            risk_id=risk.id,
            risk_code=risk.risk_code,
            action="created",
            actor=actor,
            changes=creation_changes(snapshot(risk)),
        )
    )
    await db.commit()
    await db.refresh(risk)
    return risk


@router.get("", response_model=list[RiskRead])
async def list_risks(
    db: AsyncSession = Depends(get_db),
    category: str | None = Query(default=None),
    status: RiskStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Risk]:
    stmt = select(Risk).order_by(Risk.risk_code)
    if category:
        stmt = (
            stmt.join(RbsSubcategory, RbsSubcategory.id == Risk.subcategory_id)
            .join(RbsCategory, RbsCategory.id == RbsSubcategory.category_id)
            .where(RbsCategory.code == category.strip().upper())
        )
    if status:
        stmt = stmt.where(Risk.status == status.value)
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{risk_id}", response_model=RiskRead)
async def get_risk(risk_id: int, db: AsyncSession = Depends(get_db)) -> Risk:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail="Risk not found")
    return risk


@router.get("/{risk_id}/history", response_model=list[RiskHistoryRead])
async def risk_history(
    risk_id: int, db: AsyncSession = Depends(get_db)
) -> list[RiskHistory]:
    res = await db.execute(
        select(RiskHistory)
        .where(RiskHistory.risk_id == risk_id)
        .order_by(RiskHistory.created_at.desc(), RiskHistory.id.desc())
    )
    return list(res.scalars().all())


@router.patch("/{risk_id}", response_model=RiskRead)
async def update_risk(
    risk_id: int,
    payload: RiskUpdate,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> Risk:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail="Risk not found")

    before = snapshot(risk)

    data = payload.model_dump(exclude_unset=True)
    if data.get("status") is not None:
        data["status"] = data["status"].value
    for field, value in data.items():
        setattr(risk, field, value)

    config = await get_active_config(db)
    _rescore(risk, config)

    changes = diff_snapshots(before, snapshot(risk))
    if changes:
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="updated",
                actor=actor,
                changes=changes,
            )
        )

    await db.commit()
    await db.refresh(risk)
    return risk


@router.delete("/{risk_id}", status_code=204)
async def delete_risk(
    risk_id: int,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> None:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail="Risk not found")

    db.add(
        RiskHistory(
            risk_id=risk.id,
            risk_code=risk.risk_code,
            action="deleted",
            actor=actor,
            changes=[],
        )
    )
    await db.execute(delete(MitigationAction).where(MitigationAction.risk_id == risk.id))
    await db.delete(risk)
    await db.commit()
