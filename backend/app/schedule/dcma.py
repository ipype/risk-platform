"""DCMA 14-point schedule assessment — the quality gate before any simulation.

``CLAUDE.md`` invariant 3: no schedule enters Monte Carlo without a DCMA pass. Garbage in,
credible-looking garbage out.

Two design decisions worth stating outright, because both are easy to get wrong in a way
that looks fine:

**Unassessable is not the same as passing.** Four of the fourteen checks need data this
stage does not have — a resource-loaded schedule, a CPM engine to run the what-if, a
separate baseline export. They report ``NOT_ASSESSED`` with the reason. A check that
quietly returns 0 offenders because it had nothing to count would let a schedule sail
through the gate on the strength of missing data.

**Not every failure blocks.** Blocking on all fourteen means no real construction schedule
ever runs. The gate blocks only on the three failures that make the *simulation itself*
invalid rather than the schedule merely poor: missing logic (activities float free of the
network), negative float (the CPM run has not converged), and invalid dates (the run is
stale). The rest are reported for the analyst to accept or fix. Override via
:attr:`DcmaThresholds.blocking_checks`.

This module is pure: schedule in, report out. No DB, no clock, no logging side effects.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schedule.model import Activity, ActivityStatus, RelationshipType, Schedule

__all__ = [
    "CheckStatus",
    "DcmaCheck",
    "DcmaReport",
    "DcmaThresholds",
    "run_dcma",
]

_OFFENDER_LIMIT = 25


class CheckStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_ASSESSED = "not_assessed"


class DcmaThresholds(BaseModel):
    """DCMA's published tolerances. Defaults are the standard values."""

    model_config = ConfigDict(frozen=True)

    max_missing_logic_pct: float = 5.0
    max_leads_pct: float = 0.0
    max_lags_pct: float = 5.0
    min_finish_start_pct: float = 90.0
    max_hard_constraints_pct: float = 5.0
    max_high_float_pct: float = 5.0
    high_float_days: float = 44.0
    max_negative_float_count: int = 0
    max_high_duration_pct: float = 5.0
    high_duration_days: float = 44.0
    max_invalid_dates_count: int = 0
    max_missing_resources_pct: float = 5.0
    max_missed_tasks_pct: float = 5.0
    min_cpli: float = 0.95
    min_bei: float = 0.95

    #: Check numbers whose failure blocks simulation. See the module docstring.
    blocking_checks: frozenset[int] = frozenset({1, 7, 9})


class DcmaCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    name: str
    status: CheckStatus
    metric: float | None = None
    metric_label: str = ""
    threshold_label: str = ""
    offender_count: int = 0
    population: int = 0
    offenders: tuple[str, ...] = ()
    truncated: bool = False
    note: str = ""
    blocking: bool = False

    @property
    def failed(self) -> bool:
        return self.status is CheckStatus.FAIL


class DcmaReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    checks: tuple[DcmaCheck, ...]
    thresholds: DcmaThresholds = Field(default_factory=DcmaThresholds)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.PASS)

    @property
    def failed_count(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.FAIL)

    @property
    def not_assessed_count(self) -> int:
        return sum(1 for c in self.checks if c.status is CheckStatus.NOT_ASSESSED)

    @property
    def blocking_failures(self) -> tuple[DcmaCheck, ...]:
        return tuple(c for c in self.checks if c.failed and c.blocking)

    @property
    def gate_passed(self) -> bool:
        """Whether this schedule may proceed to Monte Carlo."""
        return not self.blocking_failures

    def summary(self) -> str:
        verdict = "PASS" if self.gate_passed else "BLOCKED"
        return (
            f"DCMA gate {verdict}: {self.passed_count} passed, "
            f"{self.failed_count} failed ({len(self.blocking_failures)} blocking), "
            f"{self.not_assessed_count} not assessed."
        )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _count_label(n: int, noun: str = "activity", plural: str = "activities") -> str:
    return f"{n} {noun if n == 1 else plural}"


