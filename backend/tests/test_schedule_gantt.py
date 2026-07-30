"""The Gantt payload: date resolution, WBS ordering, rollup, filters, truncation.

Most of this drives ``build_payload`` directly, because the shaping rules are where the
bugs live and a route test would only reach them through a parse. The last class goes
through the real upload path to prove the two halves fit together.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.schedule import ScheduleVersion
from app.schedule.model import ActivityStatus, ActivityType, Schedule, WbsNode
from app.services.schedule_gantt import (
    MAX_GANTT_ROWS,
    NO_WBS_KEY,
    build_payload,
)
from tests.schedule_fixtures import (
    DEFAULT_CALENDAR,
    chain,
    make_activity,
    make_schedule,
)
from tests.test_schedule_api import do_upload

BASE = datetime(2026, 6, 1, 8, 0)


def version_row(**over) -> ScheduleVersion:
    """A detached version row. ``build_payload`` reads attributes and nothing else."""
    row = ScheduleVersion(
        id=over.pop("id", 7),
        file_id=1,
        source_project_id="1001",
        project_name="Test project",
        source_format="Primavera P6 XER",
        parser_version="xer-1",
        activity_count=over.pop("activity_count", 0),
        relationship_count=0,
        warnings=[],
        is_current=True,
        created_by="sam",
    )
    for key, value in over.items():
        setattr(row, key, value)
    return row


def with_wbs(activity, wbs_id: str | None):
    return activity.model_copy(update={"wbs_id": wbs_id})


def schedule_with(activities, wbs_nodes=(), **kw) -> Schedule:
    base = make_schedule(list(activities), **kw)
    return base.model_copy(update={"wbs": tuple(wbs_nodes)})


def payload_for(activities, wbs_nodes=(), *, schedule_kw=None, **kw):
    """``kw`` goes straight to ``build_payload``, so ``wbs=`` there is the filter."""
    schedule = schedule_with(activities, wbs_nodes, **(schedule_kw or {}))
    return build_payload(schedule, version_row(), None, **kw)


def bars_by_code(payload) -> dict:
    return {bar.code: bar for bar in payload.activities}


class TestDateResolution:
    def test_not_started_uses_early_dates(self):
        bar = payload_for([make_activity(1)]).activities[0]
        assert bar.basis == "planned"
        assert bar.start == BASE + timedelta(days=1)
        assert bar.finish == BASE + timedelta(days=11)

    def test_complete_uses_actual_dates(self):
        activity = make_activity(
            1,
            status=ActivityStatus.COMPLETED,
            actual_start=BASE,
            actual_finish=BASE + timedelta(days=4),
            remaining_duration=0.0,
        )
        bar = payload_for([activity]).activities[0]
        assert bar.basis == "actual"
        assert (bar.start, bar.finish) == (BASE, BASE + timedelta(days=4))
        assert bar.duration_pct_complete == 1.0

    def test_in_progress_runs_from_actual_start_to_forecast_finish(self):
        activity = make_activity(
            2,
            status=ActivityStatus.IN_PROGRESS,
            actual_start=BASE,
            early_finish=BASE + timedelta(days=20),
            remaining_duration=4.0,
        )
        bar = payload_for([activity]).activities[0]
        assert bar.basis == "in_progress"
        assert bar.start == BASE
        assert bar.finish == BASE + timedelta(days=20)
        # original and remaining are both 4 here, so nothing is burned yet
        assert bar.duration_pct_complete == 0.0

    def test_partial_progress_is_derived_from_remaining_against_original(self):
        activity = make_activity(1, remaining_duration=10.0).model_copy(
            update={
                "status": ActivityStatus.IN_PROGRESS,
                "actual_start": BASE,
                "remaining_duration": make_activity(1).original_duration.model_copy(
                    update={"days": 2.5}
                ),
            }
        )
        bar = payload_for([activity]).activities[0]
        assert bar.duration_pct_complete == pytest.approx(0.75)

    def test_undated_activity_is_reported_rather_than_placed_at_the_epoch(self):
        activity = make_activity(1, early_start=None, early_finish=None).model_copy(
            update={"early_start": None, "early_finish": None}
        )
        bar = payload_for([activity]).activities[0]
        assert bar.basis == "undated"
        assert bar.start is None and bar.finish is None

    def test_single_ended_activity_becomes_a_point(self):
        activity = make_activity(1).model_copy(update={"early_start": None})
        bar = payload_for([activity]).activities[0]
        assert bar.start == bar.finish == BASE + timedelta(days=11)

    def test_finish_before_start_is_clamped_not_drawn_backwards(self):
        activity = make_activity(1).model_copy(
            update={"early_start": BASE + timedelta(days=9), "early_finish": BASE}
        )
        bar = payload_for([activity]).activities[0]
        assert bar.finish == bar.start == BASE + timedelta(days=9)

    def test_slip_is_measured_against_baseline_finish_in_calendar_days(self):
        activity = make_activity(1, baseline_finish=BASE + timedelta(days=5))
        bar = payload_for([activity]).activities[0]
        # forecast finish is day 11, baseline day 5
        assert bar.baseline_slip_calendar_days == pytest.approx(6.0)

    def test_no_baseline_means_no_slip_rather_than_zero_slip(self):
        assert (
            payload_for([make_activity(1)]).activities[0].baseline_slip_calendar_days
            is None
        )

    def test_milestone_and_summary_rows_are_flagged(self):
        activities = [
            make_activity(1, type=ActivityType.FINISH_MILESTONE),
            make_activity(2, type=ActivityType.LEVEL_OF_EFFORT),
            make_activity(3),
        ]
        bars = bars_by_code(payload_for(activities))
        assert bars["A1010"].is_milestone is True
        assert bars["A1020"].is_summary_row is True
        assert (bars["A1030"].is_milestone, bars["A1030"].is_summary_row) == (
            False,
            False,
        )

    def test_duration_days_carry_the_calendar_they_were_measured_on(self):
        bar = payload_for([make_activity(1)]).activities[0]
        assert bar.duration_calendar_id == DEFAULT_CALENDAR.id


class TestWbsOrdering:
    def tree(self):
        return (
            WbsNode(id="P", code="PRJ", name="Project", is_project_node=True),
            WbsNode(id="W1", code="1", name="Civil", parent_id="P"),
            WbsNode(id="W11", code="1.1", name="Earthworks", parent_id="W1"),
            WbsNode(id="W2", code="2", name="Mechanical", parent_id="P"),
        )

    def activities(self):
        return [
            with_wbs(make_activity(3), "W2"),
            with_wbs(make_activity(2), "W11"),
            with_wbs(make_activity(1), "W1"),
            with_wbs(make_activity(4), None),
        ]

    def test_depth_first_in_import_order_with_depths(self):
        payload = payload_for(self.activities(), self.tree())
        assert [(w.source_id, w.depth) for w in payload.wbs] == [
            ("P", 0),
            ("W1", 1),
            ("W11", 2),
            ("W2", 1),
            (NO_WBS_KEY, 0),
        ]

    def test_activities_follow_the_tree_then_start_date(self):
        payload = payload_for(self.activities(), self.tree())
        assert [b.code for b in payload.activities] == [
            "A1010",
            "A1020",
            "A1030",
            "A1040",
        ]

    def test_path_excludes_the_project_node(self):
        payload = payload_for(self.activities(), self.tree())
        paths = {w.source_id: w.path for w in payload.wbs}
        assert paths["W11"] == "Civil > Earthworks"
        assert paths["P"] == ""

    def test_rollup_spans_the_whole_subtree(self):
        payload = payload_for(self.activities(), self.tree())
        rows = {w.source_id: w for w in payload.wbs}
        assert rows["W1"].activity_count == 2  # its own plus Earthworks
        assert rows["W1"].start == BASE + timedelta(days=1)
        assert rows["W1"].finish == BASE + timedelta(days=12)
        assert rows["P"].activity_count == 3  # everything except the unparented row

    def test_unparented_activities_are_bucketed_last_not_dropped(self):
        payload = payload_for(self.activities(), self.tree())
        assert payload.wbs[-1].source_id == NO_WBS_KEY
        assert payload.wbs[-1].activity_count == 1
        assert payload.activities[-1].code == "A1040"

    def test_dangling_wbs_reference_still_shows_the_activity(self):
        payload = payload_for([with_wbs(make_activity(1), "GONE")], self.tree())
        assert payload.total == 1
        assert payload.wbs[-1].source_id == NO_WBS_KEY

    def test_orphan_node_is_treated_as_a_root(self):
        tree = (
            *self.tree(),
            WbsNode(id="W9", code="9", name="Orphan", parent_id="MISSING"),
        )
        payload = payload_for([with_wbs(make_activity(1), "W9")], tree)
        rows = {w.source_id: w for w in payload.wbs}
        assert rows["W9"].depth == 0
        assert rows["W9"].activity_count == 1

    def test_a_cycle_does_not_hang_or_lose_activities(self):
        tree = (
            WbsNode(id="A", code="a", name="A", parent_id="B"),
            WbsNode(id="B", code="b", name="B", parent_id="A"),
        )
        payload = payload_for([with_wbs(make_activity(1), "A")], tree)
        assert {w.source_id for w in payload.wbs} == {"A", "B"}
        assert payload.total == 1

    def test_undated_activities_sort_after_dated_ones(self):
        undated = with_wbs(
            make_activity(1).model_copy(
                update={"early_start": None, "early_finish": None}
            ),
            "W1",
        )
        dated = with_wbs(make_activity(5), "W1")
        payload = payload_for([undated, dated], self.tree())
        assert [b.basis for b in payload.activities] == ["planned", "undated"]


class TestFilters:
    def tree(self):
        return (
            WbsNode(id="W1", code="1", name="Civil"),
            WbsNode(id="W11", code="1.1", name="Earthworks", parent_id="W1"),
            WbsNode(id="W2", code="2", name="Mechanical"),
        )

    def activities(self):
        return [
            with_wbs(make_activity(1), "W1"),
            with_wbs(make_activity(2), "W11"),
            with_wbs(
                make_activity(3, total_float=0.0).model_copy(
                    update={"is_critical": True}
                ),
                "W2",
            ),
        ]

    def test_wbs_filter_includes_descendants(self):
        payload = payload_for(self.activities(), self.tree(), wbs="W1")
        assert payload.total == 2
        assert {w.source_id for w in payload.wbs} == {"W1", "W11"}
        assert payload.filters["wbs"] == "W1"

    def test_critical_only_keeps_the_driving_work_and_prunes_empty_branches(self):
        payload = payload_for(self.activities(), self.tree(), critical_only=True)
        assert [b.code for b in payload.activities] == ["A1030"]
        assert {w.source_id for w in payload.wbs} == {"W2"}

    def test_text_filter_matches_code_or_name(self):
        assert payload_for(self.activities(), self.tree(), q="A1020").total == 1
        assert payload_for(self.activities(), self.tree(), q="activity").total == 3
        assert payload_for(self.activities(), self.tree(), q="nothing here").total == 0

    def test_empty_result_is_an_empty_payload_not_an_error(self):
        payload = payload_for(self.activities(), self.tree(), q="zzz")
        assert (payload.total, payload.returned, payload.truncated) == (0, 0, False)
        assert payload.window.start is None and payload.window.finish is None
        assert payload.wbs == []

    def test_window_covers_the_filtered_set_and_carries_the_data_date(self):
        payload = payload_for(
            self.activities(),
            self.tree(),
            schedule_kw={
                "data_date": BASE,
                "must_finish_by": BASE + timedelta(days=30),
            },
        )
        assert payload.window.start == BASE + timedelta(days=1)
        assert payload.window.finish == BASE + timedelta(days=13)
        assert payload.window.data_date == BASE
        assert payload.window.must_finish_by == BASE + timedelta(days=30)


class TestTruncation:
    def many(self, n=12):
        activities = [with_wbs(make_activity(i), "W1") for i in range(n)]
        return activities, (WbsNode(id="W1", code="1", name="Civil"),)

    def test_limit_cuts_the_bars_and_reports_the_true_total(self):
        activities, tree = self.many()
        payload = payload_for(activities, tree, limit=5)
        assert (payload.returned, payload.total, payload.truncated) == (5, 12, True)
        assert len(payload.activities) == 5

    def test_rollup_counts_are_computed_before_truncation(self):
        activities, tree = self.many()
        payload = payload_for(activities, tree, limit=5)
        # the branch header must not shrink to match a cut-short bar list
        assert payload.wbs[0].activity_count == 12
        assert payload.window.finish == max(
            b.finish for b in payload_for(activities, tree).activities
        )

    def test_untruncated_result_says_so(self):
        activities, tree = self.many(3)
        payload = payload_for(activities, tree, limit=100)
        assert payload.truncated is False
        assert payload.returned == payload.total == 3

    def test_limit_is_clamped_to_the_ceiling(self):
        activities, tree = self.many(2)
        assert payload_for(activities, tree, limit=10**6).limit == MAX_GANTT_ROWS
        assert payload_for(activities, tree, limit=0).limit == 1


class TestRoute:
    async def test_gantt_renders_a_real_parsed_schedule(self, client):
        upload = await do_upload(client, actor="sam")
        version_id = upload.json()["version"]["id"]

        resp = await client.get(f"/schedules/{version_id}/gantt")
        assert resp.status_code == 200
        body = resp.json()

        assert body["version"]["id"] == version_id
        assert body["returned"] == body["total"] == 4
        assert body["truncated"] is False
        assert len(body["activities"]) == 4
        assert body["window"]["start"] is not None
        assert body["window"]["finish"] is not None

    async def test_the_gate_verdict_travels_with_the_chart(self, client):
        """A failed schedule renders as well as a passing one, so it has to say so."""
        upload = await do_upload(client)
        version_id = upload.json()["version"]["id"]

        body = (await client.get(f"/schedules/{version_id}/gantt")).json()
        assert body["gate"]["gate_passed"] is False
        assert 7 in body["gate"]["blocking_failures"]
        assert body["gate"]["run_id"] == upload.json()["gate"]["run_id"]

    async def test_every_date_comes_back_naive(self, client):
        """``hydrate`` normalizes; this pins it, because a mixed set breaks min/max."""
        upload = await do_upload(client)
        body = (
            await client.get(f"/schedules/{upload.json()['version']['id']}/gantt")
        ).json()
        stamps = [b["start"] for b in body["activities"] if b["start"]]
        stamps += [b["finish"] for b in body["activities"] if b["finish"]]
        assert stamps
        assert not any(s.endswith("Z") or "+" in s[10:] for s in stamps)

    async def test_filters_reach_the_route(self, client):
        upload = await do_upload(client)
        version_id = upload.json()["version"]["id"]

        all_bars = (await client.get(f"/schedules/{version_id}/gantt")).json()
        one = (
            await client.get(
                f"/schedules/{version_id}/gantt",
                params={"q": all_bars["activities"][0]["code"]},
            )
        ).json()
        assert one["total"] == 1

        critical = (
            await client.get(
                f"/schedules/{version_id}/gantt", params={"critical_only": True}
            )
        ).json()
        assert critical["total"] <= all_bars["total"]
        assert all(b["is_critical"] for b in critical["activities"])

    async def test_limit_above_the_ceiling_is_refused_by_the_route(self, client):
        upload = await do_upload(client)
        version_id = upload.json()["version"]["id"]
        resp = await client.get(
            f"/schedules/{version_id}/gantt", params={"limit": MAX_GANTT_ROWS + 1}
        )
        assert resp.status_code == 422

    async def test_missing_version_is_a_404(self, client):
        assert (await client.get("/schedules/9999/gantt")).status_code == 404


class TestRelationshipTracing:
    async def test_touching_returns_only_that_activity_s_links(self, client):
        upload = await do_upload(client)
        version_id = upload.json()["version"]["id"]

        every = (await client.get(f"/schedules/{version_id}/relationships")).json()
        assert every["total"] == 3

        middle = every["items"][1]["successor_source_id"]
        traced = (
            await client.get(
                f"/schedules/{version_id}/relationships", params={"touching": middle}
            )
        ).json()
        assert traced["total"] >= 1
        assert all(
            middle in (item["predecessor_source_id"], item["successor_source_id"])
            for item in traced["items"]
        )

    async def test_unknown_activity_traces_to_nothing(self, client):
        upload = await do_upload(client)
        version_id = upload.json()["version"]["id"]
        traced = (
            await client.get(
                f"/schedules/{version_id}/relationships", params={"touching": "NOPE"}
            )
        ).json()
        assert traced["total"] == 0
        assert traced["items"] == []


class TestSanity:
    def test_chain_helper_is_still_wiring_logic(self):
        """Guards the fixtures this file leans on rather than the code under test."""
        activities = [make_activity(i) for i in range(3)]
        assert len(chain(activities)) == 2


class TestCounts:
    """Totals must describe the filtered schedule, not the page of bars returned."""

    def test_counts_survive_truncation(self):
        activities = [with_wbs(make_activity(i), "W1") for i in range(10)]
        critical = with_wbs(
            make_activity(99, total_float=0.0).model_copy(update={"is_critical": True}),
            "W1",
        )
        tree = (WbsNode(id="W1", code="1", name="Civil"),)
        payload = payload_for([*activities, critical], tree, limit=3)
        assert payload.returned == 3
        assert payload.counts.activities == 11
        assert payload.counts.critical == 1

    def test_counts_split_by_progress_and_flag_undated_work(self):
        undated = make_activity(1).model_copy(
            update={"early_start": None, "early_finish": None}
        )
        done = make_activity(2).model_copy(
            update={
                "status": ActivityStatus.COMPLETED,
                "actual_start": BASE,
                "actual_finish": BASE + timedelta(days=2),
            }
        )
        wip = make_activity(3).model_copy(
            update={"status": ActivityStatus.IN_PROGRESS, "actual_start": BASE}
        )
        milestone = make_activity(4, type=ActivityType.FINISH_MILESTONE)
        counts = payload_for([undated, done, wip, milestone]).counts
        assert counts.activities == 4
        assert (counts.undated, counts.complete, counts.in_progress) == (1, 1, 1)
        assert counts.milestones == 1

    def test_counts_follow_the_filter(self):
        activities = [
            with_wbs(make_activity(1), "W1"),
            with_wbs(make_activity(2), "W2"),
        ]
        tree = (
            WbsNode(id="W1", code="1", name="A"),
            WbsNode(id="W2", code="2", name="B"),
        )
        assert payload_for(activities, tree, wbs="W1").counts.activities == 1
