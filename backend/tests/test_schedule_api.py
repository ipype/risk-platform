"""Schedule ingestion: routes, persistence, and the stored gate."""

from __future__ import annotations

import pytest

from app.schedule.parsers import parse_schedule
from app.services.schedule_ingest import create_version, hydrate, store_file
from tests.schedule_fixtures import simple_xer


def upload_payload(data: bytes = None, filename: str = "pipeline.xer", **form):
    return {
        "files": {"file": (filename, data if data is not None else simple_xer())},
        "data": form or None,
    }


async def do_upload(client, data: bytes = None, filename="pipeline.xer", **form):
    payload = upload_payload(data, filename, **form)
    return await client.post("/schedules/upload", files=payload["files"], data=payload["data"])


class TestFormats:
    async def test_lists_what_is_readable_here(self, client):
        resp = await client.get("/schedules/formats")
        assert resp.status_code == 200
        by_suffix = {tuple(f["suffixes"]): f for f in resp.json()}
        assert by_suffix[(".xer",)]["available"] is True
        assert by_suffix[(".mpp",)]["available"] is False


class TestUpload:
    async def test_upload_parses_persists_and_runs_the_gate(self, client):
        resp = await do_upload(client, actor="sam")
        assert resp.status_code == 200
        body = resp.json()

        assert body["version"]["project_name"] == "Pipeline A — Phase 1"
        assert body["version"]["activity_count"] == 4
        assert body["version"]["relationship_count"] == 3
        assert body["version"]["created_by"] == "sam"
        assert body["file_created"] is True

        # the fixture schedule has negative float, which is a blocking failure
        assert body["gate"]["gate_passed"] is False
        assert 7 in body["gate"]["blocking_failures"]

    def _warnings(self, body):
        return body["version"]["warnings"]

    async def test_parse_warnings_reach_the_client(self, client):
        resp = await do_upload(client)
        assert any("outside this project" in w for w in self._warnings(resp.json()))

    async def test_identical_bytes_are_deduplicated(self, client):
        first = await do_upload(client)
        second = await do_upload(client)
        assert first.json()["file_created"] is True
        assert second.json()["file_created"] is False
        assert first.json()["version"]["file_id"] == second.json()["version"]["file_id"]

    async def test_reparsing_supersedes_the_previous_version(self, client):
        first = await do_upload(client)
        second = await do_upload(client)
        first_id = first.json()["version"]["id"]
        second_id = second.json()["version"]["id"]
        assert first_id != second_id

        # append-only: the old version is still readable, just no longer current
        old = await client.get(f"/schedules/{first_id}")
        assert old.status_code == 200
        assert old.json()["is_current"] is False
        assert second.json()["version"]["is_current"] is True

    async def test_empty_file_is_rejected(self, client):
        resp = await do_upload(client, data=b"")
        assert resp.status_code == 422

    async def test_unsupported_suffix_is_rejected_before_reading_the_body(self, client):
        resp = await do_upload(client, filename="plan.pdf")
        assert resp.status_code == 415
        assert resp.json()["error"] == "unsupported_format"

    async def test_mpp_explains_why_it_cannot_be_read(self, client):
        resp = await do_upload(client, filename="plan.mpp")
        assert resp.status_code == 415
        assert resp.json()["error"] == "parser_unavailable"
        assert "JRE" in resp.json()["reason"]

    async def test_garbage_content_is_a_422_not_a_500(self, client):
        resp = await do_upload(client, data=b"this is not a schedule at all")
        assert resp.status_code == 422
        assert resp.json()["error"] == "malformed_file"


