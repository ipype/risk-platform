"""Tests for the pure suggestion core.

No DB fixtures here on purpose — if a test in this file needs a session, the scoring has
stopped being pure and that is the bug worth failing on.
"""

from __future__ import annotations

import pytest

from app.services.mapping_suggest import (
    WEIGHTS,
    ActivityCorpus,
    ActivityRow,
    Candidate,
    Precedent,
    RiskRow,
    has_errors,
    materiality_of,
    resolve_scope,
    suggest,
    tokenize,
    validate_duration_driver,
    validate_inserted_activity,
    validate_scope,
)


def act(
    source_id: str,
    name: str,
    *,
    code: str = "",
    type: str = "task",
    status: str = "not_started",
    wbs: str | None = "W1",
    wbs_path: str = "",
    float_days: float | None = 30.0,
    critical: bool = False,
    remaining: float | None = 10.0,
    constraint: str = "none",
) -> ActivityRow:
    return ActivityRow(
        source_id=source_id,
        code=code or source_id,
        name=name,
        type=type,
        status=status,
        wbs_source_id=wbs,
        wbs_path=wbs_path,
        original_duration_days=remaining,
        remaining_duration_days=remaining,
        total_float_days=float_days,
        is_critical=critical,
        constraint_type=constraint,
    )


SCHEDULE = [
    act("A1", "Submit environmental permit application", wbs="W-PERMIT"),
    act("A2", "Regulator review of permit submission", wbs="W-PERMIT"),
    act("A3", "Respond to regulator comments", wbs="W-PERMIT"),
    act("A4", "Receive environmental approval", type="milestone_finish", wbs="W-PERMIT", remaining=0),
    act("A5", "Excavate main foundation", wbs="W-CIVIL", critical=True, float_days=0.0),
    act("A6", "Pour foundation concrete", wbs="W-CIVIL", critical=True, float_days=0.0),
    act("A7", "Install structural steel", wbs="W-STEEL"),
    act("A8", "Commission control system", wbs="W-COMM"),
    act("A9", "Dewatering system setup", wbs="W-CIVIL"),
    act("A10", "Project management", type="loe", wbs="W-MGMT"),
]

CORPUS = ActivityCorpus(SCHEDULE)


def permit_risk(**kw) -> RiskRow:
    base = dict(
        risk_id=1,
        risk_code="REG-010-0001",
        title="Environmental permit approval delayed by regulator",
        causes="Incomplete submission; regulator backlog",
        consequences="Construction start pushed back",
        category_code="REG",
        category_name="Regulatory",
        subcategory_id=7,
    )
    base.update(kw)
    return RiskRow(**base)


# --------------------------------------------------------------------------- #
# tokenisation
# --------------------------------------------------------------------------- #


def test_tokenize_drops_stopwords_and_filler():
    got = tokenize("The risk of delay to the construction activity")
    assert "the" not in got and "risk" not in got and "delay" not in got
    assert "construction" in got


def test_tokenize_keeps_meaningful_short_tokens():
    assert "ifc" in tokenize("Issue IFC drawings")
    assert "ea" in tokenize("EA submission")


def test_stemming_joins_word_forms():
    assert set(tokenize("installation")) == set(tokenize("install"))


def test_tokenize_empty_is_empty():
    assert tokenize(None) == []
    assert tokenize("   ") == []


# --------------------------------------------------------------------------- #
# ranking
# --------------------------------------------------------------------------- #


def test_permit_risk_ranks_permit_activities_first():
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS)
    assert top, "expected at least one candidate"
    assert top[0].activity.source_id in {"A1", "A2"}
    assert {c.activity.source_id for c in top[:3]} <= {"A1", "A2", "A3", "A4"}


def test_unrelated_activities_score_below_related_ones():
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS, min_score=0.0)
    by_id = {c.activity.source_id: c.score for c in top}
    assert by_id["A1"] > by_id.get("A6", 0.0)


def test_completed_and_summary_activities_are_never_suggested():
    schedule = SCHEDULE + [
        act("A11", "Submit environmental permit package", status="completed"),
        act("A12", "Permitting summary", type="wbs_summary"),
    ]
    top, _ = suggest(permit_risk(), schedule, ActivityCorpus(schedule))
    ids = {c.activity.source_id for c in top}
    assert "A11" not in ids and "A12" not in ids


def test_already_mapped_activities_are_dropped():
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS, already_mapped=frozenset({"A1"}))
    assert "A1" not in {c.activity.source_id for c in top}


