"""Dependency links on the Gantt payload.

Driven through ``build_payload`` rather than the route: the rule worth testing is which
links survive, and that is decided entirely by which bars came back after filtering and
truncation. The last class goes through a real upload so the two halves are known to fit.
"""

from __future__ import annotations

from app.schedule.model import Relationship, RelationshipType, WorkingDuration
from app.services.schedule_gantt import MAX_GANTT_LINKS, build_payload
from tests.schedule_fixtures import chain, make_activity, make_schedule
from tests.test_schedule_api import do_upload
from tests.test_schedule_gantt import version_row

# No module-level asyncio mark: most of this file is synchronous, and pytest.ini already
# runs in ``asyncio_mode = auto``.


def link(pred: str, succ: str, kind: RelationshipType = RelationshipType.FS, lag=None):
    return Relationship(
        id=f"{pred}->{succ}",
        predecessor_id=pred,
        successor_id=succ,
        type=kind,
        lag=WorkingDuration(days=lag, calendar_id="CAL-1") if lag is not None else None,
    )


def payload_for(activities, relationships=None, **kw):
    schedule = make_schedule(activities, relationships)
    return build_payload(schedule, version_row(), None, **kw)


class TestShape:
    def test_carries_every_link_between_drawn_bars(self):
        activities = [make_activity(i) for i in range(4)]
        out = payload_for(activities, chain(activities))

        assert out.link_counts.total == 3
        assert out.link_counts.drawable == 3
        assert out.link_counts.dangling == 0
        assert out.link_counts.truncated is False
        assert len(out.links) == 3

    def test_keeps_the_type_and_lag_the_arrow_needs(self):
        activities = [make_activity(i) for i in range(2)]
        out = payload_for(
            activities, [link("T0", "T1", RelationshipType.SS, lag=-3.0)]
        )

        drawn = out.links[0]
        assert drawn.predecessor_source_id == "T0"
        assert drawn.successor_source_id == "T1"
        assert drawn.type == "SS"
        assert drawn.lag_days == -3.0

    def test_a_link_is_critical_only_when_both_ends_are(self):
        activities = [
            make_activity(0).model_copy(update={"is_critical": True}),
            make_activity(1).model_copy(update={"is_critical": True}),
            make_activity(2),
        ]
        out = payload_for(activities, [link("T0", "T1"), link("T1", "T2")])

        by_pair = {
            (link_.predecessor_source_id, link_.successor_source_id): link_.is_critical
            for link_ in out.links
        }
        assert by_pair[("T0", "T1")] is True
        assert by_pair[("T1", "T2")] is False


class TestEndpointsOutsideTheView:
    def test_a_link_into_another_project_is_counted_not_drawn(self):
        activities = [make_activity(i) for i in range(2)]
        out = payload_for(
            activities, [link("T0", "T1"), link("T1", "SOMEONE-ELSES-ACTIVITY")]
        )

        assert out.link_counts.total == 2
        assert out.link_counts.drawable == 1
        assert out.link_counts.dangling == 1
        assert [(x.predecessor_source_id, x.successor_source_id) for x in out.links] == [
            ("T0", "T1")
        ]

    def test_a_filter_that_removes_one_end_removes_the_link(self):
        activities = [
            make_activity(0).model_copy(update={"is_critical": True}),
            make_activity(1).model_copy(update={"is_critical": True}),
            make_activity(2),
        ]
        out = payload_for(
            activities, [link("T0", "T1"), link("T1", "T2")], critical_only=True
        )

        assert out.returned == 2
        assert out.link_counts.drawable == 1
        assert out.link_counts.dangling == 1

    def test_truncation_drops_the_links_it_cut_the_bars_for(self):
        activities = [make_activity(i) for i in range(6)]
        out = payload_for(activities, chain(activities), limit=3)

        assert out.truncated is True
        assert out.returned == 3
        # T0-T1 and T1-T2 survive; the three that reach a truncated bar do not
        assert out.link_counts.drawable == 2
        assert out.link_counts.dangling == 3
        assert len(out.links) == 2

    def test_no_arrow_ever_terminates_in_empty_space(self):
        """The property the two counts above are really protecting."""
        activities = [make_activity(i) for i in range(8)]
        out = payload_for(activities, chain(activities), limit=4)

        drawn = {bar.source_id for bar in out.activities}
        for edge in out.links:
            assert edge.predecessor_source_id in drawn
            assert edge.successor_source_id in drawn


class TestCeiling:
    def test_the_link_list_is_capped_and_says_so(self, monkeypatch):
        # Two activities, many parallel links between them: cheap to build, and it
        # exercises the cap without constructing ten thousand bars.
        monkeypatch.setattr("app.services.schedule_gantt.MAX_GANTT_LINKS", 5)
        activities = [make_activity(0), make_activity(1)]
        relationships = [
            Relationship(
                id=f"R{i}",
                predecessor_id="T0",
                successor_id="T1",
                type=RelationshipType.FS,
            )
            for i in range(12)
        ]
        out = payload_for(activities, relationships)

        assert len(out.links) == 5
        assert out.link_counts.drawable == 12
        assert out.link_counts.truncated is True

    def test_the_real_ceiling_is_above_the_row_ceiling(self):
        """A schedule at the row cap should not routinely lose its logic."""
        assert MAX_GANTT_LINKS > 5000


class TestThroughTheRoute:
    async def test_upload_then_gantt_returns_the_parsed_logic(self, client):
        version_id = (await do_upload(client)).json()["version"]["id"]

        body = (await client.get(f"/schedules/{version_id}/gantt")).json()

        assert body["link_counts"]["total"] == 3
        assert len(body["links"]) == body["link_counts"]["drawable"]
        drawn = {bar["source_id"] for bar in body["activities"]}
        for edge in body["links"]:
            assert edge["predecessor_source_id"] in drawn
            assert edge["successor_source_id"] in drawn
            assert edge["type"] in {"FS", "SS", "FF", "SF"}
