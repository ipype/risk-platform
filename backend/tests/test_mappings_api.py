"""End-to-end over the real routes, real models, SQLite session.

Covers the paths where a unit test would lie: Pydantic shape validation, the 422/409
edges, the audit rows, coverage arithmetic, and carry-forward across two versions.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import mappings as mappings_route
from app.db.base_class import Base
from app.db.session import get_db
from app.models.mapping import MappingHistory, MappingSuggestionOutcome, RiskActivityMapping
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.schedule import (
    ScheduleActivity,
    ScheduleFile,
    ScheduleRelationship,
    ScheduleVersion,
    ScheduleWbs,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.asyncio

ACTOR = {"X-Actor": "Sam"}


@pytest_asyncio.fixture
async def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}", future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        cat = RbsCategory(code="REG", name="Regulatory", sort_order=1)
        db.add(cat)
        await db.flush()
        sub = RbsSubcategory(category_id=cat.id, code="010", name="Permitting and approvals")
        db.add(sub)
        await db.flush()

        risks = [
            Risk(subcategory_id=sub.id, seq=1, risk_code="REG-010-0001",
                 title="Environmental permit approval delayed by regulator",
                 causes="Incomplete submission; regulator backlog",
                 consequences="Construction start pushed back",
                 status="Open", probability=4, impact=4,
                 impact_scores={"SCHED": 4, "COST": 3}),
            Risk(subcategory_id=sub.id, seq=2, risk_code="REG-010-0002",
                 title="Concrete supply interruption",
                 causes="Single supplier", consequences="Foundation works extend",
                 status="Open", probability=3, impact=3,
                 impact_scores={"SCHED": 3}),
            Risk(subcategory_id=sub.id, seq=3, risk_code="REG-010-0003",
                 title="Reputational exposure from local media",
                 status="Open", probability=2, impact=2,
                 impact_scores={"REP": 3}),  # no schedule impact -> out of coverage scope
        ]
        db.add_all(risks)

        f = ScheduleFile(filename="p.xer", suffix=".xer", content=b"x",
                         content_sha256="a" * 64, size_bytes=1)
        db.add(f)
        await db.flush()

        version_ids = []
        for n in (1, 2):
            v = ScheduleVersion(file_id=f.id, source_project_id="P1", project_name="Plant",
                                source_format="xer", parser_version="1.0",
                                data_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
                                activity_count=8, relationship_count=2, is_current=(n == 2))
            db.add(v)
            await db.flush()
            version_ids.append(v.id)

            db.add_all([
                ScheduleWbs(version_id=v.id, source_id="W0", code="P", name="Plant",
                            is_project_node=True),
                ScheduleWbs(version_id=v.id, source_id="W-PERMIT", code="1.1",
                            name="Permitting", parent_source_id="W0"),
                ScheduleWbs(version_id=v.id, source_id="W-CIVIL", code="1.2",
                            name="Civil Works", parent_source_id="W0"),
            ])

            # source_id deliberately differs between versions; code is stable. This is
            # what carry-forward has to survive.
            offset = 0 if n == 1 else 900
            acts = [
                (1, "A1000", "Submit environmental permit application", "task", "not_started", "W-PERMIT", 30.0, False, 15.0, "none"),
                (2, "A1010", "Regulator review of permit submission", "task", "not_started", "W-PERMIT", 30.0, False, 40.0, "none"),
                (3, "A1020", "Respond to regulator comments", "task", "not_started", "W-PERMIT", 30.0, False, 10.0, "none"),
                (4, "A1030", "Environmental approval received", "milestone_finish", "not_started", "W-PERMIT", 0.0, False, 0.0, "none"),
                (5, "A2000", "Excavate main foundation", "task", "not_started", "W-CIVIL", 0.0, True, 20.0, "none"),
                (6, "A2010", "Pour foundation concrete", "task", "not_started", "W-CIVIL", 0.0, True, 25.0, "none"),
                (7, "A2020", "Backfill and compact", "task", "completed", "W-CIVIL", 0.0, False, 0.0, "none"),
                (8, "A3000", "Handover to operations", "task", "not_started", "W-CIVIL", 5.0, False, 5.0, "mandatory_finish"),
            ]
            for i, code, name, typ, status, wbs, tf, crit, rem, cst in acts:
                db.add(ScheduleActivity(
                    version_id=v.id, source_id=str(i + offset), code=code, name=name,
                    calendar_source_id="C1", wbs_source_id=wbs, type=typ, status=status,
                    duration_calendar_id="C1", original_duration_days=rem,
                    remaining_duration_days=rem, total_float_days=tf, free_float_days=tf,
                    is_critical=crit, constraint_type=cst))
            db.add(ScheduleRelationship(
                version_id=v.id, source_id=f"R{n}1",
                predecessor_source_id=str(3 + offset), successor_source_id=str(4 + offset),
                type="FS", lag_days=0.0))
        await db.commit()

    app = FastAPI()
    app.include_router(mappings_route.router)

    async def override():
        async with Session() as s:
            yield s

    app.dependency_overrides[get_db] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        yield {
            "client": client,
            "Session": Session,
            "v1": version_ids[0],
            "v2": version_ids[1],
            "risks": [r.id for r in risks],
        }
    await engine.dispose()


# --------------------------------------------------------------------------- #
# suggestions
# --------------------------------------------------------------------------- #


async def test_suggestions_rank_permit_activities_first(env):
    r = await env["client"].get(
        "/mappings/suggestions", params={"version_id": env["v1"], "risk_id": env["risks"][0]}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["candidates"], "expected candidates"
    top = body["candidates"][0]
    assert top["activity_code"] in {"A1000", "A1010"}
    assert set(top["signals"]) == {"lexical", "taxonomy", "wbs_affinity", "precedent"}
    assert top["signals"]["precedent"] is None
    assert body["precedent_available"] is False


async def test_completed_activity_never_suggested(env):
    r = await env["client"].get(
        "/mappings/suggestions",
        params={"version_id": env["v1"], "risk_id": env["risks"][0], "min_score": 0.0},
    )
    assert "A2020" not in {c["activity_code"] for c in r.json()["candidates"]}


async def test_milestone_is_recommended_as_inserted_activity(env):
    r = await env["client"].get(
        "/mappings/suggestions",
        params={"version_id": env["v1"], "risk_id": env["risks"][0], "min_score": 0.0},
    )
    ms = [c for c in r.json()["candidates"] if c["activity_code"] == "A1030"]
    if ms:
        assert ms[0]["recommended_type"] == "inserted_activity"
        assert any("milestone" in w for w in ms[0]["warnings"])


async def test_hard_constraint_and_float_surface_as_warnings(env):
    r = await env["client"].post("/mappings/validate", json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "8"})
    body = r.json()
    assert body["ok"] is True
    assert any("constraint" in w for w in body["warnings"])


async def test_suggestions_404_on_unknown_version(env):
    r = await env["client"].get(
        "/mappings/suggestions", params={"version_id": 9999, "risk_id": env["risks"][0]}
    )
    assert r.status_code == 404


# --------------------------------------------------------------------------- #
# create, validate, audit
# --------------------------------------------------------------------------- #


async def test_create_lands_as_proposed_and_writes_history(env):
    r = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "2"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "proposed"
    assert body["activity_name"].startswith("Regulator review")

    async with env["Session"]() as db:
        rows = (await db.scalars(select(MappingHistory))).all()
    assert len(rows) == 1 and rows[0].action == "created" and rows[0].actor == "Sam"


async def test_duration_driver_on_milestone_is_refused(env):
    r = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "4"})
    assert r.status_code == 422
    assert any("milestone" in w for w in r.json()["detail"]["warnings"])


async def test_duration_driver_on_completed_activity_is_refused(env):
    r = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "7"})
    assert r.status_code == 422


async def test_allocation_pct_rejected_on_duration_driver(env):
    """The correlation semantic is not negotiable at the API edge."""
    r = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "2",
        "allocation_pct": 50})
    assert r.status_code == 422
    assert "correlated" in str(r.json()).lower()


async def test_mixed_shape_payload_is_refused(env):
    r = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "2",
        "scope": {"field": "wbs", "op": "equals", "value": "W-CIVIL"}})
    assert r.status_code == 422


async def test_duplicate_mapping_conflicts(env):
    payload = {"risk_id": env["risks"][0], "version_id": env["v1"],
               "mapping_type": "duration_driver", "activity_source_id": "2"}
    assert (await env["client"].post("/mappings", headers=ACTOR, json=payload)).status_code == 201
    r = await env["client"].post("/mappings", headers=ACTOR, json=payload)
    assert r.status_code == 409


async def test_inserted_activity_requires_a_real_pair(env):
    ok = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "inserted_activity",
        "predecessor_source_id": "3", "successor_source_id": "4",
        "allocation_pct": 60})
    assert ok.status_code == 201
    assert ok.json()["existing_link"] is True
    assert ok.json()["allocation_pct"] == 60

    bad = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "inserted_activity",
        "predecessor_source_id": "3", "successor_source_id": "nope"})
    assert bad.status_code == 422


async def test_inserted_activity_without_link_warns_but_saves(env):
    r = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "inserted_activity",
        "predecessor_source_id": "1", "successor_source_id": "6"})
    assert r.status_code == 201
    assert r.json()["existing_link"] is False
    assert any("new logic path" in w for w in r.json()["warnings"])


async def test_scoped_driver_resolves_and_reports_membership(env):
    r = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][1], "version_id": env["v1"],
        "mapping_type": "scoped_driver",
        "scope": {"field": "wbs", "op": "equals", "value": "W-CIVIL"}})
    assert r.status_code == 201
    body = r.json()
    assert body["resolved_count"] == 4
    assert any("complete" in w for w in body["warnings"])


async def test_scope_matching_nothing_is_refused(env):
    r = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][1], "version_id": env["v1"],
        "mapping_type": "scoped_driver",
        "scope": {"field": "wbs", "op": "equals", "value": "W-NOWHERE"}})
    assert r.status_code == 422


# --------------------------------------------------------------------------- #
# accept / reject and the feedback loop
# --------------------------------------------------------------------------- #


async def test_accepting_stamps_the_decision_and_records_an_outcome(env):
    created = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "2",
        "origin": "suggested", "suggestion_score": 0.61})
    mid = created.json()["id"]

    r = await env["client"].patch(f"/mappings/{mid}", headers=ACTOR,
                                  json={"status": "accepted"})
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    assert r.json()["decided_by"] == "Sam"

    async with env["Session"]() as db:
        outcomes = (await db.scalars(select(MappingSuggestionOutcome))).all()
    assert len(outcomes) == 1
    assert outcomes[0].outcome == "accepted"
    assert "regulator" in outcomes[0].activity_tokens


async def test_rejecting_a_suggestion_trains_without_creating_a_mapping(env):
    r = await env["client"].post("/mappings/reject-suggestion", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "activity_source_id": "6", "score": 0.2})
    assert r.status_code == 201
    async with env["Session"]() as db:
        assert (await db.scalars(select(RiskActivityMapping))).all() == []
        outcomes = (await db.scalars(select(MappingSuggestionOutcome))).all()
    assert len(outcomes) == 1 and outcomes[0].outcome == "rejected"


async def test_bulk_accept_is_partial_not_all_or_nothing(env):
    r = await env["client"].post("/mappings/bulk-accept", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "items": [
            {"activity_source_id": "1", "suggestion_score": 0.7},
            {"activity_source_id": "4"},   # milestone -> refused
            {"activity_source_id": "7"},   # complete  -> refused
            {"activity_source_id": "2", "suggestion_score": 0.6},
        ]})
    assert r.status_code == 201
    body = r.json()
    assert body["created_count"] == 2
    assert len(body["refused"]) == 2
    assert all(m["status"] == "accepted" for m in body["created"])


async def test_cannot_accept_a_mapping_that_fails_validation(env):
    created = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][1], "version_id": env["v1"],
        "mapping_type": "scoped_driver",
        "scope": {"field": "wbs", "op": "equals", "value": "W-CIVIL"}})
    mid = created.json()["id"]
    bad = await env["client"].patch(f"/mappings/{mid}", headers=ACTOR, json={
        "status": "accepted",
        "scope": {"field": "wbs", "op": "equals", "value": "W-GONE"}})
    assert bad.status_code == 422


async def test_update_writes_history_and_delete_keeps_it(env):
    created = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "2"})
    mid = created.json()["id"]
    await env["client"].patch(f"/mappings/{mid}", headers=ACTOR,
                              json={"rationale": "Regulator backlog drives this review"})
    assert (await env["client"].delete(f"/mappings/{mid}", headers=ACTOR)).status_code == 204

    hist = await env["client"].get(f"/mappings/{mid}/history")
    actions = [h["action"] for h in hist.json()]
    assert actions == ["deleted", "updated", "created"]

    async with env["Session"]() as db:
        assert (await db.scalars(select(RiskActivityMapping))).all() == []


# --------------------------------------------------------------------------- #
# coverage
# --------------------------------------------------------------------------- #


async def test_coverage_counts_only_schedule_impacting_risks(env):
    r = await env["client"].get("/mappings/coverage", params={"version_id": env["v1"]})
    body = r.json()
    assert body["schedule_impact_area"] == "SCHED"
    assert body["risks_in_scope"] == 2          # the reputational risk is excluded
    assert body["risks_unmapped"] == 2
    assert body["coverage_pct"] == 0.0


async def test_coverage_reports_uncovered_critical_work(env):
    body = (await env["client"].get(
        "/mappings/coverage", params={"version_id": env["v1"]})).json()
    assert body["critical_activities"] == 2
    assert body["critical_activities_uncovered"] == 2

    await env["client"].post("/mappings/bulk-accept", headers=ACTOR, json={
        "risk_id": env["risks"][1], "version_id": env["v1"],
        "items": [{"activity_source_id": "5"}, {"activity_source_id": "6"}]})

    after = (await env["client"].get(
        "/mappings/coverage", params={"version_id": env["v1"]})).json()
    assert after["critical_activities_uncovered"] == 0
    assert after["risks_with_accepted_mapping"] == 1
    assert after["coverage_pct"] == 50.0


async def test_scoped_driver_counts_toward_activity_coverage(env):
    created = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][1], "version_id": env["v1"],
        "mapping_type": "scoped_driver",
        "scope": {"field": "wbs", "op": "equals", "value": "W-CIVIL"}})
    await env["client"].patch(f"/mappings/{created.json()['id']}", headers=ACTOR,
                              json={"status": "accepted"})
    body = (await env["client"].get(
        "/mappings/coverage", params={"version_id": env["v1"]})).json()
    assert body["critical_activities_uncovered"] == 0


# --------------------------------------------------------------------------- #
# carry-forward
# --------------------------------------------------------------------------- #


async def test_carry_forward_matches_on_activity_code_not_source_id(env):
    """source_id differs by 900 between the two versions; the code is what survives."""
    await env["client"].post("/mappings/bulk-accept", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "items": [{"activity_source_id": "1"}, {"activity_source_id": "2"}]})

    r = await env["client"].post("/mappings/carry-forward", headers=ACTOR, json={
        "from_version_id": env["v1"], "to_version_id": env["v2"]})
    assert r.status_code == 200
    assert r.json()["carried"] == 2
    assert r.json()["dropped_count"] == 0

    listed = await env["client"].get("/mappings", params={"version_id": env["v2"]})
    items = listed.json()["items"]
    assert {i["activity_source_id"] for i in items} == {"901", "902"}
    assert all(i["status"] == "proposed" for i in items)      # re-confirmed by a human
    assert all(i["origin"] == "carried_forward" for i in items)
    assert all(i["activity_code"] in {"A1000", "A1010"} for i in items)


async def test_carry_forward_is_idempotent(env):
    await env["client"].post("/mappings/bulk-accept", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "items": [{"activity_source_id": "1"}]})
    body = {"from_version_id": env["v1"], "to_version_id": env["v2"]}
    first = await env["client"].post("/mappings/carry-forward", headers=ACTOR, json=body)
    second = await env["client"].post("/mappings/carry-forward", headers=ACTOR, json=body)
    assert first.json()["carried"] == 1
    assert second.json()["carried"] == 0 and second.json()["skipped_existing"] == 1


async def test_carry_forward_only_moves_accepted_by_default(env):
    await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "1"})
    plain = await env["client"].post("/mappings/carry-forward", headers=ACTOR, json={
        "from_version_id": env["v1"], "to_version_id": env["v2"]})
    assert plain.json()["carried"] == 0

    with_proposed = await env["client"].post("/mappings/carry-forward", headers=ACTOR, json={
        "from_version_id": env["v1"], "to_version_id": env["v2"],
        "include_proposed": True})
    assert with_proposed.json()["carried"] == 1


async def test_carry_forward_to_itself_is_refused(env):
    r = await env["client"].post("/mappings/carry-forward", headers=ACTOR, json={
        "from_version_id": env["v1"], "to_version_id": env["v1"]})
    assert r.status_code == 422


async def test_scoped_driver_carries_without_remapping(env):
    created = await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][1], "version_id": env["v1"],
        "mapping_type": "scoped_driver",
        "scope": {"field": "wbs", "op": "equals", "value": "W-CIVIL"}})
    await env["client"].patch(f"/mappings/{created.json()['id']}", headers=ACTOR,
                              json={"status": "accepted"})
    r = await env["client"].post("/mappings/carry-forward", headers=ACTOR, json={
        "from_version_id": env["v1"], "to_version_id": env["v2"]})
    assert r.json()["carried"] == 1
    listed = await env["client"].get("/mappings", params={"version_id": env["v2"]})
    assert listed.json()["items"][0]["resolved_count"] == 4


# --------------------------------------------------------------------------- #
# listing
# --------------------------------------------------------------------------- #


async def test_list_attaches_live_context_and_filters(env):
    await env["client"].post("/mappings", headers=ACTOR, json={
        "risk_id": env["risks"][0], "version_id": env["v1"],
        "mapping_type": "duration_driver", "activity_source_id": "8"})
    r = await env["client"].get("/mappings", params={"version_id": env["v1"]})
    item = r.json()["items"][0]
    assert item["activity_name"] == "Handover to operations"
    assert item["materiality"]["band"] == "medium"
    assert any("constraint" in w for w in item["warnings"])

    empty = await env["client"].get(
        "/mappings", params={"version_id": env["v1"], "status": "accepted"})
    assert empty.json()["items"] == []


async def test_schedule_area_endpoint(env):
    r = await env["client"].get("/mappings/schedule-area")
    assert r.json()["schedule_impact_area"] == "SCHED"
