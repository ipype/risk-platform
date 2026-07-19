from datetime import date, datetime
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk, compute_risk_level

router = APIRouter(prefix="/risks", tags=["risks"])


class RiskStatus(str, Enum):
    open = "Open"
    analyzing = "Analyzing"
    mitigating = "Mitigating"
    closed = "Closed"


class RiskCreate(BaseModel):
    subcategory_prefix: str = Field(
        ..., examples=["ENV-030"], description="Category-subcategory code, e.g. ENV-030"
    )
    title: str
    description: str | None = None
    causes: str | None = None
    consequences: str | None = None
    status: RiskStatus = RiskStatus.open
    probability: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    mitigation_actions: str | None = None
    owner: str | None = None
    last_review_date: date | None = None
    comments: str | None = None


class RiskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    causes: str | None = None
    consequences: str | None = None
    status: RiskStatus | None = None
    probability: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    mitigation_actions: str | None = None
    owner: str | None = None
    last_review_date: date | None = None
    comments: str | None = None


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
    risk_level: str | None
    mitigation_actions: str | None
    owner: str | None
    last_review_date: date | None
    comments: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


async def _resolve_subcategory(
    db: AsyncSession, prefix: str
) -> tuple[RbsCategory, RbsSubcategory]:
    parts = prefix.strip().upper().split("-")
    if len(parts) != 2:
        raise HTTPException(
            status_code=422, detail="subcategory_prefix must look like 'ENV-030'"
        )
    cat_code, sub_code = parts
    result = await db.execute(
        select(RbsCategory, RbsSubcategory)
        .join(RbsSubcategory, RbsSubcategory.category_id == RbsCategory.id)
        .where(RbsCategory.code == cat_code, RbsSubcategory.code == sub_code)
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No subcategory '{prefix}' in the RBS"
        )
    return row[0], row[1]


@router.post("", response_model=RiskRead, status_code=201)
async def create_risk(payload: RiskCreate, db: AsyncSession = Depends(get_db)) -> Risk:
    category, subcategory = await _resolve_subcategory(db, payload.subcategory_prefix)

    max_seq = await db.execute(
        select(func.coalesce(func.max(Risk.seq), 0)).where(
            Risk.subcategory_id == subcategory.id
        )
    )
    seq = max_seq.scalar_one() + 1
    risk_code = f"{category.code}-{subcategory.code}-{seq:04d}"

    risk = Risk(
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
        risk_level=compute_risk_level(payload.probability, payload.impact),
        mitigation_actions=payload.mitigation_actions,
        owner=payload.owner,
        last_review_date=payload.last_review_date,
        comments=payload.comments,
    )
    db.add(risk)
    await db.commit()
    await db.refresh(risk)
    return risk


@router.get("", response_model=list[RiskRead])
async def list_risks(
    db: AsyncSession = Depends(get_db),
    category: str | None = Query(default=None, description="Filter by category code, e.g. ENV"),
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


@router.patch("/{risk_id}", response_model=RiskRead)
async def update_risk(
    risk_id: int, payload: RiskUpdate, db: AsyncSession = Depends(get_db)
) -> Risk:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail="Risk not found")

    data = payload.model_dump(exclude_unset=True)
    if data.get("status") is not None:
        data["status"] = data["status"].value
    for field, value in data.items():
        setattr(risk, field, value)

    risk.risk_level = compute_risk_level(risk.probability, risk.impact)

    await db.commit()
    await db.refresh(risk)
    return risk


@router.delete("/{risk_id}", status_code=204)
async def delete_risk(risk_id: int, db: AsyncSession = Depends(get_db)) -> None:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail="Risk not found")
    await db.delete(risk)
    await db.commit()