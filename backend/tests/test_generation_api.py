"""The generation routes, over the real app wiring.

Dispatch runs eagerly here (``generation_eager``), which is the same convenience
``simulation_eager`` provides and for the same reason: a route test that needed Redis and a
worker would be an integration test wearing a unit test's clothes, and the seam it would be
exercising is one function call wide.

The refusals are what this file is really for. A deployment with no ``LLM_PROVIDER`` and a
project with no documents are the two states a first-day install sits in, and both have to
come back as something a person can act on rather than a queued run that will certainly
fail. There is no delete route and no re-run route, and the test that says so is not
pedantry — it is the append-only rule, stated where a future convenience endpoint would
have to break it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import generation as generation_route
from app.api.routes import proposals as proposals_route
from app.core.config import Settings
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.db.session import get_db
from app.models.document import Document, DocumentChunk
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.scope import ScopeNode

pytestmark = pytest.mark.asyncio

SCOPE_ID = 1

# No scripted responses in this file. The routes are wired to the default ``FakeProvider``,
# which derives its answer from the chunk markers in the real prompt — so a route test that
# passes is also evidence the rendered prompt carried a resolvable citation. Scripted
# failure cases belong in ``test_risk_generate.py``, where the provider is injectable.


def _settings(**overrides) -> Settings:
    base = {
        "llm_provider": "fake",
        "llm_model": "fake-model",
        "generation_eager": True,
        "generation_window_chars": 4000,
        "generation_max_windows": 5,
    }
    base.update(overrides)
    return Settings(**base)


@pytest_asyncio.fixture
async def make_client(tmp_path, monkeypatch):
    """Builds a client against a chosen ``Settings``, with documents optional.

    A factory rather than a fixture with a fixed configuration, because half these tests
    are about what happens under a *different* configuration — no provider, eager off —
    and parametrising a single client would put the interesting variable in a decorator
    instead of in the test that depends on it.
    """
    engines: list = []

    async def build(settings: Settings, *, with_documents: bool = True):
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path/f'api{len(engines)}.db'}", future=True
        )
        engines.append(engine)
        Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with Session() as session:
            session.add(
                ScopeNode(
                    id=SCOPE_ID,
                    kind="project",
                    name="North Shore Tunnel",
                    code="NST",
                    is_default=True,
                    created_by="test",
                )
            )
            session.add(RbsCategory(id=1, code="ENV", name="Environmental"))
            session.add(
                RbsSubcategory(id=1, category_id=1, code="030", name="Permitting")
            )
            if with_documents:
                session.add(
                    Document(
                        id=1,
                        scope_id=SCOPE_ID,
                        filename="consent.pdf",
                        suffix=".pdf",
                        sha256="a" * 64,
                        byte_size=10,
                        chunk_count=1,
                        title="Environmental consent",
                    )
                )
                session.add(
                    DocumentChunk(
                        id=1,
                        document_id=1,
                        ordinal=0,
                        kind="prose",
                        text="The consent is valid for ninety days from issue.",
                        char_count=48,
                    )
                )
            await session.commit()

        # Both modules read settings independently: the route resolves the provider and
        # builds the run, the dispatcher decides eager against queued.
        monkeypatch.setattr(generation_route, "get_settings", lambda: settings)
        monkeypatch.setattr(
            "app.services.generation_dispatch.settings", settings, raising=False
        )
        monkeypatch.setattr(
            "app.services.risk_generate.get_settings", lambda: settings
        )

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(generation_route.router)
        app.include_router(proposals_route.router)

        async def override_get_db():
            async with Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        client = AsyncClient(transport=transport, base_url="http://test")
        client._maker = Session  # type: ignore[attr-defined]
        return client

    yield build
    for engine in engines:
        await engine.dispose()


class TestStart:
    async def test_a_pass_runs_and_fills_the_inbox(self, make_client) -> None:
        client = await make_client(_settings())
        response = await client.post(
            f"/generation/risk-identification?scope_id={SCOPE_ID}",
            json={},
            headers={"X-Actor": "Sam"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "succeeded"
        assert body["proposal_count"] == 1

        inbox = await client.get("/proposals/inbox/count")
        assert inbox.json()["pending"] == 1
        await client.aclose()

    async def test_the_run_records_who_asked(self, make_client) -> None:
        client = await make_client(_settings())
        body = (
            await client.post(
                f"/generation/risk-identification?scope_id={SCOPE_ID}",
                json={},
                headers={"X-Actor": "Sam"},
            )
        ).json()
        assert body["requested_by"] == "Sam"
        assert body["kind"] == "risk_identification"
        await client.aclose()

    async def test_the_prompt_version_and_provider_are_on_the_run(
        self, make_client
    ) -> None:
        from app.agents.risk_id import PROMPT_VERSION

        client = await make_client(_settings())
        body = (
            await client.post(
                f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
            )
        ).json()
        assert body["prompt_version"] == PROMPT_VERSION
        assert body["provider"] == "fake"
        assert body["temperature"] == 0.0
        await client.aclose()

    async def test_naming_a_document_that_holds_nothing_is_refused_now_not_later(
        self, make_client
    ) -> None:
        """A queued run that will certainly fail is worse than a 422 that says why."""
        client = await make_client(_settings())
        response = await client.post(
            f"/generation/risk-identification?scope_id={SCOPE_ID}",
            json={"document_ids": [999]},
        )
        assert response.status_code == 422
        assert "documents you named" in response.json()["detail"]
        await client.aclose()

    async def test_an_empty_corpus_is_refused_with_what_to_do(self, make_client) -> None:
        client = await make_client(_settings(), with_documents=False)
        response = await client.post(
            f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
        )
        assert response.status_code == 422
        assert "Upload or paste" in response.json()["detail"]
        await client.aclose()

    async def test_no_provider_is_a_503_naming_the_setting(self, make_client) -> None:
        """One response about the deployment, rather than a growing list of failed runs
        all reporting the same missing environment variable."""
        client = await make_client(_settings(llm_provider=""))
        response = await client.post(
            f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
        )
        assert response.status_code == 503
        assert "LLM_PROVIDER" in response.json()["detail"]

        runs = await client.get("/generation/runs")
        assert runs.json() == []
        await client.aclose()

    async def test_no_worker_fails_the_run_rather_than_queueing_into_nothing(
        self, make_client, monkeypatch
    ) -> None:
        """A run waiting on a worker that does not exist looks exactly like a slow run,
        for hours, with nothing written anywhere to say otherwise."""
        monkeypatch.setattr(
            "app.services.generation_dispatch.live_workers", lambda timeout: []
        )
        client = await make_client(_settings(generation_eager=False))
        body = (
            await client.post(
                f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
            )
        ).json()
        assert body["status"] == "failed"
        assert "No worker answered" in body["error"]
        await client.aclose()


class TestReads:
    async def test_a_run_is_readable_with_its_transcript(self, make_client) -> None:
        client = await make_client(_settings())
        run_id = (
            await client.post(
                f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
            )
        ).json()["id"]

        detail = (await client.get(f"/generation/runs/{run_id}")).json()
        assert detail["pack_sha256"] is not None
        assert detail["transcript"][0]["chunk_refs"] == ["doc_chunk:1"]
        await client.aclose()

    async def test_the_list_omits_the_transcript(self, make_client) -> None:
        client = await make_client(_settings())
        await client.post(f"/generation/risk-identification?scope_id={SCOPE_ID}", json={})
        listing = (await client.get("/generation/runs")).json()
        assert len(listing) == 1
        assert "transcript" not in listing[0]
        await client.aclose()

    async def test_the_batch_is_readable_from_the_run(self, make_client) -> None:
        client = await make_client(_settings())
        run_id = (
            await client.post(
                f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
            )
        ).json()["id"]
        rows = (await client.get(f"/generation/runs/{run_id}/proposals")).json()
        assert len(rows) == 1
        assert rows[0]["generation_run_id"] == run_id
        assert rows[0]["target_id"] is None
        await client.aclose()

    async def test_an_unknown_status_filter_is_refused(self, make_client) -> None:
        client = await make_client(_settings())
        response = await client.get("/generation/runs?status=maybe")
        assert response.status_code == 422
        await client.aclose()

    async def test_a_missing_run_is_a_404(self, make_client) -> None:
        client = await make_client(_settings())
        assert (await client.get("/generation/runs/999")).status_code == 404
        assert (await client.get("/generation/runs/999/proposals")).status_code == 404
        await client.aclose()


class TestAppendOnly:
    async def test_there_is_no_delete_and_no_rerun(self, make_client) -> None:
        """The record of what a model produced, and how much of it was refused before a
        reviewer saw it, is the evidence that the review process is real. Evidence that
        can be tidied up is not evidence."""
        client = await make_client(_settings())
        run_id = (
            await client.post(
                f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
            )
        ).json()["id"]

        assert (await client.delete(f"/generation/runs/{run_id}")).status_code == 405
        assert (
            await client.put(f"/generation/runs/{run_id}", json={})
        ).status_code == 405
        await client.aclose()


class TestReviewLoop:
    async def test_a_generated_proposal_can_be_accepted_into_the_register(
        self, make_client
    ) -> None:
        """The whole point, in one test: document in, proposal out, human accepts, risk
        exists — and not one step of it happens without the disposition."""
        client = await make_client(_settings())
        run_id = (
            await client.post(
                f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
            )
        ).json()["id"]
        proposal = (await client.get(f"/generation/runs/{run_id}/proposals")).json()[0]

        from app.models.risk import Risk
        from sqlalchemy import select

        async with client._maker() as session:  # type: ignore[attr-defined]
            assert list(await session.scalars(select(Risk))) == []

        accepted = await client.post(
            f"/proposals/{proposal['id']}/disposition",
            json={"action": "accept"},
            headers={"X-Actor": "Sam"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["created_target_id"] is not None

        async with client._maker() as session:  # type: ignore[attr-defined]
            risks = list(await session.scalars(select(Risk)))
        assert len(risks) == 1
        assert risks[0].risk_code == "NST-0001"
        await client.aclose()
