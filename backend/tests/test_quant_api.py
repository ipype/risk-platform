"""End-to-end tests for the quantitative elicitation endpoints.

Self-contained: builds its own app and SQLite schema rather than importing ``app.main``,
so it exercises the router, the models, and the error translation without needing Postgres
or the rest of the service graph. Explicit ``@pytest.mark.asyncio`` marks so the file runs
under either pytest-asyncio mode.
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import quant as quant_routes
from app.db.base_class import Base
from app.db.session import get_db
from app.models.history import RiskHistory
from app.models.quant import RiskDriver, RiskQuantEstimate  # noqa: F401
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.scope import ScopeNode


@pytest_asyncio.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with maker() as session:
        session.add(RbsCategory(id=1, code="TEC", name="Technical"))
        session.add(
            RbsSubcategory(id=1, category_id=1, code="DES", name="Design and engineering")
        )
        session.add(
            ScopeNode(id=1, kind="project", name="Test project", created_by="test")
        )
        session.add(
            Risk(
                id=1,
                scope_id=1,
                subcategory_id=1,
                seq=1,
                risk_code="TEC-DES-0001",
                title="Design growth",
            )
        )
        session.add(
            Risk(
                id=2,
                scope_id=1,
                subcategory_id=1,
                seq=2,
                risk_code="TEC-DES-0002",
                title="Late permits",
            )
        )
        await session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(quant_routes.router)

    async def _override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        c._maker = maker  # type: ignore[attr-defined]
        yield c

    await engine.dispose()


COST = {"dist": "pert", "min": 100000, "ml": 250000, "max": 900000}
SCHED = {"dist": "pert", "min": 5, "ml": 15, "max": 40}
PAYLOAD = {"p_occurrence": 0.3, "cost": COST, "sched": SCHED}


# ------------------------------------------------------------------------- reference


@pytest.mark.asyncio
async def test_distribution_guidance_is_served(client):
    body = (await client.get("/quant/distributions")).json()
    names = {d["value"] for d in body["distributions"]}
    assert {"pert", "triangular", "trigen", "uniform", "cumulative", "discrete"} <= names
    for d in body["distributions"]:
        assert d["use_when"] and d["avoid_when"] and d["caution"]
    assert "p10_p90" in body["bound_interpretations"]
    assert body["rationale_keys"] == ["min", "ml", "max"]


# --------------------------------------------------------------------------- preview


@pytest.mark.asyncio
async def test_preview_returns_moments_without_persisting(client):
    body = (await client.post("/quant/preview", json=PAYLOAD)).json()
    assert body["ok"] is True
    assert body["summary"]["cost"]["mean"] > 0
    assert body["summary"]["sched"]["alpha"] > 1
    assert (await client.get("/risks/1/quant")).json() == []


@pytest.mark.asyncio
async def test_preview_reports_errors_without_raising(client):
    r = await client.post("/quant/preview", json={**PAYLOAD, "cost": {**COST, "ml": 10}})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["errors"] and body["summary"] == {}


@pytest.mark.asyncio
async def test_trigen_widens_and_reports_the_elicited_values(client):
    payload = {
        **PAYLOAD,
        "bound_interpretation": "p10_p90",
        "cost": {**COST, "dist": "trigen"},
        "sched": {**SCHED, "dist": "trigen"},
    }
    cost = (await client.post("/quant/preview", json=payload)).json()["summary"]["cost"]
    assert cost["kind"] == "trigen"
    assert cost["widened"] is True
    assert cost["lo"] < 100000 and cost["hi"] > 900000
    assert cost["elicited_lo"] == 100000


@pytest.mark.asyncio
async def test_triangular_spreads_wider_than_pert_on_the_same_points(client):
    pert = (await client.post("/quant/preview", json=PAYLOAD)).json()
    tri = await client.post(
        "/quant/preview",
        json={**PAYLOAD, "cost": {**COST, "dist": "triangular"}},
    )
    assert tri.json()["summary"]["cost"]["sd"] > pert["summary"]["cost"]["sd"]


@pytest.mark.asyncio
async def test_mixed_shapes_across_dimensions(client):
    payload = {
        "p_occurrence": 0.4,
        "cost": {"dist": "cumulative", "points": [
            {"x": 0, "p": 0.0}, {"x": 50, "p": 0.3}, {"x": 120, "p": 0.8}, {"x": 400, "p": 1.0}
        ]},
        "sched": {"dist": "uniform", "min": 5, "max": 40},
    }
    body = (await client.post("/quant/preview", json=payload)).json()
    assert body["ok"] is True
    assert body["summary"]["cost"]["kind"] == "cumulative"
    assert body["summary"]["sched"]["kind"] == "uniform"


# -------------------------------------------------------------------------- estimates


@pytest.mark.asyncio
async def test_upsert_creates_then_replaces_one_row(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation", json=PAYLOAD, headers={"X-Actor": "sam"}
    )
    assert r.status_code == 200
    assert r.json()["estimate"]["estimated_by"] == "sam"
    assert r.json()["estimate"]["cost"]["dist"] == "pert"
    first_id = r.json()["estimate"]["id"]

    r2 = await client.put(
        "/risks/1/quant/pre_mitigation", json={**PAYLOAD, "p_occurrence": 0.6}
    )
    assert r2.json()["estimate"]["id"] == first_id
    assert r2.json()["estimate"]["p_occurrence"] == 0.6
    assert len((await client.get("/risks/1/quant")).json()) == 1


@pytest.mark.asyncio
async def test_switching_shape_clears_values_the_new_shape_cannot_hold(client):
    """A stale mode under a uniform is invisible and would resurrect on the next switch."""
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={**PAYLOAD, "cost": {"dist": "uniform", "min": 100000, "max": 900000}},
    )
    assert r.json()["estimate"]["cost"]["ml"] is None


@pytest.mark.asyncio
async def test_switching_to_points_clears_the_three_point(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={
            **PAYLOAD,
            "cost": {"dist": "discrete", "points": [{"x": 50, "p": 0.6}, {"x": 300, "p": 0.4}]},
        },
    )
    cost = r.json()["estimate"]["cost"]
    assert cost["min"] is None and cost["ml"] is None and cost["max"] is None
    assert len(cost["points"]) == 2


@pytest.mark.asyncio
async def test_points_are_cleared_when_leaving_a_point_shape(client):
    await client.put(
        "/risks/1/quant/pre_mitigation",
        json={
            **PAYLOAD,
            "cost": {"dist": "discrete", "points": [{"x": 50, "p": 0.6}, {"x": 300, "p": 0.4}]},
        },
    )
    r = await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    assert r.json()["estimate"]["cost"]["points"] is None


@pytest.mark.asyncio
async def test_rationale_round_trips_per_point(client):
    rationale = {
        "min": {"text": "Permit lands first pass", "source": "sme", "author": "Dana"},
        "ml": {"text": "Two comparable corridor jobs", "source": "historical"},
        "max": {"text": "Full redesign plus remobilisation", "source": "sme"},
    }
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={**PAYLOAD, "cost": {**COST, "rationale": rationale}},
    )
    stored = r.json()["estimate"]["cost"]["rationale"]
    assert stored["ml"]["text"] == "Two comparable corridor jobs"
    assert stored["ml"]["source"] == "historical"
    assert stored["min"]["author"] == "Dana"


@pytest.mark.asyncio
async def test_agent_rationale_saves_but_is_flagged(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={
            **PAYLOAD,
            "cost": {
                **COST,
                "rationale": {"ml": {"text": "Drafted from tender pack", "source": "agent_proposal"}},
            },
        },
    )
    assert r.status_code == 200
    assert any("AI proposal" in w["message"] for w in r.json()["warnings"])


@pytest.mark.asyncio
async def test_triangular_with_percentile_bounds_is_422(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={
            **PAYLOAD,
            "bound_interpretation": "p10_p90",
            "cost": {**COST, "dist": "triangular"},
        },
    )
    assert r.status_code == 422
    assert any("trigen" in i["message"] for i in r.json()["issues"])


@pytest.mark.asyncio
async def test_discrete_masses_must_sum_to_one(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={
            **PAYLOAD,
            "cost": {"dist": "discrete", "points": [{"x": 50, "p": 0.6}, {"x": 300, "p": 0.2}]},
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_invalid_estimate_lists_every_failing_rule(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={**PAYLOAD, "cost": {**COST, "ml": 10}, "sched": {**SCHED, "ml": 1}},
    )
    assert r.status_code == 422
    assert r.json()["error"] == "quant_estimate_invalid"
    assert len(r.json()["issues"]) == 2


@pytest.mark.asyncio
async def test_unknown_scenario_and_risk_are_404(client):
    assert (await client.put("/risks/1/quant/wishful", json=PAYLOAD)).status_code == 404
    assert (await client.put("/risks/999/quant/pre_mitigation", json=PAYLOAD)).status_code == 404


@pytest.mark.asyncio
async def test_both_scenarios_coexist(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    await client.put(
        "/risks/1/quant/post_mitigation",
        json={**PAYLOAD, "cost": {**COST, "ml": 120000}},
    )
    listing = (await client.get("/risks/1/quant")).json()
    assert {row["scenario"] for row in listing} == {"pre_mitigation", "post_mitigation"}


@pytest.mark.asyncio
async def test_delete_removes_the_estimate(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    assert (await client.delete("/risks/1/quant/pre_mitigation")).status_code == 204
    assert (await client.get("/risks/1/quant")).json() == []


# ------------------------------------------------------------------------------ lock


@pytest.mark.asyncio
async def test_locked_estimate_blocks_edit_and_delete(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    await client.patch("/risks/1/quant/pre_mitigation/lock", json={"locked": True})

    edit = await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    assert edit.status_code == 409 and edit.json()["error"] == "quant_estimate_locked"
    assert (await client.delete("/risks/1/quant/pre_mitigation")).status_code == 409

    await client.patch("/risks/1/quant/pre_mitigation/lock", json={"locked": False})
    assert (await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)).status_code == 200


# --------------------------------------------------------------------------- history


@pytest.mark.asyncio
async def test_every_mutation_lands_in_the_audit_trail(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD, headers={"X-Actor": "sam"})
    await client.put(
        "/risks/1/quant/pre_mitigation",
        json={**PAYLOAD, "p_occurrence": 0.5},
        headers={"X-Actor": "dana"},
    )
    await client.patch("/risks/1/quant/pre_mitigation/lock", json={"locked": True})

    from sqlalchemy import select

    async with client._maker() as session:  # type: ignore[attr-defined]
        rows = (await session.execute(select(RiskHistory))).scalars().all()

    assert [r.action for r in rows] == ["quant set", "quant updated", "quant locked"]
    assert rows[0].actor == "sam" and rows[1].actor == "dana"
    assert any(c["field"] == "p_occurrence" for c in rows[1].changes)


@pytest.mark.asyncio
async def test_rationale_edits_are_audited(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    await client.put(
        "/risks/1/quant/pre_mitigation",
        json={**PAYLOAD, "cost": {**COST, "rationale": {"ml": {"text": "Two comparable jobs"}}}},
    )

    from sqlalchemy import select

    async with client._maker() as session:  # type: ignore[attr-defined]
        rows = (await session.execute(select(RiskHistory))).scalars().all()
    assert any(c["field"] == "cost_rationale" for c in rows[-1].changes)


@pytest.mark.asyncio
async def test_unchanged_write_records_no_history(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)

    from sqlalchemy import select

    async with client._maker() as session:  # type: ignore[attr-defined]
        rows = (await session.execute(select(RiskHistory))).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------- triage


@pytest.mark.asyncio
async def test_triage_flags_in_bulk_and_is_idempotent(client):
    assert (
        await client.post("/quant/triage", json={"risk_ids": [1, 2], "quantify": True})
    ).json()["updated"] == 2
    assert (
        await client.post("/quant/triage", json={"risk_ids": [1, 2], "quantify": True})
    ).json()["updated"] == 0


@pytest.mark.asyncio
async def test_triage_flags_can_be_read_back(client):
    await client.post("/quant/triage", json={"risk_ids": [2], "quantify": True})
    assert (await client.get("/quant/triage")).json()["risk_ids"] == [2]


@pytest.mark.asyncio
async def test_coverage_reports_the_gap(client):
    await client.post("/quant/triage", json={"risk_ids": [1, 2], "quantify": True})
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)

    body = (await client.get("/quant/coverage")).json()
    assert body["flagged_for_quantification"] == 2
    assert body["estimated"] == 1
    assert body["missing"] == [2]


@pytest.mark.asyncio
async def test_coverage_ignores_a_row_with_neither_dimension_assessed(client):
    """A saved row is not an estimate. Counting it would hide the gap it represents."""
    await client.post("/quant/triage", json={"risk_ids": [1], "quantify": True})
    async with client._maker() as session:  # type: ignore[attr-defined]
        session.add(RiskQuantEstimate(risk_id=1, scenario="pre_mitigation", p_occurrence=0.5))
        await session.commit()

    assert (await client.get("/quant/coverage")).json()["missing"] == [1]


# --------------------------------------------------------------------------- drivers


@pytest.mark.asyncio
async def test_driver_crud(client):
    r = await client.post("/drivers", json={"name": "Labour market", "correlation_default": 0.7})
    assert r.status_code == 201
    driver_id = r.json()["id"]

    assert (await client.post("/drivers", json={"name": "Labour market"})).status_code == 409
    patched = await client.patch(f"/drivers/{driver_id}", json={"correlation_default": 0.4})
    assert patched.json()["correlation_default"] == 0.4
    assert (await client.delete(f"/drivers/{driver_id}")).status_code == 204
    assert (await client.get("/drivers")).json() == []


@pytest.mark.asyncio
async def test_correlation_outside_minus_one_to_one_rejected(client):
    r = await client.post("/drivers", json={"name": "Nonsense", "correlation_default": 1.4})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_risk_driver_tags_replace_wholesale(client):
    a = (await client.post("/drivers", json={"name": "Steel price"})).json()["id"]
    b = (await client.post("/drivers", json={"name": "Weather"})).json()["id"]

    r = await client.put("/risks/1/drivers", json={"driver_ids": [a, b]})
    assert {d["name"] for d in r.json()} == {"Steel price", "Weather"}
    assert [d["name"] for d in (await client.put(
        "/risks/1/drivers", json={"driver_ids": [b]}
    )).json()] == ["Weather"]
    assert (await client.put("/risks/1/drivers", json={"driver_ids": []})).json() == []


@pytest.mark.asyncio
async def test_tagging_an_unknown_driver_is_404(client):
    assert (await client.put("/risks/1/drivers", json={"driver_ids": [42]})).status_code == 404


@pytest.mark.asyncio
async def test_correlation_groups_cluster_risks_by_shared_driver(client):
    d = (
        await client.post("/drivers", json={"name": "Steel price", "correlation_default": 0.8})
    ).json()
    await client.put("/risks/1/drivers", json={"driver_ids": [d["id"]]})
    await client.put("/risks/2/drivers", json={"driver_ids": [d["id"]]})

    body = (await client.get("/quant/correlation-groups")).json()
    assert len(body["groups"]) == 1
    assert body["groups"][0]["risk_ids"] == [1, 2]
    assert body["groups"][0]["rho"] == 0.8


@pytest.mark.asyncio
async def test_deleting_a_risk_takes_its_estimate_with_it(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)

    from sqlalchemy import delete, select, text

    async with client._maker() as session:  # type: ignore[attr-defined]
        await session.execute(text("PRAGMA foreign_keys=ON"))
        await session.execute(delete(Risk).where(Risk.id == 1))
        await session.commit()
        assert (await session.execute(select(RiskQuantEstimate))).scalars().all() == []
