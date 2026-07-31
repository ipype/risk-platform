"""End-to-end tests for the quantitative elicitation endpoints.

Self-contained: builds its own app and SQLite schema rather than importing
``app.main``, so it exercises the router, the models, and the error translation without
needing Postgres or the rest of the service graph. Explicit ``@pytest.mark.asyncio``
marks so the file runs under either pytest-asyncio mode.
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
            Risk(id=1, subcategory_id=1, seq=1, risk_code="TEC-DES-0001", title="Design growth")
        )
        session.add(
            Risk(id=2, subcategory_id=1, seq=2, risk_code="TEC-DES-0002", title="Late permits")
        )
        await session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(quant_routes.router)

    async def _override():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c._maker = maker  # type: ignore[attr-defined]
        yield c

    await engine.dispose()


PAYLOAD = {
    "p_occurrence": 0.3,
    "cost_min": 100000,
    "cost_ml": 250000,
    "cost_max": 900000,
    "sched_min": 5,
    "sched_ml": 15,
    "sched_max": 40,
}


# --------------------------------------------------------------------------- preview


@pytest.mark.asyncio
async def test_preview_returns_moments_without_persisting(client):
    r = await client.post("/quant/preview", json=PAYLOAD)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["summary"]["cost"]["mean"] > 0
    assert body["summary"]["sched"]["alpha"] > 1

    listing = await client.get("/risks/1/quant")
    assert listing.json() == []


@pytest.mark.asyncio
async def test_preview_reports_errors_without_raising(client):
    bad = {**PAYLOAD, "cost_ml": 10}
    r = await client.post("/quant/preview", json=bad)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["errors"] and body["summary"] == {}


@pytest.mark.asyncio
async def test_preview_bound_interpretation_widens_the_spread(client):
    hard = await client.post("/quant/preview", json={**PAYLOAD, "bound_interpretation": "absolute"})
    soft = await client.post("/quant/preview", json={**PAYLOAD, "bound_interpretation": "p10_p90"})
    assert soft.json()["summary"]["cost"]["sd"] > hard.json()["summary"]["cost"]["sd"]
    assert soft.json()["summary"]["cost"]["lo"] < hard.json()["summary"]["cost"]["lo"]


# -------------------------------------------------------------------------- estimates


@pytest.mark.asyncio
async def test_upsert_creates_then_replaces_one_row(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation", json=PAYLOAD, headers={"X-Actor": "sam"}
    )
    assert r.status_code == 200
    assert r.json()["estimate"]["estimated_by"] == "sam"
    first_id = r.json()["estimate"]["id"]

    r2 = await client.put(
        "/risks/1/quant/pre_mitigation", json={**PAYLOAD, "p_occurrence": 0.6}
    )
    assert r2.status_code == 200
    assert r2.json()["estimate"]["id"] == first_id
    assert r2.json()["estimate"]["p_occurrence"] == 0.6

    listing = await client.get("/risks/1/quant")
    assert len(listing.json()) == 1


@pytest.mark.asyncio
async def test_both_scenarios_coexist(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    await client.put(
        "/risks/1/quant/post_mitigation", json={**PAYLOAD, "cost_ml": 120000}
    )
    listing = await client.get("/risks/1/quant")
    assert {row["scenario"] for row in listing.json()} == {
        "pre_mitigation",
        "post_mitigation",
    }


@pytest.mark.asyncio
async def test_unknown_scenario_is_404(client):
    r = await client.put("/risks/1/quant/wishful", json=PAYLOAD)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_unknown_risk_is_404(client):
    r = await client.put("/risks/999/quant/pre_mitigation", json=PAYLOAD)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_invalid_estimate_is_422_and_lists_every_failing_rule(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={**PAYLOAD, "cost_ml": 10, "sched_ml": 1},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "quant_estimate_invalid"
    assert len(body["issues"]) == 2, "both dimensions should be reported, not just the first"


@pytest.mark.asyncio
async def test_variability_without_certainty_is_rejected(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation",
        json={**PAYLOAD, "is_variability": True, "p_occurrence": 0.4},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_warnings_ride_along_with_a_successful_write(client):
    r = await client.put(
        "/risks/1/quant/pre_mitigation", json={**PAYLOAD, "p_occurrence": 0.01}
    )
    assert r.status_code == 200
    assert any(w["field"] == "p_occurrence" for w in r.json()["warnings"])


@pytest.mark.asyncio
async def test_zero_probability_rejected_by_schema(client):
    r = await client.put("/risks/1/quant/pre_mitigation", json={**PAYLOAD, "p_occurrence": 0})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_estimate_includes_summary(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    r = await client.get("/risks/1/quant/pre_mitigation")
    assert r.status_code == 200
    assert r.json()["summary"]["cost"]["mean"] > 0


@pytest.mark.asyncio
async def test_missing_estimate_is_404(client):
    r = await client.get("/risks/2/quant/pre_mitigation")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_removes_the_estimate(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    r = await client.delete("/risks/1/quant/pre_mitigation")
    assert r.status_code == 204
    assert (await client.get("/risks/1/quant")).json() == []


# ------------------------------------------------------------------------------ lock


@pytest.mark.asyncio
async def test_locked_estimate_blocks_edit_and_delete(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    lock = await client.patch("/risks/1/quant/pre_mitigation/lock", json={"locked": True})
    assert lock.status_code == 200 and lock.json()["locked"] is True

    edit = await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    assert edit.status_code == 409
    assert edit.json()["error"] == "quant_estimate_locked"

    delete = await client.delete("/risks/1/quant/pre_mitigation")
    assert delete.status_code == 409


@pytest.mark.asyncio
async def test_unlocking_restores_the_write(client):
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    await client.patch("/risks/1/quant/pre_mitigation/lock", json={"locked": True})
    await client.patch("/risks/1/quant/pre_mitigation/lock", json={"locked": False})
    r = await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    assert r.status_code == 200


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

    actions = [r.action for r in rows]
    assert actions == ["quant set", "quant updated", "quant locked"]
    assert rows[0].actor == "sam" and rows[1].actor == "dana"
    assert rows[0].risk_code == "TEC-DES-0001"
    assert any(c["field"] == "p_occurrence" for c in rows[1].changes)


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
    r = await client.post("/quant/triage", json={"risk_ids": [1, 2], "quantify": True})
    assert r.json()["updated"] == 2
    again = await client.post("/quant/triage", json={"risk_ids": [1, 2], "quantify": True})
    assert again.json()["updated"] == 0


@pytest.mark.asyncio
async def test_coverage_reports_the_gap(client):
    await client.post("/quant/triage", json={"risk_ids": [1, 2], "quantify": True})
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)

    r = await client.get("/quant/coverage")
    body = r.json()
    assert body["flagged_for_quantification"] == 2
    assert body["estimated"] == 1
    assert body["missing"] == [2]


@pytest.mark.asyncio
async def test_coverage_is_scenario_aware(client):
    await client.post("/quant/triage", json={"risk_ids": [1], "quantify": True})
    await client.put("/risks/1/quant/pre_mitigation", json=PAYLOAD)
    assert (await client.get("/quant/coverage?scenario=post_mitigation")).json()["missing"] == [1]


# --------------------------------------------------------------------------- drivers


@pytest.mark.asyncio
async def test_driver_crud(client):
    r = await client.post(
        "/drivers", json={"name": "Labour market", "correlation_default": 0.7}
    )
    assert r.status_code == 201
    driver_id = r.json()["id"]

    dupe = await client.post("/drivers", json={"name": "Labour market"})
    assert dupe.status_code == 409

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

    r2 = await client.put("/risks/1/drivers", json={"driver_ids": [b]})
    assert [d["name"] for d in r2.json()] == ["Weather"]

    cleared = await client.put("/risks/1/drivers", json={"driver_ids": []})
    assert cleared.json() == []


@pytest.mark.asyncio
async def test_tagging_an_unknown_driver_is_404(client):
    r = await client.put("/risks/1/drivers", json={"driver_ids": [42]})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_correlation_groups_cluster_risks_by_shared_driver(client):
    d = (await client.post("/drivers", json={"name": "Steel price", "correlation_default": 0.8})).json()
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
        left = (await session.execute(select(RiskQuantEstimate))).scalars().all()
    assert left == []
