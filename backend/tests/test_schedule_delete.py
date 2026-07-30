"""Deleting an imported schedule.

Driven through the real routes rather than the service, because the parts most likely to
be wrong are the edges the route owns: the 409 that protects accepted mappings, the
promotion of a surviving version to current, and whether the stored bytes go with it.

Every direct database read goes through :func:`session`, which opens a session and closes
it again. The harness runs on in-memory SQLite, and SQLAlchemy backs a memory database
with a ``StaticPool`` — one DBAPI connection for the whole engine. A session left holding
an open transaction therefore holds the *only* connection, and the next request through
the client waits on it forever rather than failing. Scoping every read to a context
manager is what keeps that from being a lockup nobody can read off the traceback.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select

from app.models.mapping import MappingHistory, RiskActivityMapping
from app.models.schedule import (
    DcmaRun,
    ScheduleActivity,
    ScheduleFile,
    ScheduleRelationship,
    ScheduleVersion,
)
from tests.schedule_fixtures import simple_xer
from tests.test_schedule_api import do_upload

pytestmark = pytest.mark.asyncio

ACTOR = {"X-Actor": "Sam"}


@asynccontextmanager
async def session(session_factory):
    async with session_factory() as db:
        yield db


async def add_mapping(
    session_factory, version_id: int, status: str, risk_id: int = 1
) -> int:
    """One risk-to-activity mapping against a version.

    ``risk_id`` points at a table this harness does not create — SQLite does not enforce
    foreign keys unless asked to, and the mapping's own columns are all this exercises.
    """
    async with session(session_factory) as db:
        row = RiskActivityMapping(
            risk_id=risk_id,
            version_id=version_id,
            mapping_type="duration_driver",
            activity_source_id="T1",
            status=status,
            origin="manual",
            proposed_by="Sam",
        )
        db.add(row)
        await db.flush()
        mapping_id = row.id
        await db.commit()
        return mapping_id


async def count(session_factory, model, **where) -> int:
    stmt = select(func.count()).select_from(model)
    for column, value in where.items():
        stmt = stmt.where(getattr(model, column) == value)
    async with session(session_factory) as db:
        return int(await db.scalar(stmt) or 0)


class TestImpact:
    async def test_counts_what_would_go(self, client):
        version_id = (await do_upload(client)).json()["version"]["id"]

        body = (await client.get(f"/schedules/{version_id}/delete-impact")).json()

        assert body["version_id"] == version_id
        assert body["activities"] == 4
        assert body["relationships"] == 3
        assert body["dcma_runs"] == 1
        assert body["is_current"] is True
        # the only version of this project, so nothing is promoted in its place
        assert body["promotes_version_id"] is None
        assert body["file_removable"] is True
        assert body["needs_force"] is False

    async def test_flags_accepted_mappings(self, client, session_factory):
        version_id = (await do_upload(client)).json()["version"]["id"]
        await add_mapping(session_factory, version_id, "accepted")
        await add_mapping(session_factory, version_id, "proposed")

        body = (await client.get(f"/schedules/{version_id}/delete-impact")).json()

        assert body["mappings_total"] == 2
        assert body["mappings_accepted"] == 1
        assert body["mappings_proposed"] == 1
        assert body["needs_force"] is True

    async def test_names_the_version_that_would_take_over(self, client):
        first = (await do_upload(client)).json()["version"]["id"]
        second = (await do_upload(client)).json()["version"]["id"]

        body = (await client.get(f"/schedules/{second}/delete-impact")).json()

        assert body["is_current"] is True
        assert body["promotes_version_id"] == first
        # the second parse deduplicated onto the same file, which therefore stays
        assert body["file_versions_remaining"] == 1
        assert body["file_removable"] is False

    async def test_unknown_version_is_a_404(self, client):
        assert (await client.get("/schedules/9999/delete-impact")).status_code == 404


class TestDelete:
    async def test_removes_the_version_and_everything_derived(self, client, session_factory):
        version_id = (await do_upload(client)).json()["version"]["id"]
        # Checked against the preview rather than against numbers copied out of the
        # fixture: what the confirmation promised and what the delete did are the two
        # things that have to agree, and hardcoding either hides a drift in the other.
        preview = (await client.get(f"/schedules/{version_id}/delete-impact")).json()

        resp = await client.delete(f"/schedules/{version_id}", headers=ACTOR)
        assert resp.status_code == 200
        body = resp.json()

        assert body["deleted"]["activities"] == preview["activities"] == 4
        assert body["deleted"]["relationships"] == preview["relationships"] == 3
        assert body["deleted"]["dcma_runs"] == preview["dcma_runs"] == 1
        assert body["deleted"]["calendars"] == preview["calendars"]
        assert body["deleted"]["wbs_nodes"] == preview["wbs_nodes"]

        assert (await client.get(f"/schedules/{version_id}")).status_code == 404
        assert await count(session_factory, ScheduleActivity, version_id=version_id) == 0
        assert await count(session_factory, ScheduleRelationship, version_id=version_id) == 0
        assert await count(session_factory, DcmaRun, version_id=version_id) == 0
        assert await count(session_factory, ScheduleVersion) == 0

    async def test_source_bytes_survive_unless_asked_for(self, client, session_factory):
        version_id = (await do_upload(client)).json()["version"]["id"]

        body = (await client.delete(f"/schedules/{version_id}", headers=ACTOR)).json()

        assert body["file_deleted"] is False
        assert await count(session_factory, ScheduleFile) == 1

    async def test_deletes_the_file_when_nothing_else_uses_it(self, client, session_factory):
        version_id = (await do_upload(client)).json()["version"]["id"]

        body = (
            await client.delete(
                f"/schedules/{version_id}?delete_file=true", headers=ACTOR
            )
        ).json()

        assert body["file_deleted"] is True
        assert body["file_retained"] is None
        assert await count(session_factory, ScheduleFile) == 0

    async def test_keeps_the_file_when_another_version_still_reads_it(self, client, session_factory):
        first = (await do_upload(client)).json()["version"]["id"]
        second = (await do_upload(client)).json()["version"]["id"]
        assert first != second

        body = (
            await client.delete(f"/schedules/{second}?delete_file=true", headers=ACTOR)
        ).json()

        assert body["file_deleted"] is False
        assert "other version" in (body["file_retained"] or "")
        assert await count(session_factory, ScheduleFile) == 1

    async def test_unknown_version_is_a_404(self, client):
        assert (await client.delete("/schedules/9999")).status_code == 404


class TestPromotion:
    async def test_deleting_the_current_version_promotes_the_previous_one(self, client):
        first = (await do_upload(client)).json()["version"]["id"]
        second = (await do_upload(client)).json()["version"]["id"]

        body = (await client.delete(f"/schedules/{second}", headers=ACTOR)).json()
        assert body["promoted_version_id"] == first

        survivor = (await client.get(f"/schedules/{first}")).json()
        assert survivor["is_current"] is True

        # and the promotion is visible through the filter every other view uses
        current = (await client.get("/schedules?current_only=true")).json()
        assert [row["id"] for row in current] == [first]

    async def test_deleting_a_superseded_version_leaves_current_alone(self, client):
        first = (await do_upload(client)).json()["version"]["id"]
        second = (await do_upload(client)).json()["version"]["id"]

        body = (await client.delete(f"/schedules/{first}", headers=ACTOR)).json()
        assert body["promoted_version_id"] is None

        assert (await client.get(f"/schedules/{second}")).json()["is_current"] is True

    async def test_last_version_of_a_project_leaves_nothing_current(self, client):
        version_id = (await do_upload(client)).json()["version"]["id"]

        body = (await client.delete(f"/schedules/{version_id}", headers=ACTOR)).json()

        assert body["promoted_version_id"] is None
        assert (await client.get("/schedules")).json() == []


class TestMappingGuard:
    async def test_accepted_mappings_block_the_delete(self, client, session_factory):
        version_id = (await do_upload(client)).json()["version"]["id"]
        await add_mapping(session_factory, version_id, "accepted")

        resp = await client.delete(f"/schedules/{version_id}", headers=ACTOR)

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "delete_blocked"
        assert body["accepted_mappings"] == 1
        # nothing was touched on the way to refusing
        assert (await client.get(f"/schedules/{version_id}")).status_code == 200
        assert await count(session_factory, ScheduleActivity, version_id=version_id) == 4

    async def test_proposed_mappings_alone_do_not_block(self, client, session_factory):
        version_id = (await do_upload(client)).json()["version"]["id"]
        await add_mapping(session_factory, version_id, "proposed")

        resp = await client.delete(f"/schedules/{version_id}", headers=ACTOR)

        assert resp.status_code == 200
        assert resp.json()["deleted"]["mappings"] == 1

    async def test_force_removes_them_and_records_that_it_did(self, client, session_factory):
        version_id = (await do_upload(client)).json()["version"]["id"]
        mapping_id = await add_mapping(session_factory, version_id, "accepted")

        body = (
            await client.delete(f"/schedules/{version_id}?force=true", headers=ACTOR)
        ).json()

        assert body["deleted"]["mappings"] == 1
        assert body["mapping_history_kept"] == 1
        assert await count(session_factory, RiskActivityMapping, version_id=version_id) == 0

        # invariant 5: the mapping goes, the record of it going does not
        async with session(session_factory) as db:
            row = await db.scalar(
                select(MappingHistory).where(MappingHistory.mapping_id == mapping_id)
            )
            assert row is not None
            assert row.action == "deleted"
            assert row.actor == "Sam"
            assert {c["field"] for c in row.changes} == {"status", "schedule_version"}

    async def test_mappings_on_other_versions_are_left_alone(self, client, session_factory):
        first = (await do_upload(client)).json()["version"]["id"]
        second = (await do_upload(client)).json()["version"]["id"]
        await add_mapping(session_factory, first, "accepted")
        keeper = await add_mapping(session_factory, second, "accepted")

        await client.delete(f"/schedules/{first}?force=true", headers=ACTOR)

        assert await count(session_factory, RiskActivityMapping) == 1
        async with session(session_factory) as db:
            assert (await db.get(RiskActivityMapping, keeper)) is not None


class TestReupload:
    async def test_the_same_file_can_be_imported_again_afterwards(self, client):
        version_id = (await do_upload(client)).json()["version"]["id"]
        await client.delete(f"/schedules/{version_id}?delete_file=true", headers=ACTOR)

        again = await do_upload(client, data=simple_xer())

        assert again.status_code == 200
        assert again.json()["file_created"] is True
        assert again.json()["version"]["is_current"] is True
