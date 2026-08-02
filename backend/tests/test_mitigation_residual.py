"""The residual projection, tested without a database.

`residual_fields` is where every argument a reviewer would have about a mitigation module
actually lives: what a factor multiplies, what it must not multiply, what happens to a
risk nobody treated, and what happens when the declared residual is nonsense. None of that
needs a session, so none of it is tested through one.

The first test in this file is the one that matters most. A residual register that only
contains the treated risks produces a post-mitigation contingency lower than the truth,
with nothing in the output to say so.
"""

from __future__ import annotations

import pytest

from app.services.mitigation_plan import (
    BASE_FIELDS,
    ResidualLine,
    Treatment,
    expected_impact,
    fingerprint,
    residual_fields,
)


def base(**overrides):
    """A fully populated pre-mitigation estimate, in the shape the service reads."""
    fields = {
        "is_variability": False,
        "bound_interpretation": "absolute",
        "cost_dist": "pert",
        "cost_pert_lambda": 4.0,
        "cost_basis": "absolute",
        "sched_dist": "pert",
        "sched_pert_lambda": 4.0,
        "sched_day_basis": "working",
        "confidence": "medium",
        "p_occurrence": 0.4,
        "cost_min": 100_000.0,
        "cost_ml": 250_000.0,
        "cost_max": 900_000.0,
        "cost_points": None,
        "sched_min": 5.0,
        "sched_ml": 15.0,
        "sched_max": 40.0,
        "sched_points": None,
    }
    fields.update(overrides)
    assert set(fields) == set(BASE_FIELDS), "test fixture drifted from BASE_FIELDS"
    return fields


# ------------------------------------------------------------------ carry-through rule


def test_a_risk_with_no_treatment_is_carried_through_unchanged():
    """The residual register is the whole register, not the treated part of it.

    Dropping untreated risks understates residual contingency, and does it invisibly.
    """
    start = base()
    out, issues = residual_fields(start, None)
    assert out == start
    assert issues == []


def test_accept_is_carried_through_unchanged_too():
    out, issues = residual_fields(base(), Treatment(treatment="accept", cost_factor=0.1))
    assert out == base()
    assert issues == []


def test_retire_produces_no_residual_at_all():
    """Elimination is the absence of a row, not a row with a probability of zero.

    ``risk_quant_estimate`` requires a positive occurrence probability, so a zeroed
    residual could not be persisted even if it were meaningful.
    """
    out, issues = residual_fields(base(), Treatment(treatment="retire"))
    assert out is None
    assert issues == []


# ------------------------------------------------------------------------ factor mode


def test_factors_scale_probability_and_both_dimensions():
    out, issues = residual_fields(
        base(), Treatment(p_factor=0.5, cost_factor=0.6, sched_factor=0.25)
    )
    assert issues == []
    assert out["p_occurrence"] == pytest.approx(0.2)
    assert out["cost_min"] == pytest.approx(60_000.0)
    assert out["cost_ml"] == pytest.approx(150_000.0)
    assert out["cost_max"] == pytest.approx(540_000.0)
    assert out["sched_min"] == pytest.approx(1.25)
    assert out["sched_max"] == pytest.approx(10.0)


def test_a_factor_of_one_leaves_its_dimension_exactly_alone():
    """"This action shortens the delay but does not touch the cost" has to be sayable."""
    out, _ = residual_fields(base(), Treatment(cost_factor=1.0, sched_factor=0.5))
    for key in ("cost_min", "cost_ml", "cost_max"):
        assert out[key] == base()[key]
    assert out["sched_max"] == pytest.approx(20.0)


def test_scaling_preserves_the_min_ml_max_ordering():
    """A positive multiplier cannot reorder a sorted triple, and the schema depends on it."""
    for factor in (0.01, 0.1, 0.5, 0.999, 1.0):
        out, _ = residual_fields(base(), Treatment(cost_factor=factor))
        assert out["cost_min"] <= out["cost_ml"] <= out["cost_max"]


def test_an_unassessed_dimension_stays_unassessed():
    start = base(sched_dist="none", sched_min=None, sched_ml=None, sched_max=None)
    out, issues = residual_fields(start, Treatment(sched_factor=0.5))
    assert out["sched_dist"] == "none"
    assert out["sched_min"] is None
    assert issues == []