def test_risk_with_no_informative_text_returns_nothing():
    top, _ = suggest(permit_risk(title="The risk", causes=None, consequences=None,
                                description=None, subcategory_name=None),
                     SCHEDULE, CORPUS)
    assert top == []


def test_limit_is_respected():
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS, limit=2, min_score=0.0)
    assert len(top) <= 2


# --------------------------------------------------------------------------- #
# abstention — the reason scores stay comparable across installs
# --------------------------------------------------------------------------- #


def test_precedent_abstains_when_there_is_no_evidence():
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS)
    assert top[0].signals["precedent"] is None


def test_unknown_category_abstains_rather_than_scoring_zero():
    """With every other signal abstaining, the blend must equal the surviving signal.

    The failure mode this guards is treating ``None`` as 0.0, which would return
    ``0.45 * lexical`` and make every score on a register outside the lexicon look weak
    regardless of how well it actually matched.
    """
    unknown, _ = suggest(
        permit_risk(category_code="ZZZ", category_name="Nonsense"), SCHEDULE, CORPUS
    )
    top = unknown[0]
    assert top.signals["taxonomy"] is None
    assert top.signals["precedent"] is None
    assert top.signals["wbs_affinity"] is None
    lexical = top.signals["lexical"]
    assert top.score == pytest.approx(lexical)
    assert top.score > lexical * WEIGHTS["lexical"]  # the None-as-zero result


def test_a_live_strong_signal_still_raises_the_score():
    """Abstention keeps scores honest; it does not make signals worthless."""
    known, _ = suggest(permit_risk(), SCHEDULE, CORPUS)
    unknown, _ = suggest(
        permit_risk(category_code="ZZZ", category_name="Nonsense"), SCHEDULE, CORPUS
    )
    assert known[0].signals["taxonomy"] == 1.0
    assert known[0].score > unknown[0].score


def test_precedent_promotes_previously_accepted_vocabulary():
    prec = Precedent()
    for _ in range(6):
        prec.accepts["dewater"] += 1
    plain, _ = suggest(permit_risk(), SCHEDULE, CORPUS, min_score=0.0)
    boosted, _ = suggest(
        permit_risk(), SCHEDULE, CORPUS, precedent=prec, min_score=0.0
    )
    before = {c.activity.source_id: c.score for c in plain}
    after = {c.activity.source_id: c.score for c in boosted}
    assert after["A9"] > before["A9"]


def test_precedent_never_invents_a_candidate_from_rejections():
    prec = Precedent()
    prec.rejects["concrete"] += 5
    assert prec.score(["concrete"]) == 0.0


def test_wbs_affinity_abstains_when_the_risk_has_no_accepted_mappings():
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS)
    assert top[0].signals["wbs_affinity"] is None


def test_wbs_affinity_lifts_activities_in_an_already_accepted_branch():
    plain, _ = suggest(permit_risk(), SCHEDULE, CORPUS, min_score=0.0)
    lifted, _ = suggest(
        permit_risk(), SCHEDULE, CORPUS, accepted_wbs=frozenset({"W-PERMIT"}),
        min_score=0.0,
    )
    assert {c.activity.source_id for c in lifted[:2]} <= {"A1", "A2", "A3", "A4"}
    assert max(c.score for c in lifted) >= max(c.score for c in plain) * 0.9


# --------------------------------------------------------------------------- #
# relevance and materiality stay on separate axes
# --------------------------------------------------------------------------- #


def test_critical_path_does_not_buy_relevance():
    """A6 is critical and irrelevant; A1 is off the critical path and relevant."""
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS, min_score=0.0)
    by_id = {c.activity.source_id: c for c in top}
    assert by_id["A1"].score > by_id["A6"].score
    assert by_id["A6"].materiality["band"] == "high"
    assert by_id["A1"].materiality["band"] in {"low", "medium"}


def test_materiality_bands():
    assert materiality_of(act("x", "n", critical=True, float_days=0.0))["band"] == "high"
    assert materiality_of(act("x", "n", float_days=5.0))["band"] == "medium"
    assert materiality_of(act("x", "n", float_days=120.0))["band"] == "low"
    assert materiality_of(act("x", "n", float_days=None))["band"] == "unknown"


