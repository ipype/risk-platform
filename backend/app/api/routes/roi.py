"""Mitigation ROI: two runs, one difference.

The whole module exists to make one comparison hard to get wrong. Two routes create a
pairing and they take opposite approaches to the same risk:

``POST /roi/plans/{id}/runs`` starts a matched pair from one request. There is a single set
of run settings and a single seed, and the two runs are built from it by changing exactly
one field. The configurations cannot drift because there is only one of them.

``POST /roi`` pairs two runs that already exist, for the case where somebody ran the
baseline last week. Here the configurations *can* differ, so every field that must match is
checked and the refusal names each one that moved. It also records ``seed_shared=False``
unless the seeds happen to agree, because a difference between two independently seeded
runs carries noise that a matched pair cancels.

Nothing here stores a computed answer. The comparison is derived on read from two immutable
runs, so it is exact every time and cannot drift from the code that produces it. What is
stored is the pairing and the package's cost at that moment — see ``models/roi.py``.

Reading is scope-filtered like every other list endpoint (4.8), and creating is refused
across scopes: a package in one project measured by another project's runs is not a
comparison, it is two unrelated numbers subtracted.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.simulations import RunRequest, RunSummary, start_run
from app.db.session import get_db
from app.models.mitigation import MitigationPlan
from app.models.roi import MitigationRoi
from app.models.simulation import SimulationRun
from app.services import roi as roi_service
from app.services.mitigation_plan import plan_cost
from app.services.scope import descendant_ids, resolve_read_scope
from app.services.sim_execute import load_run

router = APIRouter(prefix="/roi", tags=["roi"])

BASELINE_SCENARIO = "pre_mitigation"
TREATED_SCENARIO = "post_mitigation"


# --------------------------------------------------------------------------------------
# payloads
# --------------------------------------------------------------------------------------


class PairRequest(BaseModel):
    """One set of run settings, used twice.

    Deliberately not two request bodies. The single most common way a before/after
    comparison goes wrong is that the two runs were not actually comparable, and the
    cheapest defence is to make an incomparable pair unrepresentable in the request.
    ``scenario`` is absent for the same reason: it is the one thing that differs and the
    server sets it.
    """

    name: str = Field(default="", max_length=200)
    note: str | None = None
    percentile: float = Field(default=80.0, gt=0.0, lt=100.0)

    schedule_version_id: int | None = None
    iterations: int = Field(default=10_000, ge=100, le=1_000_000)
    seed: int = Field(default=12345, ge=0)
    sampling: Literal["lhs", "mc"] = "lhs"

    base_cost: float = Field(default=0.0, ge=0.0)
    burn_rate_per_day: float = Field(default=0.0, ge=0.0)
    allow_negative_delay_credit: bool = False

    correlate_occurrence: bool = True
    intra_risk_cost_sched_correlation: float = Field(default=0.0, ge=-1.0, le=1.0)

    gate_override: bool = False
    gate_override_reason: str | None = None

    def as_run(self, scenario: str, label: str) -> RunRequest:
        """This request as one of its two runs. The only argument that varies is the first."""
        return RunRequest(
            name=f"{self.name or 'ROI'} — {label}"[:200],
            scenario=scenario,
            schedule_version_id=self.schedule_version_id,
            iterations=self.iterations,
            seed=self.seed,
            sampling=self.sampling,
            base_cost=self.base_cost,
            burn_rate_per_day=self.burn_rate_per_day,
            allow_negative_delay_credit=self.allow_negative_delay_credit,
            correlate_occurrence=self.correlate_occurrence,
            intra_risk_cost_sched_correlation=self.intra_risk_cost_sched_correlation,
            gate_override=self.gate_override,
            gate_override_reason=self.gate_override_reason,
        )


class PairExisting(BaseModel):
    """Pair two runs that already exist."""

    plan_id: int
    before_run_id: int
    after_run_id: int
    name: str = Field(default="", max_length=200)
    note: str | None = None
    percentile: float = Field(default=80.0, gt=0.0, lt=100.0)

    @model_validator(mode="after")
    def _distinct(self) -> "PairExisting":
        if self.before_run_id == self.after_run_id:
            raise ValueError("A run cannot be compared with itself.")
        return self


class ReductionRead(BaseModel):
    before: float | None = None
    after: float | None = None
    reduction: float | None = None
    reduction_pct: float | None = None


class SeriesReductionRead(BaseModel):
    label: str
    units: str
    mean: ReductionRead
    at_percentile: ReductionRead
    standard_error: float | None = None
    within_noise: bool = False


class CurveRowRead(BaseModel):
    p: float
    before: float
    after: float
    reduction: float


class RiskMoverRead(BaseModel):
    risk_id: int
    code: str
    title: str
    movement: str
    share_before: float | None = None
    share_after: float | None = None
    contribution_before: float | None = None
    contribution_after: float | None = None
    contribution_reduction: float | None = None
    rank_before: int | None = None
    rank_after: int | None = None


class CriticalityMoverRead(BaseModel):
    activity_id: str
    code: str
    name: str
    index_before: float | None = None
    index_after: float | None = None
    index_change: float | None = None


class ComparisonRead(BaseModel):
    percentile: float
    contingency: SeriesReductionRead | None = None
    total_cost: SeriesReductionRead | None = None
    risk_cost: SeriesReductionRead | None = None
    delay_days: SeriesReductionRead | None = None
    finish_day: SeriesReductionRead | None = None
    schedule_driven_cost: SeriesReductionRead | None = None
    curve: list[CurveRowRead] = []
    plan_budget: float = 0.0
    plan_sched_days: float = 0.0
    plan_unpriced_count: int = 0
    benefit_cost_ratio: float | None = None
    net_at_percentile: float | None = None
    risk_movers: list[RiskMoverRead] = []
    criticality_movers: list[CriticalityMoverRead] = []
    risk_count_before: int = 0
    risk_count_after: int = 0
    retired_count: int = 0
    basis: list[str] = []
    warnings: list[str] = []


class RoiSummary(BaseModel):
    id: int
    plan_id: int
    plan_name: str
    scope_id: int
    name: str
    note: str | None = None
    percentile: float
    seed_shared: bool
    before_run_id: int
    after_run_id: int
    #: ``pending`` / ``ready`` / ``failed``, derived from the two runs rather than stored.
    #: A stored status would need updating when a worker finishes and would be wrong in
    #: between.
    status: str
    plan_budget: float
    plan_sched_days: float
    plan_unpriced_count: int
    #: The package has been re-materialised, or a residual edited and re-written, since
    #: this pair was made. The comparison still records what was run; it no longer
    #: describes the package as it now stands.
    stale: bool
    #: An action has been re-costed since the pair was made.
    cost_moved: bool
    created_by: str
    created_at: datetime


class RoiDetail(RoiSummary):
    before: RunSummary | None = None
    after: RunSummary | None = None
    #: Live re-check of comparability. Non-empty on a detail read means something moved
    #: after the pair was created.
    issues: list[str] = []
    #: Current plan cost, for comparison with the snapshot above.
    current_plan_budget: float = 0.0
    current_plan_sched_days: float = 0.0
    comparison: ComparisonRead | None = None


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


async def _get_plan(db: AsyncSession, plan_id: int) -> MitigationPlan:
    plan = await db.get(MitigationPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Mitigation plan not found")
    return plan


async def _get_roi(db: AsyncSession, roi_id: int) -> MitigationRoi:
    row = await db.get(MitigationRoi, roi_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ROI comparison not found")
    return row


def _status(before: SimulationRun | None, after: SimulationRun | None) -> str:
    statuses = {r.status for r in (before, after) if r is not None}
    if "failed" in statuses:
        return "failed"
    if statuses == {"succeeded"}:
        return "ready"
    return "pending"


async def _summary(
    db: AsyncSession,
    row: MitigationRoi,
    *,
    plan: MitigationPlan | None = None,
    before: SimulationRun | None = None,
    after: SimulationRun | None = None,
) -> RoiSummary:
    plan = plan or await _get_plan(db, row.plan_id)
    before = before or await db.get(SimulationRun, row.before_run_id)
    after = after or await db.get(SimulationRun, row.after_run_id)
    cost = await plan_cost(db, plan.id)
    return RoiSummary(
        id=row.id,
        plan_id=row.plan_id,
        plan_name=plan.name,
        scope_id=row.scope_id,
        name=row.name,
        note=row.note,
        percentile=row.percentile,
        seed_shared=bool(row.seed_shared),
        before_run_id=row.before_run_id,
        after_run_id=row.after_run_id,
        status=_status(before, after),
        plan_budget=row.plan_budget,
        plan_sched_days=row.plan_sched_days,
        plan_unpriced_count=row.plan_unpriced_count,
        stale=(
            row.plan_fingerprint is not None
            and plan.materialized_fingerprint is not None
            and row.plan_fingerprint != plan.materialized_fingerprint
        ),
        cost_moved=abs(cost.total_budget - row.plan_budget) > 1e-9
        or abs(cost.total_sched_days - row.plan_sched_days) > 1e-9,
        created_by=row.created_by,
        created_at=row.created_at,
    )


async def _detail(
    db: AsyncSession, row: MitigationRoi, *, percentile: float | None = None
) -> RoiDetail:
    plan = await _get_plan(db, row.plan_id)
    before = await load_run(db, row.before_run_id)
    after = await load_run(db, row.after_run_id)
    summary = await _summary(db, row, plan=plan, before=before, after=after)
    cost = await plan_cost(db, plan.id)

    at = percentile if percentile is not None else row.percentile
    issues = roi_service.pairing_issues(before, after) if before and after else [
        "One of the two runs no longer exists."
    ]

    comparison: ComparisonRead | None = None
    if before is not None and after is not None and not issues:
        report = roi_service.compare(
            before.result_json,
            after.result_json,
            percentile=at,
            plan_budget=row.plan_budget,
            plan_sched_days=row.plan_sched_days,
            plan_unpriced_count=row.plan_unpriced_count,
            seed_shared=bool(row.seed_shared),
        )
        if summary.stale:
            report.warnings.insert(
                0,
                "The package has been re-materialised since this pair was run, so the "
                "residual register these numbers came from is not the one on file now.",
            )
        if summary.cost_moved:
            report.warnings.append(
                "An action has been re-costed since this pair was made. The cost figures "
                "here are the snapshot taken at that time, which is what keeps a quoted "
                "ratio stable; the current cost is reported alongside."
            )
        comparison = ComparisonRead.model_validate(report, from_attributes=True)

    return RoiDetail(
        **summary.model_dump(),
        before=None if before is None else RunSummary.model_validate(before),
        after=None if after is None else RunSummary.model_validate(after),
        issues=issues,
        current_plan_budget=cost.total_budget,
        current_plan_sched_days=cost.total_sched_days,
        comparison=comparison,
    )


# --------------------------------------------------------------------------------------
# creating a pair
# --------------------------------------------------------------------------------------


@router.post("/plans/{plan_id}/runs", response_model=RoiDetail, status_code=201)
async def launch_pair(
    plan_id: int,
    payload: PairRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> RoiDetail:
    """Start a baseline run and a treated run from one set of settings.

    Both assemblies are attempted before either row is written. A pair whose second half
    cannot be assembled would otherwise leave a lone baseline run in the history claiming
    to be half of a comparison that never existed.
    """
    plan = await _get_plan(db, plan_id)
    if plan.materialized_at is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This package has not been materialised, so there is no residual register "
                "to simulate. Materialise it from the Mitigate screen first."
            ),
        )

    scope_ids = await descendant_ids(db, plan.scope_id)

    # Dry assembly of both halves. ``assemble`` raises rather than returning a failure,
    # and its exception handlers already turn it into a 422 naming what to fix.
    from app.services.sim_assembly import assemble as _assemble_check

    for scenario in (BASELINE_SCENARIO, TREATED_SCENARIO):
        await _assemble_check(
            db,
            config=payload.as_run(scenario, "check").to_config(),
            scenario=scenario,
            version_id=payload.schedule_version_id,
            gate_override=payload.gate_override,
            scope_ids=scope_ids,
        )

    before = await start_run(
        db,
        payload.as_run(BASELINE_SCENARIO, "baseline"),
        scope_id=plan.scope_id,
        actor=actor,
    )
    after = await start_run(
        db,
        payload.as_run(TREATED_SCENARIO, "treated"),
        scope_id=plan.scope_id,
        actor=actor,
    )

    cost = await plan_cost(db, plan.id)
    row = MitigationRoi(
        plan_id=plan.id,
        scope_id=plan.scope_id,
        before_run_id=before.id,
        after_run_id=after.id,
        name=payload.name or plan.name,
        note=payload.note,
        percentile=payload.percentile,
        seed_shared=True,
        plan_fingerprint=plan.materialized_fingerprint,
        plan_budget=cost.total_budget,
        plan_sched_days=cost.total_sched_days,
        plan_unpriced_count=cost.unpriced_count,
        created_by=actor,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return await _detail(db, row)


@router.post("", response_model=RoiDetail, status_code=201)
async def pair_existing(
    payload: PairExisting,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> RoiDetail:
    """Pair two runs that already exist, or explain field by field why they cannot be."""
    plan = await _get_plan(db, payload.plan_id)
    before = await db.get(SimulationRun, payload.before_run_id)
    after = await db.get(SimulationRun, payload.after_run_id)
    if before is None or after is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")

    scope_ids = set(await descendant_ids(db, plan.scope_id))
    outside = [
        run.id for run in (before, after) if run.scope_id not in scope_ids
    ]
    if outside:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Run(s) {', '.join(str(i) for i in outside)} were computed for a "
                "different project from this package. Subtracting them would compare two "
                "unrelated registers."
            ),
        )

    issues = roi_service.pairing_issues(before, after)
    if issues:
        raise HTTPException(status_code=422, detail=" ".join(issues))

    existing = await db.scalar(
        select(MitigationRoi).where(
            MitigationRoi.plan_id == plan.id,
            MitigationRoi.before_run_id == before.id,
            MitigationRoi.after_run_id == after.id,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"These two runs are already paired against this package as comparison "
                f"{existing.id}. The difference between two immutable runs does not change."
            ),
        )

    cost = await plan_cost(db, plan.id)
    row = MitigationRoi(
        plan_id=plan.id,
        scope_id=plan.scope_id,
        before_run_id=before.id,
        after_run_id=after.id,
        name=payload.name or plan.name,
        note=payload.note,
        percentile=payload.percentile,
        # Only true when the runs actually agree. Nothing else in the system can tell,
        # and the comparison's own basis note depends on it.
        seed_shared=before.seed == after.seed,
        plan_fingerprint=plan.materialized_fingerprint,
        plan_budget=cost.total_budget,
        plan_sched_days=cost.total_sched_days,
        plan_unpriced_count=cost.unpriced_count,
        created_by=actor,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return await _detail(db, row)


# --------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------


@router.get("", response_model=list[RoiSummary])
async def list_comparisons(
    db: AsyncSession = Depends(get_db),
    plan_id: int | None = Query(default=None),
    scope_id: int | None = Query(
        default=None,
        description="Restrict to this scope and everything under it. Omitted means unfiltered.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[RoiSummary]:
    query = select(MitigationRoi).order_by(
        MitigationRoi.created_at.desc(), MitigationRoi.id.desc()
    )
    if plan_id is not None:
        query = query.where(MitigationRoi.plan_id == plan_id)
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        query = query.where(MitigationRoi.scope_id.in_(scope_ids))
    rows = list((await db.scalars(query.limit(limit))).all())
    return [await _summary(db, row) for row in rows]


@router.get("/{roi_id}", response_model=RoiDetail)
async def get_comparison(
    roi_id: int,
    db: AsyncSession = Depends(get_db),
    percentile: float | None = Query(
        default=None,
        gt=0.0,
        lt=100.0,
        description=(
            "Read the headline at a different percentile. Must be one the runs computed; "
            "the grid is fixed at run time."
        ),
    ),
) -> RoiDetail:
    return await _detail(db, await _get_roi(db, roi_id), percentile=percentile)