def test_point_shapes_scale_their_values_and_not_their_probabilities():
    """`p` is a probability. Multiplying it by a cost factor breaks the distribution."""
    points = [{"x": 0.0, "p": 0.5}, {"x": 200_000.0, "p": 0.3}, {"x": 800_000.0, "p": 0.2}]
    start = base(
        cost_dist="discrete",
        cost_points=points,
        cost_min=None,
        cost_ml=None,
        cost_max=None,
    )
    out, issues = residual_fields(start, Treatment(cost_factor=0.25))
    assert issues == []
    assert [pt["x"] for pt in out["cost_points"]] == [0.0, 50_000.0, 200_000.0]
    assert [pt["p"] for pt in out["cost_points"]] == [0.5, 0.3, 0.2]
    # and the source list is untouched
    assert points[1]["x"] == 200_000.0


# ---------------------------------------------------------------------- variability


def test_variability_keeps_certain_occurrence_and_says_the_factor_was_ignored():
    """Inherent spread on a base estimate is not an event whose likelihood can be cut."""
    start = base(is_variability=True, p_occurrence=1.0)
    out, issues = residual_fields(start, Treatment(p_factor=0.5, cost_factor=0.8))
    assert out["p_occurrence"] == 1.0
    assert any("variability" in i for i in issues)
    # the range still moves, which is the thing that can actually be mitigated
    assert out["cost_max"] == pytest.approx(720_000.0)


# ------------------------------------------------------------------- absolute mode


def test_absolute_residual_replaces_only_what_was_declared():
    out, issues = residual_fields(
        base(),
        Treatment(mode="absolute", residual_p=0.1, residual_cost_max=400_000.0),
    )
    assert issues == []
    assert out["p_occurrence"] == pytest.approx(0.1)
    assert out["cost_max"] == pytest.approx(400_000.0)
    # not declared, so the elicited numbers stand
    assert out["cost_min"] == pytest.approx(100_000.0)
    assert out["cost_ml"] == pytest.approx(250_000.0)
    assert out["sched_max"] == pytest.approx(40.0)


def test_an_unordered_absolute_residual_is_refused_and_the_baseline_stands():
    """The conservative direction: too large is visible, absent is not."""
    out, issues = residual_fields(
        base(), Treatment(mode="absolute", residual_cost_max=50_000.0)
    )
    assert out["cost_min"] == pytest.approx(100_000.0)
    assert out["cost_max"] == pytest.approx(900_000.0)
    assert any("ordered" in i for i in issues)


def test_an_absolute_residual_cannot_rewrite_a_curve():
    points = [{"x": 100.0, "p": 0.1}, {"x": 500.0, "p": 0.9}]
    start = base(
        cost_dist="cumulative",
        cost_points=points,
        cost_min=None,
        cost_ml=None,
        cost_max=None,
    )
    out, issues = residual_fields(
        start, Treatment(mode="absolute", residual_cost_max=200.0)
    )
    assert out["cost_points"] == points
    assert any("cumulative" in i for i in issues)


# --------------------------------------------------------------------- summary numbers


def test_a_reduction_never_raises_the_expected_impact():
    start = base()
    out, _ = residual_fields(start, Treatment(p_factor=0.5, cost_factor=0.5))
    before = expected_impact(start, "cost")
    after = expected_impact(out, "cost")
    assert before is not None and after is not None
    assert after < before
    assert after == pytest.approx(before * 0.25)


def test_expected_impact_is_none_for_an_unassessed_dimension():
    assert expected_impact(base(sched_dist="none"), "sched") is None


# ----------------------------------------------------------------------- fingerprint


def _line(code, treatment="reduce", **residual):
    fields = base(**residual) if residual else base()
    return ResidualLine(
        risk_id=1,
        risk_code=code,
        title="x",
        treatment=treatment,
        residual=fields,
        base_p=0.4,
        residual_p=fields["p_occurrence"],
        base_cost_ev=None,
        residual_cost_ev=None,
        base_sched_ev=None,
        residual_sched_ev=None,
    )


def test_fingerprint_ignores_row_order():
    a, b = _line("A-001"), _line("B-002")
    assert fingerprint([a, b]) == fingerprint([b, a])


def test_fingerprint_moves_when_a_residual_moves():
    before = fingerprint([_line("A-001")])
    after = fingerprint([_line("A-001", cost_max=500_000.0)])
    assert before != after


def test_fingerprint_distinguishes_a_retired_risk_from_an_absent_one():
    retired = ResidualLine(
        risk_id=1,
        risk_code="A-001",
        title="x",
        treatment="retire",
        residual=None,
        base_p=0.4,
        residual_p=None,
        base_cost_ev=None,
        residual_cost_ev=None,
        base_sched_ev=None,
        residual_sched_ev=None,
    )
    assert fingerprint([retired]) != fingerprint([])
    assert fingerprint([retired]) != fingerprint([_line("A-001")])