def _codes(activities: list[Activity]) -> tuple[tuple[str, ...], bool]:
    codes = tuple(a.code for a in activities[:_OFFENDER_LIMIT])
    return codes, len(activities) > _OFFENDER_LIMIT


def _pct_check(
    *,
    number: int,
    name: str,
    offenders: list[Activity] | list[str],
    population: int,
    threshold: float,
    thresholds: DcmaThresholds,
    note: str = "",
) -> DcmaCheck:
    """Build a 'no more than X% of population' check."""
    blocking = number in thresholds.blocking_checks
    if population == 0:
        return DcmaCheck(
            number=number,
            name=name,
            status=CheckStatus.NOT_ASSESSED,
            threshold_label=f"≤ {threshold:g}%",
            note=note or "Nothing in scope to assess.",
            blocking=blocking,
        )

    if offenders and isinstance(offenders[0], Activity):
        codes, truncated = _codes(offenders)  # type: ignore[arg-type]
    else:
        codes = tuple(offenders[:_OFFENDER_LIMIT])  # type: ignore[arg-type]
        truncated = len(offenders) > _OFFENDER_LIMIT

    count = len(offenders)
    metric = count / population * 100
    return DcmaCheck(
        number=number,
        name=name,
        status=CheckStatus.PASS if metric <= threshold else CheckStatus.FAIL,
        metric=metric,
        metric_label=f"{metric:.1f}% ({count} of {population})",
        threshold_label=f"≤ {threshold:g}%",
        offender_count=count,
        population=population,
        offenders=codes,
        truncated=truncated,
        note=note,
        blocking=blocking,
    )


def _not_assessed(
    number: int, name: str, reason: str, thresholds: DcmaThresholds
) -> DcmaCheck:
    return DcmaCheck(
        number=number,
        name=name,
        status=CheckStatus.NOT_ASSESSED,
        note=reason,
        blocking=number in thresholds.blocking_checks,
    )


# --------------------------------------------------------------------------- #
# the fourteen checks
# --------------------------------------------------------------------------- #


