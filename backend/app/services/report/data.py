"""Everything a report is built from, read once and frozen.

The split this file exists to enforce: **the database is read here and nowhere else.**
Section builders in ``sections.py`` are pure functions of :class:`ReportData`, which means
they can be tested by constructing a snapshot by hand — no engine, no SQLite, no fixture
that takes four seconds to reach the assertion. It is the same boundary ``app/sim`` keeps
and for the same reason.

Two decisions worth stating, because both are the kind that reads as an oversight later:

**The run owns the scope.** When a run is named, the register, the matrix and the actions
are read for *that run's* project, and any ``scope_id`` the caller also sent is ignored.
A report whose contingency came from project A and whose register came from the portfolio
above it is internally inconsistent in a way no reader can see, so the combination is not
offered.

**A result that will not parse is a finding, not a crash.** ``result_json`` is a
``SimulationResult`` this app serialised, so it should always validate; if a schema moved
under an archived run it must still be possible to print the run's basis and say plainly
that the numbers could not be read.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.matrix import get_active_config
from app.models.mitigation import MitigationAction, MitigationPlan
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.roi import MitigationRoi
from app.models.scope import ScopeNode
from app.models.simulation import SimulationRun
from app.services import roi as roi_service
from app.services.matrix_export import (
    OVERALL,
    build_grid,
    basis_label,
    placement_for,
    valid_lens,
)
from app.services.mitigation_plan import plan_cost
from app.services.report.model import (
    MatrixBand,
    MatrixCell,
    MatrixLevel,
)
from app.services.scope import resolve_read_scope
from app.services.sim_execute import load_run
from app.sim.engine import SimulationResult

__all__ = [
    "ActionFacts",
    "MatrixFacts",
    "PlanFacts",
    "ReductionFacts",
    "ReportData",
    "RiskFacts",
    "RiskMoverFacts",
    "RoiFacts",
    "RunFacts",
    "ScopeFacts",
    "SeriesReductionFacts",
    "gather",
]


class ScopeFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    kind: str
    name: str
    code: str | None = None
    description: str | None = None
    #: Root first, this node last. What the cover page prints so a reader knows whether
    #: they are holding a project or a programme rollup.
    path: tuple[str, ...] = ()


class RiskFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    code: str
    title: str
    category: str = ""
    subcategory: str = ""
    status: str = ""
    owner: str | None = None
    probability: int | None = None
    impact: int | None = None
    score: int | None = None
    band: str | None = None
    band_color: str | None = None
    target_score: int | None = None
    target_band: str | None = None
    #: Carries at least one quantitative estimate. Not the same as "was simulated" — the
    #: run's own exclusion list says that, and the gap between the two is a finding.
    quantified: bool = False


class MatrixFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    lens_label: str
    basis_label: str
    config_name: str = ""
    probability_levels: tuple[MatrixLevel, ...] = ()
    impact_levels: tuple[MatrixLevel, ...] = ()
    cells: tuple[MatrixCell, ...] = ()
    bands: tuple[MatrixBand, ...] = ()
    placed: int = 0
    unplaced: int = 0
    off_scale: int = 0


class RunFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str = ""
    status: str = ""
    scenario: str = ""
    created_by: str = ""
    created_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None

    iterations: int = 0
    seed: int = 0
    sampling: str = ""
    engine_version: str | None = None
    chunk_size: int | None = None
    inputs_sha256: str | None = None

    base_cost: float = 0.0
    burn_rate_per_day: float = 0.0
    risk_count: int = 0
    mapped_risk_count: int = 0
    activity_count: int = 0

    schedule_version_id: int | None = None
    gate_passed: bool | None = None
    gate_override: bool = False
    gate_override_reason: str | None = None

    #: ``risk_id`` / ``risk_code`` / ``title`` / ``reason`` per item.
    excluded: tuple[dict, ...] = ()
    assembly_notes: tuple[str, ...] = ()

    #: ``None`` when the stored result could not be read back. ``result_error`` says why.
    result: SimulationResult | None = None
    result_error: str | None = None


class ActionFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    risk_code: str = ""
    risk_title: str = ""
    action: str = ""
    owner: str | None = None
    due_date: date | None = None
    budget: float | None = None
    sched_days: float | None = None
    completion_pct: int | None = None
    effectiveness: str | None = None
    status: str = ""


class PlanFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str = ""
    status: str = ""
    materialized_at: datetime | None = None
    materialized_by: str | None = None
    materialized_risk_count: int | None = None
    materialized_retired_count: int | None = None
    action_count: int = 0
    costed_count: int = 0
    unpriced_count: int = 0
    total_budget: float = 0.0
    total_sched_days: float = 0.0


class ReductionFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    before: float | None = None
    after: float | None = None
    reduction: float | None = None
    reduction_pct: float | None = None


class SeriesReductionFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    units: str
    mean: ReductionFacts
    at_percentile: ReductionFacts
    standard_error: float | None = None
    within_noise: bool = False


class RiskMoverFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str = ""
    title: str = ""
    movement: str = ""
    contribution_before: float | None = None
    contribution_after: float | None = None
    contribution_reduction: float | None = None
    rank_before: int | None = None
    rank_after: int | None = None


class RoiFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str = ""
    plan_id: int
    percentile: float = 80.0
    seed_shared: bool = True
    before_run_id: int
    after_run_id: int
    status: str = ""
    stale: bool = False
    cost_moved: bool = False
    #: Non-empty means the pair is no longer comparable and nothing below it should be
    #: quoted. The section prints these instead of the numbers, not alongside them.
    issues: tuple[str, ...] = ()

    contingency: SeriesReductionFacts | None = None
    delay_days: SeriesReductionFacts | None = None
    plan_budget: float = 0.0
    plan_sched_days: float = 0.0
    benefit_cost_ratio: float | None = None
    net_at_percentile: float | None = None
    retired_count: int = 0
    risk_movers: tuple[RiskMoverFacts, ...] = ()
    basis: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ReportData(BaseModel):
    """One report's worth of facts. Everything below this line is a pure function of it."""

    model_config = ConfigDict(frozen=True)

    title: str
    subtitle: str = ""
    prepared_by: str = ""
    currency: str = ""
    generated_on: date

    scope: ScopeFacts | None = None
    risks: tuple[RiskFacts, ...] = ()
    matrix: MatrixFacts | None = None
    run: RunFacts | None = None
    actions: tuple[ActionFacts, ...] = ()
    plan: PlanFacts | None = None
    roi: RoiFacts | None = None

    #: Findings from the read itself — a scope that was overridden, a result that would
    #: not parse. Printed in the basis section, never swallowed.
    notes: tuple[str, ...] = ()

    @property
    def has_result(self) -> bool:
        return self.run is not None and self.run.result is not None

    @property
    def has_schedule(self) -> bool:
        return (
            self.run is not None
            and self.run.result is not None
            and self.run.result.delay_days is not None
        )


