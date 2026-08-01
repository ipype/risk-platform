"""Converting between working days and elapsed days.

``WorkCalendar`` already answers "how many working days lie between these two dates".
This module answers the question the simulator needs, which is the inverse: *how long in
elapsed time* is a duration expressed in working days, and therefore how do two durations
measured against different calendars get onto one axis.

Why this is needed at all: a capital project schedule routinely carries several calendars
— a five-day standard week, a six-day construction week, a seven-day continuous operation,
and usually a separate one holding the milestones. A "10 day" activity means 14 elapsed
days on the first and 11 or 12 on the second. The critical path method adds durations
along a path, so adding a five-day-week duration to a six-day-week duration produces a
finish date that is wrong by roughly the ratio between them, with nothing in the output to
show for it. Elapsed days are the only unit every calendar agrees on.

**The approximation, stated up front.** An exact conversion is date-dependent: ten working
days starting the day before a two-week shutdown span far more elapsed time than ten
starting in June. Honouring that inside the simulation would mean carrying dates through
the CPM and doing calendar-aware addition per activity per iteration, which is not
vectorisable and would cost several orders of magnitude of runtime for a correction that
is usually under a percent.

What is done instead is to measure each calendar's *density* — real working days divided
by real elapsed days — across the window the project actually occupies, holidays and
shutdown exceptions included, and convert with that single factor. The factor is exact for
a calendar whose non-working days are evenly spread, which is what a weekly pattern plus
scattered public holidays is. It is least accurate where a long shutdown sits inside the
window and only some activities cross it.

That error is bounded, one-directional per calendar, and reported: :func:`describe`
returns the factor and the window it was measured over so the run can carry both, in the
same spirit as ``CorrelationReport.repair_max_delta``. An approximation nobody can see is
the thing this codebase refuses; an approximation on the face of the result is a modelling
choice a reviewer can weigh.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.schedule.model import WorkCalendar

__all__ = [
    "CalendarDensity",
    "add_working_days",
    "density",
    "describe",
    "elapsed_for_working",
    "working_for_elapsed",
]

#: A calendar with no working day at all in the measured window falls back to its weekly
#: pattern rather than dividing by zero. Reported as ``measured=False`` so the caller can
#: say so rather than presenting a guess as a measurement.
_MIN_WINDOW_DAYS = 7


@dataclass(frozen=True)
class CalendarDensity:
    """One calendar's working-days-per-elapsed-day, and where it came from."""

    calendar_id: str
    name: str
    #: Working days per elapsed day, in ``(0, 1]``. A seven-day calendar is 1.0.
    factor: float
    #: Whether the factor was measured over a real window or fell back to the weekly
    #: pattern. A fallback is not wrong, it is just not evidence.
    measured: bool
    window_start: date | None = None
    window_end: date | None = None
    working_days: float = 0.0
    elapsed_days: float = 0.0

    @property
    def workdays_per_week_equivalent(self) -> float:
        return self.factor * 7.0

    def to_elapsed(self, working_days: float) -> float:
        """Working days on this calendar expressed as elapsed days."""
        return working_days / self.factor if self.factor > 0 else working_days

    def to_working(self, elapsed_days: float) -> float:
        return elapsed_days * self.factor


def add_working_days(cal: WorkCalendar, start: date, days: float) -> date:
    """The date reached by working ``days`` days forward from ``start``.

    The inverse of :meth:`WorkCalendar.working_days_between`, and the primitive everything
    else here is built on. A fractional remainder does not advance the cursor: half a day
    of work finishes on the day it started, which is what a CPM finish date means.

    Walks day by day for the same reason ``working_days_between`` does — schedules span
    years, so the loop is bounded in the low thousands.
    """
    if days <= 0:
        return start

    whole = int(days)
    cursor = start
    remaining = whole
    guard = 0
    limit = max(whole * 10, 3650) + 366

    while remaining > 0:
        cursor += timedelta(days=1)
        guard += 1
        if guard > limit:
            # A calendar with no working days would otherwise spin forever. Returning
            # what we have is wrong, so say so rather than hang a worker.
            raise ValueError(
                f"calendar {cal.id!r} has no working days in the {guard} days after "
                f"{start}; cannot advance {days} working days"
            )
        if cal.is_workday(cursor):
            remaining -= 1

    return cursor


def density(cal: WorkCalendar, start: date, end: date) -> float:
    """Working days per elapsed day across ``[start, end)``.

    Counts the calendar's real exceptions, so a window containing a shutdown yields a
    lower factor than the weekly pattern alone would suggest — which is the point.
    """
    span = (end - start).days
    if span < _MIN_WINDOW_DAYS:
        return min(cal.workdays_per_week / 7.0, 1.0)
    worked = cal.working_days_between(start, end)
    if worked <= 0:
        return min(cal.workdays_per_week / 7.0, 1.0)
    return min(worked / span, 1.0)


def describe(cal: WorkCalendar, start: date | None, end: date | None) -> CalendarDensity:
    """Measure a calendar over a window, falling back to its weekly pattern without one."""
    if start is None or end is None or (end - start).days < _MIN_WINDOW_DAYS:
        return CalendarDensity(
            calendar_id=cal.id,
            name=cal.name,
            factor=min(cal.workdays_per_week / 7.0, 1.0),
            measured=False,
        )

    span = float((end - start).days)
    worked = cal.working_days_between(start, end)
    if worked <= 0:
        return CalendarDensity(
            calendar_id=cal.id,
            name=cal.name,
            factor=min(cal.workdays_per_week / 7.0, 1.0),
            measured=False,
            window_start=start,
            window_end=end,
        )

    return CalendarDensity(
        calendar_id=cal.id,
        name=cal.name,
        factor=min(worked / span, 1.0),
        measured=True,
        window_start=start,
        window_end=end,
        working_days=worked,
        elapsed_days=span,
    )


def elapsed_for_working(cal: WorkCalendar, start: date, working_days: float) -> float:
    """Exact elapsed days for a working duration starting on a known date.

    Used where the start really is known — a deterministic early start, a constraint date
    — and the day-level accuracy is worth the walk. The simulation itself uses the density
    factor, because inside a run the start is a random variable.
    """
    if working_days <= 0:
        return 0.0
    finish = add_working_days(cal, start, working_days)
    return float((finish - start).days)


def working_for_elapsed(cal: WorkCalendar, start: date, elapsed_days: float) -> float:
    """Working days contained in an elapsed span beginning at ``start``."""
    if elapsed_days <= 0:
        return 0.0
    return cal.working_days_between(start, start + timedelta(days=int(elapsed_days)))
