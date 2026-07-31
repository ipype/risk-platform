"""Quantitative elicitation endpoints.

``PUT`` rather than ``POST`` for the estimate itself: there is exactly one row per
(risk, scenario), so the write is an upsert and the URL is the identity. Nothing here
touches the qualitative matrix scores on ``Risk``.

Every mutation writes to ``RiskHistory``, the same append-only log the register and
mitigations use, so a risk's audit trail stays in one place instead of fragmenting into
a per-subsystem table nobody joins.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
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

router = APIRouter(tags=["quant"])


# --------------------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------------------


class QuantEstimateWrite(BaseModel):
    p_occurrence: float = Field(default=1.0, gt=0.0, le=1.0)
    is_variability: bool = False
    bound_interpretation: str = "absolute"
    dist_type: str = "pert"
    pert_lambda: float = Field(default=4.0, gt=0.0)

    cost_min: float | None = None
    cost_ml: float | None = None
    cost_max: float | None = None
    cost_basis: str = "absolute"

    sched_min: float | None = None
    sched_ml: float | None = None
    sched_max: float | None = None
    sched_day_basis: str = "working"

    source: str = "sme"
    confidence: str = "medium"
    notes: str | None = None

    def to_input(self) -> qv.EstimateInput:
        return qv.EstimateInput(
            p_occurrence=self.p_occurrence,
            is_variability=self.is_variability,
            bound_interpretation=self.bound_interpretation,
            dist_type=self.dist_type,
            pert_lambda=self.pert_lambda,
            cost_min=self.cost_min,
            cost_ml=self.cost_ml,
            cost_max=self.cost_max,
            cost_basis=self.cost_basis,
            sched_min=self.sched_min,
            sched_ml=self.sched_ml,
            sched_max=self.sched_max,
            sched_day_basis=self.sched_day_basis,
            source=self.source,
            confidence=self.confidence,
        )


class QuantEstimateRead(BaseModel):
    id: int
    risk_id: int
    scenario: str
    p_occurrence: float
    is_variability: bool
    bound_interpretation: str
    dist_type: str
    pert_lambda: float
    cost_min: float | None
    cost_ml: float | None
    cost_max: float | None
    cost_basis: str
    sched_min: float | None
    sched_ml: float | None
    sched_max: float | None
    sched_day_basis: str
    source: str
    confidence: str
    estimated_by: str
    estimated_at: datetime
    notes: str | None
    locked: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IssueRead(BaseModel):
    severity: str
    field: str
    message: str


class QuantEstimateResponse(BaseModel):
    """The stored row, plus what the rules thought of it and what it looks like sampled.

    Warnings ride along with the successful write rather than blocking it. An estimate
    can be odd and still be what the SME meant; the analyst decides, and gets told.
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


class CoverageResponse(BaseModel):
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
# helpers
# --------------------------------------------------------------------------------------


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
# preview
# --------------------------------------------------------------------------------------


@router.post("/quant/preview", response_model=PreviewResponse)
async def preview(payload: QuantEstimateWrite) -> PreviewResponse:
    """Run the rules and the maths without persisting anything.

    Backs the live distribution preview on the entry form. Watching the curve move while
    typing is the cheapest quality lift available in elicitation — an SME who has just
    seen their own numbers drawn will revise them, and an SME who never sees them will
    not. No DB access, so it stays cheap enough to call on every keystroke.
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
) -> list[RiskQuantEstimate]:
    await _get_risk(db, risk_id)
    res = await db.execute(
        select(RiskQuantEstimate)
        .where(RiskQuantEstimate.risk_id == risk_id)
        .order_by(RiskQuantEstimate.scenario)
    )
    return list(res.scalars().all())


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
        estimate=QuantEstimateRead.model_validate(row),
        warnings=_issues(result.warnings),
        summary=qv.summarise(est),
    )


def _to_input(row: RiskQuantEstimate) -> qv.EstimateInput:
    return qv.EstimateInput(
        p_occurrence=row.p_occurrence,
        is_variability=row.is_variability,
        bound_interpretation=row.bound_interpretation,
        dist_type=row.dist_type,
        pert_lambda=row.pert_lambda,
        cost_min=row.cost_min,
        cost_ml=row.cost_ml,
        cost_max=row.cost_max,
        cost_basis=row.cost_basis,
        sched_min=row.sched_min,
        sched_ml=row.sched_ml,
        sched_max=row.sched_max,
        sched_day_basis=row.sched_day_basis,
        source=row.source,
        confidence=row.confidence,
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

    for field_name, value in payload.model_dump().items():
        setattr(row, field_name, value)
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
        estimate=QuantEstimateRead.model_validate(row),
        warnings=_issues(result.warnings),
        summary=qv.summarise(est),
    )


@router.patch(
    "/risks/{risk_id}/quant/{scenario}/lock", response_model=QuantEstimateRead
)
async def set_lock(
    risk_id: int,
    scenario: str,
    payload: LockWrite,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> RiskQuantEstimate:
    """Freeze or release an estimate.

    A locked estimate is one a simulation run depends on. Unlocking is a deliberate act
    with its own audit entry rather than a query parameter on the write, so that
    "reproducible runs" does not quietly become "reproducible until someone saved a
    form".
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
                changes=[
                    {"field": "locked", "old": not payload.locked, "new": payload.locked}
                ],
            )
        )
    await db.commit()
    await db.refresh(row)
    return row


@router.delete(
    "/risks/{risk_id}/quant/{scenario}", status_code=204, response_model=None
)
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
    matrix band, then flag the lot. The matrix earns its keep here — as a screen for
    where to spend elicitation time, not as a source of numbers.
    """
    if not payload.risk_ids:
        return TriageResponse(updated=0)

    res = await db.execute(select(Risk).where(Risk.id.in_(payload.risk_ids)))
    risks = list(res.scalars().all())

    updated = 0
    for risk in risks:
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
                    {
                        "field": "quantify",
                        "old": not payload.quantify,
                        "new": payload.quantify,
                    }
                ],
            )
        )
    await db.commit()
    return TriageResponse(updated=updated)


@router.get("/quant/coverage", response_model=CoverageResponse)
async def coverage(
    scenario: str = "pre_mitigation", db: AsyncSession = Depends(get_db)
) -> CoverageResponse:
    """Which flagged risks still have no estimate.

    Reported as the gap, not the tally. A simulation run over a register where a third of
    the flagged risks were never elicited produces a clean, confident, and far too low
    contingency, and nothing in the output says so.
    """
    _check_scenario(scenario)

    flagged = await db.execute(select(Risk.id).where(Risk.quantify.is_(True)))
    flagged_ids = set(flagged.scalars().all())

    done = await db.execute(
        select(RiskQuantEstimate.risk_id).where(RiskQuantEstimate.scenario == scenario)
    )
    done_ids = set(done.scalars().all())

    return CoverageResponse(
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
async def create_driver(
    payload: DriverWrite, db: AsyncSession = Depends(get_db)
) -> RiskDriver:
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
        await db.execute(
            sa_delete(RiskDriverLink).where(RiskDriverLink.risk_id == risk_id)
        )
        for driver_id in sorted(wanted):
            db.add(RiskDriverLink(risk_id=risk_id, driver_id=driver_id))
        db.add(
            RiskHistory(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                action="drivers set",
                actor=actor,
                changes=[
                    {
                        "field": "drivers",
                        "old": sorted(current_ids),
                        "new": sorted(wanted),
                    }
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
    return {
        "groups": list(groups.values()),
        "estimates": total.scalar_one(),
    }
