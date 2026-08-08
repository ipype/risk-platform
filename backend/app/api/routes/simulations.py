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

The one exception is ``POST /simulations/{id}/cancel``, and it is not a PATCH in
disguise: it acts only on a run still sitting in ``queued``, before anything has come
back, and it records rather than erases — who withdrew the run and when, next to what was
asked. A run that has started, or that already carries a result, is exactly as immutable
as the paragraph above says; the cancel route refuses anything that is not ``queued``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.mapping import RiskActivityMapping
from app.models.quant import RiskQuantEstimate
from app.models.risk import Risk
from app.models.schedule import DcmaRun, ScheduleFile, ScheduleVersion
from app.models.simulation import SimulationRun
from app.services import quant_validation as qv
from app.core.errors import SimulationRunNotCancellable
from app.services.scope import descendant_ids, resolve_read_scope, resolve_write_scope
from app.services.sim_assembly import Assembly, assemble, latest_dcma
from app.services.sim_calendars import version_window
from app.services.sim_dispatch import dispatch, revoke
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
    cancelled_by: str | None
    cancelled_at: datetime | None

    model_config = {"from_attributes": True}


class RunDetail(RunSummary):
    gate_override_reason: str | None = None
    excluded: list = []
    assembly_notes: list = []
    #: Day zero of the simulated network — the date every ``finish_day`` in the result is
    #: an offset from. Resolved at read time from the schedule version rather than stored
    #: on the run: the version is append-only, so the answer cannot move, and a column
    #: would be a second copy of a fact that already has an owner. ``None`` on a cost-only
    #: run, and on a run whose schedule version has since been deleted — in both cases the
    #: day numbers are still exact and only the calendar rendering is unavailable.
    schedule_start_date: date | None = None
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
async def options(
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(default=None, description="Restrict to this scope and everything under it. Omitted means unfiltered."),
) -> OptionsResponse:
    """Everything the run form needs to render, in one request.

    Assembled here rather than left to the client to stitch from four endpoints, because
    the interesting part is the join: which versions have passed the gate, and how many
    accepted mappings each one carries. A version with a green gate and no mappings runs
    fine and tells you nothing about schedule risk.
    """
    scope_ids = await resolve_read_scope(db, scope_id)

    estimate_counts = (
        select(RiskQuantEstimate.scenario, func.count())
        .join(Risk, Risk.id == RiskQuantEstimate.risk_id)
        .group_by(RiskQuantEstimate.scenario)
    )
    if scope_ids is not None:
        estimate_counts = estimate_counts.where(Risk.scope_id.in_(scope_ids))
    counts = dict((await db.execute(estimate_counts)).all())
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

    version_query = select(ScheduleVersion).order_by(
        ScheduleVersion.is_current.desc(), ScheduleVersion.created_at.desc()
    )
    if scope_ids is not None:
        version_query = version_query.join(
            ScheduleFile, ScheduleFile.id == ScheduleVersion.file_id
        ).where(ScheduleFile.scope_id.in_(scope_ids))

    versions: list[VersionOption] = []
    for version in (await db.scalars(version_query)).all():
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


async def _assemble(
    db: AsyncSession, payload: RunRequest, scope_ids: list[int]
) -> Assembly:
    return await assemble(
        db,
        config=payload.to_config(),
        scenario=payload.scenario,
        version_id=payload.schedule_version_id,
        gate_override=payload.gate_override,
        scope_ids=scope_ids,
    )


async def run_scope_ids(db: AsyncSession, scope_id: int | None) -> list[int]:
    """The register a run reads: the project it belongs to, and nothing else.

    Resolved through ``resolve_write_scope`` rather than ``resolve_read_scope`` because a
    run is *authored* against a project — the same rule that decides where the row lands
    has to decide what it read, or the two can disagree. A project has no children, so
    the list is one id; going through ``descendant_ids`` anyway means a future scope kind
    beneath project would not need this call site changed.
    """
    scope = await resolve_write_scope(db, scope_id)
    return await descendant_ids(db, scope.id)


