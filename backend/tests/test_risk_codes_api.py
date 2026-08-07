"""The register's identifier, and raising a risk with its treatment in one request.

Three things are under test and they are related. The code became
``<program>-<project>-<sequence>``, which is what makes two registers in a program report
tell themselves apart. Because the code no longer carries the RBS, the taxonomy has to be
returned explicitly on every read or the register loses the ability to show it — and, for
the same reason, a miscategorised risk can now be recategorised in place instead of being
deleted and re-raised. And a risk may now be created with its mitigation actions in the
same request, in one transaction.

The harness mirrors ``test_scoped_reads.py``: real routers, real models, SQLite. No
``matrix_config`` row is seeded on purpose — ``get_active_config`` falls back to the
default 5x5 scheme, which is the state a fresh install scores against.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import mitigations as mitigations_route
from app.api.routes import risks as risks_route
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.db.session import get_db
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.scope import ScopeNode

pytestmark = pytest.mark.asyncio

PORTFOLIO = 1
PROGRAM = 2
PLANT_A = 3  # under the program, with an explicit code
PLANT_B = 4  # under the program, no code — abbreviated from its name
DEPOT = 5  # hangs off the portfolio directly, no program above it
LONE = 6  # no parent at all


async def _seed(session: AsyncSession) -> None:
    session.add_all(
        [
            ScopeNode(
                id=PORTFOLIO,
                kind="portfolio",
                name="Capital Delivery",
                code="CAP",
                created_by="test",
            ),
            ScopeNode(
                id=PROGRAM,
                kind="program",
                parent_id=PORTFOLIO,
                name="Water Program",
                code="WTR",
                created_by="test",
            ),
            ScopeNode(
                id=PLANT_A,
                kind="project",
                parent_id=PROGRAM,
                name="Plant A",
                code="PLA",
                is_default=True,
                created_by="test",
            ),
            ScopeNode(
                id=PLANT_B,
                kind="project",
                parent_id=PROGRAM,
                name="Plant B",
                created_by="test",
            ),
            ScopeNode(
                id=DEPOT, kind="project", parent_id=PORTFOLIO, name="Depot", created_by="test"
            ),
            ScopeNode(
                id=LONE, kind="project", name="Standalone", code="SOLO", created_by="test"
            ),
        ]
    )
    session.add_all(
        [
            RbsCategory(id=1, code="ENV", name="Environmental"),
            RbsCategory(id=2, code="CON", name="Construction"),
        ]
    )
    session.add_all(
        [
            RbsSubcategory(id=1, category_id=1, code="030", name="Permitting"),
            RbsSubcategory(id=2, category_id=2, code="010", name="Productivity"),
        ]
    )
    await session.commit()


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'codes.db'}", future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as session:
        await _seed(session)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(risks_route.router)
    app.include_router(mitigations_route.router)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


async def _create(client, scope_id: int | None = None, **body):
    payload = {"subcategory_prefix": "ENV-030", "title": "A risk", **body}
    url = "/risks" if scope_id is None else f"/risks?scope_id={scope_id}"
    return await client.post(url, json=payload, headers={"X-Actor": "Sam"})


class TestCodeShape:
    async def test_a_project_under_a_program_gets_all_three_segments(self, client) -> None:
        body = (await _create(client, PLANT_A)).json()
        assert body["risk_code"] == "WTR-PLA-0001"

    async def test_a_scope_without_a_code_is_abbreviated_from_its_name(self, client) -> None:
        body = (await _create(client, PLANT_B)).json()
        assert body["risk_code"] == "WTR-PB-0001"

    async def test_a_portfolio_stands_in_when_there_is_no_program(self, client) -> None:
        body = (await _create(client, DEPOT)).json()
        assert body["risk_code"] == "CAP-DEPO-0001"

    async def test_a_project_with_no_parent_gets_two_segments(self, client) -> None:
        """Inventing a program above a standalone project would be ceremony."""
        body = (await _create(client, LONE)).json()
        assert body["risk_code"] == "SOLO-0001"

    async def test_the_taxonomy_is_no_longer_in_the_code(self, client) -> None:
        body = (await _create(client, PLANT_A, subcategory_prefix="CON-010")).json()
        assert "CON" not in body["risk_code"]
        assert body["subcategory_prefix"] == "CON-010"


class TestSequence:
    async def test_the_sequence_runs_across_subcategories_within_a_project(
        self, client
    ) -> None:
        first = (await _create(client, PLANT_A)).json()
        second = (await _create(client, PLANT_A, subcategory_prefix="CON-010")).json()
        third = (await _create(client, PLANT_A)).json()
        assert [r["risk_code"] for r in (first, second, third)] == [
            "WTR-PLA-0001",
            "WTR-PLA-0002",
            "WTR-PLA-0003",
        ]

    async def test_every_project_starts_at_one(self, client) -> None:
        a = (await _create(client, PLANT_A)).json()
        b = (await _create(client, PLANT_B)).json()
        assert a["seq"] == b["seq"] == 1
        assert a["risk_code"] != b["risk_code"]

    async def test_a_deleted_risk_does_not_hand_its_number_to_the_next_one(
        self, client
    ) -> None:
        """The old number is in the audit trail and in whatever report went out."""
        first = (await _create(client, PLANT_A)).json()
        assert (await client.delete(f"/risks/{first['id']}")).status_code == 204
        second = (await _create(client, PLANT_A)).json()
        assert second["risk_code"] == "WTR-PLA-0002"

    async def test_the_default_project_is_used_when_no_scope_is_named(self, client) -> None:
        body = (await _create(client)).json()
        assert body["scope_id"] == PLANT_A
        assert body["risk_code"] == "WTR-PLA-0001"


class TestNestedActions:
    async def test_actions_are_created_with_the_risk(self, client) -> None:
        created = (
            await _create(
                client,
                PLANT_A,
                actions=[
                    {"action": "Pre-order long-lead valves", "owner": "Procurement"},
                    {"action": "Escalate permit to the regulator", "budget": 12000},
                ],
            )
        ).json()
        rows = (await client.get(f"/risks/{created['id']}/actions")).json()
        assert [r["action"] for r in rows] == [
            "Pre-order long-lead valves",
            "Escalate permit to the regulator",
        ]
        assert [r["sort_order"] for r in rows] == [0, 1]
        assert rows[0]["owner"] == "Procurement"
        assert rows[1]["budget"] == 12000

    async def test_no_actions_is_still_the_ordinary_case(self, client) -> None:
        created = (await _create(client, PLANT_A)).json()
        assert (await client.get(f"/risks/{created['id']}/actions")).json() == []

    async def test_each_action_gets_its_own_history_entry(self, client) -> None:
        """Indistinguishable from one added an hour later, because it is the same event."""
        created = (
            await _create(client, PLANT_A, actions=[{"action": "Do the thing"}])
        ).json()
        history = (await client.get(f"/risks/{created['id']}/history")).json()
        actions = [h["action"] for h in history]
        assert "created" in actions
        assert actions.count("mitigation added") == 1

    async def test_a_blank_action_is_refused_and_nothing_is_written(self, client) -> None:
        response = await _create(client, PLANT_A, actions=[{"action": "  "}])
        assert response.status_code == 422
        assert (await client.get("/risks")).json() == []

    async def test_actions_created_this_way_are_editable_like_any_other(
        self, client
    ) -> None:
        created = (
            await _create(client, PLANT_A, actions=[{"action": "Draft"}])
        ).json()
        action_id = (await client.get(f"/risks/{created['id']}/actions")).json()[0]["id"]
        patched = await client.patch(
            f"/risks/{created['id']}/actions/{action_id}",
            json={"action": "Redrafted", "status": "In progress"},
        )
        assert patched.json()["action"] == "Redrafted"


class TestRecategorisation:
    async def test_the_subcategory_can_be_changed_without_renumbering(self, client) -> None:
        created = (await _create(client, PLANT_A)).json()
        patched = (
            await client.patch(
                f"/risks/{created['id']}", json={"subcategory_prefix": "CON-010"}
            )
        ).json()
        assert patched["subcategory_prefix"] == "CON-010"
        assert patched["risk_code"] == created["risk_code"]

    async def test_the_change_is_audited(self, client) -> None:
        created = (await _create(client, PLANT_A)).json()
        await client.patch(
            f"/risks/{created['id']}",
            json={"subcategory_prefix": "CON-010"},
            headers={"X-Actor": "Sam"},
        )
        history = (await client.get(f"/risks/{created['id']}/history")).json()
        latest = history[0]
        assert latest["action"] == "updated"
        assert {"field": "subcategory", "old": "ENV-030", "new": "CON-010"} in [
            {"field": c["field"], "old": c["old"], "new": c["new"]}
            for c in latest["changes"]
        ]

    async def test_recategorising_to_the_same_value_writes_no_entry(self, client) -> None:
        created = (await _create(client, PLANT_A)).json()
        before = len((await client.get(f"/risks/{created['id']}/history")).json())
        await client.patch(
            f"/risks/{created['id']}", json={"subcategory_prefix": "ENV-030"}
        )
        after = len((await client.get(f"/risks/{created['id']}/history")).json())
        assert after == before

    async def test_an_unknown_subcategory_is_a_404(self, client) -> None:
        created = (await _create(client, PLANT_A)).json()
        response = await client.patch(
            f"/risks/{created['id']}", json={"subcategory_prefix": "ZZZ-999"}
        )
        assert response.status_code == 404


class TestReads:
    async def test_the_register_carries_the_taxonomy_and_the_owning_scope(
        self, client
    ) -> None:
        await _create(client, PLANT_A)
        row = (await client.get("/risks")).json()[0]
        assert row["subcategory_prefix"] == "ENV-030"
        assert row["scope_id"] == PLANT_A
        assert row["seq"] == 1

    async def test_a_rollup_sorts_into_project_blocks(self, client) -> None:
        """Shared prefix plus zero-padded sequence does the grouping for free."""
        await _create(client, PLANT_B)
        await _create(client, PLANT_A)
        await _create(client, PLANT_A)
        codes = [r["risk_code"] for r in (await client.get(f"/risks?scope_id={PROGRAM}")).json()]
        assert codes == ["WTR-PB-0001", "WTR-PLA-0001", "WTR-PLA-0002"]

    async def test_a_single_risk_read_carries_the_same_fields(self, client) -> None:
        created = (await _create(client, PLANT_A)).json()
        row = (await client.get(f"/risks/{created['id']}")).json()
        assert row["subcategory_prefix"] == "ENV-030"
        assert row["risk_code"] == "WTR-PLA-0001"
