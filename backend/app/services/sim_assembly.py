"""Turning the database into a :class:`~app.sim.inputs.SimulationRequest`.

This is the adapter ``app/sim/__init__.py`` says belongs in ``services``: everything the
engine deliberately cannot do because it may not touch a session. Three jobs, and the
reason each one lives here rather than there:

* **Bound recovery.** ``quant_validation`` widens an elicited P10/P90 pair into the
  absolute support a :class:`~app.sim.distributions.DistributionSpec` promises. By the
  time a spec reaches the engine the numbers mean what they say.
* **Scope resolution.** A ``scoped_driver`` mapping is a filter over a version, and it is
  resolved by ``mapping_suggest.resolve_scope`` — the same function the coverage report
  and the Gantt badges use, so a run can never disagree with the screen about which
  activities a risk covers.
* **Unit enforcement.** Durations reaching the engine are working days on one calendar.
  Inside the package a day is a float with nothing left to check it against, so this is
  the last point at which the question can be asked at all, and it is asked as a refusal
  rather than a warning.

Refusals, in general, are the design here. Three conditions produce a hard failure instead
of a best effort, because each one produces a complete, plausible, entirely wrong number:
a schedule impact elicited in calendar days with no calendar to convert it (the two differ
by roughly 40%), activities measured against more than one calendar, and a network that
never passed the DCMA gate. Everything softer — a mapping whose risk has no schedule
estimate, a relationship pointing outside the parse — degrades to a note carried on the
run, where a reader sees it next to the answer it affected.

The one deliberate middle case is an unsimulable estimate. Blocking the whole run on one
malformed row is hostile; dropping it silently understates the contingency, which is the
expensive direction. So the risk is excluded, named, and reported on the run record and at
the top of the result screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ScheduleGateBlocked, SimulationNotAssemblable
from app.models.mapping import RiskActivityMapping
from app.models.quant import RiskDriver, RiskDriverLink, RiskQuantEstimate
from app.models.risk import Risk
from app.models.schedule import (
    DcmaRun,
    ScheduleActivity,
    ScheduleFile,
    ScheduleRelationship,
    ScheduleVersion,
)
from app.services import quant_validation as qv
from app.services.sim_calendars import CalendarSet, load_calendar_set
from app.services.mapping_service import load_activities
from app.services.mapping_suggest import ActivityRow, resolve_scope
from app.sim import (
    ActivityInput,
    CorrelationInput,
    DistributionSpec,
    DriverSpec,
    PointMass,
    RelationshipInput,
    RiskInput,
    RiskMappingInput,
    RunConfig,
    ScheduleInput,
    SimulationRequest,
    spec_from_moments,
)

#: Relationship types the CPM understands. Anything else is a parse artefact.
_REL_TYPES = ("FS", "SS", "FF", "SF")

#: Only accepted mappings reach a run (invariant 4). A proposal is not a decision.
_LIVE_MAPPING_STATUS = "accepted"


@dataclass
class Assembly:
    """A runnable request plus everything the run record needs to describe itself."""

    request: SimulationRequest
    version: ScheduleVersion | None = None
    dcma: DcmaRun | None = None
    risk_count: int = 0
    mapped_risk_count: int = 0
    activity_count: int = 0
    #: ``[{"risk_id", "risk_code", "title", "reason"}]`` — in the register, not in the run.
    excluded: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def request_without_schedule(self) -> dict:
        """The request as stored on the run row.

        The schedule comes back off ``schedule_version_id`` at replay time. It is left out
        here because ``schedule_version`` is append-only, so the reference is exactly as
        precise as a copy and several megabytes cheaper.
        """
        payload = self.request.model_dump(mode="json")
        payload.pop("schedule", None)
        return payload


# --------------------------------------------------------------------------------------
# distributions
# --------------------------------------------------------------------------------------


def spec_for_dimension(
    dim: qv.DimensionInput, interpretation: str
) -> DistributionSpec | None:
    """One elicited dimension as a sampling shape, or ``None`` if it was not assessed.

    ``interpretation`` is the estimate-level default. Where the dimension carries its own
    override, ``qv.dimension_moments`` prefers it — resolved there rather than here so a
    caller cannot skip the override by passing the session value and getting away with it.

    Point-list shapes are built directly rather than through
    :func:`~app.sim.distributions.spec_from_moments`, which refuses them on purpose: a
    curve cannot be rebuilt from its moments, and pretending otherwise would replace the
    analyst's data with a three-point fit of it.
    """
    if not dim.assessed:
        return None

    if dim.dist in qv.POINT_DISTS:
        points = tuple(
            PointMass(x=float(p["x"]), p=float(p["p"])) for p in (dim.points or [])
        )
        return DistributionSpec(kind=dim.dist, points=points)  # type: ignore[arg-type]

    moments = qv.dimension_moments(dim, interpretation)
    if moments is None:
        return None
    return spec_from_moments(moments)


def _estimate_input(row: RiskQuantEstimate) -> qv.EstimateInput:
    """Mirror of ``routes/quant.py:_to_input``.

    Duplicated rather than imported: importing a route module into a service inverts the
    layering, and the alternative — a shared helper on the model — is a bigger change than
    this delivery earns. Worth collapsing when the elicitation agent needs the same map.

    Every field the validator reads has to appear here. A column added to the route's map
    and forgotten in this one does not fail: it silently simulates something other than
    what the screen showed, which is the single most expensive class of bug this file can
    have.
    """

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


# --------------------------------------------------------------------------------------
# the schedule
# --------------------------------------------------------------------------------------


def _activity_duration(row: ScheduleActivity, meta: ActivityRow | None) -> float:
    """Remaining work in working days.

    Remaining rather than original, because the simulation starts at the data date and
    work already done cannot be at risk. A completed activity is zero and a milestone is
    zero; anything else falls back to the original duration when the parse left remaining
    unset, which some ``.xer`` exports do for not-started work.
    """
    if meta is not None and (meta.is_complete or meta.is_milestone):
        return 0.0
    remaining = row.remaining_duration_days
    if remaining is None:
        remaining = row.original_duration_days
    return max(float(remaining or 0.0), 0.0)


def _min_start_day(row: ScheduleActivity, data_date) -> float | None:
    """A "start on or after" constraint as elapsed days from the data date.

    Only the start-side constraints convert to something the forward pass can use. A
    mandatory finish overrides network logic rather than bounding it, and honouring one
    would need the pass to push work *earlier*, which it cannot do — those are still
    counted into the warning instead.

    Elapsed days, like every other duration reaching the engine, so the constraint and
    the durations it competes with are on one axis.
    """
    kind = (row.constraint_type or "none").lower()
    if not any(k in kind for k in ("start_on_or_after", "start_no_earlier", "must_start")):
        return None
    when = row.constraint_date
    if when is None or data_date is None:
        return None
    offset = (when.date() if hasattr(when, "date") else when) - data_date
    return float(max(offset.days, 0))


async def build_schedule_input(
    db: AsyncSession, version_id: int
) -> tuple[ScheduleInput, list[str], CalendarSet]:
    """The parsed network as the engine wants it, on one calendar.

    Ordered by primary key throughout. The order fixes the variable layout and therefore
    the fingerprint, so a stable sort is part of invariant 6 rather than a tidiness
    preference.
    """
    notes: list[str] = []

    rows = list(
        (
            await db.scalars(
                select(ScheduleActivity)
                .where(ScheduleActivity.version_id == version_id)
                .order_by(ScheduleActivity.id)
            )
        ).all()
    )
    if not rows:
        raise SimulationNotAssemblable(
            [f"Schedule version {version_id} has no parsed activities."]
        )

    # Every duration below is converted from working days on its own calendar to elapsed
    # days, which is the only unit several calendars agree on. See
    # ``app.schedule.calendars`` for why the conversion is a measured density rather than
    # a date walk, and what that costs.
    calendar_set = await load_calendar_set(db, version_id)
    data_date = calendar_set.window_start
    calendars = {r.duration_calendar_id or "" for r in rows}
    unknown = sorted(c for c in calendars if c and calendar_set.get(c) is None)
    if unknown:
        notes.append(
            f"{len(unknown)} calendar(s) referenced by activities were not stored with "
            f"this parse ({', '.join(unknown)}). Their durations were taken as elapsed "
            "days unconverted, which is right for a seven-day calendar and understates "
            "any other."
        )
    if len([c for c in calendars if c]) > 1:
        pieces = [
            f"{d.name or d.calendar_id} at {d.workdays_per_week_equivalent:.1f} d/wk"
            for d in sorted(
                (calendar_set.get(c) for c in calendars if calendar_set.get(c)),
                key=lambda d: d.factor,
            )
        ]
        notes.append(
            f"Activities span {len(pieces)} calendars ({'; '.join(pieces)}). Durations "
            "were converted to elapsed days so they can be added along a path; the "
            "simulated delay is therefore in elapsed days, not working days."
        )
    measured = [d for d in calendar_set.densities.values() if d.measured]
    if calendar_set.densities and not measured:
        notes.append(
            "No calendar could be measured against real dates, so conversion used the "
            "weekly working pattern alone and ignores holidays and shutdowns."
        )
    calendar_id = "elapsed"

    meta = {a.source_id: a for a in await load_activities(db, version_id)}

    activities: list[ActivityInput] = []
    constrained = 0
    for row in rows:
        m = meta.get(row.source_id)
        has_constraint = (row.constraint_type or "none") not in ("none", "")
        if has_constraint:
            constrained += 1
        activities.append(
            ActivityInput(
                activity_id=row.source_id,
                code=row.code or "",
                name=row.name or "",
                duration_days=calendar_set.to_elapsed(
                    _activity_duration(row, m), row.duration_calendar_id
                ),
                # No elicited activity-duration uncertainty exists in the schema yet, so
                # every duration is deterministic and only discrete risk events move the
                # network. The engine warns about exactly this; the warning is correct and
                # is left to fire rather than papered over with an invented spread.
                uncertainty=None,
                min_start_day=_min_start_day(row, data_date),
                is_milestone=bool(m.is_milestone) if m is not None else False,
                has_hard_constraint=has_constraint,
            )
        )

    if constrained:
        honoured = sum(
            1 for a in activities if a.min_start_day is not None and a.min_start_day > 0
        )
        notes.append(
            f"{constrained} activity/activities carry a date constraint. {honoured} "
            '"start on or after" constraint(s) were converted to elapsed days from the '
            "data date and are honoured by the forward pass; any mandatory-finish "
            "constraint is still not enforced, so the simulated finish may fall earlier "
            "than the schedule allows."
        )

    known = {a.activity_id for a in activities}
    relationships: list[RelationshipInput] = []
    dropped = 0
    for rel in (
        await db.scalars(
            select(ScheduleRelationship)
            .where(ScheduleRelationship.version_id == version_id)
            .order_by(ScheduleRelationship.id)
        )
    ).all():
        if (
            rel.predecessor_source_id not in known
            or rel.successor_source_id not in known
            or rel.predecessor_source_id == rel.successor_source_id
        ):
            dropped += 1
            continue
        rel_type = (rel.type or "FS").upper()
        if rel_type not in _REL_TYPES:
            rel_type = "FS"
        relationships.append(
            RelationshipInput(
                predecessor_id=rel.predecessor_source_id,
                successor_id=rel.successor_source_id,
                type=rel_type,  # type: ignore[arg-type]
                lag_days=calendar_set.to_elapsed(
                    float(rel.lag_days or 0.0), rel.lag_calendar_id
                ),
            )
        )
    if dropped:
        notes.append(
            f"{dropped} relationship(s) referenced an activity outside this parse and were "
            "left out of the network."
        )

    schedule = ScheduleInput(
        calendar_id=calendar_id,
        activities=tuple(activities),
        relationships=tuple(relationships),
    )
    return schedule, notes, calendar_set


# --------------------------------------------------------------------------------------
# the gate (invariant 3)
# --------------------------------------------------------------------------------------


async def latest_dcma(db: AsyncSession, version_id: int) -> DcmaRun | None:
    return (
        await db.scalars(
            select(DcmaRun)
            .where(DcmaRun.version_id == version_id)
            .order_by(DcmaRun.created_at.desc(), DcmaRun.id.desc())
            .limit(1)
        )
    ).first()


def _check_gate(version_id: int, dcma: DcmaRun | None, override: bool) -> None:
    if dcma is None:
        raise ScheduleGateBlocked(
            version_id,
            "No DCMA 14-point assessment has been run against this schedule version. "
            "Garbage in, credible-looking garbage out — run the gate first.",
            blocking=[],
        )
    if not dcma.gate_passed and not override:
        raise ScheduleGateBlocked(
            version_id,
            f"The schedule failed {dcma.failed_count} DCMA check(s). Fix the schedule, or "
            "re-send with an explicit override and a reason.",
            blocking=list(dcma.blocking_failures or []),
        )


# --------------------------------------------------------------------------------------
# risks
# --------------------------------------------------------------------------------------


def _mapping_inputs(
    mappings: Sequence[RiskActivityMapping],
    activities: Sequence[ActivityRow],
    known: set[str],
    notes: list[str],
    risk_code: str,
) -> tuple[RiskMappingInput, ...]:
    out: list[RiskMappingInput] = []
    for m in mappings:
        if m.mapping_type == "scoped_driver":
            resolved = tuple(
                a.source_id for a in resolve_scope(m.scope, activities) if a.source_id in known
            )
            if not resolved:
                notes.append(
                    f"{risk_code}: a scoped mapping resolved to no activities on this "
                    "version and was left out."
                )
                continue
            out.append(
                RiskMappingInput(mapping_type="scoped_driver", activity_ids=resolved)
            )
        elif m.mapping_type == "duration_driver":
            if not m.activity_source_id or m.activity_source_id not in known:
                notes.append(
                    f"{risk_code}: a duration-driver mapping named an activity that is "
                    "not in this parse and was left out."
                )
                continue
            out.append(
                RiskMappingInput(
                    mapping_type="duration_driver",
                    activity_ids=(m.activity_source_id,),
                )
            )
        elif m.mapping_type == "inserted_activity":
            pred, succ = m.predecessor_source_id, m.successor_source_id
            if not pred or not succ or pred not in known or succ not in known:
                notes.append(
                    f"{risk_code}: an inserted-activity mapping named a predecessor or "
                    "successor that is not in this parse and was left out."
                )
                continue
            out.append(
                RiskMappingInput(
                    mapping_type="inserted_activity",
                    predecessor_id=pred,
                    successor_id=succ,
                    allocation_pct=m.allocation_pct,
                )
            )
    return tuple(out)


# --------------------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------------------


def _sched_to_elapsed(
    spec: DistributionSpec,
    mappings: tuple[RiskMappingInput, ...],
    activity_calendar: dict[str, str],
    calendar_set: CalendarSet | None,
    risk_code: str,
) -> tuple[DistributionSpec, str | None]:
    """Rescale a working-day schedule impact onto the elapsed-day axis.

    A risk draws one delay per iteration and applies it to every activity it drives — the
    Hulett driver semantic — so there is one conversion per risk, not one per activity.
    Where the driven activities sit on more than one calendar the slowest is used: it
    produces the longest elapsed delay, and of the two ways to be wrong here, understating
    the contingency is the one that matters.

    Scaling the whole distribution is exact because every shape in use is closed under
    multiplication by a positive constant: a PERT's alpha and beta are shape parameters
    the support does not enter, and the triangular, uniform, cumulative and discrete forms
    are all defined by their points.
    """
    if calendar_set is None or spec.is_degenerate and spec.kind != "point":
        return spec, None

    ids = {
        activity_calendar.get(a)
        for m in mappings
        for a in m.activity_ids
        if activity_calendar.get(a)
    }
    found = [d for d in (calendar_set.get(i) for i in ids) if d is not None]
    if not found:
        # Unmapped, or mapped only to activities whose calendar did not parse. The
        # project's slowest calendar is the same conservative choice made below.
        found = [calendar_set.slowest] if calendar_set.slowest else []
    if not found:
        return spec, None

    chosen = min(found, key=lambda d: d.factor)
    if chosen.factor >= 1.0:
        return spec, None

    scaled = _scale_spec(spec, 1.0 / chosen.factor)
    note = None
    if len({d.calendar_id for d in found}) > 1:
        note = (
            f"{risk_code}: drives activities on {len({d.calendar_id for d in found})} "
            f"calendars; its working-day impact was converted using the slowest "
            f"({chosen.name or chosen.calendar_id}), which is the longer elapsed delay."
        )
    return scaled, note


def _scale_spec(spec: DistributionSpec, factor: float) -> DistributionSpec:
    """Multiply a distribution's support by a positive constant, shape untouched."""
    if factor == 1.0:
        return spec
    if spec.kind in ("cumulative", "discrete"):
        return spec.model_copy(
            update={
                "points": tuple(
                    PointMass(x=pt.x * factor, p=pt.p) for pt in spec.points
                )
            }
        )
    return spec.model_copy(
        update={
            "lo": spec.lo * factor,
            "hi": spec.hi * factor,
            "ml": None if spec.ml is None else spec.ml * factor,
        }
    )


