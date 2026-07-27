"""Canonical schedule domain model.

Every parser produces *this*, whatever the source format. Nothing downstream — the DCMA
gate, risk-to-activity mapping, the Monte Carlo engine — is allowed to know whether the
schedule came from a P6 ``.xer``, an MS Project ``.mpp``, or a hand-built fixture. That
is the whole point: it makes the choice of parsing technology a swappable detail rather
than a decision baked into the analysis.

Two invariants from ``CLAUDE.md`` are enforced here structurally rather than by
convention:

* **Durations are in working days, always paired with the calendar they were computed
  against.** :class:`WorkingDuration` carries both, so a bare float can never be mistaken
  for a duration. Comparing durations across calendars raises.
* **Money never appears in this module.** Cost lives on the risk side of the mapping.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import StrEnum
from functools import cached_property

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ActivityStatus",
    "ActivityType",
    "ConstraintType",
    "RelationshipType",
    "WorkingDuration",
    "WorkCalendar",
    "WbsNode",
    "Activity",
    "Relationship",
    "Schedule",
    "HARD_CONSTRAINTS",
]


class ActivityType(StrEnum):
    TASK = "task"
    START_MILESTONE = "start_milestone"
    FINISH_MILESTONE = "finish_milestone"
    LEVEL_OF_EFFORT = "level_of_effort"
    WBS_SUMMARY = "wbs_summary"
    RESOURCE_DEPENDENT = "resource_dependent"

    @property
    def is_milestone(self) -> bool:
        return self in (ActivityType.START_MILESTONE, ActivityType.FINISH_MILESTONE)

    @property
    def is_real_work(self) -> bool:
        """Excludes the bookkeeping rows that would distort every DCMA percentage.

        Level-of-effort and WBS summary activities are hammocks: they take their dates
        from what they span, so counting them as missing-logic or high-duration
        offenders is meaningless.
        """
        return self not in (ActivityType.LEVEL_OF_EFFORT, ActivityType.WBS_SUMMARY)


class ActivityStatus(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class RelationshipType(StrEnum):
    FS = "FS"
    SS = "SS"
    FF = "FF"
    SF = "SF"


class ConstraintType(StrEnum):
    NONE = "none"
    START_ON = "start_on"
    START_ON_OR_AFTER = "start_on_or_after"
    START_ON_OR_BEFORE = "start_on_or_before"
    FINISH_ON = "finish_on"
    FINISH_ON_OR_AFTER = "finish_on_or_after"
    FINISH_ON_OR_BEFORE = "finish_on_or_before"
    MANDATORY_START = "mandatory_start"
    MANDATORY_FINISH = "mandatory_finish"
    AS_LATE_AS_POSSIBLE = "as_late_as_possible"


#: Constraints that override network logic outright. DCMA check 5 counts these and only
#: these — a "start on or after" is a soft preference the scheduler may satisfy anyway,
#: whereas a mandatory date silently discards driving relationships.
HARD_CONSTRAINTS: frozenset[ConstraintType] = frozenset(
    {
        ConstraintType.MANDATORY_START,
        ConstraintType.MANDATORY_FINISH,
        ConstraintType.START_ON,
        ConstraintType.FINISH_ON,
    }
)


class WorkingDuration(BaseModel):
    """A span of working days, inseparable from the calendar that defines a day.

    Ten working days on a 5-day calendar and ten on a 7-day calendar are different
    amounts of wall-clock time. Keeping the calendar attached is what stops the
    simulation from quietly adding them together.
    """

    model_config = ConfigDict(frozen=True)

    days: float
    calendar_id: str

    def __add__(self, other: WorkingDuration) -> WorkingDuration:
        self._assert_same_calendar(other)
        return WorkingDuration(days=self.days + other.days, calendar_id=self.calendar_id)

    def __sub__(self, other: WorkingDuration) -> WorkingDuration:
        self._assert_same_calendar(other)
        return WorkingDuration(days=self.days - other.days, calendar_id=self.calendar_id)

    def _assert_same_calendar(self, other: WorkingDuration) -> None:
        if self.calendar_id != other.calendar_id:
            raise ValueError(
                "Refusing to combine durations measured against different calendars "
                f"({self.calendar_id!r} and {other.calendar_id!r}). Convert via hours "
                "first, or normalise both to the project default calendar."
            )

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.days:g}d@{self.calendar_id}"


class WorkCalendar(BaseModel):
    """A working-time calendar: which weekdays are worked, minus exceptions."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    hours_per_day: float = 8.0
    #: Python weekday numbers that are worked, ``0`` = Monday .. ``6`` = Sunday.
    workdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    #: Non-working exception dates (holidays, shutdowns) that fall on a workday.
    holidays: frozenset[date] = frozenset()
    #: Extra working dates that fall outside :attr:`workdays`.
    extra_workdays: frozenset[date] = frozenset()
    is_default: bool = False

    def is_workday(self, day: date) -> bool:
        if day in self.holidays:
            return False
        if day in self.extra_workdays:
            return True
        return day.weekday() in self.workdays

    @property
    def workdays_per_week(self) -> int:
        return len(self.workdays) or 5

    def hours_to_days(self, hours: float | None) -> WorkingDuration | None:
        """Convert a source-format hour count into working days on this calendar."""
        if hours is None:
            return None
        per_day = self.hours_per_day or 8.0
        return WorkingDuration(days=hours / per_day, calendar_id=self.id)

    def working_days_between(self, start: date, end: date) -> float:
        """Count working days in ``[start, end)``. Negative when ``end`` precedes ``start``.

        Walks day by day. Schedules span years, not centuries, so the loop is bounded in
        the thousands and the clarity is worth more than the microseconds.
        """
        if start == end:
            return 0.0
        sign = 1.0
        if end < start:
            start, end = end, start
            sign = -1.0
        count = 0
        cursor = start
        while cursor < end:
            if self.is_workday(cursor):
                count += 1
            cursor += timedelta(days=1)
        return sign * count

    def duration_between(self, start: date, end: date) -> WorkingDuration:
        return WorkingDuration(
            days=self.working_days_between(start, end), calendar_id=self.id
        )


class WbsNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    code: str
    name: str
    parent_id: str | None = None
    is_project_node: bool = False


class Activity(BaseModel):
    """One row of the schedule, normalised.

    Date fields keep the source's distinction between *planned* (baseline), *forecast*
    (early/late from the last CPM run) and *actual*. The DCMA gate needs all three, and
    collapsing them into a single "start"/"finish" pair — a tempting simplification —
    destroys checks 9, 11 and 14.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    code: str
    name: str
    calendar_id: str
    wbs_id: str | None = None

    type: ActivityType = ActivityType.TASK
    status: ActivityStatus = ActivityStatus.NOT_STARTED

    original_duration: WorkingDuration | None = None
    remaining_duration: WorkingDuration | None = None
    total_float: WorkingDuration | None = None
    free_float: WorkingDuration | None = None

    early_start: datetime | None = None
    early_finish: datetime | None = None
    late_start: datetime | None = None
    late_finish: datetime | None = None

    baseline_start: datetime | None = None
    baseline_finish: datetime | None = None

    actual_start: datetime | None = None
    actual_finish: datetime | None = None

    constraint_type: ConstraintType = ConstraintType.NONE
    constraint_date: datetime | None = None
    secondary_constraint_type: ConstraintType = ConstraintType.NONE
    secondary_constraint_date: datetime | None = None

    is_critical: bool = False
    has_resource_assignment: bool = False
    budgeted_cost: int | None = Field(
        default=None,
        description="Minor currency units. Never a float — see CLAUDE.md conventions.",
    )

    @property
    def is_complete(self) -> bool:
        return self.status is ActivityStatus.COMPLETED

    @property
    def is_incomplete(self) -> bool:
        return self.status is not ActivityStatus.COMPLETED

    @property
    def has_hard_constraint(self) -> bool:
        return (
            self.constraint_type in HARD_CONSTRAINTS
            or self.secondary_constraint_type in HARD_CONSTRAINTS
        )

    @property
    def forecast_finish(self) -> datetime | None:
        """Best available finish: actual if it happened, otherwise the early date."""
        return self.actual_finish or self.early_finish

    @property
    def forecast_start(self) -> datetime | None:
        return self.actual_start or self.early_start


class Relationship(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    predecessor_id: str
    successor_id: str
    type: RelationshipType = RelationshipType.FS
    lag: WorkingDuration | None = None

    @property
    def lag_days(self) -> float:
        return self.lag.days if self.lag else 0.0

    @property
    def is_lead(self) -> bool:
        """A lead is negative lag — overlap asserted without logic to justify it."""
        return self.lag_days < 0

    @property
    def is_lag(self) -> bool:
        return self.lag_days > 0


class Schedule(BaseModel):
    """A parsed project schedule plus everything needed to reason about it."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    #: The CPM data date. Without it, checks 9, 11, 13 and 14 are unanswerable.
    data_date: datetime | None = None
    baseline_finish: datetime | None = None
    must_finish_by: datetime | None = None

    source_format: str = "unknown"
    source_filename: str | None = None

    calendars: tuple[WorkCalendar, ...] = ()
    wbs: tuple[WbsNode, ...] = ()
    activities: tuple[Activity, ...] = ()
    relationships: tuple[Relationship, ...] = ()

    #: Non-fatal problems found while parsing. Surfaced to the analyst, never swallowed.
    warnings: tuple[str, ...] = ()

    # -- lookups ---------------------------------------------------------------

    def calendar(self, calendar_id: str | None) -> WorkCalendar:
        """Resolve a calendar id, falling back to the project default then to 5x8."""
        for cal in self.calendars:
            if cal.id == calendar_id:
                return cal
        return self.default_calendar

    @property
    def default_calendar(self) -> WorkCalendar:
        for cal in self.calendars:
            if cal.is_default:
                return cal
        if self.calendars:
            return self.calendars[0]
        return WorkCalendar(id="__implied__", name="Implied 5x8", is_default=True)

    def activity(self, activity_id: str) -> Activity | None:
        return self._by_id.get(activity_id)

    @cached_property
    def _by_id(self) -> dict[str, Activity]:
        return {a.id: a for a in self.activities}

    @cached_property
    def predecessor_ids(self) -> dict[str, set[str]]:
        return self._adjacency[0]

    @cached_property
    def successor_ids(self) -> dict[str, set[str]]:
        return self._adjacency[1]

    @cached_property
    def _adjacency(self) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
        preds: dict[str, set[str]] = {a.id: set() for a in self.activities}
        succs: dict[str, set[str]] = {a.id: set() for a in self.activities}
        for rel in self.relationships:
            # relationships pointing outside this project were dropped at parse time,
            # but a defensive membership test costs nothing and avoids a KeyError on
            # hand-built fixtures
            if rel.successor_id in preds:
                preds[rel.successor_id].add(rel.predecessor_id)
            if rel.predecessor_id in succs:
                succs[rel.predecessor_id].add(rel.successor_id)
        return preds, succs

    # -- filtered views --------------------------------------------------------

    @cached_property
    def real_activities(self) -> list[Activity]:
        """Activities that represent work — LOE and WBS summary rows removed."""
        return [a for a in self.activities if a.type.is_real_work]

    @cached_property
    def incomplete_activities(self) -> list[Activity]:
        return [a for a in self.real_activities if a.is_incomplete]

    @cached_property
    def project_finish(self) -> datetime | None:
        finishes = [a.forecast_finish for a in self.real_activities if a.forecast_finish]
        return max(finishes) if finishes else None