# --------------------------------------------------------------------------------------
# gather
# --------------------------------------------------------------------------------------


async def _scope_facts(db: AsyncSession, scope_id: int | None) -> ScopeFacts | None:
    if scope_id is None:
        return None
    node = await db.get(ScopeNode, scope_id)
    if node is None:
        return None

    path: list[str] = [node.name]
    seen = {node.id}
    cursor = node
    while cursor.parent_id is not None and cursor.parent_id not in seen:
        parent = await db.get(ScopeNode, cursor.parent_id)
        if parent is None:
            break
        seen.add(parent.id)
        path.append(parent.name)
        cursor = parent

    return ScopeFacts(
        id=node.id,
        kind=node.kind,
        name=node.name,
        code=node.code,
        description=node.description,
        path=tuple(reversed(path)),
    )


async def _run_facts(db: AsyncSession, run: SimulationRun) -> RunFacts:
    result: SimulationResult | None = None
    result_error: str | None = None
    if run.result_json:
        try:
            result = SimulationResult.model_validate(run.result_json)
        except Exception as exc:  # noqa: BLE001 — the reason is printed, not swallowed
            result_error = f"The stored result could not be read back: {exc}"
    elif run.status == "succeeded":
        result_error = "The run succeeded but carries no stored result."

    notes = tuple(str(n) for n in (run.assembly_notes or []))
    excluded = tuple(dict(item) for item in (run.excluded or []) if isinstance(item, dict))

    return RunFacts(
        id=run.id,
        name=run.name or f"Run {run.id}",
        status=run.status,
        scenario=run.scenario,
        created_by=run.created_by,
        created_at=run.created_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        iterations=run.iterations,
        seed=run.seed,
        sampling=run.sampling,
        engine_version=run.engine_version,
        chunk_size=run.chunk_size,
        inputs_sha256=run.inputs_sha256,
        base_cost=run.base_cost,
        burn_rate_per_day=run.burn_rate_per_day,
        risk_count=run.risk_count,
        mapped_risk_count=run.mapped_risk_count,
        activity_count=run.activity_count,
        schedule_version_id=run.schedule_version_id,
        gate_passed=run.gate_passed,
        gate_override=bool(run.gate_override),
        gate_override_reason=run.gate_override_reason,
        excluded=excluded,
        assembly_notes=notes,
        result=result,
        result_error=result_error,
    )


