"""Quantitative elicitation endpoints.

``PUT`` rather than ``POST`` for the estimate itself: there is exactly one row per
(risk, scenario), so the write is an upsert and the URL is the identity.

The payload nests by dimension — ``{"cost": {...}, "sched": {...}}`` — while the table
stays flat columns. Nesting is what the form actually looks like and what the validator
reasons about; flat columns are what a sampler wants to read and what a CHECK constraint
can bind to. The mapping between them lives here and nowhere else.

Every mutation writes to ``RiskHistory``, the same append-only log the register and
mitigations use, so a risk's audit trail stays in one place instead of fragmenting into a
per-subsystem table nobody joins.
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import QuantEstimateInvalid, QuantEstimateLocked
from app.db.session import get_db
from app.models.history import RiskHistory
from app.models.quant import (
    RiskDriver,
    RiskDriverLink,
    RiskQuantEstimate,
    quant_diff,
    quant_snapshot,
)
from app.models.risk import Risk
from app.services import quant_validation as qv
from app.services.scope import resolve_read_scope

router = APIRouter(tags=["quant"])

DIMENSIONS = ("cost", "sched")


# --------------------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------------------


class PointWrite(BaseModel):
    x: float
    p: float


class RationaleEntry(BaseModel):
    """Why one of the three numbers is what it is.

    ``source`` is not decoration. When an agent starts drafting these, the field is what
    keeps its wording from silently becoming the analyst's own judgement.
    """

    text: str | None = None
    source: str = "sme"
    author: str | None = None
    at: datetime | None = None


class DimensionWrite(BaseModel):
    dist: str = "none"
    min: float | None = None
    ml: float | None = None
    max: float | None = None
    pert_lambda: float = Field(default=4.0, gt=0.0)
    points: list[PointWrite] | None = None
    rationale: dict[str, RationaleEntry] | None = None
    #: Override of the estimate-level interpretation. Omitted means inherit, which is what
    #: every client written before the split sends and what it has always meant.
    bound_interpretation: str | None = None

    def to_input(self) -> qv.DimensionInput:
        return qv.DimensionInput(
            dist=self.dist,
            lo=self.min,
            ml=self.ml,
            hi=self.max,
            pert_lambda=self.pert_lambda,
            points=[p.model_dump() for p in self.points] if self.points else None,
            rationale=(
                {k: v.model_dump(mode="json") for k, v in self.rationale.items()}
                if self.rationale
                else None
            ),
            bound_interpretation=self.bound_interpretation,
        )


class DimensionRead(BaseModel):
    dist: str
    min: float | None = None
    ml: float | None = None
    max: float | None = None
    pert_lambda: float = 4.0
    points: list[PointWrite] | None = None
    rationale: dict[str, Any] | None = None
    bound_interpretation: str | None = None


class QuantEstimateWrite(BaseModel):
    p_occurrence: float = Field(default=1.0, gt=0.0, le=1.0)
    is_variability: bool = False
    bound_interpretation: str = "absolute"
    cost: DimensionWrite = Field(default_factory=DimensionWrite)
    sched: DimensionWrite = Field(default_factory=DimensionWrite)
    cost_basis: str = "absolute"
    #: Unconstrained here on purpose — a zero or negative base is a modelling error with a
    #: message worth reading, not a 422 with a field path. ``quant_validation`` owns it.
    cost_base_value: float | None = None
    sched_day_basis: str = "working"
    source: str = "sme"
    confidence: str = "medium"
    notes: str | None = None

    def to_input(self) -> qv.EstimateInput:
        return qv.EstimateInput(
            p_occurrence=self.p_occurrence,
            is_variability=self.is_variability,
            bound_interpretation=self.bound_interpretation,
            cost=self.cost.to_input(),
            sched=self.sched.to_input(),
            cost_basis=self.cost_basis,
            sched_day_basis=self.sched_day_basis,
            source=self.source,
            confidence=self.confidence,
            cost_base_value=self.cost_base_value,
        )


class QuantEstimateRead(BaseModel):
    id: int
    risk_id: int
    scenario: str
    p_occurrence: float
    is_variability: bool
    bound_interpretation: str
    cost: DimensionRead
    sched: DimensionRead
    cost_basis: str
    cost_base_value: float | None
    sched_day_basis: str
    source: str
    confidence: str
    estimated_by: str
    estimated_at: datetime
    notes: str | None
    locked: bool
    created_at: datetime
    updated_at: datetime


class IssueRead(BaseModel):
    severity: str
    field: str
    message: str


class QuantEstimateResponse(BaseModel):
    """The stored row, plus what the rules thought of it and what it looks like sampled.

    Warnings ride along with a successful write rather than blocking it. An estimate can
    be odd and still be exactly what the SME meant; the analyst decides, and gets told.
    """

    estimate: QuantEstimateRead
    warnings: list[IssueRead] = []
    summary: dict = {}


class PreviewResponse(BaseModel):
    ok: bool
    errors: list[IssueRead] = []
    warnings: list[IssueRead] = []
    summary: dict = {}


class LockWrite(BaseModel):
    locked: bool


class TriageWrite(BaseModel):
    risk_ids: list[int]
    quantify: bool = True


class TriageResponse(BaseModel):
    updated: int


class QuantCoverageResponse(BaseModel):
    flagged_for_quantification: int
    estimated: int
    missing: list[int]


class DriverWrite(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    correlation_default: float = Field(default=0.5, ge=-1.0, le=1.0)


class DriverUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    correlation_default: float | None = Field(default=None, ge=-1.0, le=1.0)


class DriverRead(BaseModel):
    id: int
    name: str
    description: str | None
    correlation_default: float

    model_config = {"from_attributes": True}


class DriverLinkWrite(BaseModel):
    driver_ids: list[int]


# --------------------------------------------------------------------------------------
# nested payload <-> flat columns
# --------------------------------------------------------------------------------------

_DIM_COLUMNS = {
    "dist": "{d}_dist",
    "min": "{d}_min",
    "ml": "{d}_ml",
    "max": "{d}_max",
    "pert_lambda": "{d}_pert_lambda",
    "points": "{d}_points",
    "rationale": "{d}_rationale",
    "bound_interpretation": "{d}_bound_interpretation",
}


def _apply_dimension(row: RiskQuantEstimate, dim: str, payload: DimensionWrite) -> None:
    """Write one nested dimension onto its flat columns.

    Values from shapes that do not use them are cleared rather than left behind. A stale
    ``ml`` under a uniform is invisible on screen and would reappear the moment someone
    switched the shape back, silently resurrecting a number nobody re-confirmed. The bound
    interpretation is cleared on the same rule: a cumulative curve defines its own support
    point by point, so an interpretation stored against it means nothing and would only
    come back to life under a later shape change.
    """
    uses_three_point = payload.dist in qv.THREE_POINT_DISTS
    uses_bounds = uses_three_point or payload.dist == "uniform"
    uses_points = payload.dist in qv.POINT_DISTS

    setattr(row, _DIM_COLUMNS["dist"].format(d=dim), payload.dist)
    setattr(row, _DIM_COLUMNS["min"].format(d=dim), payload.min if uses_bounds else None)
    setattr(row, _DIM_COLUMNS["ml"].format(d=dim), payload.ml if uses_three_point else None)
    setattr(row, _DIM_COLUMNS["max"].format(d=dim), payload.max if uses_bounds else None)
    setattr(row, _DIM_COLUMNS["pert_lambda"].format(d=dim), payload.pert_lambda)
    setattr(
        row,
        _DIM_COLUMNS["points"].format(d=dim),
        [p.model_dump() for p in payload.points] if (uses_points and payload.points) else None,
    )
    setattr(
        row,
        _DIM_COLUMNS["rationale"].format(d=dim),
        {k: v.model_dump(mode="json") for k, v in payload.rationale.items()}
        if payload.rationale
        else None,
    )
    setattr(
        row,
        _DIM_COLUMNS["bound_interpretation"].format(d=dim),
        payload.bound_interpretation if uses_bounds else None,
    )


def _read_dimension(row: RiskQuantEstimate, dim: str) -> DimensionRead:
    return DimensionRead(
        dist=getattr(row, f"{dim}_dist"),
        min=getattr(row, f"{dim}_min"),
        ml=getattr(row, f"{dim}_ml"),
        max=getattr(row, f"{dim}_max"),
        pert_lambda=getattr(row, f"{dim}_pert_lambda"),
        points=[PointWrite(**p) for p in (getattr(row, f"{dim}_points") or [])] or None,
        rationale=getattr(row, f"{dim}_rationale"),
        bound_interpretation=getattr(row, f"{dim}_bound_interpretation"),
    )


def _read_estimate(row: RiskQuantEstimate) -> QuantEstimateRead:
    return QuantEstimateRead(
        id=row.id,
        risk_id=row.risk_id,
        scenario=row.scenario,
        p_occurrence=row.p_occurrence,
        is_variability=row.is_variability,
        bound_interpretation=row.bound_interpretation,
        cost=_read_dimension(row, "cost"),
        sched=_read_dimension(row, "sched"),
        cost_basis=row.cost_basis,
        cost_base_value=row.cost_base_value,
        sched_day_basis=row.sched_day_basis,
        source=row.source,
        confidence=row.confidence,
        estimated_by=row.estimated_by,
        estimated_at=row.estimated_at,
        notes=row.notes,
        locked=row.locked,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_input(row: RiskQuantEstimate) -> qv.EstimateInput:
    def dim(d: str) -> qv.DimensionInput:
        return qv.DimensionInput(
            dist=getattr(row, f"{d}_dist"),
            lo=getattr(row, f"{d}_min"),
            ml=getattr(row, f"{d}_ml"),
            hi=getattr(row, f"{d}_max"),
            pert_lambda=getattr(row, f"{d}_pert_lambda"),
            points=getattr(row, f"{d}_points"),
            rationale=getattr(row, f"{d}_rationale"),
            bound_interpretation=getattr(row, f"{d}_bound_interpretation"),
        )

    return qv.EstimateInput(
        p_occurrence=row.p_occurrence,
        is_variability=row.is_variability,
        bound_interpretation=row.bound_interpretation,
        cost=dim("cost"),
        sched=dim("sched"),
        cost_basis=row.cost_basis,
        sched_day_basis=row.sched_day_basis,
        source=row.source,
        confidence=row.confidence,
        cost_base_value=row.cost_base_value,
    )


def _issues(items: list[qv.Issue]) -> list[IssueRead]:
    return [IssueRead(severity=i.severity, field=i.field, message=i.message) for i in items]


async def _get_risk(db: AsyncSession, risk_id: int) -> Risk:
    risk = await db.get(Risk, risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail="Risk not found")
    return risk


def _check_scenario(scenario: str) -> None:
    if scenario not in qv.SCENARIOS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown scenario '{scenario}'. Expected one of {', '.join(qv.SCENARIOS)}.",
        )


async def _find(db: AsyncSession, risk_id: int, scenario: str) -> RiskQuantEstimate | None:
    res = await db.execute(
        select(RiskQuantEstimate).where(
            RiskQuantEstimate.risk_id == risk_id,
            RiskQuantEstimate.scenario == scenario,
        )
    )
    return res.scalar_one_or_none()


# --------------------------------------------------------------------------------------
# reference data
# --------------------------------------------------------------------------------------


@router.get("/quant/distributions", response_model=dict)
async def distributions() -> dict:
    """Every shape, with guidance on when it is the right one.

    Served rather than duplicated in the frontend so the picker's advice, the validator's
    rules, and the docs cannot drift apart. If a shape is added here it appears in the UI
    with its guidance and nowhere needs editing twice.
    """
    return {
        "distributions": [
            {"value": key, **qv.DISTRIBUTION_GUIDANCE[key]}
            for key in qv.DIST_TYPES
            if key in qv.DISTRIBUTION_GUIDANCE
        ],
        "bound_interpretations": list(qv.BOUND_INTERPRETATIONS),
        "scenarios": list(qv.SCENARIOS),
        "sources": list(qv.SOURCES),
        "confidences": list(qv.CONFIDENCES),
        "day_bases": list(qv.DAY_BASES),
        "cost_bases": list(qv.COST_BASES),
        "rationale_keys": list(qv.RATIONALE_KEYS),
    }


# --------------------------------------------------------------------------------------
# preview
# --------------------------------------------------------------------------------------


@router.post("/quant/preview", response_model=PreviewResponse)
async def preview(payload: QuantEstimateWrite) -> PreviewResponse:
    """Run the rules and the maths without persisting anything.

    Backs the live distribution preview on the entry form. Watching the curve move while
    typing is the cheapest quality lift available in elicitation — an SME who has just
    seen their own numbers drawn will revise them, and one who never sees them will not.
    No DB access, so it stays cheap enough to call on every keystroke.
    """
    est = payload.to_input()
    result = qv.validate(est)
    return PreviewResponse(
        ok=result.ok,
        errors=_issues(result.errors),
        warnings=_issues(result.warnings),
        summary=qv.summarise(est) if result.ok else {},
    )


# --------------------------------------------------------------------------------------
# estimates
# --------------------------------------------------------------------------------------


@router.get("/risks/{risk_id}/quant", response_model=list[QuantEstimateRead])
async def list_estimates(
    risk_id: int, db: AsyncSession = Depends(get_db)
) -> list[QuantEstimateRead]:
    await _get_risk(db, risk_id)
    res = await db.execute(
        select(RiskQuantEstimate)
        .where(RiskQuantEstimate.risk_id == risk_id)
        .order_by(RiskQuantEstimate.scenario)
    )
    return [_read_estimate(row) for row in res.scalars().all()]


@router.get("/risks/{risk_id}/quant/{scenario}", response_model=QuantEstimateResponse)
async def get_estimate(
    risk_id: int, scenario: str, db: AsyncSession = Depends(get_db)
) -> QuantEstimateResponse:
    _check_scenario(scenario)
    await _get_risk(db, risk_id)
    row = await _find(db, risk_id, scenario)
    if row is None:
        raise HTTPException(status_code=404, detail="No estimate for that scenario")

    est = _to_input(row)
    result = qv.validate(est)
    return QuantEstimateResponse(
        estimate=_read_estimate(row),
        warnings=_issues(result.warnings),
        summary=qv.summarise(est) if result.ok else {},
    )


@router.put("/risks/{risk_id}/quant/{scenario}", response_model=QuantEstimateResponse)
async def upsert_estimate(
    risk_id: int,
    scenario: str,
    payload: QuantEstimateWrite,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> QuantEstimateResponse:
    _check_scenario(scenario)
    risk = await _get_risk(db, risk_id)

    est = payload.to_input()
    result = qv.validate(est)
    if not result.ok:
        raise QuantEstimateInvalid(
            [{"field": i.field, "message": i.message} for i in result.errors]
        )

    row = await _find(db, risk_id, scenario)
    creating = row is None

    if row is not None and row.locked:
        raise QuantEstimateLocked(risk_id, scenario)

    if row is None:
        row = RiskQuantEstimate(risk_id=risk_id, scenario=scenario)
        db.add(row)
        before: dict = {}
    else:
        before = quant_snapshot(row)

    row.p_occurrence = payload.p_occurrence
    row.is_variability = payload.is_variability
    row.bound_interpretation = payload.bound_interpretation
    row.cost_basis = payload.cost_basis
    # Same discipline as the per-dimension clears in ``_apply_dimension``: a base amount
    # left behind under an absolute basis is invisible on screen and would start scaling
    # the numbers again the moment somebody switched the basis back.
    row.cost_base_value = (
        payload.cost_base_value if payload.cost_basis == "pct_of_base" else None
    )
    row.sched_day_basis = payload.sched_day_basis
    row.source = payload.source
    row.confidence = payload.confidence
    row.notes = payload.notes
    for dim in DIMENSIONS:
        _apply_dimension(row, dim, getattr(payload, dim))
    row.estimated_by = actor
    row.estimated_at = datetime.now().astimezone()

    changes = quant_diff(before, quant_snapshot(row))
    if changes:
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="quant set" if creating else "quant updated",
                actor=actor,
                changes=changes,
            )
        )

    await db.commit()
    await db.refresh(row)

    return QuantEstimateResponse(
        estimate=_read_estimate(row),
        warnings=_issues(result.warnings),
        summary=qv.summarise(est),
    )


@router.patch("/risks/{risk_id}/quant/{scenario}/lock", response_model=QuantEstimateRead)
async def set_lock(
    risk_id: int,
    scenario: str,
    payload: LockWrite,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> QuantEstimateRead:
    """Freeze or release an estimate.

    A locked estimate is one a simulation run depends on. Unlocking is a deliberate act
    with its own audit entry rather than a query parameter on the write, so "reproducible
    runs" does not quietly become "reproducible until someone saved a form".
    """
    _check_scenario(scenario)
    risk = await _get_risk(db, risk_id)
    row = await _find(db, risk_id, scenario)
    if row is None:
        raise HTTPException(status_code=404, detail="No estimate for that scenario")

    if row.locked != payload.locked:
        row.locked = payload.locked
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="quant locked" if payload.locked else "quant unlocked",
                actor=actor,
                changes=[{"field": "locked", "old": not payload.locked, "new": payload.locked}],
            )
        )
    await db.commit()
    await db.refresh(row)
    return _read_estimate(row)


@router.delete("/risks/{risk_id}/quant/{scenario}", status_code=204, response_model=None)
async def delete_estimate(
    risk_id: int,
    scenario: str,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> None:
    _check_scenario(scenario)
    risk = await _get_risk(db, risk_id)
    row = await _find(db, risk_id, scenario)
    if row is None:
        raise HTTPException(status_code=404, detail="No estimate for that scenario")
    if row.locked:
        raise QuantEstimateLocked(risk_id, scenario)

    db.add(
        RiskHistory(
            risk_id=risk.id,
            risk_code=risk.risk_code,
            action="quant removed",
            actor=actor,
            changes=[{"field": "scenario", "old": scenario, "new": None}],
        )
    )
    await db.delete(row)
    await db.commit()


# --------------------------------------------------------------------------------------
# triage
# --------------------------------------------------------------------------------------


@router.post("/quant/triage", response_model=TriageResponse)
async def set_triage(
    payload: TriageWrite,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> TriageResponse:
    """Flag which risks are worth quantifying.

    Bulk because that is how it is used: filter the register to everything at or above a
    matrix band, then flag the lot. The matrix earns its keep here — as a screen for where
    to spend elicitation time, not as a source of numbers.
    """
    if not payload.risk_ids:
        return TriageResponse(updated=0)

    res = await db.execute(select(Risk).where(Risk.id.in_(payload.risk_ids)))
    updated = 0
    for risk in res.scalars().all():
        if risk.quantify == payload.quantify:
            continue
        risk.quantify = payload.quantify
        updated += 1
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="quant triaged",
                actor=actor,
                changes=[
                    {"field": "quantify", "old": not payload.quantify, "new": payload.quantify}
                ],
            )
        )
    await db.commit()
    return TriageResponse(updated=updated)


@router.get("/quant/triage", response_model=dict)
async def get_triage(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None, description="Restrict to this scope and everything under it. Omitted means unfiltered."),
) -> dict:
    """Which risks are flagged for quantification.

    Lives here rather than as a field on ``RiskRead`` so the register's payload does not
    grow a column only this workflow reads, and so triage stays owned by one router.
    """
    triage = select(Risk.id).where(Risk.quantify.is_(True))
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        triage = triage.where(Risk.scope_id.in_(scope_ids))
    res = await db.execute(triage)
    return {"risk_ids": sorted(res.scalars().all())}


@router.get("/quant/coverage", response_model=QuantCoverageResponse)
async def coverage(
    scenario: str = "pre_mitigation",
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None, description="Restrict to this scope and everything under it. Omitted means unfiltered."),
) -> QuantCoverageResponse:
    """Which flagged risks still have no estimate.

    Reported as the gap, not the tally. A run over a register where a third of the flagged
    risks were never elicited produces a clean, confident, and far too low contingency, and
    nothing in the output says so.
    """
    _check_scenario(scenario)

    flagged_query = select(Risk.id).where(Risk.quantify.is_(True))
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        flagged_query = flagged_query.where(Risk.scope_id.in_(scope_ids))
    flagged = await db.execute(flagged_query)
    flagged_ids = set(flagged.scalars().all())

    done = await db.execute(
        select(RiskQuantEstimate.risk_id).where(
            RiskQuantEstimate.scenario == scenario,
            RiskQuantEstimate.cost_dist != "none",
        )
    )
    done_cost = set(done.scalars().all())
    done_sched = await db.execute(
        select(RiskQuantEstimate.risk_id).where(
            RiskQuantEstimate.scenario == scenario,
            RiskQuantEstimate.sched_dist != "none",
        )
    )
    done_ids = done_cost | set(done_sched.scalars().all())

    return QuantCoverageResponse(
        flagged_for_quantification=len(flagged_ids),
        estimated=len(flagged_ids & done_ids),
        missing=sorted(flagged_ids - done_ids),
    )


# --------------------------------------------------------------------------------------
# drivers
# --------------------------------------------------------------------------------------


@router.get("/drivers", response_model=list[DriverRead])
async def list_drivers(db: AsyncSession = Depends(get_db)) -> list[RiskDriver]:
    res = await db.execute(select(RiskDriver).order_by(RiskDriver.name))
    return list(res.scalars().all())


@router.post("/drivers", response_model=DriverRead, status_code=201)
async def create_driver(payload: DriverWrite, db: AsyncSession = Depends(get_db)) -> RiskDriver:
    existing = await db.execute(select(RiskDriver).where(RiskDriver.name == payload.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="A driver with that name exists")

    driver = RiskDriver(**payload.model_dump())
    db.add(driver)
    await db.commit()
    await db.refresh(driver)
    return driver


@router.patch("/drivers/{driver_id}", response_model=DriverRead)
async def update_driver(
    driver_id: int, payload: DriverUpdate, db: AsyncSession = Depends(get_db)
) -> RiskDriver:
    driver = await db.get(RiskDriver, driver_id)
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(driver, field_name, value)
    await db.commit()
    await db.refresh(driver)
    return driver


@router.delete("/drivers/{driver_id}", status_code=204, response_model=None)
async def delete_driver(driver_id: int, db: AsyncSession = Depends(get_db)) -> None:
    driver = await db.get(RiskDriver, driver_id)
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    await db.delete(driver)
    await db.commit()


@router.get("/risks/{risk_id}/drivers", response_model=list[DriverRead])
async def list_risk_drivers(
    risk_id: int, db: AsyncSession = Depends(get_db)
) -> list[RiskDriver]:
    await _get_risk(db, risk_id)
    res = await db.execute(
        select(RiskDriver)
        .join(RiskDriverLink, RiskDriverLink.driver_id == RiskDriver.id)
        .where(RiskDriverLink.risk_id == risk_id)
        .order_by(RiskDriver.name)
    )
    return list(res.scalars().all())


@router.put("/risks/{risk_id}/drivers", response_model=list[DriverRead])
async def set_risk_drivers(
    risk_id: int,
    payload: DriverLinkWrite,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> list[RiskDriver]:
    """Replace the risk's driver tags wholesale."""
    risk = await _get_risk(db, risk_id)

    wanted = set(payload.driver_ids)
    if wanted:
        found = await db.execute(select(RiskDriver.id).where(RiskDriver.id.in_(wanted)))
        missing = wanted - set(found.scalars().all())
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown driver id(s): {', '.join(str(m) for m in sorted(missing))}",
            )

    current = await db.execute(
        select(RiskDriverLink.driver_id).where(RiskDriverLink.risk_id == risk_id)
    )
    current_ids = set(current.scalars().all())

    if current_ids != wanted:
        await db.execute(sa_delete(RiskDriverLink).where(RiskDriverLink.risk_id == risk_id))
        for driver_id in sorted(wanted):
            db.add(RiskDriverLink(risk_id=risk_id, driver_id=driver_id))
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="drivers set",
                actor=actor,
                changes=[
                    {"field": "drivers", "old": sorted(current_ids), "new": sorted(wanted)}
                ],
            )
        )
        await db.commit()

    res = await db.execute(
        select(RiskDriver)
        .join(RiskDriverLink, RiskDriverLink.driver_id == RiskDriver.id)
        .where(RiskDriverLink.risk_id == risk_id)
        .order_by(RiskDriver.name)
    )
    return list(res.scalars().all())