async def assemble(
    db: AsyncSession,
    *,
    config: RunConfig,
    scenario: str = "pre_mitigation",
    version_id: int | None = None,
    gate_override: bool = False,
    scope_ids: Sequence[int] | None = None,
) -> Assembly:
    """Build a runnable request, or explain precisely why there isn't one.

    ``scope_ids`` narrows the register to one project and everything under it. ``None``
    means unfiltered, which is what a single-project install and every pre-hierarchy
    caller get. It is a parameter rather than a lookup because assembly does not know
    whose run this is — the route does, and it is the route that resolved the scope the
    run row will carry.

    Passing it is not optional in practice. Without the filter a run assembled for one
    project reads every project's estimates, and the number that comes out is a
    contingency for a portfolio nobody asked about. That is invisible in the result: the
    risk count is simply larger than the register the analyst was looking at.
    """
    if scenario not in qv.SCENARIOS:
        raise SimulationNotAssemblable(
            [f"Unknown scenario {scenario!r}. Expected one of {', '.join(qv.SCENARIOS)}."]
        )

    notes: list[str] = []
    excluded: list[dict] = []

    version: ScheduleVersion | None = None
    dcma: DcmaRun | None = None
    schedule: ScheduleInput | None = None
    calendar_set: CalendarSet | None = None
    activities: list[ActivityRow] = []
    known: set[str] = set()
    #: activity source id -> the calendar its duration was measured against.
    activity_calendar: dict[str, str] = {}

    if version_id is not None:
        version = await db.get(ScheduleVersion, version_id)
        if version is None:
            raise SimulationNotAssemblable(
                [f"Schedule version {version_id} does not exist."]
            )
        if scope_ids is not None:
            owner = await db.scalar(
                select(ScheduleFile.scope_id).where(ScheduleFile.id == version.file_id)
            )
            if owner not in set(scope_ids):
                # Same failure as an out-of-scope estimate, one level up: the register
                # would be this project's and the network somebody else's, and every
                # mapping between them would silently resolve to nothing.
                raise SimulationNotAssemblable(
                    [
                        f"Schedule version {version_id} belongs to a different project "
                        "from the one this run is for."
                    ]
                )
        dcma = await latest_dcma(db, version_id)
        _check_gate(version_id, dcma, gate_override)
        schedule, sched_notes, calendar_set = await build_schedule_input(db, version_id)
        notes.extend(sched_notes)
        activities = await load_activities(db, version_id)
        known = {a.activity_id for a in schedule.activities}
        activity_calendar = {
            row.source_id: row.duration_calendar_id
            for row in (
                await db.scalars(
                    select(ScheduleActivity).where(
                        ScheduleActivity.version_id == version_id
                    )
                )
            ).all()
            if row.duration_calendar_id
        }
        if gate_override and dcma is not None and not dcma.gate_passed:
            notes.append(
                "The DCMA gate failed and was overridden. Every number below rests on a "
                "schedule the quality assessment rejected."
            )
    else:
        notes.append(
            "No schedule was selected, so this is a cost-only run: schedule impacts and "
            "the burn-rate term are not part of the answer."
        )

    # -- estimates, drivers and mappings, all keyed by risk ------------------------
    estimate_stmt = (
        select(RiskQuantEstimate)
        .where(RiskQuantEstimate.scenario == scenario)
        .order_by(RiskQuantEstimate.risk_id)
    )
    if scope_ids is not None:
        # Scope lives on ``risk``; the estimate hangs off it. Joined rather than
        # subqueried so the filter is one statement and cannot be forgotten by a caller
        # that builds the risk map separately below.
        estimate_stmt = estimate_stmt.join(
            Risk, Risk.id == RiskQuantEstimate.risk_id
        ).where(Risk.scope_id.in_(list(scope_ids)))
    estimates = list((await db.scalars(estimate_stmt)).all())
    if not estimates:
        raise SimulationNotAssemblable(
            [
                f"No {scenario.replace('_', ' ')} estimates have been elicited, so there "
                "is nothing to simulate."
            ]
        )

    risks = {
        r.id: r
        for r in (
            await db.scalars(
                select(Risk)
                .where(Risk.id.in_([e.risk_id for e in estimates]))
                .order_by(Risk.risk_code)
            )
        ).all()
    }

    driver_names: dict[int, list[str]] = {}
    driver_coeff: dict[str, float] = {}
    for link, driver in (
        await db.execute(
            select(RiskDriverLink, RiskDriver)
            .join(RiskDriver, RiskDriver.id == RiskDriverLink.driver_id)
            .order_by(RiskDriver.name)
        )
    ).all():
        driver_names.setdefault(link.risk_id, []).append(driver.name)
        driver_coeff[driver.name] = driver.correlation_default

    mappings_by_risk: dict[int, list[RiskActivityMapping]] = {}
    if version_id is not None:
        for m in (
            await db.scalars(
                select(RiskActivityMapping)
                .where(
                    RiskActivityMapping.version_id == version_id,
                    RiskActivityMapping.status == _LIVE_MAPPING_STATUS,
                )
                .order_by(RiskActivityMapping.id)
            )
        ).all():
            mappings_by_risk.setdefault(m.risk_id, []).append(m)

    # -- build one RiskInput per estimate ------------------------------------------
    inputs: list[RiskInput] = []
    used_drivers: set[str] = set()
    mapped_count = 0

    for est in sorted(estimates, key=lambda e: risks[e.risk_id].risk_code if e.risk_id in risks else ""):
        risk = risks.get(est.risk_id)
        if risk is None:
            continue

        payload = _estimate_input(est)
        result = qv.validate(payload)
        if not result.ok:
            excluded.append(
                {
                    "risk_id": risk.id,
                    "risk_code": risk.risk_code,
                    "title": risk.title,
                    "reason": "; ".join(f"{i.field}: {i.message}" for i in result.errors),
                }
            )
            continue

        # Each dimension is widened under its own interpretation. A contract-capped delay
        # and a P10/P90 cost live on one estimate and no longer have to agree.
        cost_spec = spec_for_dimension(
            payload.cost, payload.interpretation_for(payload.cost)
        )
        sched_spec = spec_for_dimension(
            payload.sched, payload.interpretation_for(payload.sched)
        )

        mapping_inputs = _mapping_inputs(
            mappings_by_risk.get(risk.id, ()), activities, known, notes, risk.risk_code
        )

        # The engine's axis is elapsed days. An impact elicited in calendar days is
        # already on it; one elicited in working days is not, and the two differ by
        # roughly 40% on a five-day week. The conversion uses the calendar of the
        # activities the risk drives, because that is whose working week the SME had in
        # mind when they said "ten days".
        if sched_spec is not None and est.sched_day_basis == "working":
            sched_spec, conversion_note = _sched_to_elapsed(
                sched_spec, mapping_inputs, activity_calendar, calendar_set, risk.risk_code
            )
            if conversion_note:
                notes.append(conversion_note)

        # The engine refuses a schedule impact with nowhere to land and a mapping with
        # nothing to contribute, both for good reasons. Neither is worth failing a whole
        # run over, so the unusable half is dropped and said out loud.
        if sched_spec is not None and not mapping_inputs:
            sched_spec = None
            notes.append(
                f"{risk.risk_code}: a schedule impact is elicited but no accepted mapping "
                "puts it on an activity, so its delay was not simulated."
            )
        if sched_spec is None and mapping_inputs:
            mapping_inputs = ()
            notes.append(
                f"{risk.risk_code}: mapped to the schedule but carries no schedule "
                "estimate, so the mapping contributes nothing."
            )

        if cost_spec is None and sched_spec is None:
            excluded.append(
                {
                    "risk_id": risk.id,
                    "risk_code": risk.risk_code,
                    "title": risk.title,
                    "reason": "Nothing left to sample once unusable dimensions were dropped.",
                }
            )
            continue

        # A percentage cost with no base of its own falls back to the run's ``base_cost``
        # inside the engine. That is right for a risk scaling with the whole project and
        # wrong for one scaling with a package, so the fallback is said out loud here
        # rather than left to be inferred from a number that looks perfectly reasonable.
        if est.cost_basis == "pct_of_base" and cost_spec is not None:
            if est.cost_base_value is None:
                notes.append(
                    f"{risk.risk_code}: cost is a percentage with no base of its own, so "
                    f"it was taken against the run's base cost of "
                    f"{config.base_cost:,.0f}."
                )

        tags = tuple(sorted(driver_names.get(risk.id, ())))
        used_drivers.update(tags)
        if mapping_inputs:
            mapped_count += 1

        inputs.append(
            RiskInput(
                risk_id=risk.id,
                code=risk.risk_code,
                title=risk.title,
                p_occurrence=1.0 if est.is_variability else est.p_occurrence,
                is_variability=est.is_variability,
                cost=cost_spec,
                cost_basis=est.cost_basis,  # type: ignore[arg-type]
                cost_base_reference=est.cost_base_value,
                sched=sched_spec,
                drivers=tags,
                mappings=mapping_inputs,
            )
        )

    if not inputs:
        raise SimulationNotAssemblable(
            ["Every estimate was excluded, so there is nothing left to simulate."]
            + [f"{e['risk_code']}: {e['reason']}" for e in excluded]
        )

    correlation = CorrelationInput(
        drivers=tuple(
            DriverSpec(name=name, coefficient=driver_coeff[name])
            for name in sorted(used_drivers)
        )
    )

    request = SimulationRequest(
        risks=tuple(inputs),
        schedule=schedule,
        correlation=correlation,
        config=config,
    )

    return Assembly(
        request=request,
        version=version,
        dcma=dcma,
        risk_count=len(inputs),
        mapped_risk_count=mapped_count,
        activity_count=0 if schedule is None else len(schedule.activities),
        excluded=excluded,
        notes=notes,
    )


async def rebuild(
    db: AsyncSession, *, request_json: dict[str, Any], version_id: int | None
) -> SimulationRequest:
    """Reconstruct a stored run's request, schedule and all.

    The schedule is rebuilt from the version rather than read back from the row, which is
    what makes the stored fingerprint worth checking: if the two disagree, something moved
    that was supposed to be immutable and the run is not the run it claims to be.
    """
    payload = dict(request_json)
    if version_id is None:
        payload["schedule"] = None
    else:
        if await db.get(ScheduleVersion, version_id) is None:
            raise SimulationNotAssemblable(
                [
                    f"Schedule version {version_id} has been deleted, so this run cannot "
                    "be replayed."
                ]
            )
        schedule, _, _cal = await build_schedule_input(db, version_id)
        payload["schedule"] = schedule.model_dump(mode="json")
    return SimulationRequest.model_validate(payload)
