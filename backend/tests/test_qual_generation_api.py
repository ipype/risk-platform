"""The qualitative evaluation route, over the real app wiring.

Dispatch runs eagerly here (``generation_eager``), the same convenience
``test_generation_api.py`` takes and for the same reason: a route test that needed Redis
and a worker would be an integration test wearing a unit test's clothes.

No scripted responses. The route is wired to the default ``FakeProvider``, which derives
its answer from the scale and the evidence identifiers in the *real* prompt — so a passing
test here is also evidence that the rendered prompt carried a resolvable citation and a
scale the parser recognises. Scripted failure cases belong in ``test_qual_generate.py``,
where the provider is injectable.

The refusals are what this file is really for. "Nothing left to score" and "no such
register" are the two states this route meets on a first-day install, and each has to come
back as something a person can act on rather than as a queued run that will certainly
produce nothing.
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
from app.models.risk import Risk
from app.models.scope import ScopeNode

pytestmark = pytest.mark.asyncio

SCOPE_ID = 1

CHUNKS = [
    (
        1,
        "The environmental consent for the tunnel tie-in is valid for ninety days "
        "from issue and lapses without extension.",
    ),
    (
        2,
        "Welding procedures shall be qualified to the referenced standard before "
        "production welding begins on the mainline.",
    ),
    (
        3,
        "Long-lead valve packages are procured against the appended vendor list.",
    ),
    (
        4,
        "Hydrotest pressures and hold durations are recorded on the commissioning "
        "certificates for each section.",
    ),
]


def _settings(**overrides) -> Settings:
    base = {
        "llm_provider": "fake",
        "llm_model": "fake-model",
        "generation_eager": True,
        "generation_max_subjects": 10,
        "generation_evidence_limit": 5,
    }
    base.update(overrides)
    return Settings(**base)


@pytest_asyncio.fixture
async def make_client(tmp_path, monkeypatch):
    engines: list = []

    async def build(settings: Settings, *, risks=((1, "NST-TUN-0001"),)):
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path/f'q{len(engines)}.db'}", future=True
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
            session.add(
                Document(
                    id=1,
                    scope_id=SCOPE_ID,
                    filename="consent.pdf",
                    suffix=".pdf",
                    sha256="a" * 64,
                    byte_size=10,
                    chunk_count=len(CHUNKS),
                    title="Environmental consent",
                )
            )
            for ordinal, (chunk_id, text) in enumerate(CHUNKS):
                session.add(
                    DocumentChunk(
                        id=chunk_id,
                        document_id=1,
                        ordinal=ordinal,
                        kind="prose",
                        text=text,
                        char_count=len(text),
                    )
                )
            for risk_id, code in risks:
                session.add(
                    Risk(
                        id=risk_id,
                        scope_id=SCOPE_ID,
                        subcategory_id=1,
                        seq=risk_id,
                        risk_code=code,
                        title="Environmental consent lapses before tunnel tie-in",
                        description="The environmental consent is valid for ninety days.",
                        causes="consent validity ninety days from issue",
                    )
                )
            await session.commit()

        monkeypatch.setattr(generation_route, "get_settings", lambda: settings)
        monkeypatch.setattr(
            "app.services.generation_dispatch.settings", settings, raising=False
        )
        monkeypatch.setattr("app.services.qual_generate.get_settings", lambda: settings)

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
            f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}",
            json={},
            headers={"X-Actor": "Sam"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "succeeded"
        assert body["kind"] == "qualitative_evaluation"
        assert body["requested_by"] == "Sam"
        assert body["subject_ids"] == [1]
        assert body["proposal_count"] >= 1

        inbox = await client.get("/proposals/inbox/count")
        assert inbox.json()["pending"] == body["proposal_count"]
        await client.aclose()

    async def test_the_prompt_version_is_stamped_on_the_run(self, make_client) -> None:
        from app.agents.qual_eval import PROMPT_VERSION

        client = await make_client(_settings())
        body = (
            await client.post(
                f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
            )
        ).json()
        assert body["prompt_version"] == PROMPT_VERSION
        await client.aclose()

    async def test_naming_risks_narrows_the_pass(self, make_client) -> None:
        client = await make_client(
            _settings(), risks=((1, "NST-TUN-0001"), (2, "NST-TUN-0002"))
        )
        body = (
            await client.post(
                f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}",
                json={"risk_ids": [2]},
            )
        ).json()
        assert body["subject_ids"] == [2]
        await client.aclose()

    async def test_the_subject_list_is_fixed_when_the_run_is_created(
        self, make_client
    ) -> None:
        """Resolved in the request, not in the worker.

        A pass whose subjects were computed at execution time would silently cover a
        different set from the one the analyst was looking at when they pressed the
        button — the register moves while a queue drains.
        """
        client = await make_client(_settings())
        body = (
            await client.post(
                f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
            )
        ).json()
        detail = (await client.get(f"/generation/runs/{body['id']}")).json()
        assert detail["subject_ids"] == [1]
        await client.aclose()


class TestRefusals:
    async def test_an_empty_register_is_refused_before_a_run_exists(
        self, make_client
    ) -> None:
        client = await make_client(_settings(), risks=())
        response = await client.post(
            f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
        )
        assert response.status_code == 422
        assert "nothing in this project's register" in response.json()["detail"].lower()
        assert (await client.get("/generation/runs")).json() == []
        await client.aclose()

    async def test_a_fully_scored_register_is_refused_with_a_usable_reason(
        self, make_client
    ) -> None:
        client = await make_client(_settings())
        async with client._maker() as session:  # type: ignore[attr-defined]
            risk = await session.get(Risk, 1)
            risk.probability = 3
            risk.impact_scores = {
                "COST": 2,
                "SCHED": 2,
                "SAFE": 1,
                "REP": 1,
                "ENV": 1,
            }
            await session.commit()

        response = await client.post(
            f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
        )
        assert response.status_code == 422
        assert "clear a field" in response.json()["detail"].lower()
        await client.aclose()

    async def test_no_provider_is_a_503_naming_the_setting(self, make_client) -> None:
        client = await make_client(_settings(llm_provider=""))
        response = await client.post(
            f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
        )
        assert response.status_code == 503
        assert "LLM_PROVIDER" in response.json()["detail"]
        # Refused before a row exists: otherwise a misconfigured deployment accumulates
        # identical failed runs all reporting the same missing environment variable.
        assert (await client.get("/generation/runs")).json() == []
        await client.aclose()


class TestListing:
    async def test_runs_can_be_filtered_by_kind(self, make_client) -> None:
        client = await make_client(_settings())
        await client.post(
            f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
        )
        await client.post(
            f"/generation/risk-identification?scope_id={SCOPE_ID}", json={}
        )

        everything = (await client.get("/generation/runs")).json()
        assert len(everything) == 2

        quals = (
            await client.get("/generation/runs?kind=qualitative_evaluation")
        ).json()
        assert [r["kind"] for r in quals] == ["qualitative_evaluation"]
        await client.aclose()

    async def test_an_unknown_kind_is_refused_rather_than_silently_empty(
        self, make_client
    ) -> None:
        client = await make_client(_settings())
        response = await client.get("/generation/runs?kind=premortem")
        assert response.status_code == 422
        await client.aclose()

    async def test_the_summary_carries_the_skip_count(self, make_client) -> None:
        """"Nothing proposed" and "nothing proposed because there was nothing to go on"
        are different results, and a list view showing only the first invites the wrong
        conclusion about the second."""
        client = await make_client(
            _settings(), risks=((1, "NST-TUN-0001"), (2, "NST-TUN-0002"))
        )
        async with client._maker() as session:  # type: ignore[attr-defined]
            risk = await session.get(Risk, 2)
            risk.title = "Zzzqx wibblefrotz kerplunk"
            risk.description = "Zzzqx wibblefrotz kerplunk"
            risk.causes = None
            await session.commit()

        await client.post(
            f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
        )
        listed = (await client.get("/generation/runs")).json()
        assert listed[0]["skipped_count"] == 1
        await client.aclose()

    async def test_a_runs_proposals_are_reachable_from_the_run(
        self, make_client
    ) -> None:
        client = await make_client(_settings())
        body = (
            await client.post(
                f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
            )
        ).json()
        rows = (await client.get(f"/generation/runs/{body['id']}/proposals")).json()
        assert len(rows) == body["proposal_count"]
        assert {r["field_path"] for r in rows} <= {"probability", "impact_scores"}
        assert all(r["target_id"] == 1 for r in rows)
        await client.aclose()


class TestAppendOnly:
    async def test_there_is_no_delete_and_no_rerun_route(self, make_client) -> None:
        client = await make_client(_settings())
        body = (
            await client.post(
                f"/generation/qualitative-evaluation?scope_id={SCOPE_ID}", json={}
            )
        ).json()
        assert (await client.delete(f"/generation/runs/{body['id']}")).status_code == 405
        assert (
            await client.post(f"/generation/runs/{body['id']}/rerun", json={})
        ).status_code == 404
        await client.aclose()
