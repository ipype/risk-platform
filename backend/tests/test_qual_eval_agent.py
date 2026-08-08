"""The qualitative evaluation agent, with no database and no network.

Everything here is a string and a frozen dataclass, which is the point of keeping
``app/agents/`` pure: the claims this stage makes — that a score is refused when it is off
the scale, that an area nobody configured never reaches the register, that a field a person
already ruled on is not re-scored, that an answer citing nothing it was shown is dropped —
are all decided by code that needs neither.

``TestRefusalToScore`` carries the stage's central property. A probability is a number that
looks the same whether it was reasoned from a document or produced to fill a field, and it
multiplies into a matrix that decides which risks get an expensive elicitation. Every test
in that class is a way the model can offer a score with nothing behind it, and every one of
them must come back as a drop.
"""

from __future__ import annotations

import json
from dataclasses import replace

from app.agents import qual_eval as agent
from app.agents.types import (
    NOT_AN_OBJECT,
    NOTHING_TO_SCORE,
    OUT_OF_RANGE,
    UNGROUNDED,
    UNKNOWN_AREA,
    UNPARSEABLE,
    EvidenceItem,
    ImpactArea,
    Level,
    RiskSubject,
    Scale,
)
from app.llm.fake import OBJECT_CLOSING, REF_MARKER

SCALE = Scale(
    probability=tuple(
        Level(n, label) for n, label in enumerate(("Rare", "Unlikely", "Possible"), 1)
    ),
    impact=tuple(
        Level(n, label) for n, label in enumerate(("Negligible", "Minor", "Moderate"), 1)
    ),
    areas=(
        ImpactArea("COST", "Cost", {1: "< $50k", 2: "$50k – $250k", 3: "> $250k"}),
        ImpactArea("SCHED", "Schedule", {1: "< 1 week", 2: "1–4 weeks", 3: "> 1 month"}),
    ),
)

#: A three-point scale, deliberately not the default 5x5. Every range check in this file
#: would pass against a hard-coded five-point scale whether or not the code read the
#: configuration, which would make the tests agree with a bug.
ALLOWED = frozenset({"doc_chunk:1", "risk:9"})

SUBJECT = RiskSubject(
    risk_id=4,
    risk_code="NST-TUN-0007",
    title="Consent lapses before tie-in",
    statement="The environmental consent is valid for ninety days from issue.",
    category="ENV-030 Permitting",
)

ITEMS = [
    EvidenceItem(
        kind="doc_chunk",
        ref="doc_chunk:1",
        excerpt="The consent is valid for ninety days from the date of issue.",
        label="Environmental consent",
    ),
    EvidenceItem(
        kind="risk",
        ref="risk:9",
        excerpt="Permit expiry delays commissioning.",
        label="ENV-030-0002 — Riverside Crossing",
        from_other_scope=True,
        assessed="probability 4; COST 3",
    ),
]


def _answer(**overrides) -> str:
    body = {
        "probability": 2,
        "probability_rationale": "The window is short and the tie-in is late.",
        "probability_confidence": 0.6,
        "impacts": {"COST": 3, "SCHED": 2},
        "impact_rationales": {"COST": "Reapplication fee.", "SCHED": "Six weeks."},
        "impact_confidence": 0.5,
        "evidence_refs": ["doc_chunk:1"],
    }
    body.update(overrides)
    return json.dumps(body)


