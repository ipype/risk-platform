from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.history import RiskHistory
from app.models.mitigation import MitigationAction, action_diff, action_snapshot
from app.models.risk import Risk

router = APIRouter(prefix="/risks", tags=["mitigations"])


class MitigationCreate(BaseModel):
    action: str = ""
    owner: str | None = None
    due_date: date | None = None
    budget: float | None = Field(default=None, ge=0)
    #: Programme the action itself consumes, not the delay it removes. The two are
    #: different numbers and a package priced only in money hides the second one.
    sched_days: float | None = Field(default=None, ge=0)
    completion_pct: int | None = Field(default=None, ge=0, le=100)
    effectiveness: str | None = None
    status: str = "Proposed"
    plan_id: int | None = None


class MitigationUpdate(BaseModel):
    action: str | None = None
    owner: str | None = None
    due_date: date | None = None
    budget: float | None = Field(default=None, ge=0)
    sched_days: float | None = Field(default=None, ge=0)
    completion_pct: int | None = Field(default=None, ge=0, le=100)
    effectiveness: str | None = None
    status: str | None = None
    plan_id: int | None = None


class MitigationRead(BaseModel):
    id: int
    risk_id: int
    plan_id: int | None
    action: str
    owner: str | None
    due_date: date | None
    budget: float | None
    sched_days: float | None
    completion_pct: int | None
    effectiveness: str | None
    status: str
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


async def _get_risk(db: AsyncSession, risk_id: int) -> Risk:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail="Risk not found")
    return risk


async def _get_action(
    db: AsyncSession, risk_id: int, action_id: int
) -> MitigationAction:
    action = await db.get(MitigationAction, action_id)
    if action is None or action.risk_id != risk_id:
        raise HTTPException(status_code=404, detail="Action not found")
    return action


@router.get("/{risk_id}/actions", response_model=list[MitigationRead])
async def list_actions(
    risk_id: int, db: AsyncSession = Depends(get_db)
) -> list[MitigationAction]:
    res = await db.execute(
        select(MitigationAction)
        .where(MitigationAction.risk_id == risk_id)
        .order_by(MitigationAction.sort_order, MitigationAction.id)
    )
    return list(res.scalars().all())


@router.post("/{risk_id}/actions", response_model=MitigationRead, status_code=201)
async def create_action(
    risk_id: int,
    payload: MitigationCreate,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> MitigationAction:
    risk = await _get_risk(db, risk_id)

    max_order = await db.execute(
        select(func.coalesce(func.max(MitigationAction.sort_order), -1)).where(
            MitigationAction.risk_id == risk_id
        )
    )
    action = MitigationAction(
        risk_id=risk_id,
        action=payload.action,
        owner=payload.owner,
        due_date=payload.due_date,
        budget=payload.budget,
        sched_days=payload.sched_days,
        completion_pct=payload.completion_pct,
        effectiveness=payload.effectiveness,
        status=payload.status,
        plan_id=payload.plan_id,
        sort_order=max_order.scalar_one() + 1,
    )
    db.add(action)
    db.add(
        RiskHistory(
            risk_id=risk.id,
            risk_code=risk.risk_code,
            action="mitigation added",
            actor=actor,
            changes=[{"field": "action", "old": None, "new": payload.action or "(new action)"}],
        )
    )
    await db.commit()
    await db.refresh(action)
    return action


@router.patch(
    "/{risk_id}/actions/{action_id}", response_model=MitigationRead
)
async def update_action(
    risk_id: int,
    action_id: int,
    payload: MitigationUpdate,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> MitigationAction:
    risk = await _get_risk(db, risk_id)
    action = await _get_action(db, risk_id, action_id)

    before = action_snapshot(action)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(action, field, value)
    changes = action_diff(before, action_snapshot(action))

    if changes:
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="mitigation updated",
                actor=actor,
                changes=changes,
            )
        )
    await db.commit()
    await db.refresh(action)
    return action


@router.delete("/{risk_id}/actions/{action_id}", status_code=204, response_model=None)
async def delete_action(
    risk_id: int,
    action_id: int,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> None:
    risk = await _get_risk(db, risk_id)
    action = await _get_action(db, risk_id, action_id)

    db.add(
        RiskHistory(
            risk_id=risk.id,
            risk_code=risk.risk_code,
            action="mitigation removed",
            actor=actor,
            changes=[{"field": "action", "old": action.action, "new": None}],
        )
    )
    await db.delete(action)
    await db.commit()