@router.get("/quant/correlation-groups", response_model=dict)
async def correlation_groups(db: AsyncSession = Depends(get_db)) -> dict:
    """Driver tags grouped into the clusters the correlation matrix will be built from.

    Read-only, and deliberately ahead of the sampler: an analyst should be able to see
    which risks are about to move together before a run tells them the P80 doubled. The
    assembled matrix will frequently not be positive semi-definite and will need a
    nearest-PSD repair, which is the sampler's job — and which must be logged with the
    run, because it changes the answer.
    """
    res = await db.execute(
        select(RiskDriver.id, RiskDriver.name, RiskDriver.correlation_default, Risk.id)
        .join(RiskDriverLink, RiskDriverLink.driver_id == RiskDriver.id)
        .join(Risk, Risk.id == RiskDriverLink.risk_id)
        .order_by(RiskDriver.name, Risk.id)
    )
    groups: dict[int, dict] = {}
    for driver_id, name, rho, risk_id in res.all():
        g = groups.setdefault(
            driver_id, {"driver_id": driver_id, "name": name, "rho": rho, "risk_ids": []}
        )
        g["risk_ids"].append(risk_id)

    total = await db.execute(select(func.count()).select_from(RiskQuantEstimate))
    return {"groups": list(groups.values()), "estimates": total.scalar_one()}
