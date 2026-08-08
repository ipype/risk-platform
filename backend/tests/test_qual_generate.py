"""One qualitative evaluation pass, end to end, with a scripted model.

The whole path: the active matrix out of the database, subjects out of the register,
retrieval through the evidence service, a call, a parse, rows in the proposal ledger.
Driven through ``qual_generate.execute`` rather than the route so the model's answer can be
*supplied* — the interesting cases are all "what does this do when the model says something
wrong", and a real provider cannot be asked to.

Four properties are worth more than the rest and get their own classes.

``TestAbstention`` — a risk retrieval found nothing for is never sent to a model. This is
the stage's central claim and it is asserted twice over: no proposal, and no call. A
version of this generator that asked anyway and let the parser catch the answer would pass
every other test in this file while spending money to invent probabilities.

``TestInvariantFour`` — nothing generated reaches ``risk`` without a human disposition,
asserted as an absence: after a full successful pass the register is still unscored.

``TestHumanJudgement`` — a field a person set is never re-scored, and the values they set
survive an accept. ``impact_scores`` is one JSON column the applier writes whole, so a
proposal holding only the model's areas would erase theirs on acceptance. That is the
failure this platform would be least able to explain afterwards.

``TestRerun`` — a second pass supersedes rather than doubles. Update proposals carry a
target, so the ledger's partial unique index does the work here that deduplication had to
do for creations.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents.types import ALREADY_ASSESSED, NO_EVIDENCE, SUBJECT_LIMIT
from app.core.config import Settings
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.db.base_class import Base
from app.llm.fake import FakeProvider
from app.models.document import Document, DocumentChunk
from app.models.generation import (
    FAILED,
    QUALITATIVE_EVALUATION,
    SUCCEEDED,
    GenerationRun,
)
from app.models.proposal import PENDING, SUPERSEDED, Proposal
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk
from app.models.scope import ScopeNode
from app.services import proposal_ledger, qual_generate

pytestmark = pytest.mark.asyncio

SCOPE_ID = 1
OTHER_SCOPE_ID = 2

SETTINGS = Settings(
    llm_provider="fake",
    llm_model="fake-model",
    generation_max_subjects=10,
    generation_evidence_limit=5,
    llm_max_output_tokens=2000,
)


def _assessment(**overrides) -> str:
    body = {
        "probability": 3,
        "probability_rationale": "The consent window is short.",
        "probability_confidence": 0.6,
        "impacts": {"COST": 4, "SCHED": 3},
        "impact_rationales": {"COST": "Reapplication.", "SCHED": "Six weeks."},
        "impact_confidence": 0.5,
        "evidence_refs": ["doc_chunk:1"],
    }
    body.update(overrides)
    return json.dumps(body)


#: ``(id, text, section)``. Only the first is about consents; the rest exist so the word
#: "consent" is rare in this corpus rather than universal.
CHUNKS = [
    (
        1,
        "The environmental consent for the tunnel tie-in is valid for ninety days "
        "from issue and lapses without extension.",
        "Consents › Validity",
    ),
    (
        2,
        "Welding procedures shall be qualified to the referenced standard before any "
        "production welding begins on the mainline.",
        "Construction › Welding",
    ),
    (
        3,
        "Long-lead valve packages are procured against the vendor list appended to "
        "this specification.",
        "Procurement › Long lead",
    ),
    (
        4,
        "Hydrotest pressures and hold durations are recorded on the commissioning "
        "certificates for each section.",
        "Commissioning › Hydrotest",
    ),
    (
        5,
        "This document is issued for construction and supersedes all previous "
        "revisions circulated to the distribution list.",
        "Front matter",
    ),
]

#: Risks in a sibling project, unrelated to the subject, so history IDF is meaningful.
DECOYS = [
    (101, "RVC-XNG-0101", "Welding procedure qualification rejected by the inspector"),
    (102, "RVC-XNG-0102", "Long-lead valve packages arrive after the mainline is ready"),
    (103, "RVC-XNG-0103", "Hydrotest hold duration not achieved on the river section"),
]


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'q.db'}", future=True)
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
        session.add(
            ScopeNode(
                id=OTHER_SCOPE_ID,
                kind="project",
                name="Riverside Crossing",
                code="RVC",
                created_by="test",
            )
        )
        session.add(RbsCategory(id=1, code="ENV", name="Environmental", sort_order=1))
        session.add(RbsSubcategory(id=1, category_id=1, code="030", name="Permitting"))
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
        # Several chunks, not one. BM25 gives a term appearing in every candidate an IDF
        # of zero — deliberately, so a query made of universal terms abstains rather than
        # returning the corpus in arbitrary order — which means a one-chunk corpus can
        # never produce a hit. A fixture that hid that would test a retrieval path no
        # deployment has.
        for ordinal, (chunk_id, text, section) in enumerate(CHUNKS):
            session.add(
                DocumentChunk(
                    id=chunk_id,
                    document_id=1,
                    ordinal=ordinal,
                    kind="prose",
                    text=text,
                    section=section,
                    locator={"page": ordinal + 1},
                    char_count=len(text),
                )
            )
        # The subject. Deliberately worded so it shares real vocabulary with the chunk
        # above — retrieval is BM25 with an overlap floor, and a subject nothing matches
        # is the abstention case, which has its own test.
        session.add(
            Risk(
                id=1,
                scope_id=SCOPE_ID,
                subcategory_id=1,
                seq=1,
                risk_code="NST-TUN-0001",
                title="Environmental consent lapses before tunnel tie-in",
                description="The environmental consent is valid for ninety days.",
                causes="consent validity ninety days from issue",
                consequences="tie-in delayed, fresh consent application required",
            )
        )
        # Decoys in a sibling project, so the register corpus is big enough for IDF to
        # mean anything. Same reason as the chunks above, and they sit in another scope so
        # they never become subjects of a pass over this one.
        for risk_id, code, title in DECOYS:
            session.add(
                Risk(
                    id=risk_id,
                    scope_id=OTHER_SCOPE_ID,
                    subcategory_id=1,
                    seq=risk_id,
                    risk_code=code,
                    title=title,
                    description=title,
                )
            )
        await session.commit()
    yield Session
    await engine.dispose()


async def _run(
    Session,
    *,
    script=None,
    provider=None,
    settings=SETTINGS,
    subject_ids=None,
) -> GenerationRun:
    async with Session() as session:
        run = GenerationRun(
            scope_id=SCOPE_ID,
            kind=QUALITATIVE_EVALUATION,
            prompt_version="test",
            provider="fake",
            model="fake-model",
            temperature=0.0,
            subject_ids=subject_ids,
            requested_by="tester",
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        run_id = run.id

    async with Session() as session:
        await qual_generate.execute(
            session,
            run_id,
            settings=settings,
            provider=provider or FakeProvider(script=script),
        )

    async with Session() as session:
        return await session.get(GenerationRun, run_id)


async def _proposals(Session) -> list[Proposal]:
    async with Session() as session:
        return list(await session.scalars(select(Proposal).order_by(Proposal.id)))


class TestHappyPath:
    async def test_a_pass_raises_one_proposal_per_field(self, factory) -> None:
        run = await _run(factory, script=[_assessment()])
        assert run.status == SUCCEEDED
        assert run.proposal_count == 2

        rows = await _proposals(factory)
        assert [r.field_path for r in rows] == ["probability", "impact_scores"]
        assert rows[0].proposed_value == 3
        assert rows[1].proposed_value == {"COST": 4, "SCHED": 3}
        assert all(r.target_id == 1 and r.target_type == "risk" for r in rows)

    async def test_confidence_is_carried_per_half_and_never_coerced(
        self, factory
    ) -> None:
        await _run(factory, script=[_assessment()])
        rows = await _proposals(factory)
        assert rows[0].confidence == 0.6
        assert rows[1].confidence == 0.5

    async def test_an_abstained_confidence_stays_null(self, factory) -> None:
        await _run(
            factory,
            script=[_assessment(probability_confidence=None, impact_confidence=None)],
        )
        rows = await _proposals(factory)
        assert rows[0].confidence is None and rows[1].confidence is None

    async def test_every_proposal_carries_evidence_that_resolves(self, factory) -> None:
        await _run(factory, script=[_assessment()])
        for row in await _proposals(factory):
            assert row.evidence_refs
            assert all(ref["ref"].startswith("doc_chunk:") for ref in row.evidence_refs)

    async def test_the_rationale_declares_what_the_judgement_rests_on(
        self, factory
    ) -> None:
        await _run(factory, script=[_assessment()])
        rows = await _proposals(factory)
        assert "Basis:" in rows[0].rationale
        assert "document extract" in rows[0].rationale

    async def test_the_impacts_rationale_names_each_area(self, factory) -> None:
        await _run(factory, script=[_assessment()])
        rows = await _proposals(factory)
        assert "COST: 4" in rows[1].rationale
        assert "SCHED: 3" in rows[1].rationale

    async def test_the_run_records_what_it_read_and_who_it_read_it_for(
        self, factory
    ) -> None:
        run = await _run(factory, script=[_assessment()])
        assert run.subject_ids == [1]
        assert run.window_count == 1  # one call, one subject
        assert run.chunk_count >= 1  # evidence items retrieved
        assert run.candidate_count == 1
        assert run.transcript and run.transcript[0]["subject"] == "NST-TUN-0001"
        assert run.pack_sha256

    async def test_the_prompt_carries_the_configured_scale(self, factory) -> None:
        provider = FakeProvider(script=[_assessment()])
        await _run(factory, provider=provider)
        sent = provider.calls[0]["messages"][0]["content"]
        assert "Probability scale:" in sent
        assert "COST — Cost:" in sent
        assert "$250k - $1M" in sent  # a default-matrix descriptor, read from the config

    async def test_the_subject_is_not_its_own_evidence(self, factory) -> None:
        provider = FakeProvider(script=[_assessment()])
        await _run(factory, provider=provider)
        sent = provider.calls[0]["messages"][0]["content"]
        # The risk would match itself perfectly on every term and take the top slot.
        assert "[risk:1]" not in sent


class TestAbstention:
    """No evidence means no call. The refusal happens before the money is spent."""

    async def test_a_risk_nothing_matches_is_skipped_without_a_call(
        self, factory
    ) -> None:
        async with factory() as session:
            session.add(
                Risk(
                    id=2,
                    scope_id=SCOPE_ID,
                    subcategory_id=1,
                    seq=2,
                    risk_code="NST-TUN-0002",
                    title="Zzzqx wibblefrotz kerplunk",
                    description="Zzzqx wibblefrotz kerplunk",
                )
            )
            await session.commit()

        provider = FakeProvider(script=[_assessment()])
        run = await _run(factory, provider=provider, subject_ids=[2])

        assert run.status == SUCCEEDED
        assert run.proposal_count == 0
        assert run.window_count == 0
        assert provider.calls == []
        assert run.skipped and run.skipped[0]["reason"] == NO_EVIDENCE

    async def test_a_skip_is_not_a_drop(self, factory) -> None:
        async with factory() as session:
            session.add(
                Risk(
                    id=2,
                    scope_id=SCOPE_ID,
                    subcategory_id=1,
                    seq=2,
                    risk_code="NST-TUN-0002",
                    title="Zzzqx wibblefrotz kerplunk",
                )
            )
            await session.commit()
        run = await _run(factory, script=[_assessment()], subject_ids=[2])
        # "Never asked" and "asked and refused" produce the same proposal count and mean
        # opposite things. They must not share a list.
        assert run.skipped and run.dropped is None

    async def test_an_answer_scoring_nothing_raises_nothing(self, factory) -> None:
        run = await _run(
            factory, script=[_assessment(probability=None, impacts={})]
        )
        assert run.status == SUCCEEDED
        assert run.proposal_count == 0
        assert await _proposals(factory) == []
        assert run.dropped and run.dropped[0]["reason"] == "nothing_to_score"

    async def test_an_ungrounded_answer_raises_nothing(self, factory) -> None:
        run = await _run(
            factory, script=[_assessment(evidence_refs=["doc_chunk:9001"])]
        )
        assert run.proposal_count == 0
        assert run.dropped and run.dropped[0]["reason"] == "ungrounded"
        # The drop names the risk, so a forty-subject run's flat list is actionable.
        assert "NST-TUN-0001" in run.dropped[0]["detail"]

    async def test_a_score_off_the_scale_never_reaches_the_ledger(
        self, factory
    ) -> None:
        run = await _run(factory, script=[_assessment(probability=9)])
        rows = await _proposals(factory)
        assert [r.field_path for r in rows] == ["impact_scores"]
        assert run.dropped and run.dropped[0]["reason"] == "out_of_range"


class TestInvariantFour:
    async def test_a_successful_pass_leaves_the_register_unscored(
        self, factory
    ) -> None:
        await _run(factory, script=[_assessment()])
        async with factory() as session:
            risk = await session.get(Risk, 1)
            assert risk.probability is None
            assert risk.impact_scores is None
            assert risk.risk_level is None

    async def test_a_score_lands_only_through_a_disposition(self, factory) -> None:
        await _run(factory, script=[_assessment()])
        async with factory() as session:
            rows = list(await session.scalars(select(Proposal).order_by(Proposal.id)))
            for row in rows:
                await proposal_ledger.dispose(
                    session, row, action="accept", actor="analyst"
                )
            await session.commit()

        async with factory() as session:
            risk = await session.get(Risk, 1)
            assert risk.probability == 3
            assert risk.impact_scores == {"COST": 4, "SCHED": 3}
            # Worst case across areas, computed by the applier and never by the model.
            assert risk.impact == 4


class TestHumanJudgement:
    async def test_a_scored_probability_is_not_re_scored(self, factory) -> None:
        async with factory() as session:
            risk = await session.get(Risk, 1)
            risk.probability = 5
            await session.commit()

        run = await _run(factory, script=[_assessment()])
        rows = await _proposals(factory)
        assert [r.field_path for r in rows] == ["impact_scores"]
        assert run.proposal_count == 1

    async def test_a_scored_area_survives_the_proposal_it_is_carried_into(
        self, factory
    ) -> None:
        async with factory() as session:
            risk = await session.get(Risk, 1)
            risk.impact_scores = {"SAFE": 5}
            await session.commit()

        await _run(factory, script=[_assessment()])
        rows = await _proposals(factory)
        impacts = next(r for r in rows if r.field_path == "impact_scores")
        # The applier sets the column whole. A payload without SAFE would erase it.
        assert impacts.proposed_value == {"SAFE": 5, "COST": 4, "SCHED": 3}
        assert "carried through unchanged: SAFE" in impacts.rationale

    async def test_accepting_does_not_erase_what_a_person_scored(
        self, factory
    ) -> None:
        async with factory() as session:
            risk = await session.get(Risk, 1)
            risk.impact_scores = {"SAFE": 5}
            await session.commit()

        await _run(factory, script=[_assessment()])
        async with factory() as session:
            row = await session.scalar(
                select(Proposal).where(Proposal.field_path == "impact_scores")
            )
            await proposal_ledger.dispose(
                session, row, action="accept", actor="analyst"
            )
            await session.commit()

        async with factory() as session:
            risk = await session.get(Risk, 1)
            assert risk.impact_scores["SAFE"] == 5
            assert risk.impact == 5

    async def test_a_fully_scored_risk_is_skipped_with_a_reason(self, factory) -> None:
        async with factory() as session:
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

        provider = FakeProvider(script=[])
        run = await _run(factory, provider=provider)
        assert run.proposal_count == 0
        assert provider.calls == []
        assert run.skipped and run.skipped[0]["reason"] == ALREADY_ASSESSED


class TestRerun:
    async def test_a_second_pass_supersedes_rather_than_doubles(
        self, factory
    ) -> None:
        await _run(factory, script=[_assessment()])
        await _run(factory, script=[_assessment(probability=2)])

        async with factory() as session:
            rows = list(await session.scalars(select(Proposal).order_by(Proposal.id)))
        pending = [r for r in rows if r.status == PENDING]
        superseded = [r for r in rows if r.status == SUPERSEDED]

        assert len(rows) == 4
        assert len(pending) == 2
        assert len(superseded) == 2
        # The older row keeps its rationale and evidence; only its claim on attention goes.
        assert all(r.superseded_by is not None for r in superseded)
        assert next(r for r in pending if r.field_path == "probability").proposed_value == 2


class TestCap:
    async def test_subjects_beyond_the_cap_are_reported_not_dropped_silently(
        self, factory
    ) -> None:
        async with factory() as session:
            for n in range(2, 6):
                session.add(
                    Risk(
                        id=n,
                        scope_id=SCOPE_ID,
                        subcategory_id=1,
                        seq=n,
                        risk_code=f"NST-TUN-000{n}",
                        title="Environmental consent lapses before tunnel tie-in",
                        description="The environmental consent is valid for ninety days.",
                    )
                )
            await session.commit()

        capped = SETTINGS.model_copy(update={"generation_max_subjects": 2})
        run = await _run(
            factory, script=[_assessment(), _assessment()], settings=capped
        )
        assert run.window_count == 2
        assert run.subject_ids == [1, 2]
        reasons = [s["reason"] for s in (run.skipped or [])]
        assert reasons.count(SUBJECT_LIMIT) == 3


class TestFailures:
    async def test_an_empty_register_fails_the_run_with_a_reason(
        self, factory
    ) -> None:
        async with factory() as session:
            risk = await session.get(Risk, 1)
            await session.delete(risk)
            await session.commit()

        run = await _run(factory, script=[])
        assert run.status == FAILED
        assert "register is empty" in run.error

    async def test_a_finished_run_is_not_re_executed(self, factory) -> None:
        run = await _run(factory, script=[_assessment()])
        async with factory() as session:
            again = await qual_generate.execute(
                session, run.id, settings=SETTINGS, provider=FakeProvider(script=[])
            )
        assert again.status == SUCCEEDED
        assert len(await _proposals(factory)) == 2

    async def test_a_missing_run_is_none_rather_than_an_exception(
        self, factory
    ) -> None:
        async with factory() as session:
            assert (
                await qual_generate.execute(
                    session, 9999, settings=SETTINGS, provider=FakeProvider(script=[])
                )
                is None
            )


class TestReferenceClass:
    async def test_a_comparable_from_another_project_is_labelled_as_such(
        self, factory
    ) -> None:
        async with factory() as session:
            session.add(
                Risk(
                    id=9,
                    scope_id=OTHER_SCOPE_ID,
                    subcategory_id=1,
                    seq=1,
                    risk_code="RVC-XNG-0001",
                    title="Environmental consent lapses before tie-in",
                    description="The environmental consent is valid for ninety days.",
                    probability=4,
                    impact_scores={"COST": 3},
                )
            )
            await session.commit()

        provider = FakeProvider(script=[_assessment(evidence_refs=["risk:9"])])
        await _run(factory, provider=provider)

        sent = provider.calls[0]["messages"][0]["content"]
        assert "[risk:9]" in sent
        assert "(another project)" in sent
        assert "Scored there as: probability 4; COST 3" in sent

        rows = await _proposals(factory)
        cited = rows[0].evidence_refs[0]
        assert cited["ref"] == "risk:9"
        # The inbox must not read a sibling project's history as this project's own.
        assert "Riverside Crossing" in cited["excerpt"]
        assert "not observed outcomes" in rows[0].rationale