class TestPrompt:
    def test_renders_every_level_of_the_configured_scale(self) -> None:
        rendered = agent.render_scale(SCALE)
        assert "1  Rare" in rendered
        assert "3  Possible" in rendered
        assert "COST — Cost:" in rendered
        assert "$50k – $250k" in rendered

    def test_does_not_invent_levels_the_install_does_not_have(self) -> None:
        # A four or a five would mean the renderer is printing a scale of its own.
        assert "  4  " not in agent.render_scale(SCALE)
        assert "  5  " not in agent.render_scale(SCALE)

    def test_evidence_carries_the_identifier_the_model_must_cite(self) -> None:
        rendered = agent.render_evidence(ITEMS)
        assert "[doc_chunk:1]" in rendered
        assert "[risk:9]" in rendered

    def test_a_comparable_from_elsewhere_says_so_and_shows_its_scores(self) -> None:
        rendered = agent.render_evidence(ITEMS)
        assert "(another project)" in rendered
        assert "Scored there as: probability 4; COST 3" in rendered

    def test_the_message_names_the_risk_and_ends_with_the_object_contract(self) -> None:
        content = agent.build_messages(
            SUBJECT, ITEMS, SCALE, project_name="North Shore Tunnel"
        )
        assert "NST-TUN-0007" in content
        assert "ENV-030 Permitting" in content
        assert content.rstrip().endswith(agent.CLOSING_LINE)

    def test_a_probability_a_person_set_is_declared_and_fenced_off(self) -> None:
        content = agent.build_messages(
            replace(SUBJECT, scored_probability=4),
            ITEMS,
            SCALE,
            project_name="North Shore Tunnel",
        )
        assert "already judged by a person: 4" in content
        assert "return null for probability" in content

    def test_areas_a_person_scored_are_named_rather_than_hidden(self) -> None:
        content = agent.build_messages(
            SUBJECT, ITEMS, SCALE, project_name="P", skip_areas=["SAFE"]
        )
        # Named, not removed from the scale: a model that cannot see safety at all folds
        # safety reasoning into the cost score.
        assert "do not score: SAFE" in content

    def test_the_prompt_forbids_an_overall_impact(self) -> None:
        assert "overall or combined impact" in agent.SYSTEM_PROMPT

    def test_the_prompt_declares_what_a_register_comparable_is(self) -> None:
        assert "not observed outcomes" in agent.SYSTEM_PROMPT


class TestFakeContract:
    """The fake dispatches on a real difference between the two prompts, not a sentinel."""

    def test_the_two_generators_ask_for_different_shapes(self) -> None:
        from app.agents.risk_id import CLOSING_LINE as ARRAY_CLOSING

        assert agent.CLOSING_LINE == OBJECT_CLOSING
        assert ARRAY_CLOSING != OBJECT_CLOSING

    def test_the_ref_marker_matches_what_the_renderer_writes(self) -> None:
        rendered = agent.render_evidence(ITEMS)
        assert set(REF_MARKER.findall(rendered)) == {"doc_chunk:1", "risk:9"}


class TestParse:
    def test_a_clean_answer_survives_whole(self) -> None:
        assessment, drops = agent.parse(
            _answer(), allowed_refs=ALLOWED, scale=SCALE
        )
        assert drops == []
        assert assessment is not None
        assert assessment.probability == 2
        assert assessment.impacts == {"COST": 3, "SCHED": 2}
        assert assessment.impact_rationales["COST"] == "Reapplication fee."
        assert assessment.probability_confidence == 0.6
        assert assessment.impact_confidence == 0.5

    def test_prose_around_the_object_is_tolerated_once(self) -> None:
        raw = "Here is my assessment.\n" + _answer() + "\nLet me know."
        assessment, _ = agent.parse(raw, allowed_refs=ALLOWED, scale=SCALE)
        assert assessment is not None and assessment.probability == 2

    def test_a_fenced_block_is_tolerated(self) -> None:
        assessment, _ = agent.parse(
            "```json\n" + _answer() + "\n```", allowed_refs=ALLOWED, scale=SCALE
        )
        assert assessment is not None

    def test_area_codes_are_normalised_to_upper_case(self) -> None:
        assessment, _ = agent.parse(
            _answer(impacts={"cost": 3}, impact_rationales={"cost": "why"}),
            allowed_refs=ALLOWED,
            scale=SCALE,
        )
        assert assessment is not None
        assert assessment.impacts == {"COST": 3}
        assert assessment.impact_rationales == {"COST": "why"}

    def test_only_citations_that_were_shown_are_kept(self) -> None:
        assessment, _ = agent.parse(
            _answer(evidence_refs=["doc_chunk:1", "doc_chunk:9001"]),
            allowed_refs=ALLOWED,
            scale=SCALE,
        )
        assert assessment is not None
        assert assessment.evidence_refs == ("doc_chunk:1",)

    def test_a_missing_rationale_does_not_cost_the_score(self) -> None:
        assessment, _ = agent.parse(
            _answer(impact_rationales={}), allowed_refs=ALLOWED, scale=SCALE
        )
        assert assessment is not None
        assert assessment.impacts == {"COST": 3, "SCHED": 2}
        assert assessment.impact_rationales == {}


