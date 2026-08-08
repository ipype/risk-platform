"""End-to-end over the real proposal routes, real appliers, SQLite session.

What is under test is the ledger's promise: nothing generated reaches a domain table
without a human disposition, no disposition is reversible, and no proposal can be marked
accepted without its value having landed. Each of those is a property of the whole path —
route, service, applier, audit row — so the tests drive the route rather than the service,
and the two that cannot be seen from HTTP (the CHECK constraint, the audit round trip) go
through a second session.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
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
from app.models.risk import Risk
from app.models.scope import ScopeNode

pytestmark = pytest.mark.asyncio

SCOPE_ID = 1
RISK_ID = 1

PROPOSAL = {
    "target_type": "risk",
    "target_id": RISK_ID,
    "field_path": "consequences",
    "proposed_value": "Consent lapses; the tie-in window is missed.",
    "rationale": "Section 4.2 of the permit sets a 90-day validity the plan overruns.",
    "evidence_refs": [
        {"kind": "doc_chunk", "ref": "chunk:88", "excerpt": "valid for ninety days"}
    ],
    "generator_model": "test-model",
    "generator_prompt_version": "v1",
}


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'p.db'}", future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as session:
        session.add(
            ScopeNode(
                id=SCOPE_ID,
                kind="project",
                name="Terminal",
                is_default=True,
                created_by="test",
            )
        )
        session.add(RbsCategory(id=1, code="ENV", name="Environmental"))
        session.add(RbsSubcategory(id=1, category_id=1, code="030", name="Permitting"))
        await session.flush()
        session.add(
            Risk(
                id=RISK_ID,
                scope_id=SCOPE_ID,
                subcategory_id=1,
                seq=1,
                risk_code="TRM-001",
                title="Permit expiry",
                consequences="Unknown",
                status="Open",
                probability=1,
                impact=5,
                risk_level="Medium",
            )
        )
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
        # Handed to the tests so a round trip can be verified on a session that never
        # saw the object: ``session.get`` returns the identity map's copy without
        # issuing a SELECT, which would pass whether or not anything reached the disk.
        ac._maker = Session  # type: ignore[attr-defined]
        yield ac
    await engine.dispose()


async def _raise(client, **overrides) -> dict:
    body = {**PROPOSAL, **overrides}
    response = await client.post("/proposals", json=body)
    assert response.status_code == 201, response.text
    return response.json()


class TestRaising:
    async def test_a_proposal_starts_pending_and_unparked(self, client) -> None:
        row = await _raise(client)
        assert row["status"] == "pending"
        assert row["parked"] is False
        assert row["applied_value"] is None
        assert row["disposed_by"] is None

    async def test_evidence_is_required(self, client) -> None:
        response = await client.post("/proposals", json={**PROPOSAL, "evidence_refs": []})
        assert response.status_code == 422

    async def test_the_database_refuses_evidence_free_rows_too(self, client) -> None:
        """The Pydantic rule is a courtesy. This is the one that holds for a generator.

        Written through the driver rather than the ORM, because the point is that the
        constraint lives in the schema and applies to any writer at all.
        """
        async with client._maker() as session:  # type: ignore[attr-defined]
            with pytest.raises(sa.exc.IntegrityError):
                await session.execute(
                    sa.text(
                        "INSERT INTO proposal (scope_id, target_type, target_id, "
                        "field_path, proposed_value, rationale, evidence_refs, "
                        "generator_model, generator_prompt_version, status, parked) "
                        "VALUES (1, 'risk', 1, 'title', '\"x\"', 'because', '[]', "
                        "'m', 'v1', 'pending', 0)"
                    )
                )
                await session.commit()

    async def test_confidence_may_be_absent(self, client) -> None:
        """NULL means the generator abstained. Zero would be a claim it did not make."""
        assert (await _raise(client))["confidence"] is None

    async def test_a_second_pass_supersedes_the_first(self, client) -> None:
        first = await _raise(client)
        second = await _raise(client, proposed_value="Different wording entirely.")

        prior = (await client.get(f"/proposals/{first['id']}")).json()
        assert prior["status"] == "superseded"
        assert prior["superseded_by"] == second["id"]
        # The superseded row keeps everything it was raised with. Only its claim on the
        # reviewer's attention is gone.
        assert prior["rationale"] == PROPOSAL["rationale"]
        assert prior["evidence_refs"] == PROPOSAL["evidence_refs"]

    async def test_a_different_field_is_not_superseded(self, client) -> None:
        first = await _raise(client)
        await _raise(client, field_path="title", proposed_value="Permit lapse")
        assert (await client.get(f"/proposals/{first['id']}")).json()["status"] == "pending"

    async def test_creation_proposals_do_not_collide(self, client) -> None:
        """Two suggested new risks are two suggestions, not a duplicate of one."""
        a = await _raise(client, target_id=None, field_path="*")
        b = await _raise(client, target_id=None, field_path="*")
        assert (await client.get(f"/proposals/{a['id']}")).json()["status"] == "pending"
        assert (await client.get(f"/proposals/{b['id']}")).json()["status"] == "pending"


class TestAccepting:
    async def test_accept_writes_the_value_onto_the_risk(self, client) -> None:
        row = await _raise(client)
        response = await client.post(
            f"/proposals/{row['id']}/disposition",
            json={"action": "accept"},
            headers={"X-Actor": "Dana"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "accepted"
        assert body["applied_value"] == PROPOSAL["proposed_value"]
        assert body["disposed_by"] == "Dana"
        assert body["disposed_at"] is not None

        risk = (await client.get(f"/risks/{RISK_ID}")).json()
        assert risk["consequences"] == PROPOSAL["proposed_value"]

    async def test_the_audit_row_says_where_the_change_came_from(self, client) -> None:
        row = await _raise(client)
        await client.post(
            f"/proposals/{row['id']}/disposition",
            json={"action": "accept"},
            headers={"X-Actor": "Dana"},
        )
        async with client._maker() as session:  # type: ignore[attr-defined]
            entries = (await session.scalars(sa.select(RiskHistory))).all()
        assert len(entries) == 1
        assert entries[0].provenance == f"proposal:{row['id']}"
        assert entries[0].actor == "Dana"
        assert [c["field"] for c in entries[0].changes] == ["consequences"]

    async def test_edit_records_both_values(self, client) -> None:
        """The delta between proposed and applied is the signal, so both are kept."""
        row = await _raise(client)
        response = await client.post(
            f"/proposals/{row['id']}/disposition",
            json={"action": "edit", "applied_value": "Consent lapses before tie-in."},
        )
        body = response.json()
        assert body["status"] == "edited"
        assert body["proposed_value"] == PROPOSAL["proposed_value"]
        assert body["applied_value"] == "Consent lapses before tie-in."

        risk = (await client.get(f"/risks/{RISK_ID}")).json()
        assert risk["consequences"] == "Consent lapses before tie-in."

    async def test_edit_without_a_value_is_refused(self, client) -> None:
        row = await _raise(client)
        response = await client.post(
            f"/proposals/{row['id']}/disposition", json={"action": "edit"}
        )
        assert response.status_code == 422

    async def test_accepting_a_probability_rescores_the_risk(self, client) -> None:
        """The applier runs the same scoring the PATCH route does, or the two drift.

        The band is derived, never proposed: a generator that could set ``risk_level``
        directly could put a score on the register that its own probability and impact do
        not support, and nothing downstream would catch it.
        """
        before = (await client.get(f"/risks/{RISK_ID}")).json()
        row = await _raise(client, field_path="probability", proposed_value=5)
        await client.post(f"/proposals/{row['id']}/disposition", json={"action": "accept"})
        after = (await client.get(f"/risks/{RISK_ID}")).json()
        assert after["probability"] == 5
        assert after["risk_level"] is not None
        assert after["risk_level"] != before["risk_level"]

    async def test_the_rescore_matches_what_the_patch_route_produces(self, client) -> None:
        """Two write paths onto the same field must not disagree about the band."""
        row = await _raise(client, field_path="probability", proposed_value=5)
        await client.post(f"/proposals/{row['id']}/disposition", json={"action": "accept"})
        via_proposal = (await client.get(f"/risks/{RISK_ID}")).json()

        await client.patch(f"/risks/{RISK_ID}", json={"probability": 1})
        await client.patch(f"/risks/{RISK_ID}", json={"probability": 5})
        via_patch = (await client.get(f"/risks/{RISK_ID}")).json()

        assert via_proposal["risk_level"] == via_patch["risk_level"]
        assert via_proposal["impact"] == via_patch["impact"]


class TestRefusals:
    async def test_a_field_outside_the_whitelist_is_refused(self, client) -> None:
        row = await _raise(client, field_path="risk_level", proposed_value="High")
        response = await client.post(
            f"/proposals/{row['id']}/disposition", json={"action": "accept"}
        )
        assert response.status_code == 422
        assert response.json()["error"] == "proposal_target_invalid"

    async def test_a_refused_apply_leaves_the_proposal_pending(self, client) -> None:
        """The whole point: accepted-but-not-applied must be unreachable."""
        row = await _raise(client, field_path="risk_level", proposed_value="High")
        await client.post(f"/proposals/{row['id']}/disposition", json={"action": "accept"})
        assert (await client.get(f"/proposals/{row['id']}")).json()["status"] == "pending"

    async def test_an_unknown_target_type_is_refused(self, client) -> None:
        row = await _raise(client, target_type="widget")
        response = await client.post(
            f"/proposals/{row['id']}/disposition", json={"action": "accept"}
        )
        assert response.status_code == 422

    async def test_creation_proposals_are_not_materialised_yet(self, client) -> None:
        row = await _raise(client, target_id=None, field_path="*")
        response = await client.post(
            f"/proposals/{row['id']}/disposition", json={"action": "accept"}
        )
        assert response.status_code == 422
        assert (await client.get(f"/proposals/{row['id']}")).json()["status"] == "pending"

    async def test_a_rejection_needs_a_reason(self, client) -> None:
        row = await _raise(client)
        response = await client.post(
            f"/proposals/{row['id']}/disposition", json={"action": "reject"}
        )
        assert response.status_code == 422

    async def test_a_rejection_with_a_reason_is_recorded(self, client) -> None:
        row = await _raise(client)
        response = await client.post(
            f"/proposals/{row['id']}/disposition",
            json={"action": "reject", "note": "Already covered by TRM-004."},
        )
        body = response.json()
        assert body["status"] == "rejected"
        assert body["disposition_note"] == "Already covered by TRM-004."
        assert body["applied_value"] is None
        risk = (await client.get(f"/risks/{RISK_ID}")).json()
        assert risk["consequences"] == "Unknown"


class TestTerminality:
    @pytest.mark.parametrize(
        "first",
        [
            {"action": "accept"},
            {"action": "reject", "note": "no"},
            {"action": "edit", "applied_value": "Something else."},
        ],
    )
    async def test_a_disposed_proposal_cannot_be_disposed_again(
        self, client, first
    ) -> None:
        row = await _raise(client)
        assert (
            await client.post(f"/proposals/{row['id']}/disposition", json=first)
        ).status_code == 200
        again = await client.post(
            f"/proposals/{row['id']}/disposition", json={"action": "accept"}
        )
        assert again.status_code == 409
        assert again.json()["error"] == "proposal_not_disposable"

    async def test_a_superseded_proposal_cannot_be_accepted(self, client) -> None:
        first = await _raise(client)
        await _raise(client, proposed_value="Newer wording.")
        response = await client.post(
            f"/proposals/{first['id']}/disposition", json={"action": "accept"}
        )
        assert response.status_code == 409

    async def test_there_is_no_delete_route(self, client) -> None:
        row = await _raise(client)
        assert (await client.delete(f"/proposals/{row['id']}")).status_code == 405


class TestStaleness:
    async def test_an_accept_over_a_newer_edit_is_refused(self, client) -> None:
        row = await _raise(client, observed_value="Unknown")
        await client.patch(f"/risks/{RISK_ID}", json={"consequences": "Human wrote this."})

        response = await client.post(
            f"/proposals/{row['id']}/disposition", json={"action": "accept"}
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"] == "proposal_stale"
        assert body["observed_value"] == "Unknown"
        assert body["current_value"] == "Human wrote this."

        risk = (await client.get(f"/risks/{RISK_ID}")).json()
        assert risk["consequences"] == "Human wrote this."

    async def test_it_applies_once_the_reviewer_confirms(self, client) -> None:
        row = await _raise(client, observed_value="Unknown")
        await client.patch(f"/risks/{RISK_ID}", json={"consequences": "Human wrote this."})
        response = await client.post(
            f"/proposals/{row['id']}/disposition",
            json={"action": "accept", "confirm_stale": True},
        )
        assert response.status_code == 200
        risk = (await client.get(f"/risks/{RISK_ID}")).json()
        assert risk["consequences"] == PROPOSAL["proposed_value"]

    async def test_no_observed_value_means_no_staleness_check(self, client) -> None:
        """A proposal that made no claim about the prior state cannot conflict with it."""
        row = await _raise(client)
        await client.patch(f"/risks/{RISK_ID}", json={"consequences": "Human wrote this."})
        response = await client.post(
            f"/proposals/{row['id']}/disposition", json={"action": "accept"}
        )
        assert response.status_code == 200


class TestMerge:
    async def test_merge_points_at_the_survivor(self, client) -> None:
        a = await _raise(client, field_path="title", proposed_value="Permit lapse")
        b = await _raise(client)
        response = await client.post(
            f"/proposals/{a['id']}/disposition",
            json={"action": "merge", "merge_into": b["id"], "note": "Same finding."},
        )
        body = response.json()
        assert body["status"] == "superseded"
        assert body["superseded_by"] == b["id"]
        assert body["applied_value"] is None

    async def test_merging_into_nothing_is_refused(self, client) -> None:
        row = await _raise(client)
        assert (
            await client.post(
                f"/proposals/{row['id']}/disposition", json={"action": "merge"}
            )
        ).status_code == 422

    async def test_a_proposal_cannot_merge_into_itself(self, client) -> None:
        row = await _raise(client)
        response = await client.post(
            f"/proposals/{row['id']}/disposition",
            json={"action": "merge", "merge_into": row["id"]},
        )
        assert response.status_code == 422


class TestParking:
    async def test_parking_leaves_it_pending(self, client) -> None:
        row = await _raise(client)
        body = (
            await client.post(f"/proposals/{row['id']}/park", json={"parked": True})
        ).json()
        assert body["parked"] is True
        assert body["status"] == "pending"

    async def test_a_parked_proposal_can_still_be_accepted(self, client) -> None:
        row = await _raise(client)
        await client.post(f"/proposals/{row['id']}/park", json={"parked": True})
        body = (
            await client.post(
                f"/proposals/{row['id']}/disposition", json={"action": "accept"}
            )
        ).json()
        assert body["status"] == "accepted"
        assert body["parked"] is False

    async def test_a_disposed_proposal_cannot_be_parked(self, client) -> None:
        row = await _raise(client)
        await client.post(f"/proposals/{row['id']}/disposition", json={"action": "accept"})
        assert (
            await client.post(f"/proposals/{row['id']}/park", json={"parked": True})
        ).status_code == 409

    async def test_the_inbox_count_separates_parked_from_waiting(self, client) -> None:
        a = await _raise(client)
        await _raise(client, field_path="title", proposed_value="Permit lapse")
        await client.post(f"/proposals/{a['id']}/park", json={"parked": True})
        assert (await client.get("/proposals/inbox/count")).json() == {
            "pending": 1,
            "parked": 1,
        }


class TestListing:
    async def test_newest_first(self, client) -> None:
        await _raise(client, field_path="title", proposed_value="A")
        second = await _raise(client, field_path="owner", proposed_value="B")
        rows = (await client.get("/proposals")).json()
        assert rows[0]["id"] == second["id"]

    async def test_filtering_by_status(self, client) -> None:
        row = await _raise(client)
        await _raise(client, field_path="title", proposed_value="A")
        await client.post(
            f"/proposals/{row['id']}/disposition",
            json={"action": "reject", "note": "duplicate"},
        )
        rows = (await client.get("/proposals", params={"status": "pending"})).json()
        assert [r["field_path"] for r in rows] == ["title"]

    async def test_an_unknown_status_filter_is_refused(self, client) -> None:
        assert (
            await client.get("/proposals", params={"status": "maybe"})
        ).status_code == 422

    async def test_scope_filtering_rolls_up(self, client) -> None:
        """A portfolio reads as everything beneath it, like every other scoped read."""
        async with client._maker() as session:  # type: ignore[attr-defined]
            session.add(
                ScopeNode(id=10, kind="portfolio", name="Capital", created_by="test")
            )
            await session.flush()
            await session.execute(
                sa.update(ScopeNode).where(ScopeNode.id == SCOPE_ID).values(parent_id=10)
            )
            await session.commit()

        await _raise(client)
        assert len((await client.get("/proposals", params={"scope_id": 10})).json()) == 1
        assert len((await client.get("/proposals", params={"scope_id": 1})).json()) == 1


class TestAppendOnly:
    async def test_a_disposition_is_never_rewritten(self, client) -> None:
        """The ledger keeps the decision that was made, not the latest opinion of it."""
        row = await _raise(client)
        await client.post(
            f"/proposals/{row['id']}/disposition",
            json={"action": "reject", "note": "Out of scope."},
        )
        async with client._maker() as session:  # type: ignore[attr-defined]
            stored = (
                await session.scalars(
                    sa.select(Proposal).where(Proposal.id == row["id"])
                )
            ).one()
        assert stored.status == "rejected"
        assert stored.disposition_note == "Out of scope."
        assert stored.applied_value is None
