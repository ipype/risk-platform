"""End-to-end tests for the mitigation plan endpoints and the residual writer.

Self-contained, in the shape ``test_quant_api.py`` established: its own app, its own
SQLite schema, no Postgres and no Redis.

The last class is the one that justifies the module's design. A materialised plan has to
leave the database in a state ``sim_assembly.assemble(scenario="post_mitigation")`` can
read with no new branch, because that is what makes re-simulation ROI a comparison of two
runs rather than a second engine.
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import mitigation_plans as plan_routes
from app.api.routes import mitigations as action_routes
from app.db.base_class import Base
from app.db.session import get_db
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.models.history import RiskHistory
from app.models.quant import RiskQuantEstimate
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.scope import ScopeNode

PROJECT_A = 1
PROJECT_B = 2

COST = {"cost_dist": "pert", "cost_min": 100_000.0, "cost_ml": 250_000.0, "cost_max": 900_000.0}
SCHED = {"sched_dist": "pert", "sched_min": 5.0, "sched_ml": 15.0, "sched_max": 40.0}


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with maker() as session:
        session.add(RbsCategory(id=1, code="TEC", name="Technical"))
        session.add(RbsSubcategory(id=1, category_id=1, code="DES", name="Design"))
        session.add(
            ScopeNode(id=PROJECT_A, kind="project", name="Project A", created_by="test")
        )
        session.add(
            ScopeNode(id=PROJECT_B, kind="project", name="Project B", created_by="test")
        )
        for i, (scope, title) in enumerate(
            [
                (PROJECT_A, "Design growth"),
                (PROJECT_A, "Late permits"),
                (PROJECT_A, "Weather"),
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
        # Three baselines in project A; the fourth risk deliberately has none, so the
        # "every risk with a baseline" rule is testable against a register where that is
        # not the same as "every risk".
        for risk_id, p in ((1, 0.4), (2, 0.3), (3, 0.6)):
            session.add(
                RiskQuantEstimate(
                    risk_id=risk_id, scenario="pre_mitigation", p_occurrence=p, **COST, **SCHED
                )
            )
        await session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(plan_routes.router)
    app.include_router(action_routes.router)

    async def _override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c._maker = maker  # type: ignore[attr-defined]
        yield c

    await engine.dispose()


async def make_plan(client, name="Package A", scope=PROJECT_A) -> int:
    res = await client.post(f"/mitigation/plans?scope_id={scope}", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def treat(client, plan_id, risk_id, **payload):
    body = {"treatment": "reduce", "mode": "factor", **payload}
    res = await client.put(f"/mitigation/plans/{plan_id}/risks/{risk_id}", json=body)
    assert res.status_code == 200, res.text
    return res.json()


# ------------------------------------------------------------------------------- CRUD


class TestPlans:
    @pytest.mark.asyncio
    async def test_vocabulary_is_served_not_hardcoded(self, client):
        body = (await client.get("/mitigation/vocabulary")).json()
        assert "draft" in body["plan_statuses"]
        assert set(body["treatments"]) == {"reduce", "retire", "accept"}
        assert set(body["modes"]) == {"factor", "absolute"}

    @pytest.mark.asyncio
    async def test_create_read_update(self, client):
        plan_id = await make_plan(client)
        body = (await client.get(f"/mitigation/plans/{plan_id}")).json()
        assert body["status"] == "draft"
        assert body["materialized_at"] is None
        assert body["cost"]["action_count"] == 0

        patched = await client.patch(
            f"/mitigation/plans/{plan_id}", json={"status": "approved"}
        )
        assert patched.json()["status"] == "approved"

    @pytest.mark.asyncio
    async def test_an_unknown_status_is_refused(self, client):
        res = await client.post(
            f"/mitigation/plans?scope_id={PROJECT_A}",
            json={"name": "Bad", "status": "maybe"},
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_plans_are_listed_per_scope(self, client):
        await make_plan(client, "A plan", PROJECT_A)
        await make_plan(client, "B plan", PROJECT_B)
        names = [
            p["name"]
            for p in (await client.get(f"/mitigation/plans?scope_id={PROJECT_A}")).json()
        ]
        assert names == ["A plan"]

    @pytest.mark.asyncio
    async def test_a_risk_outside_the_plans_scope_is_refused(self, client):
        plan_id = await make_plan(client)
        res = await client.put(
            f"/mitigation/plans/{plan_id}/risks/4", json={"treatment": "reduce"}
        )
        assert res.status_code == 422
        assert "outside the scope" in res.json()["detail"]


# ------------------------------------------------------------------------------- cost


class TestPlanCost:
    @pytest.mark.asyncio
    async def test_cost_sums_money_and_days_and_names_what_is_unpriced(self, client):
        plan_id = await make_plan(client)
        for payload in (
            {"action": "Pre-order steel", "budget": 120_000, "sched_days": 10, "plan_id": plan_id},
            {"action": "Second supplier", "budget": 40_000, "plan_id": plan_id},
            {"action": "Workshop", "plan_id": plan_id},
            {
                "action": "Abandoned idea",
                "budget": 999_999,
                "status": "Cancelled",
                "plan_id": plan_id,
            },
        ):
            res = await client.post("/risks/1/actions", json=payload)
            assert res.status_code == 201, res.text

        cost = (await client.get(f"/mitigation/plans/{plan_id}")).json()["cost"]
        assert cost["action_count"] == 4
        assert cost["costed_count"] == 2
        # An action with neither a budget nor a duration is a hole in the cost side, and
        # a rollup that treats it as zero is the cost-side twin of dropping a risk.
        assert cost["unpriced_count"] == 1
        assert cost["cancelled_count"] == 1
        assert cost["total_budget"] == pytest.approx(160_000.0)
        assert cost["total_sched_days"] == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_actions_can_be_listed_across_the_register(self, client):
        plan_id = await make_plan(client)
        await client.post("/risks/1/actions", json={"action": "In plan", "plan_id": plan_id})
        await client.post("/risks/2/actions", json={"action": "Loose"})

        every = (await client.get(f"/mitigation/actions?scope_id={PROJECT_A}")).json()
        assert len(every) == 2
        assert every[0]["risk_code"] == "TEC-DES-0001"

        in_plan = (await client.get(f"/mitigation/actions?plan_id={plan_id}")).json()
        assert [a["action"] for a in in_plan] == ["In plan"]

        loose = (await client.get("/mitigation/actions?unassigned=true")).json()
        assert [a["action"] for a in loose] == ["Loose"]

    @pytest.mark.asyncio
    async def test_deleting_a_plan_detaches_its_actions_rather_than_deleting_them(
        self, client
    ):
        plan_id = await make_plan(client)
        await client.post("/risks/1/actions", json={"action": "Keep me", "plan_id": plan_id})
        assert (await client.delete(f"/mitigation/plans/{plan_id}")).status_code == 204
        remaining = (await client.get("/risks/1/actions")).json()
        assert [a["action"] for a in remaining] == ["Keep me"]
        assert remaining[0]["plan_id"] is None


# --------------------------------------------------------------------------- residual


class TestResidualPreview:
    @pytest.mark.asyncio
    async def test_untreated_risks_appear_at_full_size(self, client):
        """The whole register, not the treated part of it."""
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.5, p_factor=0.5)

        body = (await client.get(f"/mitigation/plans/{plan_id}/residual")).json()
        assert len(body["lines"]) == 3
        assert body["treated"] == 1
        assert body["untreated"] == 2

        untreated = [ln for ln in body["lines"] if ln["treatment"] == "untreated"]
        for line in untreated:
            assert line["base_p"] == line["residual_p"]
            assert line["base_cost_ev"] == pytest.approx(line["residual_cost_ev"])

    @pytest.mark.asyncio
    async def test_a_reduction_shows_up_in_the_expected_impact(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.5, p_factor=0.5)
        body = (await client.get(f"/mitigation/plans/{plan_id}/residual")).json()
        line = next(ln for ln in body["lines"] if ln["risk_id"] == 1)
        assert line["residual_cost_ev"] == pytest.approx(line["base_cost_ev"] * 0.25)
        assert body["residual_cost_ev_total"] < body["base_cost_ev_total"]

    @pytest.mark.asyncio
    async def test_a_retired_risk_is_marked_and_carries_no_residual(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 2, treatment="retire")
        body = (await client.get(f"/mitigation/plans/{plan_id}/residual")).json()
        line = next(ln for ln in body["lines"] if ln["risk_id"] == 2)
        assert line["retired"] is True
        assert line["residual_p"] is None
        assert body["retired"] == 1

    @pytest.mark.asyncio
    async def test_preview_writes_nothing(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.5)
        await client.get(f"/mitigation/plans/{plan_id}/residual")
        async with client._maker() as db:
            rows = (
                await db.scalars(
                    select(RiskQuantEstimate).where(
                        RiskQuantEstimate.scenario == "post_mitigation"
                    )
                )
            ).all()
        assert list(rows) == []


# ----------------------------------------------------------------------- materialising


class TestMaterialize:
    @pytest.mark.asyncio
    async def test_every_baseline_risk_gets_a_residual_including_the_untreated(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.5, p_factor=0.5)

        body = (
            await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        ).json()
        assert body["written"] == 3
        assert body["retired"] == 0

        async with client._maker() as db:
            rows = {
                r.risk_id: r
                for r in (
                    await db.scalars(
                        select(RiskQuantEstimate).where(
                            RiskQuantEstimate.scenario == "post_mitigation"
                        )
                    )
                ).all()
            }
        assert set(rows) == {1, 2, 3}
        assert rows[1].p_occurrence == pytest.approx(0.2)
        assert rows[1].cost_max == pytest.approx(450_000.0)
        # untreated, so identical to the baseline
        assert rows[2].p_occurrence == pytest.approx(0.3)
        assert rows[2].cost_max == pytest.approx(900_000.0)
        # provenance survives
        assert rows[1].source == "analyst"
        assert "Package A" in (rows[1].notes or "")

    @pytest.mark.asyncio
    async def test_project_b_is_untouched(self, client):
        """A plan writes into its own scope and nowhere else."""
        plan_id = await make_plan(client)
        await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        async with client._maker() as db:
            rows = (
                await db.execute(
                    select(RiskQuantEstimate.risk_id)
                    .join(Risk, Risk.id == RiskQuantEstimate.risk_id)
                    .where(Risk.scope_id == PROJECT_B)
                )
            ).all()
        assert rows == []

    @pytest.mark.asyncio
    async def test_retiring_removes_a_residual_that_was_already_there(self, client):
        plan_id = await make_plan(client)
        await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        await treat(client, plan_id, 2, treatment="retire")
        body = (
            await client.post(
                f"/mitigation/plans/{plan_id}/materialize",
                json={"confirm_replace_edited": True},
            )
        ).json()
        assert body["retired"] == 1
        async with client._maker() as db:
            ids = (
                await db.scalars(
                    select(RiskQuantEstimate.risk_id).where(
                        RiskQuantEstimate.scenario == "post_mitigation"
                    )
                )
            ).all()
        assert set(ids) == {1, 3}

    @pytest.mark.asyncio
    async def test_a_locked_residual_is_never_overwritten(self, client):
        """A run froze this estimate. Invariant 6 outranks the plan."""
        plan_id = await make_plan(client)
        await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        async with client._maker() as db:
            row = (
                await db.scalars(
                    select(RiskQuantEstimate).where(
                        RiskQuantEstimate.scenario == "post_mitigation",
                        RiskQuantEstimate.risk_id == 1,
                    )
                )
            ).one()
            row.locked = True
            # a valid triple: the CHECK constraints still apply to a frozen row
            row.cost_max = 1_234_567.0
            await db.commit()

        await treat(client, plan_id, 1, cost_factor=0.1)
        body = (
            await client.post(
                f"/mitigation/plans/{plan_id}/materialize",
                json={"confirm_replace_edited": True},
            )
        ).json()
        assert body["skipped_locked"] == ["TEC-DES-0001"]
        async with client._maker() as db:
            row = (
                await db.scalars(
                    select(RiskQuantEstimate).where(
                        RiskQuantEstimate.scenario == "post_mitigation",
                        RiskQuantEstimate.risk_id == 1,
                    )
                )
            ).one()
        assert row.cost_max == pytest.approx(1_234_567.0)

    @pytest.mark.asyncio
    async def test_overwriting_a_hand_written_residual_needs_confirmation(self, client):
        """Elicited work is not something a factor sheet gets to destroy silently."""
        async with client._maker() as db:
            db.add(
                RiskQuantEstimate(
                    risk_id=1, scenario="post_mitigation", p_occurrence=0.05, **COST
                )
            )
            await db.commit()

        plan_id = await make_plan(client)
        blocked = await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        assert blocked.status_code == 409
        assert "TEC-DES-0001" in blocked.json()["detail"]

        async with client._maker() as db:
            row = (
                await db.scalars(
                    select(RiskQuantEstimate).where(
                        RiskQuantEstimate.scenario == "post_mitigation"
                    )
                )
            ).one()
        assert row.p_occurrence == pytest.approx(0.05), "the refusal must not half-write"

        ok = await client.post(
            f"/mitigation/plans/{plan_id}/materialize",
            json={"confirm_replace_edited": True},
        )
        assert ok.status_code == 200
        assert ok.json()["replaced_edited"] == ["TEC-DES-0001"]

    @pytest.mark.asyncio
    async def test_re_materialising_an_unchanged_plan_writes_nothing_new(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.5)
        first = (
            await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        ).json()
        second = (
            await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        ).json()
        assert first["written"] == 3
        assert second["written"] == 0
        assert second["unchanged"] == 3
        assert second["replaced_edited"] == []
        assert first["fingerprint"] == second["fingerprint"]

    @pytest.mark.asyncio
    async def test_the_plan_records_what_it_wrote(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 2, treatment="retire")
        result = (
            await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        ).json()
        plan = (await client.get(f"/mitigation/plans/{plan_id}")).json()
        assert plan["materialized_at"] is not None
        assert plan["materialized_fingerprint"] == result["fingerprint"]
        assert plan["materialized_risk_count"] == 2
        assert plan["materialized_retired_count"] == 1

        # and the preview agrees the register is still the one the plan wrote
        preview = (await client.get(f"/mitigation/plans/{plan_id}/residual")).json()
        assert preview["matches_materialized"] is True

    @pytest.mark.asyncio
    async def test_an_orphaned_residual_is_reported_not_hidden(self, client):
        """A residual whose baseline was deleted still feeds a post-mitigation run."""
        plan_id = await make_plan(client)
        await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        async with client._maker() as db:
            baseline = (
                await db.scalars(
                    select(RiskQuantEstimate).where(
                        RiskQuantEstimate.scenario == "pre_mitigation",
                        RiskQuantEstimate.risk_id == 3,
                    )
                )
            ).one()
            await db.delete(baseline)
            await db.commit()

        body = (
            await client.post(
                f"/mitigation/plans/{plan_id}/materialize",
                json={"confirm_replace_edited": True},
            )
        ).json()
        assert body["orphans"] == ["TEC-DES-0003"]

    @pytest.mark.asyncio
    async def test_materialising_lands_in_the_audit_trail(self, client):
        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.5)
        await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})
        async with client._maker() as db:
            actions = (
                await db.scalars(select(RiskHistory.action).order_by(RiskHistory.id))
            ).all()
        assert "treatment set" in actions
        assert actions.count("residual set") == 3


# --------------------------------------------------------- the reason any of this works


class TestAssemblesAsPostMitigation:
    @pytest.mark.asyncio
    async def test_a_materialised_plan_is_directly_simulable(self, client):
        """No new engine path, no new assembly branch: the scenario column already did it."""
        from app.services.sim_assembly import assemble
        from app.sim import RunConfig

        plan_id = await make_plan(client)
        await treat(client, plan_id, 1, cost_factor=0.5, p_factor=0.5)
        await treat(client, plan_id, 2, treatment="retire")
        await client.post(f"/mitigation/plans/{plan_id}/materialize", json={})

        async with client._maker() as db:
            before = await assemble(db, config=RunConfig(iterations=100), scenario="pre_mitigation")
            after = await assemble(db, config=RunConfig(iterations=100), scenario="post_mitigation")

        assert before.risk_count == 3
        # the retired risk is gone, the untreated one is still there at full size
        assert after.risk_count == 2
        codes = {r.code for r in after.request.risks}
        assert codes == {"TEC-DES-0001", "TEC-DES-0003"}

        treated = next(r for r in after.request.risks if r.code == "TEC-DES-0001")
        baseline = next(r for r in before.request.risks if r.code == "TEC-DES-0001")
        assert treated.p_occurrence == pytest.approx(baseline.p_occurrence * 0.5)
        assert treated.cost.hi == pytest.approx(baseline.cost.hi * 0.5)

        untouched = next(r for r in after.request.risks if r.code == "TEC-DES-0003")
        original = next(r for r in before.request.risks if r.code == "TEC-DES-0003")
        assert untouched.cost.hi == pytest.approx(original.cost.hi)
