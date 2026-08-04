"""Which project an uploaded schedule lands on.

Reads were scoped in 4.8 and tested in ``test_scoped_reads.py``; the *write* side of
schedule ingestion was not. ``POST /schedules/upload`` declared ``scope_id`` as a form
field while every client sends scope as a query parameter, so the selected scope was
dropped on the floor and every upload landed on the auto-created default project.

Nothing errored. The upload returned 200 with a real activity count, and then the version
list — correctly filtered to the selected scope — did not contain it. From the analyst's
seat that is "schedule import does not work".

These tests pin the query parameter, and pin the two refusals that silently defaulting
was hiding: a non-project scope and an unknown one.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.models.schedule import ScheduleFile
from app.models.scope import ScopeNode
from tests.conftest import DEFAULT_SCOPE_ID
from tests.schedule_fixtures import simple_xer

pytestmark = pytest.mark.asyncio

PROJECT_B = 7
PROGRAM = 8


@pytest_asyncio.fixture
async def tree(db):
    """A second project and a program alongside the harness's default project."""
    db.add_all(
        [
            ScopeNode(id=PROGRAM, kind="program", name="Water", created_by="test"),
            ScopeNode(
                id=PROJECT_B,
                kind="project",
                parent_id=PROGRAM,
                name="Plant B",
                created_by="test",
            ),
        ]
    )
    await db.commit()
    return db


async def upload(client, *, data: bytes | None = None, **params):
    return await client.post(
        "/schedules/upload",
        files={"file": ("pipeline.xer", data if data is not None else simple_xer())},
        params=params or None,
    )


class TestUploadScope:
    async def test_the_upload_lands_on_the_scope_the_client_selected(self, client, tree):
        resp = await upload(client, scope_id=PROJECT_B)
        assert resp.status_code == 200
        version_id = resp.json()["version"]["id"]

        file_row = await tree.get(ScheduleFile, resp.json()["version"]["file_id"])
        assert file_row is not None
        assert file_row.scope_id == PROJECT_B

        # The read half already worked. This is the pair that was broken: the version is
        # invisible from the scope it was uploaded under.
        rows = (await client.get("/schedules", params={"scope_id": PROJECT_B})).json()
        assert [v["id"] for v in rows] == [version_id]

    async def test_it_rolls_up_to_the_program_above_it(self, client, tree):
        resp = await upload(client, scope_id=PROJECT_B)
        rows = (await client.get("/schedules", params={"scope_id": PROGRAM})).json()
        assert [v["id"] for v in rows] == [resp.json()["version"]["id"]]

    async def test_no_scope_still_means_the_default_project(self, client, tree):
        """A single-project install sends nothing and must keep working."""
        resp = await upload(client)
        assert resp.status_code == 200
        rows = (await client.get("/schedules")).json()
        assert [v["id"] for v in rows] == [resp.json()["version"]["id"]]

    async def test_a_program_is_refused_rather_than_silently_defaulted(self, client, tree):
        resp = await upload(client, scope_id=PROGRAM)
        assert resp.status_code == 422
        assert resp.json()["error"] == "scope_invalid"

    async def test_an_unknown_scope_is_a_404(self, client, tree):
        resp = await upload(client, scope_id=999)
        assert resp.status_code == 404
        assert resp.json()["error"] == "scope_not_found"

    async def test_the_same_export_can_belong_to_two_projects(self, client, tree):
        """Dedup is per scope: an integrated master schedule legitimately sits in both.

        With the scope dropped, both uploads collapsed onto one file row and the second
        project inherited the first's — which is the same defect seen from the other side.
        """
        first = await upload(client, scope_id=DEFAULT_SCOPE_ID)
        second = await upload(client, scope_id=PROJECT_B)

        assert first.json()["file_created"] is True
        assert second.json()["file_created"] is True
        assert first.json()["version"]["file_id"] != second.json()["version"]["file_id"]
