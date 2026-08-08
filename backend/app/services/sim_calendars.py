"""Loading a version's calendars and putting them on one axis.

The database half of the calendar work: ``app.schedule.calendars`` is pure arithmetic and
may not touch a session, so the ``ScheduleCalendar`` rows are turned into
``WorkCalendar`` objects here and measured against the window the version actually spans.

The window matters more than it looks. Density is measured over the project's own dates,
so a shutdown that falls inside the schedule is counted and one that falls outside it is
not. Measuring over an arbitrary year would import holidays the project never meets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule import ScheduleActivity, ScheduleCalendar, ScheduleVersion
from app.schedule.calendars import CalendarDensity, describe
from app.schedule.model import WorkCalendar

__all__ = ["CalendarSet", "load_calendar_set", "version_window"]


def _as_date(value: datetime | date | None) -> date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def _to_work_calendar(row: ScheduleCalendar) -> WorkCalendar:
    """One stored row as the pure model.

    Stored weekday lists and ISO date strings are normalised defensively: a parser that
    wrote a string where an int was expected should cost this calendar its exception, not
    take down the run.
    """
    workdays: set[int] = set()
    for value in row.workdays or []:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            workdays.add(day)
    if not workdays:
        workdays = {0, 1, 2, 3, 4}

    def dates(values: list | None) -> frozenset[date]:
        out: set[date] = set()
        for value in values or []:
            if isinstance(value, date) and not isinstance(value, datetime):
                out.add(value)
                continue
            if isinstance(value, datetime):
                out.add(value.date())
                continue
            try:
                out.add(date.fromisoformat(str(value)[:10]))
            except ValueError:
                continue
        return frozenset(out)

    return WorkCalendar(
        id=row.source_id,
        name=row.name or row.source_id,
        hours_per_day=row.hours_per_day or 8.0,
        workdays=frozenset(workdays),
        holidays=dates(row.holidays),
        extra_workdays=dates(row.extra_workdays),
        is_default=bool(row.is_default),
    )


@dataclass(frozen=True)
class CalendarSet:
    """Every calendar in a version, measured, with a lookup by source id."""

    densities: dict[str, CalendarDensity]
    window_start: date | None
    window_end: date | None
    #: The densest calendar present. Not used for conversion — carried so a caller can
    #: report how far apart the calendars in one schedule actually are.
    fastest: CalendarDensity | None = None
    slowest: CalendarDensity | None = None

    def get(self, calendar_id: str | None) -> CalendarDensity | None:
        if not calendar_id:
            return None
        return self.densities.get(calendar_id)

    def to_elapsed(self, working_days: float, calendar_id: str | None) -> float:
        """Working days on a named calendar as elapsed days.

        An unknown calendar id converts at 1.0 rather than guessing a pattern. Silently
        applying the default calendar's factor to an activity whose calendar failed to
        parse would be exactly the invisible unit error this module exists to remove; the
        caller is expected to have noted the unknown id already.
        """
        found = self.get(calendar_id)
        return working_days if found is None else found.to_elapsed(working_days)

    @property
    def spread(self) -> float:
        """Ratio between the slowest and fastest calendar. 1.0 when they agree."""
        if self.fastest is None or self.slowest is None:
            return 1.0
        if self.slowest.factor <= 0:
            return 1.0
        return self.fastest.factor / self.slowest.factor


async def version_window(
    db: AsyncSession, version_id: int
) -> tuple[date | None, date | None]:
    """The calendar span a version occupies: data date to latest early finish.

    Its own function because the start of this window is *day zero* for everything the
    engine returns. Activity durations reach the engine as elapsed days from here, the
    forward pass counts from here, and a simulated finish day is only a calendar date
    because this is the date it is added to. Two places computing that anchor with two
    slightly different fallbacks would put the run's dates and the run's arithmetic on
    different origins, and nothing downstream would show it.

    Falling back to the earliest activity start rather than requiring a data date keeps a
    schedule that parsed without one usable, at the cost of a window that starts wherever
    the work does — which is why the caller is told which of the two it got.
    """
    version = await db.get(ScheduleVersion, version_id)

    bounds = (
        await db.execute(
            select(
                func.min(ScheduleActivity.early_start),
                func.max(ScheduleActivity.early_finish),
            ).where(ScheduleActivity.version_id == version_id)
        )
    ).one()

    start = _as_date(version.data_date if version else None) or _as_date(bounds[0])
    end = _as_date(bounds[1]) or _as_date(version.baseline_finish if version else None)
    return start, end


async def load_calendar_set(db: AsyncSession, version_id: int) -> CalendarSet:
    """Every calendar for a version, measured over the dates that version occupies.

    The window comes from :func:`version_window`, which is also what dates every
    simulated finish day off — see there for why the anchor has exactly one owner.
    """
    start, end = await version_window(db, version_id)

    rows = (
        await db.scalars(
            select(ScheduleCalendar)
            .where(ScheduleCalendar.version_id == version_id)
            .order_by(ScheduleCalendar.id)
        )
    ).all()

    densities: dict[str, CalendarDensity] = {}
    for row in rows:
        measured = describe(_to_work_calendar(row), start, end)
        densities[row.source_id] = measured

    ordered = sorted(densities.values(), key=lambda d: d.factor)
    return CalendarSet(
        densities=densities,
        window_start=start,
        window_end=end,
        slowest=ordered[0] if ordered else None,
        fastest=ordered[-1] if ordered else None,
    )