async def _register(
    db: AsyncSession,
    scope_ids: list[int] | None,
    config: dict,
    lens: str,
    basis: str,
) -> tuple[tuple[RiskFacts, ...], MatrixFacts]:
    stmt = (
        select(Risk, RbsSubcategory, RbsCategory)
        .join(RbsSubcategory, RbsSubcategory.id == Risk.subcategory_id)
        .join(RbsCategory, RbsCategory.id == RbsSubcategory.category_id)
        .order_by(Risk.risk_code)
    )
    if scope_ids is not None:
        stmt = stmt.where(Risk.scope_id.in_(scope_ids))
    rows = list((await db.execute(stmt)).all())

    quantified: set[int] = set()
    if rows:
        from app.models.quant import RiskQuantEstimate

        ids = [risk.id for risk, _sub, _cat in rows]
        quantified = set(
            (
                await db.scalars(
                    select(RiskQuantEstimate.risk_id).where(
                        RiskQuantEstimate.risk_id.in_(ids)
                    )
                )
            ).all()
        )

    band_color = {
        band["name"]: band["color"] for band in config.get("bands", []) if band.get("name")
    }

    placements = []
    facts: list[RiskFacts] = []
    for risk, subcat, cat in rows:
        placement = placement_for(risk, config, lens=lens, basis=basis, category=cat.name)
        target = placement_for(risk, config, lens=lens, basis="target", category=cat.name)
        placements.append(placement)
        facts.append(
            RiskFacts(
                id=risk.id,
                code=risk.risk_code,
                title=risk.title,
                category=cat.name,
                subcategory=f"{subcat.code} — {subcat.name}",
                status=risk.status,
                owner=risk.owner,
                probability=placement.probability,
                impact=placement.impact,
                score=placement.score,
                band=placement.band,
                band_color=band_color.get(placement.band or ""),
                target_score=target.score,
                target_band=target.band,
                quantified=risk.id in quantified,
            )
        )

    grid = build_grid(placements, config, lens=lens, basis=basis)
    matrix = MatrixFacts(
        lens_label=grid.lens_label,
        basis_label=basis_label(grid.basis),
        config_name=grid.config_name or "",
        probability_levels=tuple(
            MatrixLevel(level=level["level"], label=level["label"]) for level in grid.rows
        ),
        impact_levels=tuple(
            MatrixLevel(level=level["level"], label=level["label"]) for level in grid.columns
        ),
        cells=tuple(
            MatrixCell(
                probability=cell.probability,
                impact=cell.impact,
                score=cell.score,
                band=cell.band,
                color=cell.color,
                count=cell.count,
                codes=tuple(p.code for p in cell.placements),
            )
            for cell in grid.cells.values()
        ),
        bands=tuple(
            MatrixBand(
                name=band["name"],
                color=band["color"],
                min_score=band["min_score"],
                max_score=band["max_score"],
            )
            for band in config.get("bands", [])
        ),
        placed=len(grid.placed),
        unplaced=len(grid.unplaced),
        off_scale=sum(1 for p in grid.unplaced if p.off_scale),
    )
    return tuple(facts), matrix


async def _actions(db: AsyncSession, scope_ids: list[int] | None) -> tuple[ActionFacts, ...]:
    stmt = (
        select(MitigationAction, Risk)
        .join(Risk, Risk.id == MitigationAction.risk_id)
        .order_by(Risk.risk_code, MitigationAction.sort_order, MitigationAction.id)
    )
    if scope_ids is not None:
        stmt = stmt.where(Risk.scope_id.in_(scope_ids))
    rows = (await db.execute(stmt)).all()
    return tuple(
        ActionFacts(
            risk_code=risk.risk_code,
            risk_title=risk.title,
            action=action.action or "",
            owner=action.owner,
            due_date=action.due_date,
            budget=action.budget,
            sched_days=action.sched_days,
            completion_pct=action.completion_pct,
            effectiveness=action.effectiveness,
            status=action.status,
        )
        for action, risk in rows
    )


async def _plan_facts(db: AsyncSession, plan: MitigationPlan) -> PlanFacts:
    cost = await plan_cost(db, plan.id)
    return PlanFacts(
        id=plan.id,
        name=plan.name,
        status=plan.status,
        materialized_at=plan.materialized_at,
        materialized_by=plan.materialized_by,
        materialized_risk_count=plan.materialized_risk_count,
        materialized_retired_count=plan.materialized_retired_count,
        action_count=cost.action_count,
        costed_count=cost.costed_count,
        unpriced_count=cost.unpriced_count,
        total_budget=cost.total_budget,
        total_sched_days=cost.total_sched_days,
    )