def test_milestone_is_recommended_as_an_inserted_activity():
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS, min_score=0.0)
    milestone = next(c for c in top if c.activity.source_id == "A4")
    assert milestone.recommended_type == "inserted_activity"


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def test_duration_driver_on_a_milestone_is_an_error():
    w = validate_duration_driver(act("m", "Approval received", type="milestone_finish"))
    assert has_errors(w)


def test_duration_driver_on_a_complete_activity_is_an_error():
    assert has_errors(validate_duration_driver(act("c", "Done", status="completed")))


def test_level_of_effort_warns_but_does_not_block():
    w = validate_duration_driver(act("l", "Project management", type="loe"))
    assert w and not has_errors(w)


def test_hard_constraint_warns():
    w = validate_duration_driver(act("h", "Handover", constraint="mandatory_finish"))
    assert any("constraint" in x for x in w)
    assert not has_errors(w)


def test_high_float_warns_about_absorption():
    w = validate_duration_driver(act("f", "Landscaping", float_days=180.0))
    assert any("float" in x for x in w)


def test_clean_activity_produces_no_warnings():
    assert validate_duration_driver(act("ok", "Install steel", float_days=10.0)) == []


def test_inserted_activity_requires_two_distinct_existing_endpoints():
    a, b = act("p", "Design"), act("s", "Build")
    assert has_errors(validate_inserted_activity(a, None, True))
    assert has_errors(validate_inserted_activity(a, a, True))
    assert not has_errors(validate_inserted_activity(a, b, True))


def test_inserted_activity_without_an_existing_link_warns():
    w = validate_inserted_activity(act("p", "Design"), act("s", "Build"), linked=False)
    assert w and not has_errors(w)


def test_empty_scope_is_an_error():
    assert has_errors(validate_scope([]))


def test_broad_scope_warns():
    many = [act(f"x{i}", f"Activity {i}") for i in range(250)]
    w = validate_scope(many)
    assert any("broad" in x for x in w)
    assert not has_errors(w)


# --------------------------------------------------------------------------- #
# scope
# --------------------------------------------------------------------------- #


def test_scope_suggestion_offered_when_candidates_cluster_in_one_branch():
    civil = [act(f"C{i}", f"Concrete pour section {i} foundation", wbs="W-CIVIL")
             for i in range(8)]
    schedule = civil + [act("Z1", "Commission controls", wbs="W-COMM")]
    risk = RiskRow(
        risk_id=2,
        risk_code="CON-020-0001",
        title="Concrete foundation pour productivity below plan",
        causes="Crew availability",
        consequences="Foundation works extend",
        category_code="CON",
        category_name="Construction",
    )
    top, scope = suggest(risk, schedule, ActivityCorpus(schedule))
    assert scope is not None
    assert scope.field == "wbs" and scope.value == "W-CIVIL"
    assert scope.total_in_scope == 8


def test_no_scope_suggestion_when_candidates_are_scattered():
    _, scope = suggest(permit_risk(), SCHEDULE, CORPUS)
    assert scope is None or scope.value == "W-PERMIT"


def test_resolve_scope_operators():
    assert len(resolve_scope({"field": "wbs", "op": "equals", "value": "W-CIVIL"}, SCHEDULE)) == 3
    assert len(resolve_scope({"field": "name", "op": "contains", "value": "permit"}, SCHEDULE)) == 2
    assert resolve_scope(None, SCHEDULE) == []
    assert resolve_scope({"field": "wbs", "op": "equals", "value": "nope"}, SCHEDULE) == []


def test_resolve_scope_is_case_insensitive():
    assert resolve_scope({"field": "wbs", "op": "equals", "value": "w-civil"}, SCHEDULE)


# --------------------------------------------------------------------------- #
# empty and degenerate inputs
# --------------------------------------------------------------------------- #


def test_empty_schedule_returns_no_candidates():
    top, scope = suggest(permit_risk(), [], ActivityCorpus([]))
    assert top == [] and scope is None


def test_activity_with_an_unusable_name_is_skipped():
    schedule = [act("N1", "1234"), act("N2", "Submit permit application")]
    top, _ = suggest(permit_risk(), schedule, ActivityCorpus(schedule))
    assert {c.activity.source_id for c in top} == {"N2"}


def test_candidate_serialises_to_plain_json_types():
    top, _ = suggest(permit_risk(), SCHEDULE, CORPUS)
    d = top[0].as_dict()
    assert isinstance(d["score"], float)
    assert set(d["signals"]) == {"lexical", "taxonomy", "wbs_affinity", "precedent"}
    assert isinstance(d["materiality"], dict)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
