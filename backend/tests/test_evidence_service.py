"""The evidence service end to end, over real documents, risks and activities.

The properties under test are the ones a generator depends on: retrieval abstains rather
than returning filler, a hit carries enough to be cited *and* to be argued with, a
reference resolves back to readable text long after the fact, and a precedent drawn from
another project says so on its face.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import documents as documents_route
from app.api.routes import evidence as evidence_route
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.db.session import get_db
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.schedule import ScheduleActivity, ScheduleFile, ScheduleVersion
from app.models.scope import ScopeNode

pytestmark = pytest.mark.asyncio

PROJECT = 1
SIBLING = 2
PORTFOLIO = 3

PERMIT_TEXT = (
    "# Consents\n"
    "## Validity\n"
    "The environmental consent is valid for ninety days from the date of issue.\n"
    "Dewatering of the excavation requires a separate discharge licence.\n"
)
UNRELATED_TEXT = (
    "# Welding\n"
    "Radiographic inspection of girth welds shall follow the approved procedure.\n"
)


@pytest_asyncio.fixture
async def client(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'e.db'}", future=True)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as session:
        session.add_all(
            [
                ScopeNode(
                    id=PORTFOLIO, kind="portfolio", name="Capital", created_by="test"
                ),
                ScopeNode(
                    id=PROJECT,
                    kind="project",
                    parent_id=PORTFOLIO,
                    name="Terminal",
                    is_default=True,
                    created_by="test",
                ),
                ScopeNode(
                    id=SIBLING,
                    kind="project",
                    parent_id=PORTFOLIO,
                    name="Depot",
                    created_by="test",
                ),
            ]
        )
        session.add(RbsCategory(id=1, code="ENV", name="Environmental"))
        session.add(RbsSubcategory(id=1, category_id=1, code="030", name="Permitting"))
        await session.flush()

        session.add_all(
            [
                Risk(
                    id=1,
                    scope_id=PROJECT,
                    subcategory_id=1,
                    seq=1,
                    risk_code="TRM-001",
                    title="Girth weld rework",
                    consequences="Radiographic rejection drives rework of the welds.",
                    status="Open",
                ),
                Risk(
                    id=2,
                    scope_id=SIBLING,
                    subcategory_id=1,
                    seq=1,
                    risk_code="DEP-001",
                    title="Dewatering discharge licence delay",
                    consequences="The excavation cannot be dewatered until issued.",
                    status="Open",
                ),
            ]
        )

        session.add(
            ScheduleFile(
                id=1,
                scope_id=PROJECT,
                filename="m.xer",
                suffix=".xer",
                content=b"",
                content_sha256="a" * 64,
                size_bytes=0,
            )
        )
        session.add(
            ScheduleVersion(
                id=1,
                file_id=1,
                source_project_id="P1",
                project_name="Terminal",
                source_format="xer",
                parser_version="1.0",
            )
        )
        await session.flush()
        for index, (code, name) in enumerate(
            [
                ("CON-3010", "Site establishment"),
                ("CON-3020", "Excavation and dewatering"),
                ("CON-3040", "Girth welding and NDT"),
            ],
            start=1,
        ):
            session.add(
                ScheduleActivity(
                    id=index,
                    version_id=1,
                    source_id=f"T{index}",
                    code=code,
                    name=name,
                    calendar_source_id="CAL",
                    type="task",
                    status="not_started",
                    duration_calendar_id="CAL",
                )
            )
        await session.commit()

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(evidence_route.router)
    app.include_router(documents_route.router)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac._maker = Session  # type: ignore[attr-defined]
        yield ac
    await engine.dispose()


async def _paste(client, title: str, text: str, scope_id: int = PROJECT):
    return await client.post(
        "/documents/paste",
        params={"scope_id": scope_id},
        json={"title": title, "text": text},
    )


async def _search(client, q: str, **params):
    return (await client.get("/evidence/search", params={"q": q, **params})).json()


class TestDocumentRetrieval:
    async def test_a_matching_chunk_comes_back_citable(self, client) -> None:
        await _paste(client, "Permit", PERMIT_TEXT)
        body = await _search(client, "dewatering discharge licence", source=["doc_chunk"])
        assert body["abstained"] is False
        top = body["results"][0]
        assert "discharge licence" in top["excerpt"]
        assert top["ref"].startswith("doc_chunk:")
        assert top["source_label"] == "Permit"
        assert top["locator"] is not None

    async def test_a_hit_says_which_terms_caused_it(self, client) -> None:
        """A citation nobody can interrogate is a citation nobody should accept."""
        await _paste(client, "Permit", PERMIT_TEXT)
        body = await _search(client, "dewatering excavation", source=["doc_chunk"])
        assert body["results"][0]["matched"]
        assert body["results"][0]["idf_share"] > 0

    async def test_the_section_path_is_searched_and_returned(self, client) -> None:
        await _paste(client, "Permit", PERMIT_TEXT)
        body = await _search(client, "consents validity", source=["doc_chunk"])
        assert any(r["section"] for r in body["results"])

    async def test_a_withdrawn_document_stops_being_retrieved(self, client) -> None:
        created = (await _paste(client, "Permit", PERMIT_TEXT)).json()["document"]
        assert (await _search(client, "dewatering", source=["doc_chunk"]))["results"]
        await client.post(f"/documents/{created['id']}/withdraw", json={"reason": "rev C"})
        assert (await _search(client, "dewatering", source=["doc_chunk"]))["abstained"]

    async def test_a_withdrawn_document_is_still_resolvable(self, client) -> None:
        """Withdrawal is what lets a citation made months ago keep opening."""
        created = (await _paste(client, "Permit", PERMIT_TEXT)).json()["document"]
        ref = (await _search(client, "dewatering", source=["doc_chunk"]))["results"][0]["ref"]
        await client.post(f"/documents/{created['id']}/withdraw", json={})
        resolved = (await client.get("/evidence/resolve", params={"ref": ref})).json()
        assert "discharge licence" in resolved["excerpt"]


class TestAbstention:
    async def test_nothing_relevant_abstains_rather_than_returning_filler(
        self, client
    ) -> None:
        await _paste(client, "Permit", PERMIT_TEXT)
        body = await _search(client, "hydrostatic cathodic protection", source=["doc_chunk"])
        assert body["abstained"] is True
        assert body["results"] == []
        assert body["reason"]

    async def test_a_query_of_only_stopwords_abstains_with_a_reason(self, client) -> None:
        body = await _search(client, "the and of it")
        assert body["abstained"] is True
        assert "stopwords" in body["reason"]

    async def test_an_empty_corpus_abstains(self, client) -> None:
        body = await _search(client, "dewatering excavation", source=["doc_chunk"])
        assert body["abstained"] is True

    async def test_corpus_sizes_are_on_the_face_of_the_result(self, client) -> None:
        """'No evidence' over forty chunks and over four thousand are different claims."""
        await _paste(client, "Permit", PERMIT_TEXT)
        body = await _search(client, "cathodic protection", source=["doc_chunk"])
        assert body["abstained"] is True
        assert body["corpus_sizes"]["doc_chunk"] > 0
        assert body["truncated"] == []


class TestReferenceClass:
    async def test_a_precedent_from_a_sibling_project_is_found(self, client) -> None:
        """A reference class limited to this project is empty exactly when it matters."""
        body = await _search(client, "dewatering discharge licence", source=["risk"])
        assert body["results"]
        assert body["results"][0]["source_label"].startswith("DEP-001")

    async def test_it_says_the_precedent_came_from_elsewhere(self, client) -> None:
        body = await _search(
            client, "dewatering discharge licence", source=["risk"], scope_id=PROJECT
        )
        assert body["results"][0]["from_other_scope"] is True

    async def test_the_sibling_can_be_excluded(self, client) -> None:
        body = await _search(
            client,
            "dewatering discharge licence",
            source=["risk"],
            scope_id=PROJECT,
            history_across_scopes=False,
        )
        assert body["abstained"] is True

    async def test_a_risk_in_this_project_is_not_flagged(self, client) -> None:
        body = await _search(
            client, "girth weld radiographic rework", source=["risk"], scope_id=PROJECT
        )
        assert body["results"][0]["from_other_scope"] is False


class TestScheduleSource:
    async def test_activities_are_searched_relationally(self, client) -> None:
        body = await _search(client, "excavation dewatering", source=["activity"])
        assert body["results"][0]["source_label"] == "CON-3020"
        assert body["results"][0]["locator"]["source_id"] == "T2"

    async def test_a_schedule_file_is_not_a_document_source(self, client) -> None:
        listed = (await client.get("/evidence/sources")).json()
        assert "schedule_file" in listed["not_a_document_source"]
        assert "cost_model" in listed["not_built"]


class TestMixedSources:
    async def test_every_source_is_searched_by_default(self, client) -> None:
        await _paste(client, "Permit", PERMIT_TEXT)
        body = await _search(client, "dewatering excavation licence", limit=20)
        assert set(body["searched"]) == {"doc_chunk", "risk", "activity"}
        assert {r["kind"] for r in body["results"]} >= {"doc_chunk", "activity"}

    async def test_the_limit_applies_across_sources(self, client) -> None:
        await _paste(client, "Permit", PERMIT_TEXT)
        body = await _search(client, "dewatering excavation licence", limit=2)
        assert len(body["results"]) == 2

    async def test_an_unknown_source_is_ignored_rather_than_erroring(self, client) -> None:
        await _paste(client, "Permit", PERMIT_TEXT)
        body = await _search(client, "dewatering", source=["doc_chunk", "cost_model"])
        assert body["searched"] == ["doc_chunk"]

    async def test_scope_filtering_narrows_the_corpus(self, client) -> None:
        await _paste(client, "Permit", PERMIT_TEXT, scope_id=PROJECT)
        await _paste(client, "Welding", UNRELATED_TEXT, scope_id=SIBLING)
        narrow = await _search(
            client, "radiographic girth welds", source=["doc_chunk"], scope_id=PROJECT
        )
        wide = await _search(
            client, "radiographic girth welds", source=["doc_chunk"], scope_id=PORTFOLIO
        )
        assert narrow["abstained"] is True
        assert wide["abstained"] is False


class TestResolve:
    async def test_a_risk_reference_resolves(self, client) -> None:
        body = (await client.get("/evidence/resolve", params={"ref": "risk:2"})).json()
        assert body["source_label"] == "DEP-001"
        assert "Dewatering" in body["excerpt"]

    async def test_an_activity_reference_resolves(self, client) -> None:
        body = (await client.get("/evidence/resolve", params={"ref": "activity:2"})).json()
        assert "CON-3020" in body["excerpt"]

    async def test_a_malformed_reference_is_a_404_that_says_why(self, client) -> None:
        response = await client.get("/evidence/resolve", params={"ref": "nonsense"})
        assert response.status_code == 404
        assert response.json()["error"] == "evidence_ref_unresolvable"

    async def test_a_reference_to_a_missing_row_is_a_404(self, client) -> None:
        assert (
            await client.get("/evidence/resolve", params={"ref": "risk:9999"})
        ).status_code == 404

    async def test_a_non_numeric_id_is_a_404(self, client) -> None:
        assert (
            await client.get("/evidence/resolve", params={"ref": "risk:abc"})
        ).status_code == 404


class TestLedgerContract:
    async def test_a_result_converts_straight_into_an_evidence_ref(self, client) -> None:
        """A generator must never compose a reference by hand — that is how a citation
        gets written for something that was never found."""
        from app.services import evidence as service

        await _paste(client, "Permit", PERMIT_TEXT)
        async with client._maker() as session:  # type: ignore[attr-defined]
            found = await service.search(session, query="dewatering discharge licence")
        ref = found.results[0].as_ref()
        assert set(ref) == {"kind", "ref", "excerpt"}
        assert all(isinstance(v, str) and v for v in ref.values())
