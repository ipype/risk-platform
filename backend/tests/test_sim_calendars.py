"""Working days to elapsed days, and back.

The arithmetic is small and the consequences are not: every duration in a multi-calendar
run passes through it, and an error here moves the finish date without moving anything
visible. So the tests are against closed forms — a five-day week over a whole number of
weeks has exactly 5/7 density, ten working days from a Monday lands on a Monday — rather
than against the implementation's own output.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.schedule.calendars import (
    add_working_days,
    density,
    describe,
    elapsed_for_working,
    working_for_elapsed,
)
from app.schedule.model import WorkCalendar

FIVE_DAY = WorkCalendar(id="CAL-STD", name="Standard 5-day", workdays=frozenset({0, 1, 2, 3, 4}))
SIX_DAY = WorkCalendar(id="CAL-6D", name="Six-day", workdays=frozenset({0, 1, 2, 3, 4, 5}))
SEVEN_DAY = WorkCalendar(id="CAL-7D", name="Continuous", workdays=frozenset(range(7)))

# 2026-01-05 is a Monday, which keeps every whole-week assertion below exact.
MONDAY = date(2026, 1, 5)


def test_ten_working_days_on_a_five_day_week_spans_two_calendar_weeks():
    # Mon + 10 working days: the tenth is the Friday of week two, so the cursor lands on
    # the following Monday, 14 elapsed days out.
    assert add_working_days(FIVE_DAY, MONDAY, 10) == date(2026, 1, 19)
    assert elapsed_for_working(FIVE_DAY, MONDAY, 10) == 14.0


def test_the_same_ten_days_on_a_six_day_week_is_shorter_in_elapsed_time():
    assert elapsed_for_working(SIX_DAY, MONDAY, 10) < elapsed_for_working(FIVE_DAY, MONDAY, 10)


def test_a_seven_day_calendar_makes_working_and_elapsed_the_same_thing():
    assert elapsed_for_working(SEVEN_DAY, MONDAY, 10) == 10.0
    assert density(SEVEN_DAY, MONDAY, date(2026, 3, 2)) == 1.0


@pytest.mark.parametrize(
    "calendar,expected",
    [(FIVE_DAY, 5 / 7), (SIX_DAY, 6 / 7), (SEVEN_DAY, 1.0)],
)
def test_density_over_whole_weeks_is_the_working_week_over_seven(calendar, expected):
    # Eight whole weeks from a Monday: no partial week to round.
    assert density(calendar, MONDAY, MONDAY.replace(day=2, month=3)) == pytest.approx(
        expected, abs=1e-9
    )


def test_a_shutdown_inside_the_window_lowers_the_measured_density():
    shutdown = frozenset(date(2026, 1, 5 + n) for n in range(5))  # a full working week
    closed = FIVE_DAY.model_copy(update={"holidays": shutdown})
    window_end = date(2026, 3, 2)
    assert density(closed, MONDAY, window_end) < density(FIVE_DAY, MONDAY, window_end)


def test_a_holiday_outside_the_window_is_not_counted():
    away = FIVE_DAY.model_copy(update={"holidays": frozenset({date(2027, 6, 1)})})
    window_end = date(2026, 3, 2)
    assert density(away, MONDAY, window_end) == density(FIVE_DAY, MONDAY, window_end)


def test_conversion_round_trips_within_a_day():
    """Elapsed and working are inverses up to the resolution of a single day."""
    elapsed = elapsed_for_working(FIVE_DAY, MONDAY, 20)
    assert working_for_elapsed(FIVE_DAY, MONDAY, elapsed) == pytest.approx(20, abs=1)


def test_describe_reports_a_measurement_as_a_measurement():
    d = describe(FIVE_DAY, MONDAY, date(2026, 3, 2))
    assert d.measured is True
    assert d.factor == pytest.approx(5 / 7, abs=1e-9)
    assert d.workdays_per_week_equivalent == pytest.approx(5.0, abs=1e-9)


def test_describe_without_a_window_falls_back_and_admits_it():
    """A guess and a measurement must not be indistinguishable on the result."""
    d = describe(FIVE_DAY, None, None)
    assert d.measured is False
    assert d.factor == pytest.approx(5 / 7, abs=1e-9)


def test_a_too_short_window_does_not_produce_a_freak_factor():
    # Three days spanning a weekend would measure 1/3 rather than 5/7.
    d = describe(FIVE_DAY, date(2026, 1, 9), date(2026, 1, 12))
    assert d.measured is False
    assert d.factor == pytest.approx(5 / 7, abs=1e-9)


def test_a_calendar_that_never_works_is_refused_rather_than_looping():
    never = WorkCalendar(id="DEAD", name="No working days", workdays=frozenset())
    # workdays_per_week floors at 5 for the density fallback, but the day walk has no
    # working day to find and must raise instead of spinning a worker forever.
    never_really = never.model_copy(update={"workdays": frozenset()})
    with pytest.raises(ValueError, match="no working days"):
        add_working_days(never_really, MONDAY, 5)


def test_zero_and_negative_durations_are_inert():
    assert add_working_days(FIVE_DAY, MONDAY, 0) == MONDAY
    assert elapsed_for_working(FIVE_DAY, MONDAY, 0) == 0.0
    assert elapsed_for_working(FIVE_DAY, MONDAY, -3) == 0.0


def test_the_six_to_five_day_ratio_matches_the_hand_calculation():
    """12 working days on a six-day week is 10 on a five-day week: both 14 elapsed."""
    six = describe(SIX_DAY, MONDAY, date(2026, 3, 2))
    five = describe(FIVE_DAY, MONDAY, date(2026, 3, 2))
    assert six.to_elapsed(12) == pytest.approx(14.0, abs=0.01)
    assert five.to_elapsed(10) == pytest.approx(14.0, abs=0.01)
