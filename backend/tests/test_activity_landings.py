"""``GET /mappings/activity-landings`` — the Gantt's risk overlay.

Self-contained env fixture rather than sharing ``test_mappings_api``'s: this endpoint
needs a scoped driver resolving against a WBS branch, which that fixture does not build.

The properties worth holding: accepted and proposed never merge into one number, a scoped
driver resolves to the branch rather than to nothing, and a mapping pointing at an
activity this version does not carry does not invent a bar.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import mappings as mappings_route
from app.db.base_class import Base
from app.db.session import get_db
from app.models.mapping import RiskActivityMapping
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.schedule import (
    ScheduleActivity,
    ScheduleFile,
    ScheduleVersion,
    ScheduleWbs,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.asyncio

VERSION_ID = 1


def activity(source_id: str, code: str, name: str, wbs: str | None, critical=False):
    return ScheduleActivity(
        version_id=VERSION_ID,
        source_id=source_id,
        code=code,
        name=name,
        calendar_source_id="CAL-1",
        wbs_source_id=wbs,
        type="task",
        status="not_started",
        duration_calendar_id="CAL-1",
        original_duration_days=10.0,
        remaining_duration_days=10.0,
        total_float_days=0.0 if critical else 12.0,
        is_critical=critical,
    )


@pytest_asyncio.fixture
async def env(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path/'landings.db'}", future=True
    )
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        cat = RbsCategory(code="REG", name="Regulatory", sort_order=1)
        db.add(cat)
        await db.flush()
        sub = RbsSubcategory(category_id=cat.id, code="010", name="Permitting")
        db.add(sub)
        await db.flush()

        risks = [
            Risk(
                subcategory_id=sub.id,
                seq=i,
                risk_code=f"REG-010-000{i}",
                title=f"Risk {i}",
                status="Open",
                probability=3,
                impact=3,
                impact_scores={"SCHED": 4},
            )
            for i in (1, 2, 3)
        ]
        db.add_all(risks)

        file_row = ScheduleFile(
            filename="p.xer",
            suffix=".xer",
            content=b"x",
            content_sha256="a" * 64,
            size_bytes=1,
        )
        db.add(file_row)
        await db.flush()
        db.add(
            ScheduleVersion(
                id=VERSION_ID,
                file_id=file_row.id,
                source_project_id="1001",
                project_name="P",
                source_format="Primavera P6 XER",
                parser_version="xer-1",
                activity_count=3,
                relationship_count=0,
            )
        )
        db.add_all(
            [
                ScheduleWbs(
                    version_id=VERSION_ID, source_id="W1", code="1", name="Civil"
                ),
                ScheduleWbs(
                    version_id=VERSION_ID,
                    source_id="W11",
                    code="1.1",
                    name="Earthworks",
                    parent_source_id="W1",
                ),
                ScheduleWbs(
                    version_id=VERSION_ID, source_id="W2", code="2", name="Mechanical"
                ),
            ]
        )
        db.add_all(
            [
                activity("T1", "A100", "Excavate trench", "W11", critical=True),
                activity("T2", "A110", "Backfill trench", "W11"),
                activity("T3", "A200", "Install pumps", "W2"),
            ]
        )
        await db.commit()

    app = FastAPI()
    app.include_router(mappings_route.router)

    async def override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, Session, [r.id for r in risks]
    await engine.dispose()


async def add(Session, **kw):
    async with Session() as db:
        row = RiskActivityMapping(version_id=VERSION_ID, **kw)
        db.add(row)
        await db.commit()
        return row.id


async def landings(client):
    resp = await client.get(
        "/mappings/activity-landings", params={"version_id": VERSION_ID}
    )
    assert resp.status_code == 200
    return resp.json()


class TestLandings:
    async def test_empty_register_lands_nothing(self, env):
        client, _, _ = env
        body = await landings(client)
        assert body["landings"] == {}
        assert body["activities_touched"] == 0
        assert body["risks_landed"] == 0

    async def test_direct_driver_lands_on_its_activity(self, env):
        client, Session, risk_ids = env
        await add(
            Session,
            risk_id=risk_ids[0],
            mapping_type="duration_driver",
            activity_source_id="T1",
            status="accepted",
        )
        body = await landings(client)
        entry = body["landings"]["T1"]
        assert (entry["accepted"], entry["proposed"]) == (1, 0)
        assert entry["risks"][0]["risk_code"] == "REG-010-0001"
        assert entry["risks"][0]["via"] == "direct"
        assert body["activities_touched"] == 1

    async def test_accepted_and_proposed_are_counted_apart(self, env):
        """A proposal is not register state; a bar must not present three of them as
        three decisions (invariant 4)."""
        client, Session, risk_ids = env
        await add(
            Session,
            risk_id=risk_ids[0],
            mapping_type="duration_driver",
            activity_source_id="T1",
            status="accepted",
        )
        await add(
            Session,
            risk_id=risk_ids[1],
            mapping_type="duration_driver",
            activity_source_id="T1",
            status="proposed",
        )
        entry = (await landings(client))["landings"]["T1"]
        assert (entry["accepted"], entry["proposed"]) == (1, 1)
        assert len(entry["risks"]) == 2

    async def test_rejected_mappings_do_not_land(self, env):
        client, Session, risk_ids = env
        await add(
            Session,
            risk_id=risk_ids[0],
            mapping_type="duration_driver",
            activity_source_id="T1",
            status="rejected",
        )
        assert (await landings(client))["landings"] == {}

    async def test_scoped_driver_resolves_to_the_branch(self, env):
        """The reason this cannot be assembled client-side from ``GET /mappings``."""
        client, Session, risk_ids = env
        await add(
            Session,
            risk_id=risk_ids[0],
            mapping_type="scoped_driver",
            scope={"field": "wbs", "op": "equals", "value": "W11"},
            status="accepted",
        )
        body = await landings(client)
        assert set(body["landings"]) == {"T1", "T2"}
        assert body["landings"]["T1"]["risks"][0]["via"] == "scope"
        assert body["scoped_drivers"] == 1

    async def test_scoped_driver_matching_on_name_reaches_across_branches(self, env):
        client, Session, risk_ids = env
        await add(
            Session,
            risk_id=risk_ids[0],
            mapping_type="scoped_driver",
            scope={"field": "name", "op": "contains", "value": "trench"},
            status="accepted",
        )
        assert set((await landings(client))["landings"]) == {"T1", "T2"}

    async def test_inserted_activity_lands_on_both_ends(self, env):
        client, Session, risk_ids = env
        await add(
            Session,
            risk_id=risk_ids[0],
            mapping_type="inserted_activity",
            predecessor_source_id="T1",
            successor_source_id="T2",
            allocation_pct=100.0,
            status="accepted",
        )
        body = await landings(client)
        assert body["landings"]["T1"]["risks"][0]["via"] == "insert_predecessor"
        assert body["landings"]["T2"]["risks"][0]["via"] == "insert_successor"
        assert body["risks_landed"] == 1

    async def test_stale_activity_reference_does_not_invent_a_bar(self, env):
        client, Session, risk_ids = env
        await add(
            Session,
            risk_id=risk_ids[0],
            mapping_type="duration_driver",
            activity_source_id="GONE",
            status="accepted",
        )
        body = await landings(client)
        assert body["landings"] == {}
        assert body["mappings_live"] == 1

    async def test_several_risks_on_one_activity_are_all_named(self, env):
        client, Session, risk_ids = env
        for risk_id in risk_ids:
            await add(
                Session,
                risk_id=risk_id,
                mapping_type="duration_driver",
                activity_source_id="T3",
                status="accepted",
            )
        entry = (await landings(client))["landings"]["T3"]
        assert entry["accepted"] == 3
        assert {r["risk_code"] for r in entry["risks"]} == {
            "REG-010-0001",
            "REG-010-0002",
            "REG-010-0003",
        }
        assert entry["risks_truncated"] == 0

    async def test_unknown_version_is_a_404(self, env):
        client, _, _ = env
        resp = await client.get(
            "/mappings/activity-landings", params={"version_id": 9999}
        )
        assert resp.status_code == 404

    async def test_version_id_is_required(self, env):
        client, _, _ = env
        assert (await client.get("/mappings/activity-landings")).status_code == 422
