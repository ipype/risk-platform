"""Reads narrowed by the scope tree.

Writes were scoped when the hierarchy landed; reads were not, which meant a second project
could be created and every register, schedule list and run history in the platform would
still show all of it. These are the tests for the other half.

The shape being asserted is the rollup: a project reads as itself, a program reads as
every project under it, a portfolio reads as everything, and no scope at all still reads
unfiltered — the behaviour every call site had before the tree existed and the one a
single-project install depends on.

Rows are seeded through the session rather than the API because the point under test is
the filter, not the create paths, and the create paths for four different tables would
bury it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import risks as risks_route
from app.api.routes import schedules as schedules_route
from app.api.routes import simulations as simulations_route
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.db.session import get_db
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.schedule import ScheduleFile, ScheduleVersion
from app.models.scope import ScopeNode
from app.models.simulation import SimulationRun

pytestmark = pytest.mark.asyncio

PORTFOLIO = 1
PROGRAM = 2
PROJECT_A = 3  # under the program
PROJECT_B = 4  # directly under the portfolio


async def _seed(session: AsyncSession) -> None:
    session.add_all(
        [
            ScopeNode(id=PORTFOLIO, kind="portfolio", name="Capital", created_by="test"),
            ScopeNode(
                id=PROGRAM, kind="program", parent_id=PORTFOLIO, name="Water", created_by="test"
            ),
            ScopeNode(
                id=PROJECT_A,
                kind="project",
                parent_id=PROGRAM,
                name="Plant A",
                is_default=True,
                created_by="test",
            ),
            ScopeNode(
                id=PROJECT_B, kind="project", parent_id=PORTFOLIO, name="Depot", created_by="test"
            ),
        ]
    )
    session.add(RbsCategory(id=1, code="ENV", name="Environmental"))
    session.add(RbsSubcategory(id=1, category_id=1, code="030", name="Permitting"))
    await session.flush()

    for scope_id, seq in ((PROJECT_A, 1), (PROJECT_B, 1), (PROJECT_B, 2)):
        session.add(
            Risk(
                scope_id=scope_id,
                subcategory_id=1,
                seq=seq,
                risk_code=f"ENV-030-{scope_id}{seq:03d}",
                title=f"Risk {scope_id}.{seq}",
            )
        )

    for file_id, scope_id in ((1, PROJECT_A), (2, PROJECT_B)):
        session.add(
            ScheduleFile(
                id=file_id,
                scope_id=scope_id,
                filename=f"s{file_id}.xer",
                suffix=".xer",
                content=b"x",
                content_sha256=f"sha{file_id}",
                size_bytes=1,
            )
        )
        session.add(
            ScheduleVersion(
                id=file_id,
                file_id=file_id,
                source_project_id=f"P{file_id}",
                project_name=f"Project {file_id}",
                source_format="xer",
                parser_version="test",
            )
        )

    session.add_all(
        [
            SimulationRun(scope_id=PROJECT_A),
            SimulationRun(scope_id=PROJECT_B),
            SimulationRun(scope_id=PROJECT_B),
        ]
    )
    await session.commit()


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scoped.db'}", future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as session:
        await _seed(session)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(risks_route.router)
    app.include_router(schedules_route.router)
    app.include_router(simulations_route.router)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


class TestRegisterReads:
    async def test_a_project_reads_only_its_own_risks(self, client) -> None:
        rows = (await client.get(f"/risks?scope_id={PROJECT_A}")).json()
        assert [r["title"] for r in rows] == ["Risk 3.1"]

    async def test_a_program_rolls_up_its_projects(self, client) -> None:
        rows = (await client.get(f"/risks?scope_id={PROGRAM}")).json()
        assert [r["title"] for r in rows] == ["Risk 3.1"]

    async def test_a_portfolio_rolls_up_everything_beneath_it(self, client) -> None:
        rows = (await client.get(f"/risks?scope_id={PORTFOLIO}")).json()
        assert len(rows) == 3

    async def test_no_scope_reads_unfiltered(self, client) -> None:
        assert len((await client.get("/risks")).json()) == 3

    async def test_an_unknown_scope_is_a_named_404_not_an_empty_register(self, client) -> None:
        r = await client.get("/risks?scope_id=999")
        assert r.status_code == 404
        assert r.json()["error"] == "scope_not_found"

    async def test_scope_composes_with_the_other_filters(self, client) -> None:
        rows = (await client.get(f"/risks?scope_id={PROJECT_B}&category=ENV")).json()
        assert len(rows) == 2
        rows = (await client.get(f"/risks?scope_id={PROJECT_B}&category=CON")).json()
        assert rows == []


class TestScheduleReads:
    async def test_versions_follow_the_scope_of_the_stored_file(self, client) -> None:
        rows = (await client.get(f"/schedules?scope_id={PROJECT_B}")).json()
        assert [r["id"] for r in rows] == [2]

    async def test_the_portfolio_sees_both(self, client) -> None:
        rows = (await client.get(f"/schedules?scope_id={PORTFOLIO}")).json()
        assert sorted(r["id"] for r in rows) == [1, 2]

    async def test_a_sibling_project_sees_none_of_them(self, client) -> None:
        # PROJECT_A owns file 1 only; asking as the program above it must not widen to B.
        rows = (await client.get(f"/schedules?scope_id={PROGRAM}")).json()
        assert [r["id"] for r in rows] == [1]


class TestRunReads:
    async def test_runs_are_filtered_to_the_selected_scope(self, client) -> None:
        rows = (await client.get(f"/simulations?scope_id={PROJECT_A}")).json()
        assert len(rows) == 1

    async def test_runs_roll_up(self, client) -> None:
        assert len((await client.get(f"/simulations?scope_id={PORTFOLIO}")).json()) == 3

    async def test_run_options_only_offer_schedules_in_scope(self, client) -> None:
        body = (await client.get(f"/simulations/options?scope_id={PROJECT_A}")).json()
        assert [v["id"] for v in body["schedule_versions"]] == [1]