def _check_01_logic(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    population = schedule.incomplete_activities
    preds, succs = schedule.predecessor_ids, schedule.successor_ids

    no_pred = [a for a in population if not preds.get(a.id)]
    no_succ = [a for a in population if not succs.get(a.id)]

    # One dangling start and one dangling finish are legitimate — every network has to
    # begin and end somewhere. Excuse the earliest opener and the latest closer only.
    excused: set[str] = set()
    if no_pred:
        excused.add(min(no_pred, key=lambda a: (a.forecast_start is None, a.forecast_start or 0)).id)
    if no_succ:
        excused.add(max(no_succ, key=lambda a: (a.forecast_finish is not None, a.forecast_finish or 0)).id)

    offender_ids = {a.id for a in no_pred + no_succ} - excused
    offenders = [a for a in population if a.id in offender_ids]

    return _pct_check(
        number=1,
        name="Logic — activities missing a predecessor or successor",
        offenders=offenders,
        population=len(population),
        threshold=t.max_missing_logic_pct,
        thresholds=t,
        note=(
            "The project's own start and finish activities are excused. Open-ended "
            "activities elsewhere float free of the network, so any delay they absorb "
            "is invisible to the simulation."
        ),
    )


def _check_02_leads(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    total = len(schedule.relationships)
    offenders = [r.id for r in schedule.relationships if r.is_lead]
    return _pct_check(
        number=2,
        name="Leads — negative lag",
        offenders=offenders,
        population=total,
        threshold=t.max_leads_pct,
        thresholds=t,
        note="A lead asserts overlap without logic to justify it and can shorten the "
        "critical path artificially.",
    )


def _check_03_lags(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    total = len(schedule.relationships)
    offenders = [r.id for r in schedule.relationships if r.is_lag]
    return _pct_check(
        number=3,
        name="Lags — positive lag",
        offenders=offenders,
        population=total,
        threshold=t.max_lags_pct,
        thresholds=t,
        note="Lag is duration with no activity to attach risk to; it cannot be sampled.",
    )


def _check_04_relationship_types(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    total = len(schedule.relationships)
    blocking = 4 in t.blocking_checks
    if total == 0:
        return _not_assessed(
            4, "Relationship types — proportion Finish-to-Start", "No relationships.", t
        )
    fs = sum(1 for r in schedule.relationships if r.type is RelationshipType.FS)
    metric = fs / total * 100
    non_fs = [r.id for r in schedule.relationships if r.type is not RelationshipType.FS]
    return DcmaCheck(
        number=4,
        name="Relationship types — proportion Finish-to-Start",
        status=CheckStatus.PASS
        if metric >= t.min_finish_start_pct
        else CheckStatus.FAIL,
        metric=metric,
        metric_label=f"{metric:.1f}% FS ({fs} of {total})",
        threshold_label=f"≥ {t.min_finish_start_pct:g}%",
        offender_count=len(non_fs),
        population=total,
        offenders=tuple(non_fs[:_OFFENDER_LIMIT]),
        truncated=len(non_fs) > _OFFENDER_LIMIT,
        note="SS/FF pairs used to force overlap tend to hide the true driving path.",
        blocking=blocking,
    )


def _check_05_hard_constraints(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    population = schedule.incomplete_activities
    offenders = [a for a in population if a.has_hard_constraint]
    return _pct_check(
        number=5,
        name="Hard constraints",
        offenders=offenders,
        population=len(population),
        threshold=t.max_hard_constraints_pct,
        thresholds=t,
        note="A mandatory date overrides network logic, so a simulated delay upstream "
        "will not propagate through it. Soft constraints are not counted.",
    )


def _check_06_high_float(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    population = [a for a in schedule.incomplete_activities if a.total_float is not None]
    offenders = [
        a
        for a in population
        if a.total_float is not None and a.total_float.days > t.high_float_days
    ]
    return _pct_check(
        number=6,
        name=f"High float — total float above {t.high_float_days:g} working days",
        offenders=offenders,
        population=len(population),
        threshold=t.max_high_float_pct,
        thresholds=t,
        note="Usually a symptom of missing successor logic rather than genuine slack.",
    )


def _check_07_negative_float(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    population = [a for a in schedule.incomplete_activities if a.total_float is not None]
    blocking = 7 in t.blocking_checks
    if not population:
        return _not_assessed(
            7, "Negative float", "No activity carries a total float value.", t
        )
    offenders = [
        a for a in population if a.total_float is not None and a.total_float.days < 0
    ]
    codes, truncated = _codes(offenders)
    return DcmaCheck(
        number=7,
        name="Negative float",
        status=CheckStatus.PASS
        if len(offenders) <= t.max_negative_float_count
        else CheckStatus.FAIL,
        metric=float(len(offenders)),
        metric_label=_count_label(len(offenders)),
        threshold_label=f"≤ {t.max_negative_float_count}",
        offender_count=len(offenders),
        population=len(population),
        offenders=codes,
        truncated=truncated,
        note="Negative float means the schedule has not been resolved against its "
        "constraints. Simulating from it propagates an already-broken network.",
        blocking=blocking,
    )


def _check_08_high_duration(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    population = [
        a
        for a in schedule.incomplete_activities
        if a.remaining_duration is not None and not a.type.is_milestone
    ]
    offenders = [
        a
        for a in population
        if a.remaining_duration is not None
        and a.remaining_duration.days > t.high_duration_days
    ]
    return _pct_check(
        number=8,
        name=f"High duration — remaining duration above {t.high_duration_days:g} working days",
        offenders=offenders,
        population=len(population),
        threshold=t.max_high_duration_pct,
        thresholds=t,
        note="Long activities hide their own risk. Three-point estimates on a 200-day "
        "bar are guesses; break it down before eliciting ranges.",
    )


def _check_09_invalid_dates(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    if schedule.data_date is None:
        return _not_assessed(
            9,
            "Invalid dates — forecast in the past or actuals in the future",
            "No data date in the file, so 'past' and 'future' are undefined.",
            t,
        )

    data_date = schedule.data_date
    offenders: list[Activity] = []
    for activity in schedule.real_activities:
        # An in-progress activity legitimately started before the data date — P6 pins its
        # early start to the actual start. Only work that has not begun at all is suspect
        # when its forecast start sits in the past.
        if activity.status is ActivityStatus.NOT_STARTED and (
            activity.early_start and activity.early_start < data_date
        ):
            offenders.append(activity)
            continue
        # A finish forecast in the past is invalid whatever the status: remaining work
        # cannot complete before now.
        if (
            activity.is_incomplete
            and activity.early_finish
            and activity.early_finish < data_date
        ):
            offenders.append(activity)
            continue
        if (activity.actual_start and activity.actual_start > data_date) or (
            activity.actual_finish and activity.actual_finish > data_date
        ):
            offenders.append(activity)

    codes, truncated = _codes(offenders)
    return DcmaCheck(
        number=9,
        name="Invalid dates — forecast in the past or actuals in the future",
        status=CheckStatus.PASS
        if len(offenders) <= t.max_invalid_dates_count
        else CheckStatus.FAIL,
        metric=float(len(offenders)),
        metric_label=_count_label(len(offenders)),
        threshold_label=f"≤ {t.max_invalid_dates_count}",
        offender_count=len(offenders),
        population=len(schedule.real_activities),
        offenders=codes,
        truncated=truncated,
        note="Indicates the schedule was not rescheduled at the stated data date. "
        "Every forecast downstream of it is stale.",
        blocking=9 in t.blocking_checks,
    )


def _check_10_resources(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    has_any = any(
        a.has_resource_assignment or a.budgeted_cost is not None
        for a in schedule.activities
    )
    if not has_any:
        return _not_assessed(
            10,
            "Resources — activities carry cost or resource assignment",
            "The file contains no resource assignments at all, so this cannot be "
            "distinguished from a schedule that was simply exported without them. "
            "Re-export with resources to assess.",
            t,
        )
    population = [
        a
        for a in schedule.incomplete_activities
        if not a.type.is_milestone
        and a.remaining_duration is not None
        and a.remaining_duration.days > 0
    ]
    offenders = [
        a for a in population if not a.has_resource_assignment and a.budgeted_cost is None
    ]
    return _pct_check(
        number=10,
        name="Resources — activities carry cost or resource assignment",
        offenders=offenders,
        population=len(population),
        threshold=t.max_missing_resources_pct,
        thresholds=t,
        note="Unresourced activities cannot contribute to the cost side of an "
        "integrated cost/schedule simulation.",
    )


def _check_11_missed_tasks(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    if schedule.data_date is None:
        return _not_assessed(
            11, "Missed tasks", "No data date to compare baseline finishes against.", t
        )
    data_date = schedule.data_date
    population = [
        a
        for a in schedule.real_activities
        if a.baseline_finish is not None and a.baseline_finish <= data_date
    ]
    if not population:
        return _not_assessed(
            11,
            "Missed tasks",
            "No activity was baselined to finish on or before the data date.",
            t,
        )
    offenders = [
        a
        for a in population
        if a.is_incomplete
        or (
            a.actual_finish is not None
            and a.baseline_finish is not None
            and a.actual_finish > a.baseline_finish
        )
    ]
    return _pct_check(
        number=11,
        name="Missed tasks — finished late or not at all against baseline",
        offenders=offenders,
        population=len(population),
        threshold=t.max_missed_tasks_pct,
        thresholds=t,
        note="Baseline dates here come from the planned dates in this file. For a true "
        "comparison, upload the baseline export alongside the current schedule.",
    )


def _check_12_critical_path_test(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    return _not_assessed(
        12,
        "Critical path test",
        "Requires re-running CPM with an injected delay on a critical activity and "
        "observing the project finish move. Lands with the scheduling engine in the "
        "Monte Carlo stage; reporting a pass without running it would be a fabrication.",
        t,
    )


def _check_13_cpli(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    blocking = 13 in t.blocking_checks
    if schedule.data_date is None or schedule.project_finish is None:
        return _not_assessed(
            13,
            "Critical Path Length Index (CPLI)",
            "Needs both a data date and a forecast project finish.",
            t,
        )

    calendar = schedule.default_calendar
    critical_path_length = calendar.working_days_between(
        schedule.data_date.date(), schedule.project_finish.date()
    )
    if critical_path_length <= 0:
        return _not_assessed(
            13,
            "Critical Path Length Index (CPLI)",
            "The forecast project finish is not after the data date; CPLI is undefined.",
            t,
        )

    if schedule.must_finish_by is not None:
        project_total_float = calendar.working_days_between(
            schedule.project_finish.date(), schedule.must_finish_by.date()
        )
        basis = "project must-finish-by date"
    else:
        finish_activity = max(
            (a for a in schedule.real_activities if a.forecast_finish),
            key=lambda a: a.forecast_finish,  # type: ignore[arg-type,return-value]
            default=None,
        )
        if finish_activity is None or finish_activity.total_float is None:
            return _not_assessed(
                13,
                "Critical Path Length Index (CPLI)",
                "No project must-finish-by date and no total float on the finish "
                "activity, so project total float is unknown.",
                t,
            )
        project_total_float = finish_activity.total_float.days
        basis = f"total float of finish activity {finish_activity.code}"

    cpli = (critical_path_length + project_total_float) / critical_path_length
    return DcmaCheck(
        number=13,
        name="Critical Path Length Index (CPLI)",
        status=CheckStatus.PASS if cpli >= t.min_cpli else CheckStatus.FAIL,
        metric=cpli,
        metric_label=(
            f"{cpli:.2f} (path {critical_path_length:g}d, float "
            f"{project_total_float:g}d, from {basis})"
        ),
        threshold_label=f"≥ {t.min_cpli:g}",
        population=len(schedule.real_activities),
        note="Below 1.00 the schedule must beat its own plan to hit the date; the "
        "contingency conversation should start here.",
        blocking=blocking,
    )


def _check_14_bei(schedule: Schedule, t: DcmaThresholds) -> DcmaCheck:
    blocking = 14 in t.blocking_checks
    if schedule.data_date is None:
        return _not_assessed(
            14, "Baseline Execution Index (BEI)", "No data date in the file.", t
        )
    data_date = schedule.data_date
    due = [
        a
        for a in schedule.real_activities
        if a.baseline_finish is not None and a.baseline_finish <= data_date
    ]
    if not due:
        return _not_assessed(
            14,
            "Baseline Execution Index (BEI)",
            "No activity was baselined to finish on or before the data date.",
            t,
        )
    completed = sum(1 for a in schedule.real_activities if a.is_complete)
    bei = completed / len(due)
    return DcmaCheck(
        number=14,
        name="Baseline Execution Index (BEI)",
        status=CheckStatus.PASS if bei >= t.min_bei else CheckStatus.FAIL,
        metric=bei,
        metric_label=f"{bei:.2f} ({completed} completed / {len(due)} baselined due)",
        threshold_label=f"≥ {t.min_bei:g}",
        population=len(due),
        note="Below 1.00 the project is completing work more slowly than planned; that "
        "trend is direct evidence for the schedule risk ranges.",
        blocking=blocking,
    )


_CHECKS = (
    _check_01_logic,
    _check_02_leads,
    _check_03_lags,
    _check_04_relationship_types,
    _check_05_hard_constraints,
    _check_06_high_float,
    _check_07_negative_float,
    _check_08_high_duration,
    _check_09_invalid_dates,
    _check_10_resources,
    _check_11_missed_tasks,
    _check_12_critical_path_test,
    _check_13_cpli,
    _check_14_bei,
)


def run_dcma(schedule: Schedule, thresholds: DcmaThresholds | None = None) -> DcmaReport:
    """Run all fourteen checks and return the gate decision."""
    t = thresholds or DcmaThresholds()
    return DcmaReport(
        project_id=schedule.project_id,
        project_name=schedule.project_name,
        checks=tuple(check(schedule, t) for check in _CHECKS),
        thresholds=t,
    )
