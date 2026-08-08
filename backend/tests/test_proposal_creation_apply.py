"""Creation proposals, from pending to a row in the register.

5.1 shipped the ledger with creation deliberately unmaterialised: accepting one raised a
422 rather than inventing a risk code, a scope handoff and a subcategory from a payload no
generator wrote yet. 5.4 writes that payload, so this is the other half.

The properties under test are the ones that would be expensive to get wrong and cheap to
get wrong quietly:

- The scope comes from the *proposal*, never from the payload. A payload that could name a
  project would give an accepted suggestion a way to land a risk somewhere nobody was
  looking.
- The payload whitelist is narrower than ``RiskCreate``. A generator that ships an
  unreviewed probability inside a creation payload gets it accepted as a side effect of
  accepting the risk statement — one click, two decisions, one of them invisible.
- The created row is findable afterwards, through ``created_target_id``. A creation
  proposal whose row cannot be found again is a decision with nothing traceable behind it.
- The audit row carries ``provenance``, so "did the AI decide this?" has an answer that
  does not depend on anyone remembering.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import proposals as proposals_route
from app.api.routes import risks as risks_route
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.db.session import get_db
from app.models.history import RiskHistory
from app.models.proposal import Proposal
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.scope import ScopeNode

pytestmark = pytest.mark.asyncio

PROJECT_ID = 2
PROGRAM_ID = 1

CREATION = {
    "target_type": "risk",
    "target_id": None,
    "field_path": "*",
    "proposed_value": {
        "subcategory_prefix": "ENV-030",
        "title": "Consent lapses before tie-in",
        "description": "Because the consent is valid for ninety days, the tie-in may "
        "slip past that window, which would delay commissioning.",
        "causes": "the consent is valid for ninety days from issue",
        "consequences": "delay to commissioning",
    },
    "rationale": "Section 4.2 sets a ninety-day validity the plan overruns.",
    "evidence_refs": [
        {"kind": "doc_chunk", "ref": "doc_chunk:12", "excerpt": "valid for ninety days"}
    ],
    "generator_model": "test-model",
    "generator_prompt_version": "risk-id/v1",
}


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'c.db'}", future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as session:
        session.add(
            ScopeNode(
                id=PROGRAM_ID, kind="program", name="Water", code="WTR", created_by="test"
            )
        )
        session.add(
            ScopeNode(
                id=PROJECT_ID,
                kind="project",
                parent_id=PROGRAM_ID,
                name="Plant",
                code="PLA",
                is_default=True,
                created_by="test",
            )
        )
        session.add(RbsCategory(id=1, code="ENV", name="Environmental"))
        session.add(RbsSubcategory(id=1, category_id=1, code="030", name="Permitting"))
        await session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(proposals_route.router)
    app.include_router(risks_route.router)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac._maker = Session  # type: ignore[attr-defined]
        yield ac
    await engine.dispose()


async def _raise(client, **overrides) -> dict:
    body = {**CREATION, **overrides}
    response = await client.post(f"/proposals?scope_id={PROJECT_ID}", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _accept(client, proposal_id: int, **payload):
    return await client.post(
        f"/proposals/{proposal_id}/disposition",
        json={"action": "accept", **payload},
        headers={"X-Actor": "Sam"},
    )


class TestAcceptCreatesTheRisk:
    async def test_a_register_row_appears(self, client) -> None:
        proposal = await _raise(client)
        response = await _accept(client, proposal["id"])
        assert response.status_code == 200, response.text

        listing = await client.get(f"/risks?scope_id={PROJECT_ID}")
        rows = listing.json()
        assert len(rows) == 1
        assert rows[0]["title"] == "Consent lapses before tie-in"

    async def test_it_gets_a_real_scoped_risk_code(self, client) -> None:
        """The same allocator ``POST /risks`` uses, so a generated risk and a typed one
        are indistinguishable in the register — which they should be."""
        proposal = await _raise(client)
        await _accept(client, proposal["id"])
        listing = await client.get(f"/risks?scope_id={PROJECT_ID}")
        assert listing.json()[0]["risk_code"] == "WTR-PLA-0001"

    async def test_the_created_row_is_findable_from_the_proposal(self, client) -> None:
        proposal = await _raise(client)
        accepted = (await _accept(client, proposal["id"])).json()
        listing = await client.get(f"/risks?scope_id={PROJECT_ID}")
        assert accepted["created_target_id"] == listing.json()[0]["id"]

    async def test_target_id_stays_null_so_this_still_reads_as_a_creation(
        self, client
    ) -> None:
        """Back-filling ``target_id`` would move the row into the partial unique index's
        scope and destroy the only signal saying this made a row rather than changed one."""
        proposal = await _raise(client)
        accepted = (await _accept(client, proposal["id"])).json()
        assert accepted["target_id"] is None
        assert accepted["status"] == "accepted"

    async def test_the_statement_lands_in_its_three_parts(self, client) -> None:
        """Writing only the assembled sentence would make the first analyst who wants to
        change the effect rewrite the whole thing."""
        proposal = await _raise(client)
        await _accept(client, proposal["id"])
        risk = (await client.get(f"/risks?scope_id={PROJECT_ID}")).json()[0]
        assert risk["causes"] == "the consent is valid for ninety days from issue"
        assert risk["consequences"] == "delay to commissioning"
        assert "ninety days" in risk["description"]

    async def test_it_lands_unassessed(self, client) -> None:
        """Identification says what the risk is. The numbers come from an elicitation with
        the people who own the work."""
        proposal = await _raise(client)
        await _accept(client, proposal["id"])
        risk = (await client.get(f"/risks?scope_id={PROJECT_ID}")).json()[0]
        assert risk["probability"] is None
        assert risk["risk_level"] is None
        assert risk["status"] == "Open"

    async def test_the_taxonomy_prefix_survives(self, client) -> None:
        proposal = await _raise(client)
        await _accept(client, proposal["id"])
        risk = (await client.get(f"/risks?scope_id={PROJECT_ID}")).json()[0]
        assert risk["subcategory_prefix"] == "ENV-030"


class TestAudit:
    async def test_the_history_row_names_the_proposal(self, client) -> None:
        """"Did the AI decide this?" has to have an answer that does not depend on anyone
        remembering."""
        proposal = await _raise(client)
        await _accept(client, proposal["id"])
        async with client._maker() as session:  # type: ignore[attr-defined]
            rows = list(await session.scalars(select(RiskHistory)))
        assert len(rows) == 1
        assert rows[0].action == "created"
        assert rows[0].provenance == f"proposal:{proposal['id']}"
        assert rows[0].actor == "Sam"

    async def test_a_typed_risk_records_no_provenance(self, client) -> None:
        """NULL means human. The distinction is the whole reason the column exists."""
        await client.post(
            f"/risks?scope_id={PROJECT_ID}",
            json={"subcategory_prefix": "ENV-030", "title": "Typed by hand"},
        )
        async with client._maker() as session:  # type: ignore[attr-defined]
            rows = list(await session.scalars(select(RiskHistory)))
        assert [r.provenance for r in rows] == [None]


class TestEditOnCreation:
    async def test_the_reviewers_wording_is_what_lands(self, client) -> None:
        proposal = await _raise(client)
        edited = dict(CREATION["proposed_value"])
        edited["title"] = "Environmental consent expires before tie-in"
        response = await client.post(
            f"/proposals/{proposal['id']}/disposition",
            json={"action": "edit", "applied_value": edited},
            headers={"X-Actor": "Sam"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "edited"
        risk = (await client.get(f"/risks?scope_id={PROJECT_ID}")).json()[0]
        assert risk["title"] == "Environmental consent expires before tie-in"


class TestRefusals:
    async def test_an_unknown_subcategory_is_refused_and_nothing_lands(
        self, client
    ) -> None:
        payload = dict(CREATION["proposed_value"])
        payload["subcategory_prefix"] = "ZZZ-999"
        proposal = await _raise(client, proposed_value=payload)
        response = await _accept(client, proposal["id"])
        assert response.status_code == 422

        listing = await client.get(f"/risks?scope_id={PROJECT_ID}")
        assert listing.json() == []

    async def test_a_refused_creation_stays_pending(self, client) -> None:
        """A proposal marked accepted whose value never landed is worse than no ledger."""
        payload = dict(CREATION["proposed_value"])
        payload["subcategory_prefix"] = "ZZZ-999"
        proposal = await _raise(client, proposed_value=payload)
        await _accept(client, proposal["id"])
        row = (await client.get(f"/proposals/{proposal['id']}")).json()
        assert row["status"] == "pending"
        assert row["created_target_id"] is None

    async def test_a_field_outside_the_whitelist_is_refused(self, client) -> None:
        """``probability`` accepted as a side effect of accepting a risk statement is one
        click and two decisions, one of them invisible."""
        payload = dict(CREATION["proposed_value"])
        payload["probability"] = 5
        proposal = await _raise(client, proposed_value=payload)
        response = await _accept(client, proposal["id"])
        assert response.status_code == 422
        assert "probability" in response.json()["detail"]

    async def test_a_missing_title_is_refused(self, client) -> None:
        payload = dict(CREATION["proposed_value"])
        payload["title"] = "   "
        proposal = await _raise(client, proposed_value=payload)
        response = await _accept(client, proposal["id"])
        assert response.status_code == 422
        assert "title" in response.json()["detail"]

    async def test_a_non_object_payload_is_refused(self, client) -> None:
        proposal = await _raise(client, proposed_value="just a sentence")
        response = await _accept(client, proposal["id"])
        assert response.status_code == 422

    async def test_a_creation_for_an_unknown_target_type_names_what_is_creatable(
        self, client
    ) -> None:
        proposal = await _raise(client, target_type="risk_quant_estimate")
        response = await _accept(client, proposal["id"])
        assert response.status_code == 422
        assert "risk" in response.json()["detail"]

    async def test_a_program_scope_cannot_hold_a_generated_risk(self, client) -> None:
        """Work is authored on projects. A rolled-up risk is a read of a project's risk,
        not a row of its own."""
        response = await client.post(
            f"/proposals?scope_id={PROGRAM_ID}", json=CREATION
        )
        # The route resolves the write scope before the ledger sees it, so this never
        # becomes a proposal at all.
        assert response.status_code == 422


class TestSequenceAllocation:
    async def test_two_accepted_creations_do_not_collide(self, client) -> None:
        first = await _raise(client)
        await _accept(client, first["id"])
        second = await _raise(client)
        await _accept(client, second["id"])
        codes = [r["risk_code"] for r in (await client.get(f"/risks?scope_id={PROJECT_ID}")).json()]
        assert sorted(codes) == ["WTR-PLA-0001", "WTR-PLA-0002"]

    async def test_a_generated_risk_and_a_typed_one_share_one_sequence(
        self, client
    ) -> None:
        await client.post(
            f"/risks?scope_id={PROJECT_ID}",
            json={"subcategory_prefix": "ENV-030", "title": "Typed by hand"},
        )
        proposal = await _raise(client)
        await _accept(client, proposal["id"])
        codes = sorted(
            r["risk_code"]
            for r in (await client.get(f"/risks?scope_id={PROJECT_ID}")).json()
        )
        assert codes == ["WTR-PLA-0001", "WTR-PLA-0002"]


class TestRejection:
    async def test_rejecting_a_creation_writes_nothing_to_the_register(
        self, client
    ) -> None:
        proposal = await _raise(client)
        response = await client.post(
            f"/proposals/{proposal['id']}/disposition",
            json={"action": "reject", "note": "Already covered by the permit schedule."},
        )
        assert response.status_code == 200
        assert (await client.get(f"/risks?scope_id={PROJECT_ID}")).json() == []
        async with client._maker() as session:  # type: ignore[attr-defined]
            stored = await session.get(Proposal, proposal["id"])
            assert stored is not None
            assert stored.status == "rejected"
            assert stored.created_target_id is None
