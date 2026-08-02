"""Mitigation plans: the package, its cost, and the residual register it writes.

No ``from __future__ import annotations`` anywhere in this package. Under postponed
evaluation FastAPI reads a ``-> None`` return annotation as a response body and refuses to
register a 204 route at all (``claude/REFERENCE.md``, 2026-08-01).

Two things this module deliberately does not do. It never reports what a plan *buys* —
that is the difference between two simulations and belongs to re-simulation ROI, not to a
CRUD layer. And it never adds a plan's cost to a contingency figure: cost is deterministic
and additive, contingency is a percentile and is not.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.history import RiskHistory
from app.models.mitigation import (
    PLAN_STATUSES,
    TREATMENT_MODES,
    TREATMENTS,
    MitigationAction,
    MitigationPlan,
    MitigationPlanRisk,
    plan_risk_diff,
    plan_risk_snapshot,
)
from app.models.risk import Risk
from app.services import mitigation_plan as mp
from app.services.scope import resolve_read_scope, resolve_write_scope

router = APIRouter(prefix="/mitigation", tags=["mitigation"])


# --------------------------------------------------------------------------------- io


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: str = "draft"


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: str | None = None


class PlanCostRead(BaseModel):
    action_count: int
    costed_count: int
    unpriced_count: int
    cancelled_count: int
    total_budget: float
    total_sched_days: float
    by_status: dict[str, int]


class PlanRead(BaseModel):
    id: int
    scope_id: int
    name: str
    description: str | None
    status: str
    materialized_at: datetime | None
    materialized_by: str | None
    materialized_fingerprint: str | None
    materialized_risk_count: int | None
    materialized_retired_count: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlanDetail(PlanRead):
    cost: PlanCostRead
    treated_count: int


class TreatmentWrite(BaseModel):
    treatment: str = "reduce"
    mode: str = "factor"
    p_factor: float = Field(default=1.0, gt=0, le=1)
    cost_factor: float = Field(default=1.0, gt=0, le=1)
    sched_factor: float = Field(default=1.0, gt=0, le=1)
    residual_p: float | None = Field(default=None, gt=0, le=1)
    residual_cost_min: float | None = None
    residual_cost_ml: float | None = None
    residual_cost_max: float | None = None
    residual_sched_min: float | None = None
    residual_sched_ml: float | None = None
    residual_sched_max: float | None = None
    rationale: str | None = None


class TreatmentRead(TreatmentWrite):
    id: int
    plan_id: int
    risk_id: int

    model_config = {"from_attributes": True}


class ResidualLineRead(BaseModel):
    risk_id: int
    risk_code: str
    title: str
    treatment: str
    retired: bool
    base_p: float
    residual_p: float | None
    base_cost_ev: float | None
    residual_cost_ev: float | None
    base_sched_ev: float | None
    residual_sched_ev: float | None
    issues: list[str]
    locked: bool
    edited_since: bool


class ResidualPreview(BaseModel):
    plan_id: int
    fingerprint: str
    #: True when the residual register on file still hashes to what this plan last wrote.
    matches_materialized: bool
    lines: list[ResidualLineRead]
    treated: int
    untreated: int
    retired: int
    locked: list[str]
    edited_since: list[str]
    #: Sum of the per-risk expected impacts, which is a sanity check and not a
    #: contingency. Means add; percentiles do not.
    base_cost_ev_total: float
    residual_cost_ev_total: float


class MaterializeRequest(BaseModel):
    confirm_replace_edited: bool = False


class MaterializeRead(BaseModel):
    written: int
    unchanged: int
    retired: int
    skipped_locked: list[str]
    replaced_edited: list[str]
    orphans: list[str]
    issues: list[str]
    fingerprint: str


class ActionRead(BaseModel):
    id: int
    risk_id: int
    risk_code: str
    plan_id: int | None
    action: str
    owner: str | None
    budget: float | None
    sched_days: float | None
    status: str


class VocabularyRead(BaseModel):
    plan_statuses: list[str]
    treatments: list[str]
    modes: list[str]


# ---------------------------------------------------------------------------- helpers


async def _get_plan(db: AsyncSession, plan_id: int) -> MitigationPlan:
    plan = await db.get(MitigationPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Mitigation plan not found")
    return plan


async def _detail(db: AsyncSession, plan: MitigationPlan) -> PlanDetail:
    cost = await mp.plan_cost(db, plan.id)
    treated = len(
        (
            await db.scalars(
                select(MitigationPlanRisk.id).where(MitigationPlanRisk.plan_id == plan.id)
            )
        ).all()
    )
    return PlanDetail(
        **PlanRead.model_validate(plan).model_dump(),
        cost=PlanCostRead(**vars(cost)),
        treated_count=treated,
    )


def _check(value: str | None, allowed: tuple[str, ...], field: str) -> None:
    if value is not None and value not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be one of {', '.join(allowed)}",
        )


# ------------------------------------------------------------------------- vocabulary


@router.get("/vocabulary", response_model=VocabularyRead)
async def vocabulary() -> VocabularyRead:
    """Served rather than hardcoded in the client, so the two cannot drift apart."""
    return VocabularyRead(
        plan_statuses=list(PLAN_STATUSES),
        treatments=list(TREATMENTS),
        modes=list(TREATMENT_MODES),
    )


# ------------------------------------------------------------------------------ plans


@router.get("/plans", response_model=list[PlanRead])
async def list_plans(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(
        default=None,
        description="Restrict to this scope and everything under it. Omitted means unfiltered.",
    ),
) -> list[MitigationPlan]:
    stmt = select(MitigationPlan).order_by(MitigationPlan.created_at.desc(), MitigationPlan.id.desc())
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        stmt = stmt.where(MitigationPlan.scope_id.in_(scope_ids))
    return list((await db.scalars(stmt)).all())


@router.post("/plans", response_model=PlanDetail, status_code=201)
async def create_plan(
    payload: PlanCreate,
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None, description="Project this plan treats."),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> PlanDetail:
    _check(payload.status, PLAN_STATUSES, "status")
    scope = await resolve_write_scope(db, scope_id)
    plan = MitigationPlan(
        scope_id=scope.id,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        created_by=actor,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return await _detail(db, plan)


@router.get("/plans/{plan_id}", response_model=PlanDetail)
async def get_plan(plan_id: int, db: AsyncSession = Depends(get_db)) -> PlanDetail:
    return await _detail(db, await _get_plan(db, plan_id))


@router.patch("/plans/{plan_id}", response_model=PlanDetail)
async def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    db: AsyncSession = Depends(get_db),
) -> PlanDetail:
    _check(payload.status, PLAN_STATUSES, "status")
    plan = await _get_plan(db, plan_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, field, value)
    await db.commit()
    await db.refresh(plan)
    return await _detail(db, plan)


@router.delete("/plans/{plan_id}", status_code=204, response_model=None)
async def delete_plan(plan_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """Remove the package. The residual estimates it wrote are left alone.

    Deleting a plan is a decision about a proposal, not a decision to un-write a scenario
    a simulation may already have quoted. The residual register stays until somebody
    materialises a different plan over it or edits it directly.
    """
    plan = await _get_plan(db, plan_id)
    # SQLite ignores ON DELETE without PRAGMA foreign_keys, so the children go explicitly
    # (``REFERENCE.md`` gotchas). Actions are detached rather than removed: they are the
    # record of work people have been doing.
    for entry in (
        await db.scalars(select(MitigationPlanRisk).where(MitigationPlanRisk.plan_id == plan_id))
    ).all():
        await db.delete(entry)
    for action in (
        await db.scalars(select(MitigationAction).where(MitigationAction.plan_id == plan_id))
    ).all():
        action.plan_id = None
    await db.delete(plan)
    await db.commit()


# -------------------------------------------------------------------------- treatments


@router.get("/plans/{plan_id}/risks", response_model=list[TreatmentRead])
async def list_treatments(
    plan_id: int, db: AsyncSession = Depends(get_db)
) -> list[MitigationPlanRisk]:
    await _get_plan(db, plan_id)
    return list(
        (
            await db.scalars(
                select(MitigationPlanRisk)
                .where(MitigationPlanRisk.plan_id == plan_id)
                .order_by(MitigationPlanRisk.risk_id)
            )
        ).all()
    )


@router.put("/plans/{plan_id}/risks/{risk_id}", response_model=TreatmentRead)
async def set_treatment(
    plan_id: int,
    risk_id: int,
    payload: TreatmentWrite,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> MitigationPlanRisk:
    """Declare what this plan leaves behind on one risk.

    A declaration, not a measurement: nothing here says the treatment works. It says what
    to simulate, and the simulation says the rest.
    """
    plan = await _get_plan(db, plan_id)
    _check(payload.treatment, TREATMENTS, "treatment")
    _check(payload.mode, TREATMENT_MODES, "mode")

    risk = await db.get(Risk, risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail="Risk not found")
    scope_ids = await resolve_read_scope(db, plan.scope_id)
    if scope_ids is not None and risk.scope_id not in scope_ids:
        raise HTTPException(
            status_code=422,
            detail="That risk is outside the scope this plan treats.",
        )

    row = (
        await db.scalars(
            select(MitigationPlanRisk).where(
                MitigationPlanRisk.plan_id == plan_id,
                MitigationPlanRisk.risk_id == risk_id,
            )
        )
    ).first()
    created = row is None
    if row is None:
        row = MitigationPlanRisk(plan_id=plan_id, risk_id=risk_id)
        db.add(row)

    before = plan_risk_snapshot(row)
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    changes = plan_risk_diff(before, plan_risk_snapshot(row))

    if changes:
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="treatment set" if created else "treatment updated",
                actor=actor,
                changes=changes,
            )
        )
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/plans/{plan_id}/risks/{risk_id}", status_code=204, response_model=None)
async def clear_treatment(
    plan_id: int,
    risk_id: int,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> None:
    await _get_plan(db, plan_id)
    row = (
        await db.scalars(
            select(MitigationPlanRisk).where(
                MitigationPlanRisk.plan_id == plan_id,
                MitigationPlanRisk.risk_id == risk_id,
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No treatment on that risk")
    risk = await db.get(Risk, risk_id)
    if risk is not None:
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="treatment cleared",
                actor=actor,
                changes=[{"field": "treatment", "old": row.treatment, "new": None}],
            )
        )
    await db.delete(row)
    await db.commit()


# ---------------------------------------------------------------------------- residual


@router.get("/plans/{plan_id}/residual", response_model=ResidualPreview)
async def residual_preview(plan_id: int, db: AsyncSession = Depends(get_db)) -> ResidualPreview:
    """What materialising would write, without writing it.

    Every risk in scope with a baseline appears, treated or not. That is the point of the
    screen: the residual register is the whole register, and a reviewer needs to see the
    untreated lines carried through at full size rather than infer them from an absence.
    """
    plan = await _get_plan(db, plan_id)
    lines = await mp.load_lines(db, plan)
    digest = mp.fingerprint(lines)
    return ResidualPreview(
        plan_id=plan.id,
        fingerprint=digest,
        matches_materialized=(
            plan.materialized_fingerprint is not None
            and plan.materialized_fingerprint == digest
        ),
        lines=[
            ResidualLineRead(
                risk_id=ln.risk_id,
                risk_code=ln.risk_code,
                title=ln.title,
                treatment=ln.treatment,
                retired=ln.residual is None,
                base_p=ln.base_p,
                residual_p=ln.residual_p,
                base_cost_ev=ln.base_cost_ev,
                residual_cost_ev=ln.residual_cost_ev,
                base_sched_ev=ln.base_sched_ev,
                residual_sched_ev=ln.residual_sched_ev,
                issues=ln.issues,
                locked=ln.locked,
                edited_since=ln.edited_since,
            )
            for ln in lines
        ],
        treated=sum(1 for ln in lines if ln.treatment == "reduce"),
        untreated=sum(1 for ln in lines if ln.treatment == "untreated"),
        retired=sum(1 for ln in lines if ln.residual is None),
        locked=[ln.risk_code for ln in lines if ln.locked],
        edited_since=[ln.risk_code for ln in lines if ln.edited_since and not ln.locked],
        base_cost_ev_total=sum(ln.base_cost_ev or 0.0 for ln in lines),
        residual_cost_ev_total=sum(ln.residual_cost_ev or 0.0 for ln in lines),
    )


@router.post("/plans/{plan_id}/materialize", response_model=MaterializeRead)
async def materialize_plan(
    plan_id: int,
    payload: MaterializeRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> MaterializeRead:
    """Write the residual register, so a post-mitigation run has something to read.

    One transaction: the estimates and the plan's own record of having written them land
    together or not at all.
    """
    plan = await _get_plan(db, plan_id)
    try:
        result = await mp.materialize(
            db, plan, actor=actor, confirm_replace_edited=payload.confirm_replace_edited
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return MaterializeRead(**vars(result))


# ----------------------------------------------------------------------------- actions


@router.get("/actions", response_model=list[ActionRead])
async def list_scope_actions(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(
        default=None,
        description="Restrict to this scope and everything under it. Omitted means unfiltered.",
    ),
    plan_id: int | None = Query(default=None, description="Only actions in this plan."),
    unassigned: bool = Query(
        default=False, description="Only actions not yet in any plan."
    ),
) -> list[ActionRead]:
    """Every mitigation action in scope, across risks.

    The per-risk endpoint under ``/risks`` is for editing one risk's actions. This one is
    for assembling a package, which is a question about the register rather than about a
    row in it.
    """
    stmt = (
        select(MitigationAction, Risk.risk_code)
        .join(Risk, Risk.id == MitigationAction.risk_id)
        .order_by(Risk.risk_code, MitigationAction.sort_order, MitigationAction.id)
    )
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        stmt = stmt.where(Risk.scope_id.in_(scope_ids))
    if plan_id is not None:
        stmt = stmt.where(MitigationAction.plan_id == plan_id)
    if unassigned:
        stmt = stmt.where(MitigationAction.plan_id.is_(None))
    return [
        ActionRead(
            id=a.id,
            risk_id=a.risk_id,
            risk_code=code,
            plan_id=a.plan_id,
            action=a.action,
            owner=a.owner,
            budget=a.budget,
            sched_days=a.sched_days,
            status=a.status,
        )
        for a, code in (await db.execute(stmt)).all()
    ]
