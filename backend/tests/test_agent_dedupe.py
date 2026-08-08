"""The two thresholds, and the band between them.

The asymmetry is the design, so it is what gets tested. A false suppression is invisible
and permanent — a real risk that never reaches anyone, with nothing recording that it was
found. A false pass is one inbox row a reviewer rejects in four seconds. So the register
threshold sits well above the batch one, and the gap between them resolves into a citation
rather than into a suppression or a silence.
"""

from __future__ import annotations

from app.agents.dedupe import (
    PRECEDENT_AT,
    SUPPRESS_AT,
    best_match,
    similarity,
)


def _tokens(text: str) -> list[str]:
    return text.lower().split()


class TestSimilarity:
    def test_identical_token_sets_score_one(self) -> None:
        assert similarity(_tokens("permit expiry delay"), _tokens("permit expiry delay")) == 1.0

    def test_disjoint_sets_score_zero(self) -> None:
        assert similarity(_tokens("permit expiry"), _tokens("weather storm")) == 0.0

    def test_an_empty_side_scores_zero_rather_than_dividing_by_nothing(self) -> None:
        assert similarity([], _tokens("permit")) == 0.0
        assert similarity(_tokens("permit"), []) == 0.0

    def test_word_order_does_not_matter(self) -> None:
        """"Permit expires before tie-in" and "tie-in blocked by expired permit" are the
        same risk written two ways; any order-sensitive measure scores them apart."""
        assert similarity(
            _tokens("permit expires before tie-in"),
            _tokens("tie-in before expires permit"),
        ) == 1.0

    def test_repeats_do_not_inflate_the_score(self) -> None:
        assert similarity(
            _tokens("permit permit permit expiry"), _tokens("permit expiry")
        ) == 1.0

    def test_partial_overlap_lands_between(self) -> None:
        score = similarity(
            _tokens("consent lapses delaying tie-in"),
            _tokens("permit expiry delaying tie-in"),
        )
        assert 0.0 < score < 1.0


class TestThresholds:
    def test_suppression_is_stricter_than_precedent(self) -> None:
        """The whole design in one assertion. If these ever meet, the band where a
        reviewer decides disappears."""
        assert SUPPRESS_AT > PRECEDENT_AT

    def test_a_near_copy_suppresses(self) -> None:
        match = best_match(
            _tokens("consent lapses before tie-in delaying commissioning"),
            [
                (
                    "risk:1",
                    "WTR-PLA-0007",
                    _tokens("consent lapses before tie-in delaying commissioning date"),
                )
            ],
        )
        assert match is not None and match.suppresses

    def test_a_related_risk_is_precedent_not_a_duplicate(self) -> None:
        """The band between the thresholds: enough overlap to show the reviewer, not
        enough to decide for them."""
        match = best_match(
            _tokens("consent lapses before tie-in delaying commissioning"),
            [
                (
                    "risk:1",
                    "WTR-PLA-0007",
                    _tokens("consent lapses before tie-in delaying handover"),
                )
            ],
        )
        assert match is not None
        assert match.is_precedent
        assert not match.suppresses

    def test_a_shared_subject_alone_is_not_even_precedent(self) -> None:
        """Two risks about consents are not the same risk. If this ever starts matching,
        the register threshold is suppressing real findings."""
        assert (
            best_match(
                _tokens("consent lapses before tie-in delaying commissioning"),
                [("risk:1", "A-0001", _tokens("consent lapses during winter works"))],
            )
            is None
        )

    def test_an_unrelated_risk_matches_nothing(self) -> None:
        assert (
            best_match(
                _tokens("consent lapses before tie-in"),
                [("risk:1", "WTR-PLA-0007", _tokens("storm damages temporary works"))],
            )
            is None
        )

    def test_the_highest_scorer_wins(self) -> None:
        match = best_match(
            _tokens("consent lapses before tie-in delaying commissioning"),
            [
                ("risk:1", "A-0001", _tokens("consent lapses during winter")),
                (
                    "risk:2",
                    "B-0002",
                    _tokens("consent lapses before tie-in delaying commissioning"),
                ),
            ],
        )
        assert match is not None and match.key == "risk:2"

    def test_ties_break_deterministically_on_the_label(self) -> None:
        """A dedupe that names a different twin on each pass makes its own output
        impossible to diff."""
        known = [
            ("risk:1", "B-0002", _tokens("consent lapses before tie-in")),
            ("risk:2", "A-0001", _tokens("consent lapses before tie-in")),
        ]
        first = best_match(_tokens("consent lapses before tie-in"), known)
        second = best_match(_tokens("consent lapses before tie-in"), list(reversed(known)))
        assert first is not None and second is not None
        assert first.key == second.key

    def test_an_empty_register_matches_nothing(self) -> None:
        assert best_match(_tokens("anything at all"), []) is None

    def test_a_raised_floor_only_returns_suppressions(self) -> None:
        """Same pair as the precedent test above, which is the point: it matches at the
        default floor and not at the suppression one."""
        precedent = [
            (
                "risk:1",
                "A-0001",
                _tokens("consent lapses before tie-in delaying handover"),
            )
        ]
        query = _tokens("consent lapses before tie-in delaying commissioning")
        assert best_match(query, precedent) is not None
        assert best_match(query, precedent, floor=SUPPRESS_AT) is None
