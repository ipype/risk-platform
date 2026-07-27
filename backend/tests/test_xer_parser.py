"""XER parser tests — driven from real XER text, not mocks."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.core.errors import (
    AmbiguousProjectError,
    MalformedScheduleFile,
    ParserUnavailable,
    ProjectNotFound,
    UnsupportedScheduleFormat,
)
from app.schedule.model import (
    ActivityStatus,
    ActivityType,
    ConstraintType,
    RelationshipType,
)
from app.schedule.parsers import (
    list_projects,
    parse_schedule,
    parser_for,
    supported_formats,
)
from tests.schedule_fixtures import simple_xer


@pytest.fixture
def schedule():
    return parse_schedule(simple_xer(), "pipeline.xer")


class TestStructure:
    def test_project_identity_comes_from_the_wbs_root_not_the_short_name(self, schedule):
        assert schedule.project_id == "1001"
        assert schedule.project_name == "Pipeline A — Phase 1"

    def test_data_date_and_must_finish_by(self, schedule):
        assert schedule.data_date == datetime(2026, 6, 1, 8, 0)
        assert schedule.must_finish_by == datetime(2026, 7, 10, 17, 0)

    def test_all_activities_parsed(self, schedule):
        assert {a.code for a in schedule.activities} == {"A1000", "A1010", "A1020", "A1030"}

    def test_wbs_parsed_with_parentage(self, schedule):
        by_id = {n.id: n for n in schedule.wbs}
        assert by_id["W0"].is_project_node is True
        assert by_id["W1"].parent_id == "W0"

    def test_source_metadata_recorded(self, schedule):
        assert schedule.source_format == "Primavera P6 XER"
        assert schedule.source_filename == "pipeline.xer"


class TestActivityMapping:
    def test_types_and_statuses(self, schedule):
        by_code = {a.code: a for a in schedule.activities}
        assert by_code["A1000"].type is ActivityType.START_MILESTONE
        assert by_code["A1030"].type is ActivityType.FINISH_MILESTONE
        assert by_code["A1010"].status is ActivityStatus.COMPLETED
        assert by_code["A1020"].status is ActivityStatus.IN_PROGRESS
        assert by_code["A1030"].status is ActivityStatus.NOT_STARTED

    def test_durations_convert_hours_to_working_days_on_the_activity_calendar(self, schedule):
        by_code = {a.code: a for a in schedule.activities}

        # 80h on the 8h/day calendar
        clearance = by_code["A1010"]
        assert clearance.original_duration.days == pytest.approx(10.0)
        assert clearance.original_duration.calendar_id == "CAL-1"

        # 240h on the 10h/day calendar is 24 days, not 30 — the calendar has to be
        # honoured per activity, not taken from the project default
        laying = by_code["A1020"]
        assert laying.original_duration.days == pytest.approx(24.0)
        assert laying.remaining_duration.days == pytest.approx(12.0)
        assert laying.original_duration.calendar_id == "CAL-2"

    def test_negative_float_survives_conversion(self, schedule):
        laying = next(a for a in schedule.activities if a.code == "A1020")
        assert laying.total_float.days == pytest.approx(-4.0)

    def test_constraints_mapped_and_classified(self, schedule):
        laying = next(a for a in schedule.activities if a.code == "A1020")
        assert laying.constraint_type is ConstraintType.START_ON
        assert laying.has_hard_constraint is True

        clearance = next(a for a in schedule.activities if a.code == "A1010")
        assert clearance.constraint_type is ConstraintType.NONE
        assert clearance.has_hard_constraint is False

    def test_actual_dates_kept_separate_from_forecast(self, schedule):
        clearance = next(a for a in schedule.activities if a.code == "A1010")
        assert clearance.actual_finish == datetime(2026, 5, 15, 17, 0)
        assert clearance.baseline_finish == datetime(2026, 5, 15, 17, 0)

        milestone = next(a for a in schedule.activities if a.code == "A1030")
        assert milestone.actual_finish is None
        assert milestone.forecast_finish == datetime(2026, 7, 3, 17, 0)

    def test_cost_is_integer_minor_units(self, schedule):
        laying = next(a for a in schedule.activities if a.code == "A1020")
        assert laying.budgeted_cost == 12500050
        assert laying.has_resource_assignment is True

        clearance = next(a for a in schedule.activities if a.code == "A1010")
        assert clearance.budgeted_cost is None
        assert clearance.has_resource_assignment is False


class TestRelationships:
    def test_types_and_lag(self, schedule):
        by_id = {r.id: r for r in schedule.relationships}
        assert by_id["R2"].type is RelationshipType.SS
        # 16h lag measured on the successor's 10h/day calendar
        assert by_id["R2"].lag.days == pytest.approx(1.6)
        assert by_id["R2"].is_lag is True

    def test_negative_lag_is_flagged_as_a_lead(self, schedule):
        lead = next(r for r in schedule.relationships if r.id == "R3")
        assert lead.is_lead is True
        assert lead.lag_days == pytest.approx(-1.0)

    def test_relationships_outside_the_project_are_dropped_with_a_warning(self, schedule):
        assert "X9" not in {r.predecessor_id for r in schedule.relationships}
        assert any("outside this project" in w for w in schedule.warnings)

    def test_adjacency_is_built_both_ways(self, schedule):
        assert schedule.predecessor_ids["T2"] == {"T1"}
        assert schedule.successor_ids["T1"] == {"T2"}


class TestCalendars:
    def test_workweek_read_from_the_calendar_blob(self, schedule):
        five_day = schedule.calendar("CAL-1")
        six_day = schedule.calendar("CAL-2")
        assert five_day.workdays == frozenset({0, 1, 2, 3, 4})
        assert six_day.workdays == frozenset({0, 1, 2, 3, 4, 5})
        assert five_day.is_default is True

    def test_holidays_are_honoured_in_working_day_arithmetic(self):
        # 2026-06-15 is a Monday
        schedule = parse_schedule(simple_xer(holidays=[date(2026, 6, 15)]), "p.xer")
        calendar = schedule.calendar("CAL-1")
        assert calendar.is_workday(date(2026, 6, 15)) is False
        # Mon 8 Jun to Mon 22 Jun is 10 working days, less the holiday
        assert calendar.working_days_between(date(2026, 6, 8), date(2026, 6, 22)) == 9

    def test_working_days_between_is_signed_and_symmetric(self, schedule):
        calendar = schedule.calendar("CAL-1")
        forward = calendar.working_days_between(date(2026, 6, 1), date(2026, 6, 15))
        backward = calendar.working_days_between(date(2026, 6, 15), date(2026, 6, 1))
        assert forward == 10
        assert backward == -10

    def test_unknown_calendar_falls_back_to_the_default(self, schedule):
        assert schedule.calendar("nope").id == "CAL-1"


class TestProjectSelection:
    def test_multiple_projects_refuses_to_guess(self):
        data = simple_xer(extra_projects=True)
        with pytest.raises(AmbiguousProjectError) as excinfo:
            parse_schedule(data, "multi.xer")
        assert "1001" in str(excinfo.value)
        assert "2002" in str(excinfo.value)

    def test_explicit_project_id_selects_one(self):
        data = simple_xer(extra_projects=True)
        schedule = parse_schedule(data, "multi.xer", project_id="2002")
        assert schedule.project_id == "2002"
        assert len(schedule.activities) == 1

    def test_unknown_project_id_is_rejected(self):
        with pytest.raises(ProjectNotFound):
            parse_schedule(simple_xer(extra_projects=True), "multi.xer", project_id="9999")

    def test_list_projects_reports_activity_counts(self):
        found = list_projects(simple_xer(extra_projects=True), "multi.xer")
        assert dict((pid, count) for pid, _, count in found) == {"1001": 4, "2002": 1}


class TestEdges:
    def test_a_file_with_no_task_table_is_rejected(self):
        from tests.schedule_fixtures import xer_document

        data = xer_document([("PROJECT", [{"proj_id": "1", "proj_short_name": "X"}])])
        with pytest.raises(MalformedScheduleFile):
            parse_schedule(data, "empty.xer")

    def test_missing_trailing_columns_are_padded_not_dropped(self):
        # rows exported with trailing empty columns omitted are common in the wild
        text = (
            "ERMHDR\t19.12\t2026-07-26\tProject\tadmin\tAdmin\tdb\tProject\tCP1252\n"
            "%T\tPROJECT\n%F\tproj_id\tproj_short_name\tlast_recalc_date\n"
            "%R\t1001\tP\n"
            "%T\tTASK\n"
            "%F\ttask_id\tproj_id\ttask_code\ttask_name\ttask_type\tstatus_code\t"
            "target_drtn_hr_cnt\tremain_drtn_hr_cnt\ttotal_float_hr_cnt\n"
            "%R\tT1\t1001\tA10\tOnly activity\tTT_Task\tTK_NotStart\n"
            "%E\n"
        ).encode("cp1252")
        schedule = parse_schedule(text, "short.xer")
        assert schedule.activities[0].code == "A10"
        assert schedule.activities[0].original_duration is None

    def test_utf8_declared_files_decode(self):
        schedule = parse_schedule(simple_xer(code_page="CP1252"), "p.xer")
        assert "—" in schedule.project_name

    def test_absent_calendar_table_warns_and_assumes_5x8(self):
        text = (
            "ERMHDR\t19.12\t2026-07-26\tProject\tadmin\tAdmin\tdb\tProject\tCP1252\n"
            "%T\tPROJECT\n%F\tproj_id\tproj_short_name\n%R\t1001\tP\n"
            "%T\tTASK\n"
            "%F\ttask_id\tproj_id\ttask_code\ttask_name\ttask_type\tstatus_code\t"
            "target_drtn_hr_cnt\n"
            "%R\tT1\t1001\tA10\tOnly activity\tTT_Task\tTK_NotStart\t40\n"
            "%E\n"
        ).encode("cp1252")
        schedule = parse_schedule(text, "nocal.xer")
        assert any("Monday–Friday 8h/day" in w for w in schedule.warnings)
        assert schedule.activities[0].original_duration.days == pytest.approx(5.0)


class TestRegistry:
    def test_unknown_suffix_is_rejected(self):
        with pytest.raises(UnsupportedScheduleFormat):
            parser_for("plan.pdf")

    def test_mpp_is_known_but_unavailable_and_says_why(self):
        with pytest.raises(ParserUnavailable) as excinfo:
            parser_for("plan.mpp")
        message = str(excinfo.value)
        assert "JRE" in message
        assert ".xer" in message

    def test_supported_formats_distinguishes_known_from_usable(self):
        formats = {tuple(f["suffixes"]): f for f in supported_formats()}
        assert formats[(".xer",)]["available"] is True
        assert formats[(".mpp",)]["available"] is False
        assert formats[(".mpp",)]["reason"]
