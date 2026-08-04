"""End-to-end tests for mitigation ROI: launching a pair, and refusing a bad one.

Self-contained in the style of ``test_simulations_api.py`` — its own app, its own SQLite
schema, ``simulation_eager`` so the real sampler runs inside the request. Cost-only runs
throughout: the schedule half of the engine is exercised in ``test_simulations_api.py``
and repeating it here would double the runtime to test the same code.

Two projects are seeded, and most of the interesting assertions are about the second one.
A comparison that quietly read another project's register would produce a perfectly
plausible number, which is why 4.5 could not start until assembly was scope-filtered.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import mitigation_plans as plan_routes
from app.api.routes import roi as roi_routes
from app.api.routes import simulations as simulation_routes
from app.core.config import settings
from app.db.base_class import Base
from app.db.session import get_db
from app.models.mitigation import MitigationAction
from app.models.quant import RiskQuantEstimate
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.roi import MitigationRoi  # noqa: F401  (registers the table)
from app.models.scope import ScopeNode

PROJECT_A = 1
PROJECT_B = 2

FAST = {"iterations": 400, "seed": 7}

#: Big, wide, and certain enough that halving it moves the P80 far outside the error bar
#: a 400-iteration run carries. A test that asserted on a reduction inside the noise would
#: be asserting on the seed.
COST = {
    "bound_interpretation": "absolute",
    "cost_dist": "pert",
    "cost_min": 400_000.0,
    "cost_ml": 800_000.0,
    "cost_max": 2_000_000.0,
    "confidence": "high",
}


async def _seed(session) -> None:
    session.add(RbsCategory(id=1, code="TEC", name="Technical"))
    session.add(RbsSubcategory(id=1, category_id=1, code="DES", name="Design"))
    session.add(
        ScopeNode(
            id=PROJECT_A,
            kind="project",
            name="Project A",
            is_default=True,
            created_by="test",
        )
    )
    session.add(
        ScopeNode(id=PROJECT_B, kind="project", name="Project B", created_by="test")
    )
    for i, (scope, title) in enumerate(
        [
            (PROJECT_A, "Scope growth"),
            (PROJECT_A, "Ground conditions"),
            (PROJECT_A, "Late permits"),
            (PROJECT_B, "Someone else's risk"),
        ],
        start=1,
    ):
        session.add(
            Risk(
                id=i,
                scope_id=scope,
                subcategory_id=1,
                seq=i,
                risk_code=f"TEC-DES-000{i}",
                title=title,
            )
        )
    for risk_id, p in ((1, 0.6), (2, 0.5), (3, 0.4), (4, 0.9)):
        session.add(
            RiskQuantEstimate(
                risk_id=risk_id, scenario="pre_mitigation", p_occurrence=p, **COST
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
    app.include_router(plan_routes.router)
    app.include_router(roi_routes.router)

    async def _override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c._maker = maker  # type: ignore[attr-defined]
        yield c
    await engine.dispose()


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


async def make_plan(client, scope: int = PROJECT_A, name: str = "Package 1") -> int:
    res = await client.post(f"/mitigation/plans?scope_id={scope}", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def treat(client, plan_id: int, risk_id: int, **payload) -> None:
    body = {"treatment": "reduce", "mode": "factor", **payload}
    res = await client.put(f"/mitigation/plans/{plan_id}/risks/{risk_id}", json=body)
    assert res.status_code == 200, res.text


async def materialize(client, plan_id: int) -> None:
    res = await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
    assert res.status_code == 200, res.text


async def a_measured_package(client, *, plan_id: int | None = None) -> dict:
    """A plan that halves two risks, materialised, and run as a matched pair."""
    plan_id = plan_id if plan_id is not None else await make_plan(client)
    await treat(client, plan_id, 1, cost_factor=0.4, p_factor=0.5)
    await treat(client, plan_id, 2, treatment="retire")
    await materialize(client, plan_id)
    res = await client.post(f"/roi/plans/{plan_id}/runs", json={**FAST, "name": "Q3"})
    assert res.status_code == 201, res.text
    return res.json()


# --------------------------------------------------------------------------------------
# launching a matched pair
# --------------------------------------------------------------------------------------


class TestLaunchPair:
    @pytest.mark.asyncio
    async def test_one_request_produces_two_runs_that_differ_only_in_scenario(self, client):
        body = await a_measured_package(client)

        assert body["status"] == "ready"
        assert body["before"]["scenario"] == "pre_mitigation"
        assert body["after"]["scenario"] == "post_mitigation"
        assert body["issues"] == []
        for field in ("seed", "iterations", "sampling", "base_cost", "burn_rate_per_day"):
            assert body["before"][field] == body["after"][field]
        # Different registers, therefore different fingerprints. Identical ones would mean
        # the treated run read the baseline scenario.
        assert body["before"]["inputs_sha256"] != body["after"]["inputs_sha256"]

    @pytest.mark.asyncio
    async def test_the_treated_run_reads_the_residual_register(self, client):
        body = await a_measured_package(client)
        # Three baselines in project A; one risk retired.
        assert body["before"]["risk_count"] == 3
        assert body["after"]["risk_count"] == 2

    @pytest.mark.asyncio
    async def test_the_comparison_reports_a_reduction(self, client):
        body = await a_measured_package(client)
        contingency = body["comparison"]["contingency"]["at_percentile"]
        assert contingency["reduction"] > 0
        assert contingency["before"] > contingency["after"]
        assert body["comparison"]["retired_count"] == 1

    @pytest.mark.asyncio
    async def test_an_unmaterialised_package_is_refused_with_a_reason(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.5)
        res = await client.post(f"/roi/plans/{plan_id}/runs", json=FAST)
        assert res.status_code == 409
        assert "materialised" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_neither_run_is_written_when_the_pair_cannot_be_assembled(self, client):
        """A lone baseline claiming to be half of a comparison is worse than a refusal."""
        plan_id = await make_plan(client)
        await materialize(client, plan_id)
        # Every residual removed by hand: the treated half now has nothing to simulate.
        async with client._maker() as session:
            for row in (
                await session.scalars(
                    select(RiskQuantEstimate).where(
                        RiskQuantEstimate.scenario == "post_mitigation"
                    )
                )
            ).all():
                await session.delete(row)
            await session.commit()

        res = await client.post(f"/roi/plans/{plan_id}/runs", json=FAST)
        assert res.status_code == 422, res.text
        assert (await client.get("/simulations")).json() == []
        assert (await client.get("/roi")).json() == []

    @pytest.mark.asyncio
    async def test_the_runs_land_in_the_plans_project(self, client):
        plan_id = await make_plan(client, scope=PROJECT_B)
        await materialize(client, plan_id)
        res = await client.post(f"/roi/plans/{plan_id}/runs", json=FAST)
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["scope_id"] == PROJECT_B
        assert body["before"]["risk_count"] == 1  # project B has one risk, not four


# --------------------------------------------------------------------------------------
# pairing runs that already exist
# --------------------------------------------------------------------------------------


async def run_once(client, scenario: str, scope: int = PROJECT_A, **overrides) -> dict:
    res = await client.post(
        f"/simulations?scope_id={scope}",
        json={**FAST, "scenario": scenario, **overrides},
    )
    assert res.status_code == 201, res.text
    return res.json()


class TestPairExisting:
    @pytest.mark.asyncio
    async def test_two_matching_runs_can_be_paired_after_the_fact(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.4)
        await materialize(client, plan_id)
        before = await run_once(client, "pre_mitigation")
        after = await run_once(client, "post_mitigation")

        res = await client.post(
            "/roi",
            json={
                "plan_id": plan_id,
                "before_run_id": before["id"],
                "after_run_id": after["id"],
            },
        )
        assert res.status_code == 201, res.text
        body = res.json()
        assert body["status"] == "ready"
        assert body["seed_shared"] is True
        assert body["comparison"]["contingency"]["at_percentile"]["reduction"] > 0

    @pytest.mark.asyncio
    async def test_a_different_seed_is_recorded_rather_than_assumed_away(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.4)
        await materialize(client, plan_id)
        before = await run_once(client, "pre_mitigation")
        after = await run_once(client, "post_mitigation", seed=99)

        res = await client.post(
            "/roi",
            json={
                "plan_id": plan_id,
                "before_run_id": before["id"],
                "after_run_id": after["id"],
            },
        )
        # The seed is a paired field, so this is refused outright rather than recorded.
        assert res.status_code == 422
        assert "seed" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_the_scenarios_must_be_the_right_way_round(self, client):
        plan_id = await make_plan(client)
        await materialize(client, plan_id)
        before = await run_once(client, "pre_mitigation")
        after = await run_once(client, "post_mitigation")

        res = await client.post(
            "/roi",
            json={
                "plan_id": plan_id,
                "before_run_id": after["id"],
                "after_run_id": before["id"],
            },
        )
        assert res.status_code == 422
        assert "pre-mitigation" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_a_run_from_another_project_is_refused(self, client):
        """The whole reason 4.5 waited on the assembly scope filter."""
        plan_id = await make_plan(client, scope=PROJECT_A)
        await materialize(client, plan_id)
        before = await run_once(client, "pre_mitigation", scope=PROJECT_A)
        elsewhere = await run_once(client, "pre_mitigation", scope=PROJECT_B)

        res = await client.post(
            "/roi",
            json={
                "plan_id": plan_id,
                "before_run_id": before["id"],
                "after_run_id": elsewhere["id"],
            },
        )
        assert res.status_code == 422
        assert "different project" in res.json()["detail"]

    @pytest.mark.asyncio
    async def test_a_run_cannot_be_paired_with_itself(self, client):
        plan_id = await make_plan(client)
        await materialize(client, plan_id)
        before = await run_once(client, "pre_mitigation")
        res = await client.post(
            "/roi",
            json={
                "plan_id": plan_id,
                "before_run_id": before["id"],
                "after_run_id": before["id"],
            },
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_the_same_pair_twice_is_a_duplicate_not_a_second_opinion(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.4)
        await materialize(client, plan_id)
        before = await run_once(client, "pre_mitigation")
        after = await run_once(client, "post_mitigation")
        payload = {
            "plan_id": plan_id,
            "before_run_id": before["id"],
            "after_run_id": after["id"],
        }
        assert (await client.post("/roi", json=payload)).status_code == 201
        second = await client.post("/roi", json=payload)
        assert second.status_code == 409
        assert "already paired" in second.json()["detail"]

    @pytest.mark.asyncio
    async def test_an_unknown_plan_or_run_is_a_named_404(self, client):
        plan_id = await make_plan(client)
        await materialize(client, plan_id)
        before = await run_once(client, "pre_mitigation")
        after = await run_once(client, "post_mitigation")
        assert (
            await client.post(
                "/roi",
                json={
                    "plan_id": 999,
                    "before_run_id": before["id"],
                    "after_run_id": after["id"],
                },
            )
        ).status_code == 404
        assert (
            await client.post(
                "/roi",
                json={
                    "plan_id": plan_id,
                    "before_run_id": before["id"],
                    "after_run_id": 999,
                },
            )
        ).status_code == 404


# --------------------------------------------------------------------------------------
# what the record remembers
# --------------------------------------------------------------------------------------


class TestSnapshots:
    @pytest.mark.asyncio
    async def test_plan_cost_is_frozen_at_pairing_and_divergence_is_visible(self, client):
        plan_id = await make_plan(client)
        async with client._maker() as session:
            session.add(
                MitigationAction(
                    risk_id=1, plan_id=plan_id, action="Pre-purchase", budget=100_000.0
                )
            )
            await session.commit()

        body = await a_measured_package(client, plan_id=plan_id)
        roi_id = body["id"]
        assert body["plan_budget"] == pytest.approx(100_000.0)
        assert body["cost_moved"] is False
        assert body["comparison"]["benefit_cost_ratio"] is not None

        # Re-cost the action afterwards. A quoted ratio must not move underneath a report.
        async with client._maker() as session:
            action = await session.get(MitigationAction, 1)
            action.budget = 250_000.0
            await session.commit()

        after = (await client.get(f"/roi/{roi_id}")).json()
        assert after["plan_budget"] == pytest.approx(100_000.0)
        assert after["current_plan_budget"] == pytest.approx(250_000.0)
        assert after["cost_moved"] is True
        assert any("re-costed" in w for w in after["comparison"]["warnings"])

    @pytest.mark.asyncio
    async def test_re_materialising_the_package_marks_the_comparison_stale(self, client):
        plan_id = await make_plan(client)
        body = await a_measured_package(client, plan_id=plan_id)
        assert body["stale"] is False

        await treat(client, plan_id, 3, cost_factor=0.2)
        res = await client.post(
            f"/mitigation/plans/{plan_id}/materialize",
            json={"confirm_replace_edited": True},
        )
        assert res.status_code == 200, res.text

        after = (await client.get(f"/roi/{body['id']}")).json()
        assert after["stale"] is True
        assert any("re-materialised" in w for w in after["comparison"]["warnings"])
        # Still readable. The pair records what was run; staleness is about the package.
        assert after["comparison"]["contingency"]["at_percentile"]["reduction"] > 0

    @pytest.mark.asyncio
    async def test_the_headline_can_be_read_at_another_computed_percentile(self, client):
        body = await a_measured_package(client)
        at_50 = (await client.get(f"/roi/{body['id']}?percentile=50")).json()
        assert at_50["comparison"]["percentile"] == 50.0
        assert (
            at_50["comparison"]["contingency"]["at_percentile"]["before"]
            < body["comparison"]["contingency"]["at_percentile"]["before"]
        )

    @pytest.mark.asyncio
    async def test_a_percentile_the_runs_never_computed_is_refused_not_invented(self, client):
        body = await a_measured_package(client)
        at_63 = (await client.get(f"/roi/{body['id']}?percentile=63")).json()
        assert at_63["comparison"]["contingency"]["at_percentile"]["reduction"] is None
        assert any("did not compute" in w for w in at_63["comparison"]["warnings"])


class TestListing:
    @pytest.mark.asyncio
    async def test_comparisons_are_scoped_like_every_other_list(self, client):
        await a_measured_package(client)
        other = await make_plan(client, scope=PROJECT_B, name="B package")
        await materialize(client, other)
        assert (
            await client.post(f"/roi/plans/{other}/runs", json=FAST)
        ).status_code == 201

        assert len((await client.get("/roi")).json()) == 2
        a_only = (await client.get(f"/roi?scope_id={PROJECT_A}")).json()
        assert [r["scope_id"] for r in a_only] == [PROJECT_A]
        b_only = (await client.get(f"/roi?scope_id={PROJECT_B}")).json()
        assert [r["scope_id"] for r in b_only] == [PROJECT_B]

    @pytest.mark.asyncio
    async def test_a_plan_filter_narrows_to_one_package(self, client):
        first = await a_measured_package(client)
        second_plan = await make_plan(client, name="Package 2")
        # The first package already wrote this scope's residuals, so a second one
        # overwriting them is exactly the case that needs confirming (4.4's guard).
        res = await client.post(
            f"/mitigation/plans/{second_plan}/materialize",
            json={"confirm_replace_edited": True},
        )
        assert res.status_code == 200, res.text
        assert (
            await client.post(f"/roi/plans/{second_plan}/runs", json=FAST)
        ).status_code == 201

        rows = (await client.get(f"/roi?plan_id={first['plan_id']}")).json()
        assert [r["id"] for r in rows] == [first["id"]]

    @pytest.mark.asyncio
    async def test_an_unknown_comparison_is_a_404(self, client):
        assert (await client.get("/roi/999")).status_code == 404
