from datetime import date, datetime
from enum import Enum
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import delete, select
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
from app.services.risk_code import next_code
from app.services.scope import resolve_read_scope, resolve_write_scope

router = APIRouter(prefix="/risks", tags=["risks"])


class RiskStatus(str, Enum):
    open = "Open"
    analyzing = "Analyzing"
    mitigating = "Mitigating"
    closed = "Closed"


class NestedActionCreate(BaseModel):
    """A mitigation action supplied as part of the risk that owns it.

    Deliberately its own shape rather than an import of ``mitigations.MitigationCreate``,
    because the two are allowed to disagree on exactly one point and do: the standalone
    endpoint accepts a blank ``action`` because the actions panel creates an empty card and
    fills it in afterwards, whereas nothing here is ever going to come back and fill in a
    blank, so a blank is refused rather than silently written or silently dropped.
    """

    #: Stripped then required non-empty: a card holding two spaces is a blank card, and
    #: refusing it at the boundary is the only place the distinction is cheap to make.
    action: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    owner: str | None = None
    due_date: date | None = None
    budget: float | None = Field(default=None, ge=0)
    #: Programme the action itself consumes, not the delay it removes.
    sched_days: float | None = Field(default=None, ge=0)
    completion_pct: int | None = Field(default=None, ge=0, le=100)
    effectiveness: str | None = None
    status: str = "Proposed"
    plan_id: int | None = None


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
    #: Actions raised in the same breath as the risk. One transaction: a risk that saved
    #: while its treatment did not is worse than neither saving, because the register then
    #: shows an untreated risk that somebody believes they have treated.
    actions: list[NestedActionCreate] = Field(default_factory=list)


class RiskUpdate(BaseModel):
    #: Recategorisation, which only became possible once the identifier stopped encoding
    #: the taxonomy (0019). The code does not change: it is the register's reference to
    #: this row and it appears in issued reports, so a correction to the filing is not
    #: allowed to renumber it.
    subcategory_prefix: str | None = None
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
    #: Position in the owning project's register. Sent so a client can sort a rollup by
    #: register order without re-deriving it from the code.
    seq: int
    scope_id: int
    #: ``ENV-030``. The code no longer carries the taxonomy, so it is sent explicitly —
    #: without this the register would have no way to show or edit a risk's category.
    subcategory_prefix: str
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

    # ``<program>-<project>-<sequence>``, sequenced within the project. See
    # ``services/risk_code.py`` for why the taxonomy is no longer part of it.
    seq, risk_code = await next_code(db, scope)

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

    changes = creation_changes(snapshot(risk))
    changes.append(
        {
            "field": "subcategory",
            "old": None,
            "new": f"{category.code}-{subcategory.code}",
        }
    )
    db.add(
        RiskHistory(
            risk_id=risk.id,
            risk_code=risk.risk_code,
            action="created",
            actor=actor,
            changes=changes,
        )
    )

    # Actions raised with the risk. One history entry each, exactly as the standalone
    # endpoint writes them, so a treatment added at creation and one added an hour later
    # are indistinguishable in the trail — which they should be, because they are.
    for order, item in enumerate(payload.actions):
        db.add(
            MitigationAction(
                risk_id=risk.id,
                action=item.action,
                owner=item.owner,
                due_date=item.due_date,
                budget=item.budget,
                sched_days=item.sched_days,
                completion_pct=item.completion_pct,
                effectiveness=item.effectiveness,
                status=item.status,
                plan_id=item.plan_id,
                sort_order=order,
            )
        )
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="mitigation added",
                actor=actor,
                changes=[{"field": "action", "old": None, "new": item.action}],
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
    scope_id: int | None = Query(default=None, description="Restrict to this scope and everything under it. Omitted means unfiltered."),
) -> list[Risk]:
    """The register for one scope, or for a whole program or portfolio.

    A portfolio reads as itself plus every project beneath it, which is what makes the
    scope tree a rollup rather than a filing cabinet. Omitting the scope reads everything,
    which is what every caller did before the tree existed.

    Ordered by code, which now sorts a rollup into project blocks for free: every code in
    one project shares a prefix, and the zero-padded sequence orders the block within it.
    """
    stmt = select(Risk).order_by(Risk.risk_code)
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        stmt = stmt.where(Risk.scope_id.in_(scope_ids))
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

    # Taken before the field loop and diffed by hand. ``subcategory_prefix`` is a property
    # over a relationship, so reading it after ``subcategory_id`` changes but before the
    # flush would return the value it had a moment ago and the change would audit as no
    # change at all.
    subcategory_change: dict | None = None
    prefix = data.pop("subcategory_prefix", None)
    if prefix is not None:
        old_prefix = risk.subcategory_prefix
        category, subcategory = await _resolve_subcategory(db, prefix)
        new_prefix = f"{category.code}-{subcategory.code}"
        if new_prefix != old_prefix:
            risk.subcategory_id = subcategory.id
            subcategory_change = {
                "field": "subcategory",
                "old": old_prefix,
                "new": new_prefix,
            }

    if data.get("status") is not None:
        data["status"] = data["status"].value
    for field, value in data.items():
        setattr(risk, field, value)

    config = await get_active_config(db)
    _rescore(risk, config)

    changes = diff_snapshots(before, snapshot(risk))
    if subcategory_change is not None:
        changes.append(subcategory_change)
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
