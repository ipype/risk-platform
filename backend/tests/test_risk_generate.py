"""One generation pass, end to end, with a scripted model.

The whole path: corpus out of the database, windows, a call, a parse, deduplication, rows
in the proposal ledger. Driven through ``risk_generate.execute`` rather than the route so
the model's answer can be *supplied* — the interesting cases are all "what does this do
when the model says something wrong", and a real provider cannot be asked to.

Two properties are worth more than the rest and get their own classes.

``TestInvariantFour`` — nothing generated reaches ``risk`` without a human disposition. It
is asserted here as an absence: after a full successful pass, the register is empty. That
is a weak-looking test guarding the strongest claim this platform makes about its AI
features, and it would fail loudly the day someone adds a convenience write.

``TestRerun`` — a second pass over an unchanged corpus. Creation proposals carry
``target_id IS NULL`` and are exempt from the ledger's one-pending-per-field index, so
nothing in 5.1 stops a rerun doubling the inbox. Deduplication in the generator is the only
thing that does, which makes this the test that decides whether the feature is usable twice.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.llm.fake import FakeProvider
from app.models.document import Document, DocumentChunk
from app.models.generation import (
    FAILED,
    RISK_IDENTIFICATION,
    SUCCEEDED,
    GenerationRun,
)
from app.models.proposal import Proposal
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.scope import ScopeNode
from app.services import risk_generate

pytestmark = pytest.mark.asyncio

SCOPE_ID = 1

SETTINGS = Settings(
    llm_provider="fake",
    llm_model="fake-model",
    generation_window_chars=400,
    generation_max_windows=10,
    llm_max_output_tokens=2000,
)


def _candidate(**overrides) -> dict:
    base = {
        "title": "Consent lapses before tie-in",
        "cause": "the environmental consent is valid for ninety days from issue",
        "event": "the tie-in slips beyond that validity window",
        "effect": "delay commissioning and require a fresh application",
        "subcategory_prefix": "ENV-030",
        "evidence_refs": ["doc_chunk:1"],
        "rationale": "The ninety-day validity is stated in the extract.",
        "confidence": 0.6,
    }
    base.update(overrides)
    return base


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'g.db'}", future=True)
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
        session.add(RbsCategory(id=1, code="ENV", name="Environmental", sort_order=1))
        session.add(RbsCategory(id=2, code="STG", name="Stakeholder", sort_order=2))
        session.add(RbsSubcategory(id=1, category_id=1, code="030", name="Permitting"))
        session.add(RbsSubcategory(id=2, category_id=2, code="010", name="Third parties"))
        session.add(
            Document(
                id=1,
                scope_id=SCOPE_ID,
                filename="consent.pdf",
                suffix=".pdf",
                sha256="a" * 64,
                byte_size=10,
                chunk_count=2,
                title="Environmental consent",
            )
        )
        session.add(
            DocumentChunk(
                id=1,
                document_id=1,
                ordinal=0,
                kind="prose",
                text="The consent is valid for ninety days from the date of issue.",
                section="Consents › Validity",
                locator={"page": 4},
                char_count=60,
            )
        )
        session.add(
            DocumentChunk(
                id=2,
                document_id=1,
                ordinal=1,
                kind="prose",
                text="Dewatering may not begin before the consent has been granted.",
                char_count=60,
            )
        )
        await session.commit()
    yield Session
    await engine.dispose()


async def _run(
    Session, *, script=None, provider=None, settings=SETTINGS, documents=None
) -> GenerationRun:
    async with Session() as session:
        run = GenerationRun(
            scope_id=SCOPE_ID,
            kind=RISK_IDENTIFICATION,
            prompt_version="risk-id/test",
            provider="fake",
            model="fake-model",
            temperature=0.0,
            document_ids=documents,
            requested_by="Sam",
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    async with Session() as session:
        await risk_generate.execute(
            session,
            run_id,
            settings=settings,
            provider=provider or FakeProvider(script=script, model="fake-model"),
        )

    async with Session() as session:
        result = await session.get(GenerationRun, run_id)
        assert result is not None
        return result


async def _proposals(Session) -> list[Proposal]:
    async with Session() as session:
        return list(await session.scalars(select(Proposal).order_by(Proposal.id)))


class TestHappyPath:
    async def test_a_pass_raises_one_proposal_per_surviving_candidate(
        self, factory
    ) -> None:
        run = await _run(factory, script=[json.dumps([_candidate()])])
        assert run.status == SUCCEEDED
        assert run.proposal_count == 1
        assert len(await _proposals(factory)) == 1

    async def test_the_proposal_is_a_creation_addressing_the_whole_row(
        self, factory
    ) -> None:
        await _run(factory, script=[json.dumps([_candidate()])])
        row = (await _proposals(factory))[0]
        assert row.target_type == "risk"
        assert row.target_id is None
        assert row.field_path == "*"
        assert row.status == "pending"

    async def test_the_payload_is_exactly_what_the_applier_accepts(self, factory) -> None:
        from app.services.proposal_apply import CREATABLE_RISK_FIELDS

        await _run(factory, script=[json.dumps([_candidate()])])
        row = (await _proposals(factory))[0]
        assert set(row.proposed_value) <= CREATABLE_RISK_FIELDS
        assert "subcategory_prefix" in row.proposed_value

    async def test_the_citation_carries_the_text_the_reviewer_will_read(
        self, factory
    ) -> None:
        """A citation nobody can read without three clicks is a citation nobody reads."""
        await _run(factory, script=[json.dumps([_candidate()])])
        ref = (await _proposals(factory))[0].evidence_refs[0]
        assert ref["kind"] == "doc_chunk"
        assert ref["ref"] == "doc_chunk:1"
        assert "ninety days" in ref["excerpt"]

    async def test_the_run_stamps_the_prompt_version_on_every_proposal(
        self, factory
    ) -> None:
        from app.agents.risk_id import PROMPT_VERSION

        await _run(factory, script=[json.dumps([_candidate()])])
        assert (await _proposals(factory))[0].generator_prompt_version == PROMPT_VERSION

    async def test_the_proposal_points_back_at_the_run(self, factory) -> None:
        run = await _run(factory, script=[json.dumps([_candidate()])])
        assert (await _proposals(factory))[0].generation_run_id == run.id

    async def test_the_run_records_what_it_read(self, factory) -> None:
        run = await _run(factory, script=[json.dumps([_candidate()])])
        assert run.document_ids == [1]
        assert run.chunk_count == 2
        assert run.window_count == 1
        assert run.pack_sha256 is not None

    async def test_the_transcript_keeps_what_the_model_actually_said(
        self, factory
    ) -> None:
        """The audit answer to "what did the model say", as opposed to what we made of
        it."""
        raw = json.dumps([_candidate()])
        run = await _run(factory, script=[raw])
        assert run.transcript is not None
        assert run.transcript[0]["response"] == raw
        assert run.transcript[0]["chunk_refs"] == ["doc_chunk:1", "doc_chunk:2"]

    async def test_token_usage_is_totalled(self, factory) -> None:
        run = await _run(factory, script=[json.dumps([_candidate()])])
        assert run.input_tokens and run.input_tokens > 0

    async def test_an_abstaining_model_is_a_successful_run(self, factory) -> None:
        """Returning nothing is a correct and useful answer. Padding is not."""
        run = await _run(factory, script=["[]"])
        assert run.status == SUCCEEDED
        assert run.proposal_count == 0
        assert run.candidate_count == 0
        assert await _proposals(factory) == []


class TestInvariantFour:
    async def test_a_successful_pass_writes_nothing_to_the_register(
        self, factory
    ) -> None:
        """The strongest claim this platform makes about its AI features, asserted as an
        absence. It should fail the day anyone adds a convenience write."""
        await _run(factory, script=[json.dumps([_candidate(), _candidate(title="Two")])])
        async with factory() as session:
            assert list(await session.scalars(select(Risk))) == []


class TestGroundingEndToEnd:
    async def test_a_candidate_citing_an_unsent_chunk_never_becomes_a_proposal(
        self, factory
    ) -> None:
        run = await _run(
            factory,
            script=[json.dumps([_candidate(evidence_refs=["doc_chunk:9001"])])],
        )
        assert run.proposal_count == 0
        assert await _proposals(factory) == []

    async def test_the_refusal_is_reported_on_the_run(self, factory) -> None:
        run = await _run(
            factory,
            script=[json.dumps([_candidate(evidence_refs=["doc_chunk:9001"])])],
        )
        assert run.dropped is not None
        assert run.dropped[0]["reason"] == "ungrounded"
        assert "doc_chunk:9001" in run.dropped[0]["detail"]

    async def test_candidate_count_exceeds_proposal_count_when_things_are_refused(
        self, factory
    ) -> None:
        """The gap between the two is the quality signal of the pass."""
        run = await _run(
            factory,
            script=[
                json.dumps(
                    [_candidate(), _candidate(title="B", evidence_refs=["doc_chunk:77"])]
                )
            ],
        )
        assert run.candidate_count == 2
        assert run.proposal_count == 1

    async def test_the_fake_default_mode_cites_a_real_chunk(self, factory) -> None:
        """The derived answer is a function of the real prompt, so this also proves the
        prompt carried a resolvable chunk id."""
        run = await _run(factory, provider=FakeProvider(model="fake-model"))
        assert run.proposal_count == 1
        assert (await _proposals(factory))[0].evidence_refs[0]["ref"] == "doc_chunk:1"


class TestBadResponses:
    async def test_an_unparseable_window_does_not_fail_the_run(self, factory) -> None:
        run = await _run(factory, script=["I could not find anything."])
        assert run.status == SUCCEEDED
        assert run.dropped[0]["reason"] == "unparseable"

    async def test_one_bad_window_does_not_cost_the_others(self, factory) -> None:
        """Twenty windows must not lose nineteen good ones to the twentieth's
        formatting."""
        settings = SETTINGS.model_copy(update={"generation_window_chars": 60})
        run = await _run(
            factory,
            script=["not json at all", json.dumps([_candidate(evidence_refs=["doc_chunk:2"])])],
            settings=settings,
        )
        assert run.window_count == 2
        assert run.proposal_count == 1

    async def test_a_truncated_response_is_flagged_in_the_transcript(
        self, factory
    ) -> None:
        provider = FakeProvider(script=["[{"], model="fake-model", stop_reason="max_tokens")
        run = await _run(factory, provider=provider)
        assert run.transcript[0]["hit_output_ceiling"] is True
        assert run.proposal_count == 0

    async def test_an_unknown_category_is_refused(self, factory) -> None:
        run = await _run(
            factory, script=[json.dumps([_candidate(subcategory_prefix="ZZZ-999")])]
        )
        assert run.proposal_count == 0
        assert run.dropped[0]["reason"] == "unknown_category"


class TestDeduplication:
    async def test_the_same_finding_twice_in_one_pass_is_raised_once(
        self, factory
    ) -> None:
        run = await _run(
            factory,
            script=[json.dumps([_candidate(), _candidate(title="Consent lapse tie-in")])],
        )
        assert run.proposal_count == 1
        assert run.dropped[0]["reason"] == "duplicate_in_batch"

    async def test_two_genuinely_different_findings_both_survive(self, factory) -> None:
        run = await _run(
            factory,
            script=[
                json.dumps(
                    [
                        _candidate(),
                        _candidate(
                            title="Landowner refuses access",
                            cause="the access agreement is unsigned",
                            event="the landowner refuses entry at mobilisation",
                            effect="stand down the crew and rebook the works window",
                            subcategory_prefix="STG-010",
                            evidence_refs=["doc_chunk:2"],
                        ),
                    ]
                )
            ],
        )
        assert run.proposal_count == 2

    async def test_a_risk_already_in_the_register_is_suppressed_and_reported(
        self, factory
    ) -> None:
        """Seeded in the shape the creation applier writes — statement in ``description``,
        parts in ``causes`` and ``consequences`` — because that is what a register row
        produced by a previous accepted pass actually looks like, and a fixture that
        omitted the event clause would be testing an easier problem than the real one.
        """
        async with factory() as session:
            session.add(
                Risk(
                    id=1,
                    scope_id=SCOPE_ID,
                    subcategory_id=1,
                    seq=1,
                    risk_code="NST-0001",
                    title="Consent lapses before tie-in",
                    description=(
                        "Because the environmental consent is valid for ninety days from "
                        "issue, the tie-in slips beyond that validity window, which would "
                        "delay commissioning and require a fresh application."
                    ),
                    causes="the environmental consent is valid for ninety days from issue",
                    consequences="delay commissioning and require a fresh application",
                    status="Open",
                )
            )
            await session.commit()

        run = await _run(factory, script=[json.dumps([_candidate()])])
        assert run.proposal_count == 0
        assert run.dropped[0]["reason"] == "already_in_register"
        assert "NST-0001" in run.dropped[0]["detail"]

    async def test_a_merely_related_risk_is_kept_with_the_precedent_attached(
        self, factory
    ) -> None:
        """The band between the thresholds resolves into a citation, not a suppression:
        the reviewer decides whether they are the same thing."""
        async with factory() as session:
            session.add(
                Risk(
                    id=1,
                    scope_id=SCOPE_ID,
                    subcategory_id=1,
                    seq=1,
                    risk_code="NST-0001",
                    title="Consent lapses before tie-in",
                    causes="the environmental consent is valid for ninety days from issue",
                    consequences="delay handover and require a fresh application",
                    status="Open",
                )
            )
            await session.commit()

        run = await _run(factory, script=[json.dumps([_candidate()])])
        if run.proposal_count == 0:  # pragma: no cover - documents the threshold
            pytest.fail(
                "A related risk was suppressed rather than attached as precedent. A "
                "false suppression is invisible and permanent; see agents/dedupe.py."
            )
        refs = (await _proposals(factory))[0].evidence_refs
        kinds = {ref["kind"] for ref in refs}
        assert "risk" in kinds
        precedent = next(ref for ref in refs if ref["kind"] == "risk")
        assert precedent["ref"] == "risk:1"
        assert "NST-0001" in precedent["excerpt"]


class TestRerun:
    async def test_a_second_pass_over_an_unchanged_corpus_does_not_double_the_inbox(
        self, factory
    ) -> None:
        """Creation proposals are exempt from the one-pending-per-field index, so nothing
        in the ledger stops this. Deduplication in the generator is the only thing that
        does — which makes this the test that decides whether the feature is usable twice.

        The first pass leaves a *pending* proposal, not a risk, so the second pass cannot
        see it in the register. What it can see is its own duplicate, which is why the
        drop reason below is the batch one.
        """
        first = await _run(factory, script=[json.dumps([_candidate()])])
        assert first.proposal_count == 1

        # Accept it, so the register now carries the risk the second pass will rediscover.
        from app.services import proposal_ledger

        async with factory() as session:
            row = (await session.scalars(select(Proposal))).one()
            await proposal_ledger.dispose(session, row, action="accept", actor="Sam")
            await session.commit()

        second = await _run(factory, script=[json.dumps([_candidate()])])
        assert second.proposal_count == 0
        assert second.dropped[0]["reason"] == "already_in_register"
        assert len(await _proposals(factory)) == 1


class TestWindowing:
    async def test_a_narrow_budget_splits_the_document_into_several_calls(
        self, factory
    ) -> None:
        settings = SETTINGS.model_copy(update={"generation_window_chars": 60})
        provider = FakeProvider(model="fake-model")
        run = await _run(factory, provider=provider, settings=settings)
        assert run.window_count == 2
        assert len(provider.calls) == 2

    async def test_the_cap_stops_short_and_says_so(self, factory) -> None:
        """A run that read one window out of two has to say so on its own face."""
        settings = SETTINGS.model_copy(
            update={"generation_window_chars": 60, "generation_max_windows": 1}
        )
        run = await _run(factory, provider=FakeProvider(model="fake-model"), settings=settings)
        assert run.window_count == 1
        assert run.windows_truncated is True

    async def test_naming_documents_narrows_the_pack(self, factory) -> None:
        run = await _run(
            factory, script=["[]"], documents=[999]
        )
        assert run.status == FAILED
        assert "nothing in this project's corpus" in (run.error or "")


class TestRefusalsBeforeAnyCall:
    async def test_an_empty_corpus_fails_the_run_without_calling_a_model(
        self, factory
    ) -> None:
        async with factory() as session:
            doc = await session.get(Document, 1)
            doc.status = "withdrawn"
            await session.commit()

        provider = FakeProvider(model="fake-model")
        run = await _run(factory, provider=provider)
        assert run.status == FAILED
        assert provider.calls == []

    async def test_a_withdrawn_document_stops_being_read(self, factory) -> None:
        """Withdrawn documents stay citable so old proposals keep resolving, and stop
        being cited so a retired drawing register does not go on producing risks."""
        async with factory() as session:
            doc = await session.get(Document, 1)
            doc.status = "withdrawn"
            await session.commit()
        chunks, _, _ = await _load(factory)
        assert chunks == []

    async def test_an_empty_rbs_fails_before_a_call(self, factory) -> None:
        async with factory() as session:
            for sub_id in (1, 2):
                sub = await session.get(RbsSubcategory, sub_id)
                await session.delete(sub)
            await session.commit()

        provider = FakeProvider(model="fake-model")
        run = await _run(factory, provider=provider)
        assert run.status == FAILED
        assert "risk breakdown structure" in (run.error or "").lower()
        assert provider.calls == []

    async def test_a_terminal_run_is_not_re_executed(self, factory) -> None:
        run = await _run(factory, script=[json.dumps([_candidate()])])
        provider = FakeProvider(script=[], model="fake-model")
        async with factory() as session:
            await risk_generate.execute(
                session, run.id, settings=SETTINGS, provider=provider
            )
        assert provider.calls == []
        assert len(await _proposals(factory)) == 1


async def _load(Session):
    async with Session() as session:
        return await risk_generate.load_pack(session, SCOPE_ID)
