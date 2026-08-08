"""End-to-end tests for the Monte Carlo run routes and the assembly adapter.

Self-contained in the style of ``test_quant_api.py``: its own app, its own SQLite schema,
no Postgres and no broker. ``simulation_eager`` runs the engine inside the request, so
these exercise the real sampler against a real four-activity network rather than a stub —
the whole point of the adapter is the join between the register, the estimates, the
mappings and the parse, and a stubbed engine would test none of it.

Iteration counts are deliberately small. What is under test here is the wiring and the
refusals; the numbers themselves are pinned in ``tests/sim/``.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import simulations as simulation_routes
from app.core.config import settings
from app.db.base_class import Base
from app.db.session import get_db
from app.models.mapping import RiskActivityMapping
from app.models.quant import RiskDriver, RiskDriverLink, RiskQuantEstimate
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.scope import ScopeNode
from app.models.schedule import (
    DcmaRun,
    ScheduleActivity,
    ScheduleFile,
    ScheduleRelationship,
    ScheduleVersion,
)
from app.models.simulation import SimulationRun  # noqa: F401  (registers the table)
from app.services.sim_calendars import load_calendar_set

FAST = {"iterations": 400, "seed": 7}


def _activity(source_id: str, code: str, days: float, *, kind: str = "task") -> ScheduleActivity:
    return ScheduleActivity(
        version_id=1,
        source_id=source_id,
        code=code,
        name=f"Activity {code}",
        calendar_source_id="CAL-1",
        wbs_source_id="W1",
        type=kind,
        status="not_started",
        duration_calendar_id="CAL-1",
        original_duration_days=days,
        remaining_duration_days=days,
        total_float_days=0.0,
    )


async def _seed(session) -> None:
    # Every authored row belongs to a project (migration 0014). One scope, seeded
    # explicitly rather than left to the API's get-or-create, so these tests keep
    # asserting about rows they put there themselves.
    #
    # ``is_default`` matters from 4.5 onward: assembly is scope-filtered, and a request
    # that names no scope resolves to the default project. Without the flag the seeded
    # project would not be it, the route would get-or-create a second one, and every run
    # here would assemble an empty register.
    session.add(
        ScopeNode(
            id=1, kind="project", name="Test project", is_default=True, created_by="test"
        )
    )
    session.add(RbsCategory(id=1, code="TEC", name="Technical"))
    session.add(RbsSubcategory(id=1, category_id=1, code="DES", name="Design"))
    session.add(Risk(
            id=1,
            scope_id=1,
            subcategory_id=1,
            seq=1,
            risk_code="TEC-DES-0001",
            title="Scope growth",
        ))
    session.add(Risk(
            id=2,
            scope_id=1,
            subcategory_id=1,
            seq=2,
            risk_code="TEC-DES-0002",
            title="Ground conditions",
        ))
    session.add(Risk(
            id=3,
            scope_id=1,
            subcategory_id=1,
            seq=3,
            risk_code="TEC-DES-0003",
            title="Half-elicited",
        ))

    # cost only
    session.add(
        RiskQuantEstimate(
            risk_id=1,
            scenario="pre_mitigation",
            p_occurrence=0.4,
            bound_interpretation="absolute",
            cost_dist="pert",
            cost_min=100_000.0,
            cost_ml=200_000.0,
            cost_max=500_000.0,
            confidence="high",
        )
    )
    # cost and schedule, mapped onto the network
    session.add(
        RiskQuantEstimate(
            risk_id=2,
            scenario="pre_mitigation",
            p_occurrence=0.5,
            bound_interpretation="absolute",
            cost_dist="pert",
            cost_min=50_000.0,
            cost_ml=80_000.0,
            cost_max=150_000.0,
            sched_dist="pert",
            sched_min=5.0,
            sched_ml=10.0,
            sched_max=30.0,
            sched_day_basis="working",
            confidence="high",
        )
    )
    # a shape with no numbers behind it: excluded, never silently dropped
    session.add(
        RiskQuantEstimate(
            risk_id=3,
            scenario="pre_mitigation",
            p_occurrence=0.3,
            bound_interpretation="absolute",
            cost_dist="pert",
        )
    )

    session.add(
        ScheduleFile(
            id=1,
            scope_id=1,
            filename="test.xer",
            suffix=".xer",
            content=b"x",
            content_sha256="a" * 64,
            size_bytes=1,
        )
    )
    session.add(
        ScheduleVersion(
            id=1,
            file_id=1,
            source_project_id="P1",
            project_name="Test project",
            source_format="xer",
            parser_version="1.0",
            activity_count=4,
            relationship_count=3,
        )
    )
    for row in (
        _activity("A1", "A1000", 5.0),
        _activity("A2", "A1010", 10.0),
        _activity("A3", "A1020", 7.0),
        _activity("A4", "A1030", 0.0, kind="milestone_finish"),
    ):
        session.add(row)
    for i, (pred, succ) in enumerate((("A1", "A2"), ("A2", "A3"), ("A3", "A4")), start=1):
        session.add(
            ScheduleRelationship(
                version_id=1,
                source_id=f"R{i}",
                predecessor_source_id=pred,
                successor_source_id=succ,
                type="FS",
                lag_days=0.0,
            )
        )

    session.add(
        RiskActivityMapping(
            risk_id=2,
            version_id=1,
            mapping_type="duration_driver",
            activity_source_id="A2",
            status="accepted",
            origin="manual",
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


async def _pass_gate(client) -> None:
    async with client._maker() as session:
        session.add(
            DcmaRun(
                version_id=1,
                gate_passed=True,
                passed_count=14,
                failed_count=0,
                not_assessed_count=0,
            )
        )
        await session.commit()


# --------------------------------------------------------------------------------------
# options
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_reports_the_gate_and_the_mapping_count(client):
    await _pass_gate(client)
    body = (await client.get("/simulations/options")).json()

    assert [s["value"] for s in body["scenarios"]] == ["pre_mitigation", "post_mitigation"]
    assert body["scenarios"][0]["estimate_count"] == 3

    version = body["schedule_versions"][0]
    assert version["id"] == 1
    assert version["gate"] == {
        "assessed": True,
        "passed": True,
        "failed_count": 0,
        "run_at": version["gate"]["run_at"],
        "blocking_failures": [],
    }
    # A green gate with no mappings runs fine and says nothing about schedule risk, which
    # is why the count sits next to it rather than being inferred from the version.
    assert version["accepted_mappings"] == 1


@pytest.mark.asyncio
async def test_options_reports_an_unassessed_version(client):
    body = (await client.get("/simulations/options")).json()
    assert body["schedule_versions"][0]["gate"]["assessed"] is False


# --------------------------------------------------------------------------------------
# the gate (invariant 3)
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unassessed_schedule_cannot_be_simulated(client):
    res = await client.post("/simulations/preview", json={"schedule_version_id": 1, **FAST})
    assert res.status_code == 409
    assert res.json()["error"] == "schedule_gate_blocked"


@pytest.mark.asyncio
async def test_a_failed_gate_needs_an_override(client):
    async with client._maker() as session:
        session.add(
            DcmaRun(
                version_id=1,
                gate_passed=False,
                passed_count=9,
                failed_count=5,
                not_assessed_count=0,
                blocking_failures=["logic", "leads"],
            )
        )
        await session.commit()

    blocked = await client.post("/simulations/preview", json={"schedule_version_id": 1, **FAST})
    assert blocked.status_code == 409
    assert blocked.json()["blocking_failures"] == ["logic", "leads"]

    # An override without a reason is not an override.
    unreasoned = await client.post(
        "/simulations/preview",
        json={"schedule_version_id": 1, "gate_override": True, **FAST},
    )
    assert unreasoned.status_code == 422

    owned = await client.post(
        "/simulations/preview",
        json={
            "schedule_version_id": 1,
            "gate_override": True,
            "gate_override_reason": "Known logic gaps in the commissioning block.",
            **FAST,
        },
    )
    assert owned.status_code == 200
    assert any("overridden" in n for n in owned.json()["notes"])


@pytest.mark.asyncio
async def test_the_override_is_recorded_on_the_run(client):
    async with client._maker() as session:
        session.add(
            DcmaRun(version_id=1, gate_passed=False, passed_count=9, failed_count=5)
        )
        await session.commit()

    body = (
        await client.post(
            "/simulations",
            json={
                "schedule_version_id": 1,
                "gate_override": True,
                "gate_override_reason": "Accepted by the PM for a screening run.",
                **FAST,
            },
        )
    ).json()
    assert body["gate_passed"] is False
    assert body["gate_override"] is True
    assert body["gate_override_reason"].startswith("Accepted by the PM")


# --------------------------------------------------------------------------------------
# assembly refusals and exclusions
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unsimulable_estimate_is_excluded_and_named(client):
    body = (await client.post("/simulations/preview", json=FAST)).json()
    assert body["risk_count"] == 2
    assert [e["risk_code"] for e in body["excluded"]] == ["TEC-DES-0003"]
    assert "missing" in body["excluded"][0]["reason"]


@pytest.mark.asyncio
async def test_a_calendar_day_impact_needs_no_conversion(client):
    """The engine's axis is elapsed days, so a calendar-day impact is already on it.

    This used to be a refusal. It stopped being one when the CPM moved onto elapsed days:
    the number the SME gave is the number the engine wants, and converting it would be
    the error rather than the fix.
    """
    async with client._maker() as session:
        estimate = await session.get(RiskQuantEstimate, 2)
        estimate.sched_day_basis = "calendar"
        await session.commit()

    await _pass_gate(client)
    res = await client.post("/simulations/preview", json={"schedule_version_id": 1, **FAST})
    assert res.status_code == 200
    assert not [x for x in res.json()["excluded"] if x["risk_code"] == "TEC-DES-0002"]


@pytest.mark.asyncio
async def test_a_working_day_impact_is_stretched_onto_the_elapsed_axis(client):
    """Ten working days on a five-day week is fourteen elapsed days, so the delay grows.

    Pinned as an inequality rather than a figure: the exact factor is a measured density
    and moving it is a modelling decision, but a working-day impact must never come out
    shorter than the same number read as calendar days.
    """
    await _pass_gate(client)
    working = (
        await client.post("/simulations", json={"schedule_version_id": 1, **FAST})
    ).json()

    async with client._maker() as session:
        await session.execute(
            RiskQuantEstimate.__table__.update().values(sched_day_basis="calendar")
        )
        await session.commit()

    calendar = (
        await client.post("/simulations", json={"schedule_version_id": 1, **FAST})
    ).json()

    assert working["result"]["delay_days"]["mean"] >= calendar["result"]["delay_days"]["mean"]


@pytest.mark.asyncio
async def test_two_calendars_are_converted_and_said_out_loud(client):
    async with client._maker() as session:
        activity = (
            await session.scalars(
                ScheduleActivity.__table__.select().where(
                    ScheduleActivity.source_id == "A3"
                )
            )
        ).first()
        assert activity is not None
        await session.execute(
            ScheduleActivity.__table__.update()
            .where(ScheduleActivity.__table__.c.source_id == "A3")
            .values(duration_calendar_id="CAL-2")
        )
        await session.commit()

    await _pass_gate(client)
    res = await client.post("/simulations/preview", json={"schedule_version_id": 1, **FAST})
    assert res.status_code == 200
    assert any("calendars" in n for n in res.json()["notes"]), res.json()["notes"]


@pytest.mark.asyncio
async def test_a_schedule_impact_with_no_mapping_is_noted_not_dropped_silently(client):
    async with client._maker() as session:
        mapping = await session.get(RiskActivityMapping, 1)
        mapping.status = "proposed"  # a proposal is not a decision (invariant 4)
        await session.commit()

    await _pass_gate(client)
    body = (
        await client.post("/simulations/preview", json={"schedule_version_id": 1, **FAST})
    ).json()
    assert body["mapped_risk_count"] == 0
    assert any("no accepted mapping" in n for n in body["notes"])


@pytest.mark.asyncio
async def test_a_burn_rate_without_a_schedule_is_rejected(client):
    res = await client.post("/simulations", json={"burn_rate_per_day": 25_000, **FAST})
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_a_cost_only_run_says_so(client):
    body = (await client.post("/simulations/preview", json=FAST)).json()
    assert body["activity_count"] == 0
    assert any("cost-only" in n for n in body["notes"])


# --------------------------------------------------------------------------------------
# running
# --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_integrated_run_produces_a_contingency(client):
    await _pass_gate(client)
    body = (
        await client.post(
            "/simulations",
            json={
                "name": "Screening run",
                "schedule_version_id": 1,
                "base_cost": 20_000_000,
                "burn_rate_per_day": 25_000,
                **FAST,
            },
        )
    ).json()

    assert body["status"] == "succeeded", body.get("error")
    assert body["risk_count"] == 2
    assert body["mapped_risk_count"] == 1
    assert body["activity_count"] == 4

    result = body["result"]
    contingency = {p["p"]: p["value"] for p in result["contingency"]["contingency"]}
    assert contingency[80] > 0

    # The engine's own deterministic pass, not the imported dates: 5 + 10 + 7 + 0.
    assert result["deterministic"]["baseline_finish_day"] == pytest.approx(22.0)

    # Invariant 1 made visible. Both numbers are carried so the gap can be read rather
    # than argued about.
    assert result["contingency"]["integrated_p80_total"] is not None
    assert result["contingency"]["additive_p80_total"] is not None
    assert result["delay_days"] is not None
    assert result["schedule_driven_cost"] is not None

    # The percentile grid and the histogram are the persisted shape, per the storage
    # decision: no per-iteration arrays are kept.
    assert len(result["total_cost"]["s_curve"]) == 101
    assert len(result["total_cost"]["histogram"]["counts"]) == 50


@pytest.mark.asyncio
async def test_the_reproducibility_record_is_written(client):
    await _pass_gate(client)
    body = (
        await client.post("/simulations", json={"schedule_version_id": 1, **FAST})
    ).json()

    assert body["engine_version"]
    assert len(body["inputs_sha256"]) == 64
    manifest = body["result"]["manifest"]
    assert manifest["seed"] == FAST["seed"]
    assert manifest["iterations"] == FAST["iterations"]
    assert manifest["inputs_sha256"] == body["inputs_sha256"]
    assert manifest["chunk_size"] == body["chunk_size"] if "chunk_size" in body else True


@pytest.mark.asyncio
async def test_identical_inputs_reproduce_the_same_number(client):
    await _pass_gate(client)
    payload = {"schedule_version_id": 1, "base_cost": 1_000_000, **FAST}

    first = (await client.post("/simulations", json=payload)).json()
    second = (await client.post("/simulations", json=payload)).json()

    assert first["id"] != second["id"]
    assert first["inputs_sha256"] == second["inputs_sha256"]
    assert first["result"]["total_cost"]["mean"] == second["result"]["total_cost"]["mean"]


@pytest.mark.asyncio
async def test_a_different_seed_moves_the_answer(client):
    await _pass_gate(client)
    a = (
        await client.post(
            "/simulations", json={"schedule_version_id": 1, "iterations": 400, "seed": 1}
        )
    ).json()
    b = (
        await client.post(
            "/simulations", json={"schedule_version_id": 1, "iterations": 400, "seed": 2}
        )
    ).json()
    assert a["inputs_sha256"] != b["inputs_sha256"]
    assert a["result"]["total_cost"]["mean"] != b["result"]["total_cost"]["mean"]


@pytest.mark.asyncio
async def test_correlation_drivers_reach_the_engine(client):
    async with client._maker() as session:
        session.add(RiskDriver(id=1, name="Ground risk", correlation_default=0.7))
        session.add(RiskDriverLink(risk_id=1, driver_id=1))
        session.add(RiskDriverLink(risk_id=2, driver_id=1))
        await session.commit()

    body = (await client.post("/simulations", json=FAST)).json()
    assert body["status"] == "succeeded", body.get("error")
    correlation = body["result"]["correlation"]
    assert correlation["variables"] >= 2
    # Achieved, not requested. A tagging that cannot be honoured shows up here.
    assert correlation["max_pair_error"] < 0.15


@pytest.mark.asyncio
async def test_runs_are_listed_newest_first_without_their_payloads(client):
    await client.post("/simulations", json={"name": "one", **FAST})
    await client.post("/simulations", json={"name": "two", **FAST})

    rows = (await client.get("/simulations")).json()
    assert [r["name"] for r in rows] == ["two", "one"]
    assert "result" not in rows[0]
    assert "excluded" not in rows[0]


@pytest.mark.asyncio
async def test_a_run_can_be_read_back_whole(client):
    created = (await client.post("/simulations", json=FAST)).json()
    fetched = (await client.get(f"/simulations/{created['id']}")).json()
    assert fetched["result"]["contingency"]["base_cost"] == 0.0
    assert [e["risk_code"] for e in fetched["excluded"]] == ["TEC-DES-0003"]


@pytest.mark.asyncio
async def test_a_missing_run_is_a_404(client):
    assert (await client.get("/simulations/9999")).status_code == 404


@pytest.mark.asyncio
async def test_a_run_cannot_be_deleted(client):
    created = (await client.post("/simulations", json=FAST)).json()
    res = await client.delete(f"/simulations/{created['id']}")
    assert res.status_code == 405


# --------------------------------------------------------------------------------------
# cancelling a queued run
# --------------------------------------------------------------------------------------
#
# ``simulation_eager`` (on for this whole file) means a run posted through the normal
# route never sits in ``queued`` long enough to cancel — it is terminal by the time the
# response comes back. So these seed a queued row directly, the same way the gate tests
# seed a ``DcmaRun`` directly: exercising the row a dead worker would actually leave
# behind, not the route that creates it.


async def _seed_queued_run(client, **overrides) -> int:
    fields = {"scope_id": 1, "name": "Stuck run", "status": "queued", **overrides}
    async with client._maker() as session:
        run = SimulationRun(**fields)
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run.id


@pytest.mark.asyncio
async def test_a_queued_run_can_be_cancelled(client):
    run_id = await _seed_queued_run(client)

    res = await client.post(
        f"/simulations/{run_id}/cancel", headers={"X-Actor": "Sam"}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "cancelled"
    assert body["cancelled_by"] == "Sam"
    assert body["cancelled_at"] is not None
    assert any("Cancelled by Sam" in n for n in body["assembly_notes"])


@pytest.mark.asyncio
async def test_a_cancelled_run_is_still_there_afterward(client):
    """Cancel records a fact; it does not remove the row (invariant 5)."""
    run_id = await _seed_queued_run(client)
    await client.post(f"/simulations/{run_id}/cancel")

    rows = (await client.get("/simulations")).json()
    assert any(r["id"] == run_id and r["status"] == "cancelled" for r in rows)
    assert (await client.get(f"/simulations/{run_id}")).status_code == 200


@pytest.mark.asyncio
async def test_a_cancelled_run_cannot_be_cancelled_again(client):
    run_id = await _seed_queued_run(client)
    await client.post(f"/simulations/{run_id}/cancel")

    res = await client.post(f"/simulations/{run_id}/cancel")
    assert res.status_code == 409
    assert res.json()["error"] == "simulation_run_not_cancellable"


@pytest.mark.asyncio
async def test_a_succeeded_run_cannot_be_cancelled(client):
    created = (await client.post("/simulations", json=FAST)).json()
    assert created["status"] == "succeeded"

    res = await client.post(f"/simulations/{created['id']}/cancel")
    assert res.status_code == 409
    assert res.json()["status"] == "succeeded"


@pytest.mark.asyncio
async def test_a_running_run_cannot_be_cancelled(client):
    run_id = await _seed_queued_run(client, status="running")
    res = await client.post(f"/simulations/{run_id}/cancel")
    assert res.status_code == 409
    assert res.json()["status"] == "running"


@pytest.mark.asyncio
async def test_cancelling_a_missing_run_is_a_404(client):
    res = await client.post("/simulations/9999/cancel")
    assert res.status_code == 404


# --------------------------------------------------------------------------------------
# the calendar anchor
# --------------------------------------------------------------------------------------
#
# Every day number the engine returns — ``baseline_finish_day``, ``finish_day``, the delay
# series — is an offset from day zero of the parsed network. The run itself has no idea
# what date that is: the anchor lives on the schedule version, and the result would be
# unreadable as a date without it. These pin which date it resolves to and, more
# importantly, that it is the *same* rule the assembly counted from.


async def _set_data_date(client, when) -> None:
    async with client._maker() as session:
        version = await session.get(ScheduleVersion, 1)
        version.data_date = when
        await session.commit()


@pytest.mark.asyncio
async def test_a_cost_only_run_has_no_calendar_anchor(client):
    created = (await client.post("/simulations", json=FAST)).json()
    assert created["schedule_start_date"] is None


@pytest.mark.asyncio
async def test_a_scheduled_run_is_anchored_on_the_data_date(client):
    await _pass_gate(client)
    await _set_data_date(client, datetime(2026, 3, 2, 8, 0))
    created = (
        await client.post("/simulations", json={"schedule_version_id": 1, **FAST})
    ).json()
    assert created["schedule_start_date"] == "2026-03-02"
    fetched = (await client.get(f"/simulations/{created['id']}")).json()
    assert fetched["schedule_start_date"] == "2026-03-02"


@pytest.mark.asyncio
async def test_a_schedule_with_no_data_date_falls_back_to_the_earliest_start(client):
    """A parse that carried no data date still simulated, off the earliest activity
    start. Reading the column alone would return no date for a run that has good ones."""
    await _pass_gate(client)
    async with client._maker() as session:
        first = await session.get(ScheduleActivity, 1)
        first.early_start = datetime(2026, 5, 11, 7, 0)
        await session.commit()
    created = (
        await client.post("/simulations", json={"schedule_version_id": 1, **FAST})
    ).json()
    assert created["schedule_start_date"] == "2026-05-11"


@pytest.mark.asyncio
async def test_the_anchor_matches_what_the_calendars_counted_from(client):
    """The one failure mode worth a test of its own: the run's dates and the run's
    arithmetic drifting onto different origins, which nothing downstream would show."""
    await _pass_gate(client)
    await _set_data_date(client, datetime(2026, 3, 2, 8, 0))
    created = (
        await client.post("/simulations", json={"schedule_version_id": 1, **FAST})
    ).json()
    async with client._maker() as session:
        calendars = await load_calendar_set(session, 1)
    assert created["schedule_start_date"] == calendars.window_start.isoformat()
