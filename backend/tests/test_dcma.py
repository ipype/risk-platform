"""DCMA 14-point gate tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.schedule.dcma import CheckStatus, DcmaThresholds, run_dcma
from app.schedule.model import (
    ActivityStatus,
    ActivityType,
    ConstraintType,
    Relationship,
    RelationshipType,
    WorkingDuration,
)
from app.schedule.parsers import parse_schedule
from tests.schedule_fixtures import chain, days, make_activity, make_schedule, simple_xer

DATA_DATE = datetime(2026, 6, 1, 8, 0)


def check(report, number):
    return next(c for c in report.checks if c.number == number)


class TestReportShape:
    def test_all_fourteen_checks_run(self):
        report = run_dcma(make_schedule([make_activity(i) for i in range(5)]))
        assert [c.number for c in report.checks] == list(range(1, 15))

    def test_counts_add_up(self):
        report = run_dcma(make_schedule([make_activity(i) for i in range(5)]))
        assert (
            report.passed_count + report.failed_count + report.not_assessed_count == 14
        )


class TestCheck01Logic:
    def test_a_clean_chain_passes_with_ends_excused(self):
        activities = [make_activity(i) for i in range(10)]
        report = run_dcma(make_schedule(activities))
        result = check(report, 1)
        assert result.status is CheckStatus.PASS
        assert result.offender_count == 0

    def test_orphan_activities_fail(self):
        activities = [make_activity(i) for i in range(10)]
        # wire only the first five; the remaining five dangle at both ends
        report = run_dcma(make_schedule(activities, chain(activities[:5])))
        result = check(report, 1)
        assert result.status is CheckStatus.FAIL
        assert result.offender_count > 0
        assert result.blocking is True

    def test_completed_activities_are_out_of_scope(self):
        activities = [
            make_activity(i, status=ActivityStatus.COMPLETED) for i in range(10)
        ]
        result = check(run_dcma(make_schedule(activities, [])), 1)
        assert result.status is CheckStatus.NOT_ASSESSED


class TestCheck02And03Leads:
    def _with_lag(self, lag_days: float):
        activities = [make_activity(i) for i in range(4)]
        links = chain(activities)
        links[0] = links[0].model_copy(update={"lag": days(lag_days)})
        return run_dcma(make_schedule(activities, links))

    def test_negative_lag_fails_the_leads_check(self):
        result = check(self._with_lag(-2), 2)
        assert result.status is CheckStatus.FAIL
        assert result.offender_count == 1

    def test_positive_lag_counts_as_lag_not_lead(self):
        report = self._with_lag(2)
        assert check(report, 2).status is CheckStatus.PASS
        # 1 of 3 relationships is 33%, above the 5% tolerance
        assert check(report, 3).status is CheckStatus.FAIL

    def test_zero_lag_is_neither(self):
        report = self._with_lag(0)
        assert check(report, 2).status is CheckStatus.PASS
        assert check(report, 3).status is CheckStatus.PASS


class TestCheck04RelationshipTypes:
    def test_all_fs_passes(self):
        activities = [make_activity(i) for i in range(5)]
        assert check(run_dcma(make_schedule(activities)), 4).status is CheckStatus.PASS

    def test_too_many_non_fs_fails(self):
        activities = [make_activity(i) for i in range(5)]
        links = [
            r.model_copy(update={"type": RelationshipType.SS}) for r in chain(activities)
        ]
        result = check(run_dcma(make_schedule(activities, links)), 4)
        assert result.status is CheckStatus.FAIL
        assert result.metric == pytest.approx(0.0)


class TestCheck05HardConstraints:
    def test_soft_constraints_do_not_count(self):
        activities = [
            make_activity(i, constraint=ConstraintType.START_ON_OR_AFTER)
            for i in range(10)
        ]
        assert check(run_dcma(make_schedule(activities)), 5).status is CheckStatus.PASS

    def test_mandatory_dates_fail(self):
        activities = [make_activity(i) for i in range(10)]
        activities[0] = activities[0].model_copy(
            update={"constraint_type": ConstraintType.MANDATORY_FINISH}
        )
        result = check(run_dcma(make_schedule(activities)), 5)
        assert result.status is CheckStatus.FAIL
        assert result.metric == pytest.approx(10.0)


class TestCheck06And07Float:
    def test_high_float_fails(self):
        activities = [make_activity(i, total_float=100.0) for i in range(10)]
        assert check(run_dcma(make_schedule(activities)), 6).status is CheckStatus.FAIL

    def test_float_at_the_threshold_passes(self):
        activities = [make_activity(i, total_float=44.0) for i in range(10)]
        assert check(run_dcma(make_schedule(activities)), 6).status is CheckStatus.PASS

    def test_a_single_negative_float_activity_blocks_the_gate(self):
        activities = [make_activity(i) for i in range(20)]
        activities[3] = activities[3].model_copy(update={"total_float": days(-2)})
        report = run_dcma(make_schedule(activities))
        result = check(report, 7)
        assert result.status is CheckStatus.FAIL
        assert result.offenders == (activities[3].code,)
        assert report.gate_passed is False


class TestCheck08Duration:
    def test_long_activities_fail(self):
        activities = [make_activity(i, remaining_duration=60.0) for i in range(10)]
        assert check(run_dcma(make_schedule(activities)), 8).status is CheckStatus.FAIL

    def test_milestones_are_excluded_from_the_population(self):
        activities = [
            make_activity(i, type=ActivityType.START_MILESTONE, remaining_duration=0.0)
            for i in range(5)
        ]
        assert check(run_dcma(make_schedule(activities)), 8).status is CheckStatus.NOT_ASSESSED


class TestCheck09InvalidDates:
    def test_unstarted_work_forecast_to_start_in_the_past_fails(self):
        activities = [make_activity(i) for i in range(5)]
        activities[2] = activities[2].model_copy(
            update={"early_start": datetime(2026, 5, 1, 8, 0)}
        )
        report = run_dcma(make_schedule(activities, data_date=DATA_DATE))
        assert check(report, 9).status is CheckStatus.FAIL
        assert report.gate_passed is False

    def test_in_progress_work_that_started_before_the_data_date_is_not_an_offender(self):
        """P6 pins an in-progress activity's early start to its actual start.

        Flagging that as an invalid date fires on every schedule with work under way,
        which would block the gate on essentially every real project.
        """
        activities = [make_activity(i) for i in range(5)]
        activities[2] = activities[2].model_copy(
            update={
                "status": ActivityStatus.IN_PROGRESS,
                "early_start": datetime(2026, 5, 1, 8, 0),
                "actual_start": datetime(2026, 5, 1, 8, 0),
                "early_finish": datetime(2026, 6, 20, 17, 0),
            }
        )
        report = run_dcma(make_schedule(activities, data_date=DATA_DATE))
        assert check(report, 9).status is CheckStatus.PASS

    def test_remaining_work_forecast_to_finish_in_the_past_still_fails(self):
        activities = [make_activity(i) for i in range(5)]
        activities[2] = activities[2].model_copy(
            update={
                "status": ActivityStatus.IN_PROGRESS,
                "actual_start": datetime(2026, 5, 1, 8, 0),
                "early_start": datetime(2026, 5, 1, 8, 0),
                "early_finish": datetime(2026, 5, 20, 17, 0),
            }
        )
        report = run_dcma(make_schedule(activities, data_date=DATA_DATE))
        assert check(report, 9).status is CheckStatus.FAIL

    def test_actuals_in_the_future_fail(self):
        activities = [make_activity(i) for i in range(5)]
        activities[1] = activities[1].model_copy(
            update={
                "status": ActivityStatus.COMPLETED,
                "actual_start": datetime(2026, 5, 20, 8, 0),
                "actual_finish": datetime(2026, 6, 20, 8, 0),
            }
        )
        assert check(run_dcma(make_schedule(activities)), 9).status is CheckStatus.FAIL

    def test_without_a_data_date_the_check_abstains_rather_than_passing(self):
        activities = [make_activity(i) for i in range(5)]
        report = run_dcma(make_schedule(activities, data_date=None))
        result = check(report, 9)
        assert result.status is CheckStatus.NOT_ASSESSED
        # abstaining must not silently open the gate on a blocking check
        assert "data date" in result.note


class TestCheck10Resources:
    def test_no_resource_data_at_all_abstains(self):
        activities = [make_activity(i) for i in range(5)]
        result = check(run_dcma(make_schedule(activities)), 10)
        assert result.status is CheckStatus.NOT_ASSESSED
        assert "exported without them" in result.note

    def test_partial_resourcing_fails(self):
        activities = [make_activity(i) for i in range(10)]
        activities[0] = activities[0].model_copy(update={"has_resource_assignment": True})
        assert check(run_dcma(make_schedule(activities)), 10).status is CheckStatus.FAIL

    def test_fully_resourced_passes(self):
        activities = [make_activity(i, has_resource_assignment=True) for i in range(10)]
        assert check(run_dcma(make_schedule(activities)), 10).status is CheckStatus.PASS


class TestCheck11And14BaselineComparisons:
    def _baselined(self, *, completed: int, total: int):
        activities = []
        for i in range(total):
            done = i < completed
            activities.append(
                make_activity(
                    i,
                    status=ActivityStatus.COMPLETED if done else ActivityStatus.NOT_STARTED,
                    baseline_finish=datetime(2026, 5, 20, 8, 0),
                    actual_finish=datetime(2026, 5, 19, 8, 0) if done else None,
                    actual_start=datetime(2026, 5, 1, 8, 0) if done else None,
                )
            )
        return run_dcma(make_schedule(activities, data_date=DATA_DATE))

    def test_all_baselined_work_done_on_time_passes_both(self):
        report = self._baselined(completed=10, total=10)
        assert check(report, 11).status is CheckStatus.PASS
        assert check(report, 14).status is CheckStatus.PASS
        assert check(report, 14).metric == pytest.approx(1.0)

    def test_slipped_work_fails_missed_tasks_and_drops_bei(self):
        report = self._baselined(completed=5, total=10)
        assert check(report, 11).status is CheckStatus.FAIL
        assert check(report, 14).status is CheckStatus.FAIL
        assert check(report, 14).metric == pytest.approx(0.5)

    def test_no_baseline_dates_means_abstain_not_pass(self):
        activities = [make_activity(i) for i in range(5)]
        report = run_dcma(make_schedule(activities, data_date=DATA_DATE))
        assert check(report, 11).status is CheckStatus.NOT_ASSESSED
        assert check(report, 14).status is CheckStatus.NOT_ASSESSED


class TestCheck12And13:
    def test_critical_path_test_abstains_and_explains(self):
        result = check(run_dcma(make_schedule([make_activity(0)])), 12)
        assert result.status is CheckStatus.NOT_ASSESSED
        assert "fabrication" in result.note

    def test_cpli_uses_the_must_finish_by_date_when_present(self):
        activities = [
            make_activity(
                0,
                early_finish=datetime(2026, 6, 29, 17, 0),  # 20 working days out
                total_float=0.0,
            )
        ]
        report = run_dcma(
            make_schedule(
                activities,
                [],
                data_date=DATA_DATE,
                must_finish_by=datetime(2026, 7, 13, 17, 0),  # 10 more working days
            )
        )
        result = check(report, 13)
        assert result.status is CheckStatus.PASS
        assert result.metric == pytest.approx(1.5)  # (20 + 10) / 20

    def test_cpli_below_one_fails(self):
        activities = [
            make_activity(0, early_finish=datetime(2026, 6, 29, 17, 0), total_float=0.0)
        ]
        report = run_dcma(
            make_schedule(
                activities,
                [],
                data_date=DATA_DATE,
                must_finish_by=datetime(2026, 6, 15, 17, 0),  # 10 working days short
            )
        )
        result = check(report, 13)
        assert result.status is CheckStatus.FAIL
        assert result.metric == pytest.approx(0.5)

    def test_cpli_abstains_without_a_data_date(self):
        report = run_dcma(make_schedule([make_activity(0)], data_date=None))
        assert check(report, 13).status is CheckStatus.NOT_ASSESSED


class TestGateSemantics:
    def test_non_blocking_failures_do_not_close_the_gate(self):
        # every activity carries a mandatory constraint: check 5 fails, gate still open
        activities = [
            make_activity(i, constraint=ConstraintType.MANDATORY_START) for i in range(10)
        ]
        report = run_dcma(make_schedule(activities, data_date=DATA_DATE))
        assert check(report, 5).status is CheckStatus.FAIL
        assert report.gate_passed is True
        assert "PASS" in report.summary()

    def test_blocking_set_is_configurable(self):
        activities = [
            make_activity(i, constraint=ConstraintType.MANDATORY_START) for i in range(10)
        ]
        thresholds = DcmaThresholds(blocking_checks=frozenset({5}))
        report = run_dcma(make_schedule(activities, data_date=DATA_DATE), thresholds)
        assert report.gate_passed is False
        assert [c.number for c in report.blocking_failures] == [5]

    def test_summary_reads_as_blocked_when_it_is(self):
        activities = [make_activity(i) for i in range(20)]
        activities[0] = activities[0].model_copy(update={"total_float": days(-1)})
        report = run_dcma(make_schedule(activities, data_date=DATA_DATE))
        assert "BLOCKED" in report.summary()


class TestEndToEnd:
    def test_the_parsed_fixture_schedule_is_blocked_on_negative_float(self):
        schedule = parse_schedule(simple_xer(), "pipeline.xer")
        report = run_dcma(schedule)
        assert report.project_name == "Pipeline A — Phase 1"
        assert check(report, 7).status is CheckStatus.FAIL
        assert report.gate_passed is False
        assert "BLOCKED" in report.summary()


class TestDurationInvariant:
    def test_durations_from_different_calendars_refuse_to_combine(self):
        a = WorkingDuration(days=5, calendar_id="CAL-1")
        b = WorkingDuration(days=5, calendar_id="CAL-2")
        with pytest.raises(ValueError, match="different calendars"):
            _ = a + b

    def test_same_calendar_arithmetic_works(self):
        a = WorkingDuration(days=5, calendar_id="CAL-1")
        b = WorkingDuration(days=3, calendar_id="CAL-1")
        assert (a + b).days == 8
        assert (a - b).days == 2

    def test_a_duration_cannot_be_mutated(self):
        duration = WorkingDuration(days=5, calendar_id="CAL-1")
        with pytest.raises(Exception):
            duration.days = 9


class TestRelationshipHelpers:
    def test_lag_defaults_to_zero_when_absent(self):
        relationship = Relationship(id="R", predecessor_id="A", successor_id="B")
        assert relationship.lag_days == 0.0
        assert relationship.is_lead is False
        assert relationship.is_lag is False