class TestRefusalToScore:
    """Every way a score can arrive with nothing behind it, and its refusal."""

    def test_an_ungrounded_answer_is_dropped_entirely(self) -> None:
        assessment, drops = agent.parse(
            _answer(evidence_refs=["doc_chunk:9001"]),
            allowed_refs=ALLOWED,
            scale=SCALE,
        )
        assert assessment is None
        assert [d.reason for d in drops] == [UNGROUNDED]
        # The invented reference is named, because that sentence is what tells an operator
        # their prompt or their model has a problem.
        assert "doc_chunk:9001" in drops[0].detail

    def test_no_citation_at_all_is_ungrounded(self) -> None:
        assessment, drops = agent.parse(
            _answer(evidence_refs=[]), allowed_refs=ALLOWED, scale=SCALE
        )
        assert assessment is None and drops[0].reason == UNGROUNDED

    def test_a_probability_off_the_scale_is_refused_not_clamped(self) -> None:
        assessment, drops = agent.parse(
            _answer(probability=7), allowed_refs=ALLOWED, scale=SCALE
        )
        assert assessment is not None
        assert assessment.probability is None
        assert [d.reason for d in drops] == [OUT_OF_RANGE]
        assert "1–3" in drops[0].detail

    def test_zero_is_off_the_scale_too(self) -> None:
        _, drops = agent.parse(
            _answer(probability=0), allowed_refs=ALLOWED, scale=SCALE
        )
        assert [d.reason for d in drops] == [OUT_OF_RANGE]

    def test_a_level_between_rungs_is_refused(self) -> None:
        _, drops = agent.parse(
            _answer(probability=2.5), allowed_refs=ALLOWED, scale=SCALE
        )
        assert [d.reason for d in drops] == [OUT_OF_RANGE]

    def test_an_impact_area_this_install_does_not_have_never_lands(self) -> None:
        assessment, drops = agent.parse(
            _answer(impacts={"COST": 3, "CARBON": 2}),
            allowed_refs=ALLOWED,
            scale=SCALE,
        )
        assert assessment is not None
        assert assessment.impacts == {"COST": 3}
        assert [d.reason for d in drops] == [UNKNOWN_AREA]

    def test_an_answer_scoring_nothing_is_a_drop_not_an_empty_proposal(self) -> None:
        assessment, drops = agent.parse(
            _answer(probability=None, impacts={}), allowed_refs=ALLOWED, scale=SCALE
        )
        assert assessment is None
        assert [d.reason for d in drops] == [NOTHING_TO_SCORE]

    def test_unparseable_text_is_a_drop_and_never_an_exception(self) -> None:
        assessment, drops = agent.parse(
            "I am not able to help with that.", allowed_refs=ALLOWED, scale=SCALE
        )
        assert assessment is None and drops[0].reason == UNPARSEABLE

    def test_an_array_where_an_object_was_asked_for_is_refused(self) -> None:
        assessment, drops = agent.parse(
            "[{\"probability\": 2}]", allowed_refs=ALLOWED, scale=SCALE
        )
        assert assessment is None and drops[0].reason == NOT_AN_OBJECT

    def test_a_true_confidence_does_not_become_certainty(self) -> None:
        assessment, _ = agent.parse(
            _answer(probability_confidence=True, impact_confidence=1),
            allowed_refs=ALLOWED,
            scale=SCALE,
        )
        assert assessment is not None
        assert assessment.probability_confidence is None
        assert assessment.impact_confidence == 1.0

    def test_a_confidence_out_of_range_abstains_rather_than_clamping(self) -> None:
        assessment, _ = agent.parse(
            _answer(probability_confidence=7), allowed_refs=ALLOWED, scale=SCALE
        )
        assert assessment is not None and assessment.probability_confidence is None


class TestHumanJudgementIsNotOverwritten:
    def test_a_probability_a_person_set_is_discarded_quietly(self) -> None:
        assessment, drops = agent.parse(
            _answer(), allowed_refs=ALLOWED, scale=SCALE, skip_probability=True
        )
        assert assessment is not None
        assert assessment.probability is None
        assert assessment.impacts == {"COST": 3, "SCHED": 2}
        # Quietly: the drop list exists to tell a reviewer something is wrong, and a model
        # answering a question it was told to skip is not that.
        assert drops == []

    def test_an_area_a_person_scored_is_discarded_quietly(self) -> None:
        assessment, drops = agent.parse(
            _answer(), allowed_refs=ALLOWED, scale=SCALE, skip_areas=["COST"]
        )
        assert assessment is not None
        assert assessment.impacts == {"SCHED": 2}
        assert drops == []

    def test_skipping_everything_leaves_nothing_to_propose(self) -> None:
        assessment, drops = agent.parse(
            _answer(),
            allowed_refs=ALLOWED,
            scale=SCALE,
            skip_areas=["COST", "SCHED"],
            skip_probability=True,
        )
        assert assessment is None
        assert [d.reason for d in drops] == [NOTHING_TO_SCORE]