async def _roi_facts(db: AsyncSession, row: MitigationRoi) -> RoiFacts:
    before = await load_run(db, row.before_run_id)
    after = await load_run(db, row.after_run_id)

    if before is None or after is None:
        issues = ["One of the two runs no longer exists."]
    else:
        issues = roi_service.pairing_issues(before, after)

    status = "ready"
    if before is None or after is None:
        status = "failed"
    elif "failed" in (before.status, after.status):
        status = "failed"
    elif before.status != "succeeded" or after.status != "succeeded":
        status = "pending"

    facts = RoiFacts(
        id=row.id,
        name=row.name or f"Comparison {row.id}",
        plan_id=row.plan_id,
        percentile=row.percentile,
        seed_shared=bool(row.seed_shared),
        before_run_id=row.before_run_id,
        after_run_id=row.after_run_id,
        status=status,
        issues=tuple(issues),
    )
    if before is None or after is None or issues or status != "ready":
        return facts

    comparison = roi_service.compare(
        before.result_json,
        after.result_json,
        percentile=row.percentile,
        plan_budget=row.plan_budget,
        plan_sched_days=row.plan_sched_days,
        plan_unpriced_count=row.plan_unpriced_count,
        seed_shared=bool(row.seed_shared),
    )
    return facts.model_copy(
        update={
            "contingency": (
                None
                if comparison.contingency is None
                else SeriesReductionFacts.model_validate(
                    comparison.contingency, from_attributes=True
                )
            ),
            "delay_days": (
                None
                if comparison.delay_days is None
                else SeriesReductionFacts.model_validate(
                    comparison.delay_days, from_attributes=True
                )
            ),
            "plan_budget": comparison.plan_budget,
            "plan_sched_days": comparison.plan_sched_days,
            "benefit_cost_ratio": comparison.benefit_cost_ratio,
            "net_at_percentile": comparison.net_at_percentile,
            "retired_count": comparison.retired_count,
            "risk_movers": tuple(
                RiskMoverFacts.model_validate(mover, from_attributes=True)
                for mover in comparison.risk_movers[:10]
            ),
            "basis": tuple(comparison.basis),
            "warnings": tuple(comparison.warnings),
        }
    )


async def gather(
    db: AsyncSession,
    *,
    title: str,
    prepared_by: str = "",
    currency: str = "",
    generated_on: date,
    run_id: int | None = None,
    scope_id: int | None = None,
    roi_id: int | None = None,
    plan_id: int | None = None,
    lens: str = OVERALL,
    basis: str = "current",
) -> ReportData:
    """Read every fact one report needs, in one pass, against one scope."""
    notes: list[str] = []

    run_facts: RunFacts | None = None
    if run_id is not None:
        run = await load_run(db, run_id)
        if run is None:
            raise LookupError(f"simulation run {run_id} does not exist")
        if scope_id is not None and scope_id != run.scope_id:
            notes.append(
                f"A scope was requested that is not the run's own (asked for {scope_id}, "
                f"run belongs to {run.scope_id}). The run's project was used for the "
                "register and the matrix, so the qualitative and quantitative halves of "
                "this report describe the same set of risks."
            )
        scope_id = run.scope_id
        run_facts = await _run_facts(db, run)
        if run_facts.result_error:
            notes.append(run_facts.result_error)

    scope_ids = await resolve_read_scope(db, scope_id)
    config = await get_active_config(db)
    resolved_lens = valid_lens(lens, config)
    risks, matrix = await _register(db, scope_ids, config, resolved_lens, basis)
    actions = await _actions(db, scope_ids)

    roi_facts: RoiFacts | None = None
    if roi_id is not None:
        roi_row = await db.get(MitigationRoi, roi_id)
        if roi_row is None:
            raise LookupError(f"ROI comparison {roi_id} does not exist")
        roi_facts = await _roi_facts(db, roi_row)
        if plan_id is None:
            plan_id = roi_row.plan_id

    plan_facts: PlanFacts | None = None
    if plan_id is not None:
        plan = await db.get(MitigationPlan, plan_id)
        if plan is None:
            raise LookupError(f"mitigation plan {plan_id} does not exist")
        plan_facts = await _plan_facts(db, plan)

    scope = await _scope_facts(db, scope_id)
    subtitle = " › ".join(scope.path) if scope else "All scopes"

    return ReportData(
        title=title,
        subtitle=subtitle,
        prepared_by=prepared_by,
        currency=currency,
        generated_on=generated_on,
        scope=scope,
        risks=risks,
        matrix=matrix,
        run=run_facts,
        actions=actions,
        plan=plan_facts,
        roi=roi_facts,
        notes=tuple(notes),
    )