@router.post("/preview", response_model=PreviewResponse)
async def preview(
    payload: RunRequest,
    db: AsyncSession = Depends(get_db),
    scope_id: int | None = Query(
        default=None,
        description="Project this run would belong to. Omitted means the default project.",
    ),
) -> PreviewResponse:
    """Deliberately takes the same scope as ``POST /simulations``.

    A preview that read a wider register than the run would is worse than no preview: the
    risk count, the exclusions and the fingerprint would all describe a different run from
    the one the button starts.
    """
    assembly = await _assemble(db, payload, await run_scope_ids(db, scope_id))
    return PreviewResponse(
        risk_count=assembly.risk_count,
        mapped_risk_count=assembly.mapped_risk_count,
        activity_count=assembly.activity_count,
        excluded=assembly.excluded,
        notes=assembly.notes,
        gate=_gate_view(assembly.dcma),
        inputs_sha256=assembly.request.fingerprint(),
    )


async def start_run(
    db: AsyncSession, payload: RunRequest, *, scope_id: int, actor: str
) -> SimulationRun:
    """Assemble, persist and queue one run. The assembly happens before the row is written.

    A run that could never have been assembled is not a failed run, it is a rejected
    request: writing it down would fill the history with rows that never had inputs.

    Shared with the ROI routes rather than duplicated there. A matched pair is two runs
    that differ in exactly one field, and the cheapest way to keep that true is for both
    of them to be born in the same function.
    """
    assembly = await _assemble(db, payload, await descendant_ids(db, scope_id))

    run = SimulationRun(
        scope_id=scope_id,
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
    return await load_run(db, run.id) or run


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
    scope = await resolve_write_scope(db, scope_id)
    run = await start_run(db, payload, scope_id=scope.id, actor=actor)
    return run_detail(run, await day_zero(db, run))


# --------------------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------------------


def run_detail(run: SimulationRun, schedule_start_date: date | None = None) -> RunDetail:
    return RunDetail(
        **RunSummary.model_validate(run).model_dump(),
        gate_override_reason=run.gate_override_reason,
        excluded=list(run.excluded or []),
        assembly_notes=list(run.assembly_notes or []),
        schedule_start_date=schedule_start_date,
        result=run.result_json,
    )


async def day_zero(db: AsyncSession, run: SimulationRun) -> date | None:
    """The date the run's day numbers count from, or ``None`` if there is no network.

    Goes through ``version_window`` rather than reading ``ScheduleVersion.data_date``
    directly, because a schedule that parsed without a data date still simulated — off the
    earliest activity start — and reading the column alone would return no date for a run
    that has perfectly good ones.
    """
    if run.schedule_version_id is None:
        return None
    start, _ = await version_window(db, run.schedule_version_id)
    return start


@router.get("", response_model=list[RunSummary])
async def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    scope_id: int | None = Query(default=None, description="Restrict to this scope and everything under it. Omitted means unfiltered."),
    db: AsyncSession = Depends(get_db),
) -> list[SimulationRun]:
    """Newest first, without the payloads.

    ``request_json`` and ``result_json`` are deferred on the model, so this query never
    touches them — a list of fifty runs would otherwise drag tens of megabytes behind it.
    """
    query = select(SimulationRun).order_by(SimulationRun.created_at.desc(), SimulationRun.id.desc())
    if status:
        query = query.where(SimulationRun.status == status)
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        query = query.where(SimulationRun.scope_id.in_(scope_ids))
    return list((await db.scalars(query.limit(limit))).all())


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)) -> RunDetail:
    run = await load_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    return run_detail(run, await day_zero(db, run))


@router.post("/{run_id}/cancel", response_model=RunDetail)
async def cancel_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    actor: str = Header(default="Unknown", alias="X-Actor"),
) -> RunDetail:
    """Withdraw a run that is still ``queued`` — most often one a dead or missing worker
    was never going to claim.

    Not a DELETE, and not available once a run leaves ``queued``: see the module
    docstring and invariant 5. A run already ``running`` has a worker to signal, not a
    queue entry to drop, and is out of scope here on purpose; a terminal run has nothing
    left to stop.
    """
    run = await load_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Simulation run not found")
    if run.status != "queued":
        raise SimulationRunNotCancellable(run_id, run.status)

    await revoke(run)
    run.status = "cancelled"
    run.cancelled_by = actor
    run.cancelled_at = datetime.now(timezone.utc)
    run.assembly_notes = [
        *(run.assembly_notes or []),
        f"Cancelled by {actor} before a worker claimed it.",
    ]
    await db.commit()
    fresh = await load_run(db, run_id) or run
    return run_detail(fresh, await day_zero(db, fresh))
