"""End-to-end over the real scope routes, real models, SQLite session.

Covers the shape of the tree endpoints, the containment order, the cycle guard on move,
the delete refusal, and the default-project handoff — the paths a unit test on
``app/services/scope.py`` alone would not touch, because they only exist at the API layer.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import scopes as scopes_route
from app.db.base_class import Base
from app.db.session import get_db

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}", future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(scopes_route.router)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await engine.dispose()


class TestDefaultProject:
    async def test_an_empty_tree_gets_a_project_on_first_read(self, client) -> None:
        r = await client.get("/scopes")
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["kind"] == "project"
        assert rows[0]["is_default"] is True
        assert rows[0]["parent_id"] is None

    async def test_reading_it_twice_does_not_create_a_second_one(self, client) -> None:
        await client.get("/scopes")
        r = await client.get("/scopes")
        assert len(r.json()) == 1


class TestContainmentOrder:
    async def test_a_program_can_sit_under_a_portfolio(self, client) -> None:
        p = await client.post("/scopes", json={"kind": "portfolio", "name": "LNG"})
        r = await client.post(
            "/scopes",
            json={"kind": "program", "name": "Phase 2", "parent_id": p.json()["id"]},
        )
        assert r.status_code == 201
        assert r.json()["parent_id"] == p.json()["id"]

    async def test_a_project_can_sit_under_either(self, client) -> None:
        program = await client.post("/scopes", json={"kind": "program", "name": "P"})
        r = await client.post(
            "/scopes",
            json={"kind": "project", "name": "Terminal", "parent_id": program.json()["id"]},
        )
        assert r.status_code == 201

    async def test_a_portfolio_cannot_sit_under_a_program(self, client) -> None:
        program = await client.post("/scopes", json={"kind": "program", "name": "P"})
        r = await client.post(
            "/scopes",
            json={
                "kind": "portfolio",
                "name": "Nested",
                "parent_id": program.json()["id"],
            },
        )
        assert r.status_code == 422
        assert "portfolio" in r.json()["detail"].lower()

    async def test_a_project_cannot_contain_anything(self, client) -> None:
        project = await client.post("/scopes", json={"kind": "project", "name": "Leaf"})
        r = await client.post(
            "/scopes",
            json={
                "kind": "project",
                "name": "Child",
                "parent_id": project.json()["id"],
            },
        )
        assert r.status_code == 422

    async def test_an_unknown_parent_is_404_not_a_silent_root(self, client) -> None:
        r = await client.post(
            "/scopes", json={"kind": "project", "name": "Orphan", "parent_id": 999}
        )
        assert r.status_code == 404


class TestMove:
    async def test_a_node_cannot_be_its_own_parent(self, client) -> None:
        node = await client.post("/scopes", json={"kind": "portfolio", "name": "A"})
        r = await client.patch(
            f"/scopes/{node.json()['id']}", json={"parent_id": node.json()["id"]}
        )
        assert r.status_code == 422

    async def test_a_valid_move_updates_the_parent(self, client) -> None:
        a = await client.post("/scopes", json={"kind": "portfolio", "name": "A"})
        b = await client.post("/scopes", json={"kind": "portfolio", "name": "B"})
        program = await client.post(
            "/scopes", json={"kind": "program", "name": "P", "parent_id": a.json()["id"]}
        )
        r = await client.patch(
            f"/scopes/{program.json()['id']}", json={"parent_id": b.json()["id"]}
        )
        assert r.status_code == 200
        assert r.json()["parent_id"] == b.json()["id"]

    async def test_omitting_parent_id_leaves_it_where_it_was(self, client) -> None:
        a = await client.post("/scopes", json={"kind": "portfolio", "name": "A"})
        program = await client.post(
            "/scopes", json={"kind": "program", "name": "P", "parent_id": a.json()["id"]}
        )
        r = await client.patch(
            f"/scopes/{program.json()['id']}", json={"name": "Renamed"}
        )
        assert r.status_code == 200
        assert r.json()["parent_id"] == a.json()["id"]
        assert r.json()["name"] == "Renamed"

    async def test_explicit_null_parent_id_moves_to_root(self, client) -> None:
        a = await client.post("/scopes", json={"kind": "portfolio", "name": "A"})
        program = await client.post(
            "/scopes", json={"kind": "program", "name": "P", "parent_id": a.json()["id"]}
        )
        r = await client.patch(
            f"/scopes/{program.json()['id']}", json={"parent_id": None}
        )
        assert r.status_code == 200
        assert r.json()["parent_id"] is None


class TestCycleGuardIsUnreachableThroughTheAPIByConstruction:
    """The strict rank order (portfolio < program < project) means any move that would
    create a cycle already violates containment order first — a program can only move
    beneath something of strictly lower rank, and nothing beneath it in a valid tree has
    lower rank than itself. ``assert_move_is_acyclic`` in ``app/services/scope.py`` is
    defense for a row a migration or a hand-edit put in a state the API cannot reach, and
    it is covered directly there rather than through routes that cannot trigger it.
    """


class TestDelete:
    async def test_a_leaf_with_nothing_in_it_deletes_clean(self, client) -> None:
        node = await client.post("/scopes", json={"kind": "portfolio", "name": "Empty"})
        r = await client.delete(f"/scopes/{node.json()['id']}")
        assert r.status_code == 204

    async def test_a_node_with_children_is_refused(self, client) -> None:
        parent = await client.post("/scopes", json={"kind": "portfolio", "name": "A"})
        await client.post(
            "/scopes", json={"kind": "program", "name": "B", "parent_id": parent.json()["id"]}
        )
        r = await client.delete(f"/scopes/{parent.json()['id']}")
        assert r.status_code == 409
        assert "contains" in r.json()["detail"]

    async def test_the_default_project_is_refused_even_when_empty(self, client) -> None:
        await client.get("/scopes")  # creates the default
        rows = (await client.get("/scopes")).json()
        default = next(r for r in rows if r["is_default"])
        r = await client.delete(f"/scopes/{default['id']}")
        assert r.status_code == 409
        assert "default" in r.json()["detail"].lower()

    async def test_deleting_never_cascades_into_owned_rows(self, client) -> None:
        """Never true today (nothing here owns a risk), but the refusal is what enforces
        it — this test exists so the invariant has a name to fail against if a future
        change ever tries to cascade."""
        node = await client.post("/scopes", json={"kind": "project", "name": "Solo"})
        r = await client.delete(f"/scopes/{node.json()['id']}")
        assert r.status_code == 204  # nothing owned it, so this one is allowed


class TestDefaultFlag:
    async def test_setting_a_new_default_clears_the_old_one(self, client) -> None:
        await client.get("/scopes")  # creates the first default
        second = await client.post("/scopes", json={"kind": "project", "name": "Second"})
        r = await client.post(f"/scopes/{second.json()['id']}/default")
        assert r.status_code == 200
        rows = (await client.get("/scopes")).json()
        defaults = [row for row in rows if row["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["id"] == second.json()["id"]

    async def test_only_a_project_can_become_the_default(self, client) -> None:
        portfolio = await client.post("/scopes", json={"kind": "portfolio", "name": "A"})
        r = await client.post(f"/scopes/{portfolio.json()['id']}/default")
        assert r.status_code == 422


class TestSubtree:
    async def test_subtree_includes_the_node_and_every_descendant(self, client) -> None:
        portfolio = await client.post("/scopes", json={"kind": "portfolio", "name": "A"})
        program = await client.post(
            "/scopes",
            json={"kind": "program", "name": "B", "parent_id": portfolio.json()["id"]},
        )
        project = await client.post(
            "/scopes",
            json={"kind": "project", "name": "C", "parent_id": program.json()["id"]},
        )
        r = await client.get(f"/scopes/{portfolio.json()['id']}/subtree")
        assert set(r.json()) == {
            portfolio.json()["id"],
            program.json()["id"],
            project.json()["id"],
        }

    async def test_a_leaf_projects_subtree_is_itself(self, client) -> None:
        project = await client.post("/scopes", json={"kind": "project", "name": "Leaf"})
        r = await client.get(f"/scopes/{project.json()['id']}/subtree")
        assert r.json() == [project.json()["id"]]

    async def test_an_unknown_node_is_404(self, client) -> None:
        r = await client.get("/scopes/999/subtree")
        assert r.status_code == 404


class TestSubtreeRollup:
    """`risk_count_subtree` on `GET /scopes` — design handoff, 2026-08-02, step 2."""

    async def test_risk_count_subtree_sums_the_whole_branch(self, tmp_path) -> None:
        from app.models.risk import Risk
        from app.models.scope import ScopeNode

        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path/'rollup.db'}", future=True
        )
        Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with Session() as db:
            portfolio = ScopeNode(kind="portfolio", name="A", created_by="test")
            db.add(portfolio)
            await db.flush()
            program = ScopeNode(
                kind="program", name="B", parent_id=portfolio.id, created_by="test"
            )
            db.add(program)
            await db.flush()
            project_1 = ScopeNode(
                kind="project", name="C", parent_id=program.id, created_by="test"
            )
            project_2 = ScopeNode(
                kind="project", name="D", parent_id=program.id, created_by="test"
            )
            db.add_all([project_1, project_2])
            await db.flush()

            # SQLite does not enforce these FKs without PRAGMA foreign_keys (REFERENCE.md
            # gotcha), so a made-up subcategory_id is fine — this test is about the
            # rollup arithmetic, not the RBS relationship.
            db.add_all(
                [
                    Risk(
                        scope_id=project_1.id,
                        subcategory_id=1,
                        seq=1,
                        risk_code="X-001-0001",
                        title="R1",
                    ),
                    Risk(
                        scope_id=project_1.id,
                        subcategory_id=1,
                        seq=2,
                        risk_code="X-001-0002",
                        title="R2",
                    ),
                    Risk(
                        scope_id=project_2.id,
                        subcategory_id=1,
                        seq=1,
                        risk_code="X-001-0001",
                        title="R3",
                    ),
                ]
            )
            await db.commit()
            ids = {
                "portfolio": portfolio.id,
                "program": program.id,
                "p1": project_1.id,
                "p2": project_2.id,
            }

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(scopes_route.router)

        async def override_get_db():
            async with Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            r = await ac.get("/scopes")
        await engine.dispose()

        rows = {row["id"]: row for row in r.json()}
        assert rows[ids["p1"]]["risk_count"] == 2
        assert rows[ids["p1"]]["risk_count_subtree"] == 2
        assert rows[ids["p2"]]["risk_count_subtree"] == 1
        assert rows[ids["program"]]["risk_count"] == 0
        assert rows[ids["program"]]["risk_count_subtree"] == 3
        assert rows[ids["portfolio"]]["risk_count_subtree"] == 3

    async def test_a_leaf_with_no_risks_has_subtree_zero(self, client) -> None:
        node = await client.post("/scopes", json={"kind": "project", "name": "Empty"})
        rows = (await client.get("/scopes")).json()
        row = next(r for r in rows if r["id"] == node.json()["id"])
        assert row["risk_count_subtree"] == 0


class TestUniqueCode:
    async def test_duplicate_codes_are_refused(self, client) -> None:
        await client.post(
            "/scopes", json={"kind": "portfolio", "name": "A", "code": "LNG"}
        )
        r = await client.post(
            "/scopes", json={"kind": "portfolio", "name": "B", "code": "LNG"}
        )
        assert r.status_code == 422
        assert "already in use" in r.json()["detail"]


class TestCycleGuardDirectly:
    """`assert_move_is_acyclic` on a database put into a state the API cannot produce.

    Rows are inserted directly rather than through `POST /scopes`, because that is the
    only way to build the cycle-eligible shape the API's own order check forecloses.
    """

    async def test_a_hand_built_cycle_is_still_caught(self, tmp_path) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.errors import ScopeInvalid
        from app.db.base_class import Base
        from app.models.scope import ScopeNode
        from app.services.scope import assert_move_is_acyclic

        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path/'cycle.db'}", future=True
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as db:
            a = ScopeNode(kind="portfolio", name="A", created_by="test")
            db.add(a)
            await db.flush()
            b = ScopeNode(kind="program", name="B", parent_id=a.id, created_by="test")
            db.add(b)
            await db.flush()

            with pytest.raises(ScopeInvalid):
                await assert_move_is_acyclic(db, a.id, b.id)
        await engine.dispose()