class TestAmbiguousProject:
    async def test_multi_project_file_returns_409_with_the_choices(self, client):
        resp = await do_upload(client, data=simple_xer(extra_projects=True))
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "ambiguous_project"
        assert {p["id"] for p in body["projects"]} == {"1001", "2002"}
        # the file is already stored, so the follow-up needs no re-upload
        assert isinstance(body["file_id"], int)

    async def test_follow_up_parse_completes_without_re_uploading(self, client):
        first = await do_upload(client, data=simple_xer(extra_projects=True))
        file_id = first.json()["file_id"]

        resp = await client.post(
            f"/schedules/files/{file_id}/parse", params={"project_id": "2002"}
        )
        assert resp.status_code == 200
        assert resp.json()["version"]["source_project_id"] == "2002"
        assert resp.json()["version"]["file_id"] == file_id

    async def test_unknown_project_id_is_404(self, client):
        first = await do_upload(client, data=simple_xer(extra_projects=True))
        file_id = first.json()["file_id"]
        resp = await client.post(
            f"/schedules/files/{file_id}/parse", params={"project_id": "nope"}
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "project_not_found"

    async def test_parsing_an_unknown_file_is_404(self, client):
        resp = await client.post(
            "/schedules/files/999/parse", params={"project_id": "1001"}
        )
        assert resp.status_code == 404


class TestReading:
    @pytest.fixture
    async def version_id(self, client):
        resp = await do_upload(client)
        return resp.json()["version"]["id"]

    async def test_list_versions(self, client, version_id):
        resp = await client.get("/schedules")
        assert resp.status_code == 200
        assert [v["id"] for v in resp.json()] == [version_id]

    async def test_current_only_filter(self, client, version_id):
        await do_upload(client)
        everything = await client.get("/schedules")
        current = await client.get("/schedules", params={"current_only": True})
        assert len(everything.json()) == 2
        assert len(current.json()) == 1

    async def test_unknown_version_is_404(self, client):
        assert (await client.get("/schedules/424242")).status_code == 404

    async def test_activities_are_paginated_and_carry_their_calendar(
        self, client, version_id
    ):
        resp = await client.get(f"/schedules/{version_id}/activities")
        body = resp.json()
        assert body["total"] == 4
        laying = next(a for a in body["items"] if a["code"] == "A1020")
        # 240h on the 10h/day calendar, and the calendar is reported alongside
        assert laying["original_duration_days"] == pytest.approx(24.0)
        assert laying["duration_calendar_id"] == "CAL-2"
        assert laying["budgeted_cost"] == 12500050

    async def test_activity_filters(self, client, version_id):
        by_status = await client.get(
            f"/schedules/{version_id}/activities", params={"status": "completed"}
        )
        assert by_status.json()["total"] == 2

        by_search = await client.get(
            f"/schedules/{version_id}/activities", params={"q": "pipe"}
        )
        assert by_search.json()["total"] == 1

    async def test_relationships_expose_lag_and_its_calendar(self, client, version_id):
        resp = await client.get(f"/schedules/{version_id}/relationships")
        body = resp.json()
        assert body["total"] == 3
        ss = next(r for r in body["items"] if r["type"] == "SS")
        assert ss["lag_days"] == pytest.approx(1.6)
        assert ss["lag_calendar_id"] == "CAL-2"

    async def test_source_bytes_come_back_unchanged(self, client, version_id):
        resp = await client.get(f"/schedules/{version_id}/source")
        assert resp.status_code == 200
        assert resp.content == simple_xer()
        assert "pipeline.xer" in resp.headers["content-disposition"]
        assert len(resp.headers["x-content-sha256"]) == 64


class TestGate:
    @pytest.fixture
    async def version_id(self, client):
        resp = await do_upload(client)
        return resp.json()["version"]["id"]

    async def test_gate_report_is_stored_in_full(self, client, version_id):
        resp = await client.get(f"/schedules/{version_id}/dcma")
        assert resp.status_code == 200
        body = resp.json()
        assert body["gate_passed"] is False
        assert len(body["report"]["checks"]) == 14
        assert body["thresholds"]["high_float_days"] == 44.0

    async def test_rerun_appends_rather_than_overwriting(self, client, version_id):
        first = await client.get(f"/schedules/{version_id}/dcma")
        rerun = await client.post(f"/schedules/{version_id}/dcma", json={"actor": "sam"})
        assert rerun.status_code == 200
        assert rerun.json()["run_id"] != first.json()["run_id"]

        latest = await client.get(f"/schedules/{version_id}/dcma")
        assert latest.json()["run_id"] == rerun.json()["run_id"]

    async def test_thresholds_can_be_overridden_per_run(self, client, version_id):
        resp = await client.post(
            f"/schedules/{version_id}/dcma",
            json={"thresholds": {"blocking_checks": []}, "actor": "sam"},
        )
        assert resp.status_code == 200
        # nothing blocks any more, so the same schedule now passes the gate
        assert resp.json()["gate_passed"] is True
        assert resp.json()["blocking_failures"] == []

    async def test_invalid_thresholds_are_422(self, client, version_id):
        resp = await client.post(
            f"/schedules/{version_id}/dcma",
            json={"thresholds": {"high_float_days": "not a number"}},
        )
        assert resp.status_code == 422

    async def test_gate_on_unknown_version_is_404(self, client):
        assert (await client.get("/schedules/999/dcma")).status_code == 404


class TestRoundTrip:
    async def test_hydrating_from_the_database_reproduces_the_parse(self, db):
        """The gate, mapping and simulation all read hydrated rows, never the file again.

        If this round trip loses anything, every downstream number is quietly computed
        from something other than what was uploaded.
        """
        data = simple_xer()
        parsed = parse_schedule(data, "pipeline.xer")
        file_row, _ = await store_file(db, filename="pipeline.xer", content=data)
        version = await create_version(db, file=file_row, schedule=parsed)
        await db.commit()

        restored = await hydrate(db, version)

        assert restored.project_id == parsed.project_id
        assert restored.project_name == parsed.project_name
        assert restored.data_date == parsed.data_date
        assert restored.must_finish_by == parsed.must_finish_by
        assert restored.warnings == parsed.warnings

        assert {c.id: c for c in restored.calendars} == {c.id: c for c in parsed.calendars}
        assert {w.id: w for w in restored.wbs} == {w.id: w for w in parsed.wbs}
        assert {a.id: a for a in restored.activities} == {
            a.id: a for a in parsed.activities
        }
        assert {r.id: r for r in restored.relationships} == {
            r.id: r for r in parsed.relationships
        }

    async def test_the_gate_agrees_before_and_after_a_round_trip(self, db):
        from app.schedule.dcma import run_dcma

        data = simple_xer()
        parsed = parse_schedule(data, "pipeline.xer")
        file_row, _ = await store_file(db, filename="pipeline.xer", content=data)
        version = await create_version(db, file=file_row, schedule=parsed)
        await db.commit()

        direct = run_dcma(parsed)
        restored = run_dcma(await hydrate(db, version))

        assert direct.gate_passed == restored.gate_passed
        assert [c.status for c in direct.checks] == [c.status for c in restored.checks]
        assert [c.metric for c in direct.checks] == [c.metric for c in restored.checks]


class TestFileStore:
    async def test_dedupe_returns_the_original_row(self, db):
        data = simple_xer()
        first, created_first = await store_file(db, filename="a.xer", content=data)
        second, created_second = await store_file(db, filename="b.xer", content=data)
        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        # the first filename wins; the bytes are what identify the source
        assert second.filename == "a.xer"

    async def test_different_bytes_are_different_files(self, db):
        a, _ = await store_file(db, filename="a.xer", content=simple_xer())
        b, _ = await store_file(
            db, filename="b.xer", content=simple_xer(extra_projects=True)
        )
        assert a.id != b.id
        assert a.content_sha256 != b.content_sha256
