"""Monte Carlo run routes.

Four verbs and a preview. The preview is not a convenience: assembling a run is where
every refusal lives — the calendar-day check, the one-calendar check, the DCMA gate, the
per-risk exclusions — and an analyst finding out about them by starting a ten-minute run
and reading a failure afterwards would stop using the preview screen and start guessing.
``POST /simulations/preview`` does exactly what ``POST /simulations`` does up to the point
of writing a row.

Runs are append-only (invariant 5). There is no PATCH and no DELETE: a run is what was
asked and what came back, and changing either after the fact is how a contingency number
stops being defensible. Re-running writes a new row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.mapping import RiskActivityMapping
from app.models.quant import RiskQuantEstimate
from app.models.schedule import DcmaRun, ScheduleVersion
from app.models.simulation import SimulationRun
from app.services import quant_validation as qv
from app.services.scope import resolve_write_scope
from app.services.sim_assembly import Assembly, assemble, latest_dcma
from app.services.sim_dispatch import dispatch
from app.services.sim_execute import load_run
from app.sim import RunConfig

router = APIRouter(prefix="/simulations", tags=["simulations"])


# --------------------------------------------------------------------------------------
# payloads
# --------------------------------------------------------------------------------------


class RunRequest(BaseModel):
    """What the run form sends.

    Deliberately not the engine's :class:`~app.sim.inputs.RunConfig` — that model carries
    memory budgets, chunk sizes and sensitivity caps which are engine tuning, not analyst
    decisions. The fields here are the ones somebody in a workshop has an opinion about.
    """

    name: str = Field(default="", max_length=200)
    scenario: str = "pre_mitigation"
    schedule_version_id: int | None = None

    iterations: int = Field(default=10_000, ge=100, le=1_000_000)
    seed: int = Field(default=12345, ge=0)
    sampling: Literal["lhs", "mc"] = "lhs"

    base_cost: float = Field(default=0.0, ge=0.0)
    #: Extended overheads per day of delay. Multiplied by the delay *inside* each
    #: iteration and never against a percentile (invariant 1).
    burn_rate_per_day: float = Field(default=0.0, ge=0.0)
    allow_negative_delay_credit: bool = False

    correlate_occurrence: bool = True
    intra_risk_cost_sched_correlation: float = Field(default=0.0, ge=-1.0, le=1.0)

    gate_override: bool = False
    gate_override_reason: str | None = None

    @model_validator(mode="after")
    def _check(self) -> RunRequest:
        if self.gate_override and not (self.gate_override_reason or "").strip():
            raise ValueError(
                "Overriding the DCMA gate needs a reason. It is recorded on the run and "
                "travels with every number the run produces."
            )
        if self.burn_rate_per_day > 0 and self.schedule_version_id is None:
            raise ValueError(
                "A burn rate prices schedule delay, and without a schedule there is no "
                "delay to price. Select a schedule version or set the burn rate to zero."
            )
        return self

    def to_config(self) -> RunConfig:
        return RunConfig(
            iterations=self.iterations,
            seed=self.seed,
            sampling=self.sampling,
            base_cost=self.base_cost,
            burn_rate_per_day=self.burn_rate_per_day,
            allow_negative_delay_credit=self.allow_negative_delay_credit,
            correlate_occurrence=self.correlate_occurrence,
            intra_risk_cost_sched_correlation=self.intra_risk_cost_sched_correlation,
        )


class GateView(BaseModel):
    assessed: bool
    passed: bool | None = None
    failed_count: int | None = None
    run_at: datetime | None = None
    blocking_failures: list = []


class PreviewResponse(BaseModel):
    """What a run would contain, without spending the CPU to find out."""

    risk_count: int
    mapped_risk_count: int
    activity_count: int
    excluded: list[dict]
    notes: list[str]
    gate: GateView
    #: The fingerprint the run would carry. Identical to an earlier run's means identical
    #: inputs, which is the cheapest way to notice nothing has actually changed.
    inputs_sha256: str


class VersionOption(BaseModel):
    id: int
    project_name: str
    source_project_id: str
    is_current: bool
    activity_count: int
    relationship_count: int
    created_at: datetime
    data_date: datetime | None = None
    gate: GateView
    accepted_mappings: int


class ScenarioOption(BaseModel):
    value: str
    label: str
    estimate_count: int


class OptionsResponse(BaseModel):
    scenarios: list[ScenarioOption]
    schedule_versions: list[VersionOption]
    defaults: dict[str, Any]


class RunSummary(BaseModel):
    id: int
    name: str
    status: str
    scenario: str
    schedule_version_id: int | None
    iterations: int
    seed: int
    sampling: str
    base_cost: float
    burn_rate_per_day: float
    risk_count: int
    mapped_risk_count: int
    activity_count: int
    engine_version: str | None
    inputs_sha256: str | None
    gate_passed: bool | None
    gate_override: bool
    created_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    error: str | None

    model_config = {"from_attributes": True}


class RunDetail(RunSummary):
    gate_override_reason: str | None = None
    excluded: list = []
    assembly_notes: list = []
    #: The engine's ``SimulationResult``, serialised whole. Not re-declared field by field
    #: here: the engine owns that shape, and a second declaration would drift from it.
    result: dict | None = None


# --------------------------------------------------------------------------------------
# options
# --------------------------------------------------------------------------------------


def _gate_view(dcma: DcmaRun | None) -> GateView:
    if dcma is None:
        return GateView(assessed=False)
    return GateView(
        assessed=True,
        passed=bool(dcma.gate_passed),
        failed_count=dcma.failed_count,
        run_at=dcma.created_at,
        blocking_failures=list(dcma.blocking_failures or []),
    )


@router.get("/options", response_model=OptionsResponse)
async def options(db: AsyncSession = Depends(get_db)) -> OptionsResponse:
    """Everything the run form needs to render, in one request.

    Assembled here rather than left to the client to stitch from four endpoints, because
    the interesting part is the join: which versions have passed the gate, and how many
    accepted mappings each one carries. A version with a green gate and no mappings runs
    fine and tells you nothing about schedule risk.
    """
    counts = dict(
        (
            await db.execute(
                select(RiskQuantEstimate.scenario, func.count())
                .group_by(RiskQuantEstimate.scenario)
            )
        ).all()
    )
    scenarios = [
        ScenarioOption(
            value=value,
            label=value.replace("_", " ").capitalize(),
            estimate_count=int(counts.get(value, 0)),
        )
        for value in qv.SCENARIOS
    ]

    mapping_counts = dict(
        (
            await db.execute(
                select(RiskActivityMapping.version_id, func.count())
                .where(RiskActivityMapping.status == "accepted")
                .group_by(RiskActivityMapping.version_id)
            )
        ).all()
    )

    versions: list[VersionOption] = []
    for version in (
        await db.scalars(
            select(ScheduleVersion).order_by(
                ScheduleVersion.is_current.desc(), ScheduleVersion.created_at.desc()
            )
        )
    ).all():
        versions.append(
            VersionOption(
                id=version.id,
                project_name=version.project_name,
                source_project_id=version.source_project_id,
                is_current=bool(version.is_current),
                activity_count=version.activity_count,
                relationship_count=version.relationship_count,
                created_at=version.created_at,
                data_date=version.data_date,
                gate=_gate_view(await latest_dcma(db, version.id)),
                accepted_mappings=int(mapping_counts.get(version.id, 0)),
            )
        )

    return OptionsResponse(
        scenarios=scenarios,
        schedule_versions=versions,
        defaults={
            "iterations": 10_000,
            "seed": 12345,
            "sampling": "lhs",
            "scenario": "pre_mitigation",
        },
    )


# --------------------------------------------------------------------------------------
# preview and run
# --------------------------------------------------------------------------------------


async def _assemble(db: AsyncSession, payload: RunRequest) -> Assembly:
    return await assemble(
        db,
        config=payload.to_config(),
        scenario=payload.scenario,
        version_id=payload.schedule_version_id,
        gate_override=payload.gate_override,
    )


@router.post("/preview", response_model=PreviewResponse)
async def preview(
    payload: RunRequest, db: AsyncSession = Depends(get_db)
) -> PreviewResponse:
    assembly = await _assemble(db, payload)
    return PreviewResponse(
        risk_count=assembly.risk_count,
        mapped_risk_count=assembly.mapped_risk_count,
        activity_count=assembly.activity_count,
        excluded=assembly.excluded,
        notes=assembly.notes,
        gate=_gate_view(assembly.dcma),
        inputs_sha256=assembly.request.fingerprint(),
    )


@router.post("", response_model=RunDetail, status_code=201)
async def create_run(
    payload: RunRequest,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
    scope_id: int | None = Query(
        default=None,
        description="Project this run belongs to. Omitted means the default project.",
    ),
) -> RunDetail:
    """Assemble, persist and queue. The assembly happens before the row is written.

    A run that could never have been assembled is not a failed run, it is a rejected
    request: writing it down would fill the history with rows that never had inputs.
    """
    scope = await resolve_write_scope(db, scope_id)
    assembly = await _assemble(db, payload)

    run = SimulationRun(
        scope_id=scope.id,
        name=payload.name or "",
        status="queued",
        scenario=payload.scenario,
        schedule_version_id=payload.schedule_version_id,
        dcma_run_id=None if assembly.dcma is None else assembly.dcma.id,
        gate_passed=None if assembly.dcma is None else bool(assembly.dcma.gate_passed),
        gate_override=payload.gate_override,
        gate_override_reason=payload.gate_override_reason,
        iterations=payload.iterations,
        seed=payload.seed,
        sampling=payload.sampling,
        base_cost=payload.base_cost,
        burn_rate_per_day=payload.burn_rate_per_day,
        risk_count=assembly.risk_count,
        mapped_risk_count=assembly.mapped_risk_count,
        activity_count=assembly.activity_count,
        excluded=assembly.excluded,
        assembly_notes=assembly.notes,
        inputs_sha256=assembly.request.fingerprint(),
        request_json=assembly.request_without_schedule(),
        created_by=actor,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    await dispatch(db, run)
    # Reloaded rather than refreshed: ``refresh`` leaves the deferred payloads unloaded,
    # and the eager path has just written a result into one of them.
    fresh = await load_run(db, run.id)
    return _detail(fresh or run)


# --------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------


def _detail(run: SimulationRun) -> RunDetail:
    return RunDetail(
        **RunSummary.model_validate(run).model_dump(),
        gate_override_reason=run.gate_override_reason,
        excluded=list(run.excluded or []),
        assembly_notes=list(run.assembly_notes or []),
        result=run.result_json,
    )


@router.get("", response_model=list[RunSummary])
async def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[SimulationRun]:
    """Newest first, without the payloads.

    ``request_json`` and ``result_json`` are deferred on the model, so this query never
    touches them — a list of fifty runs would otherwise drag tens of megabytes behind it.
    """
    query = select(SimulationRun).order_by(SimulationRun.created_at.desc(), SimulationRun.id.desc())
    if status:
        query = query.where(SimulationRun.status == status)
    return list((await db.scalars(query.limit(limit))).all())


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)) -> RunDetail:
    run = await load_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    return _detail(run)
