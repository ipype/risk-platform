"""Simulation assembly reads one project's register, and no other's.

The gap this closes was pre-existing and invisible. 4.8 scope-filtered every list and
export endpoint but not ``sim_assembly.assemble()``, so a run requested for one project
pulled every project's ``risk_quant_estimate`` rows and produced a contingency for a
portfolio nobody had asked about. Nothing in the output said so — the risk count was
simply larger than the register on screen, which is not a number anybody checks.

Every test here is written so that removing the filter fails it. Two of them were run
against the unfiltered code to confirm exactly that before the filter went in.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import simulations as simulation_routes
from app.core.config import settings
from app.core.errors import SimulationNotAssemblable
from app.db.base_class import Base
from app.db.session import get_db
from app.models.quant import RiskQuantEstimate
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.schedule import ScheduleFile, ScheduleVersion
from app.models.scope import ScopeNode
from app.models.simulation import SimulationRun  # noqa: F401  (registers the table)
from app.services.sim_assembly import assemble
from app.sim import RunConfig

PORTFOLIO = 10
PROJECT_A = 1
PROJECT_B = 2

COST = {
    "bound_interpretation": "absolute",
    "cost_dist": "pert",
    "cost_min": 100_000.0,
    "cost_ml": 200_000.0,
    "cost_max": 500_000.0,
    "confidence": "high",
}


async def _seed(session) -> None:
    session.add(RbsCategory(id=1, code="TEC", name="Technical"))
    session.add(RbsSubcategory(id=1, category_id=1, code="DES", name="Design"))
    session.add(
        ScopeNode(id=PORTFOLIO, kind="portfolio", name="Programme", created_by="test")
    )
    session.add(
        ScopeNode(
            id=PROJECT_A,
            kind="project",
            name="Project A",
            parent_id=PORTFOLIO,
            is_default=True,
            created_by="test",
        )
    )
    session.add(
        ScopeNode(
            id=PROJECT_B,
            kind="project",
            name="Project B",
            parent_id=PORTFOLIO,
            created_by="test",
        )
    )
    # Two risks in A, three in B. Deliberately lopsided: a leak shows up as a count.
    for i, scope in enumerate([PROJECT_A, PROJECT_A, PROJECT_B, PROJECT_B, PROJECT_B], 1):
        session.add(
            Risk(
                id=i,
                scope_id=scope,
                subcategory_id=1,
                seq=i,
                risk_code=f"TEC-DES-000{i}",
                title=f"Risk {i}",
            )
        )
        session.add(
            RiskQuantEstimate(risk_id=i, scenario="pre_mitigation", p_occurrence=0.5, **COST)
        )

    # A schedule belonging to project B, so a cross-project run has something to point at.
    session.add(
        ScheduleFile(
            id=1,
            scope_id=PROJECT_B,
            filename="b.xer",
            suffix=".xer",
            content=b"x",
            content_sha256="b" * 64,
            size_bytes=1,
        )
    )
    session.add(
        ScheduleVersion(
            id=1,
            file_id=1,
            source_project_id="P1",
            project_name="Project B",
            source_format="xer",
            parser_version="1.0",
            activity_count=0,
            relationship_count=0,
        )
    )
    await session.commit()


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(settings, "simulation_eager", True)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with maker() as session:
        await _seed(session)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(simulation_routes.router)

    async def _override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c._maker = maker  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


class TestAssemblyIsScoped:
    @pytest.mark.asyncio
    async def test_a_run_reads_only_its_own_projects_register(self, client):
        res = await client.post(f"/simulations?scope_id={PROJECT_A}", json={"iterations": 200})
        assert res.status_code == 201, res.text
        assert res.json()["risk_count"] == 2

        res = await client.post(f"/simulations?scope_id={PROJECT_B}", json={"iterations": 200})
        assert res.status_code == 201, res.text
        assert res.json()["risk_count"] == 3

    @pytest.mark.asyncio
    async def test_an_unscoped_request_lands_on_the_default_project(self, client):
        """Not "everything". A run has to belong somewhere, and the default is a project."""
        res = await client.post("/simulations", json={"iterations": 200})
        assert res.status_code == 201, res.text
        assert res.json()["risk_count"] == 2
        assert (await client.get("/simulations")).json()[0]["risk_count"] == 2

    @pytest.mark.asyncio
    async def test_preview_reads_exactly_what_the_run_would(self, client):
        """A preview over a wider register than the run is worse than no preview."""
        preview = await client.post(
            f"/simulations/preview?scope_id={PROJECT_A}", json={"iterations": 200}
        )
        run = await client.post(
            f"/simulations?scope_id={PROJECT_A}", json={"iterations": 200}
        )
        assert preview.json()["risk_count"] == run.json()["risk_count"] == 2
        assert preview.json()["inputs_sha256"] == run.json()["inputs_sha256"]

    @pytest.mark.asyncio
    async def test_a_project_with_no_estimates_is_told_so_rather_than_borrowing(self, client):
        async with client._maker() as session:
            session.add(
                ScopeNode(id=3, kind="project", name="Project C", created_by="test")
            )
            await session.commit()
        res = await client.post("/simulations?scope_id=3", json={"iterations": 200})
        assert res.status_code == 422
        assert "nothing to simulate" in res.text

    @pytest.mark.asyncio
    async def test_another_projects_schedule_is_refused(self, client):
        """Register from one project, network from another: every mapping resolves to
        nothing and the run comes back as a clean, wrong, cost-only answer."""
        res = await client.post(
            f"/simulations?scope_id={PROJECT_A}",
            json={"iterations": 200, "schedule_version_id": 1},
        )
        assert res.status_code == 422
        assert "different project" in res.text

    @pytest.mark.asyncio
    async def test_the_filter_is_off_when_no_scope_is_given_to_the_service(self, client):
        """``assemble`` itself still defaults to unfiltered, for pre-hierarchy callers."""
        async with client._maker() as session:
            unfiltered = await assemble(session, config=RunConfig(iterations=100))
            scoped = await assemble(
                session, config=RunConfig(iterations=100), scope_ids=[PROJECT_A]
            )
        assert unfiltered.risk_count == 5
        assert scoped.risk_count == 2

    @pytest.mark.asyncio
    async def test_a_portfolio_rolls_its_projects_up(self, client):
        """``descendant_ids`` is what the parameter takes, so a rollup needs no new path."""
        async with client._maker() as session:
            rolled = await assemble(
                session,
                config=RunConfig(iterations=100),
                scope_ids=[PORTFOLIO, PROJECT_A, PROJECT_B],
            )
        assert rolled.risk_count == 5

    @pytest.mark.asyncio
    async def test_an_empty_scope_list_is_not_the_same_as_no_scope(self, client):
        """``[]`` is a real answer — a scope containing nothing — and must not read as
        ``None``. The distinction is one falsy check away from being lost."""
        async with client._maker() as session:
            with pytest.raises(SimulationNotAssemblable):
                await assemble(session, config=RunConfig(iterations=100), scope_ids=[])
