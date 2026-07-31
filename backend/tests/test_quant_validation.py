"""Tests for the pure quantitative validation and derivation core.

The bound-recovery solvers are checked against the CDFs they claim to invert rather than
against stored expected values. A regression that quietly changed the widening would sail
past a golden-number test with the numbers updated to match.
"""

import math

import pytest

from app.services.quant_validation import (
    DISTRIBUTION_GUIDANCE,
    DIST_TYPES,
    DimensionInput,
    EstimateInput,
    absolute_bounds,
    cumulative_moments,
    dimension_moments,
    discrete_moments,
    expected_value,
    pert_moments,
    summarise,
    triangular_moments,
    uniform_bounds,
    uniform_moments,
    validate,
)


def tri_cdf(x: float, a: float, m: float, b: float) -> float:
    """Triangular CDF, used only to verify the solver."""
    if x <= a:
        return 0.0
    if x >= b:
        return 1.0
    if x <= m:
        return (x - a) ** 2 / ((b - a) * (m - a)) if m > a else 0.0
    return 1.0 - (b - x) ** 2 / ((b - a) * (b - m)) if b > m else 1.0


def three_point(dist="pert", lo=100.0, ml=250.0, hi=900.0, **kw) -> DimensionInput:
    return DimensionInput(dist=dist, lo=lo, ml=ml, hi=hi, **kw)


def good(**kw) -> EstimateInput:
    base = dict(
        p_occurrence=0.3,
        cost=three_point(),
        sched=three_point(lo=5.0, ml=15.0, hi=40.0),
    )
    base.update(kw)
    return EstimateInput(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- bounds


def test_absolute_interpretation_is_identity():
    assert absolute_bounds(10.0, 20.0, 60.0, "absolute") == (10.0, 60.0)


@pytest.mark.parametrize(
    "lo,ml,hi",
    [
        (10.0, 20.0, 60.0),
        (0.0, 5.0, 100.0),
        (-50.0, 10.0, 25.0),
        (100.0, 100.0, 400.0),
        (100.0, 400.0, 400.0),
        (1e6, 2.5e6, 9e6),
    ],
)
@pytest.mark.parametrize("interp,p_lo,p_hi", [("p10_p90", 0.10, 0.90), ("p5_p95", 0.05, 0.95)])
def test_solved_bounds_reproduce_the_elicited_quantiles(lo, ml, hi, interp, p_lo, p_hi):
    a, b = absolute_bounds(lo, ml, hi, interp)
    assert a <= lo and b >= hi
    assert tri_cdf(lo, a, ml, b) == pytest.approx(p_lo, abs=1e-9)
    assert tri_cdf(hi, a, ml, b) == pytest.approx(p_hi, abs=1e-9)


def test_p5_p95_widens_less_than_p10_p90():
    a90, b90 = absolute_bounds(10.0, 20.0, 60.0, "p10_p90")
    a95, b95 = absolute_bounds(10.0, 20.0, 60.0, "p5_p95")
    assert a90 < a95 < 10.0
    assert b90 > b95 > 60.0


def test_degenerate_estimate_is_not_widened():
    assert absolute_bounds(7.0, 7.0, 7.0, "p10_p90") == (7.0, 7.0)


@pytest.mark.parametrize("interp,p", [("p10_p90", 0.10), ("p5_p95", 0.05)])
def test_uniform_bounds_reproduce_their_quantiles(interp, p):
    a, b = uniform_bounds(20.0, 80.0, interp)
    span = b - a
    assert (20.0 - a) / span == pytest.approx(p, abs=1e-12)
    assert (80.0 - a) / span == pytest.approx(1.0 - p, abs=1e-12)


def test_uniform_absolute_is_identity():
    assert uniform_bounds(20.0, 80.0, "absolute") == (20.0, 80.0)


def test_unknown_interpretation_rejected():
    with pytest.raises(ValueError):
        absolute_bounds(1.0, 2.0, 3.0, "p1_p99")


# ---------------------------------------------------------------------------- moments


def test_symmetric_standard_pert_uses_exact_variance_not_malcolm():
    m = pert_moments(0.0, 50.0, 100.0, "absolute", 4.0)
    assert m.mean == pytest.approx(50.0)
    assert m.variance == pytest.approx(100.0**2 / 28.0)
    assert m.variance != pytest.approx(100.0**2 / 36.0)


def test_pert_alpha_beta_symmetric_case():
    m = pert_moments(0.0, 50.0, 100.0, "absolute", 4.0)
    assert m.alpha == pytest.approx(3.0) and m.beta == pytest.approx(3.0)


def test_higher_lambda_tightens_the_distribution():
    assert pert_moments(0.0, 50.0, 100.0, "absolute", 8.0).sd < (
        pert_moments(0.0, 50.0, 100.0, "absolute", 2.0).sd
    )


def test_triangular_moments_are_the_textbook_values():
    m = triangular_moments(0.0, 50.0, 100.0)
    assert m.mean == pytest.approx(50.0)
    assert m.variance == pytest.approx(100.0**2 / 24.0)
    assert m.kind == "triangular"


def test_triangular_carries_more_spread_than_pert_on_the_same_points():
    """The whole reason the choice matters: soft bounds in a triangular inflate the tail."""
    tri = triangular_moments(0.0, 50.0, 100.0)
    pert = pert_moments(0.0, 50.0, 100.0)
    assert tri.sd > pert.sd


def test_trigen_is_triangular_with_solved_bounds():
    m = triangular_moments(10.0, 20.0, 60.0, "p10_p90")
    assert m.kind == "trigen"
    assert m.lo < 10.0 and m.hi > 60.0
    assert m.sd > triangular_moments(10.0, 20.0, 60.0).sd


def test_uniform_moments():
    m = uniform_moments(0.0, 100.0)
    assert m.mean == pytest.approx(50.0)
    assert m.variance == pytest.approx(100.0**2 / 12.0)


def test_cumulative_matching_a_uniform_reproduces_uniform_moments():
    pts = [{"x": 0.0, "p": 0.0}, {"x": 100.0, "p": 1.0}]
    m = cumulative_moments(pts)
    assert m.mean == pytest.approx(50.0)
    assert m.variance == pytest.approx(100.0**2 / 12.0)


def test_cumulative_handles_a_vertical_step_as_a_point_mass():
    pts = [
        {"x": 0.0, "p": 0.0},
        {"x": 10.0, "p": 0.5},
        {"x": 10.0, "p": 0.9},
        {"x": 20.0, "p": 1.0},
    ]
    m = cumulative_moments(pts)
    assert math.isfinite(m.mean) and m.sd > 0
    assert 0.0 <= m.mean <= 20.0


def test_discrete_moments():
    pts = [{"x": 0.0, "p": 0.5}, {"x": 100.0, "p": 0.5}]
    m = discrete_moments(pts)
    assert m.mean == pytest.approx(50.0)
    assert m.sd == pytest.approx(50.0)


def test_expected_value_scales_by_occurrence():
    assert expected_value(pert_moments(0.0, 50.0, 100.0), 0.25) == pytest.approx(12.5)


def test_dimension_moments_returns_none_for_unassessed():
    assert dimension_moments(DimensionInput(dist="none"), "absolute") is None


# --------------------------------------------------------------------------- guidance


def test_every_distribution_has_guidance():
    for name in DIST_TYPES:
        g = DISTRIBUTION_GUIDANCE[name]
        assert {"label", "inputs", "summary", "use_when", "avoid_when", "caution"} <= set(g)
        assert all(isinstance(v, str) and v.strip() for v in g.values())


def test_guidance_input_kinds_are_recognised():
    assert {g["inputs"] for g in DISTRIBUTION_GUIDANCE.values()} == {
        "three_point",
        "bounds_only",
        "points",
        "none",
    }


# ------------------------------------------------------------------------- validation


def test_well_formed_estimate_passes():
    assert validate(good()).ok


def test_out_of_order_three_point_is_an_error():
    est = good(cost=three_point(ml=50.0))
    assert not validate(est).ok


def test_partial_three_point_is_an_error():
    est = good(cost=DimensionInput(dist="pert", lo=1.0, ml=2.0))
    result = validate(est)
    assert not result.ok
    assert any("missing" in i.message for i in result.errors)


def test_estimate_with_no_dimension_is_an_error():
    result = validate(EstimateInput(p_occurrence=0.5))
    assert not result.ok
    assert any("cannot be simulated" in i.message for i in result.errors)


def test_one_dimension_alone_is_enough():
    assert validate(good(sched=DimensionInput(dist="none"))).ok


@pytest.mark.parametrize("p", [0.0, -0.1, 1.5, float("nan")])
def test_probability_outside_range_is_an_error(p):
    assert not validate(good(p_occurrence=p)).ok


def test_variability_must_be_certain():
    assert not validate(good(is_variability=True, p_occurrence=0.4)).ok
    assert validate(good(is_variability=True, p_occurrence=1.0)).ok


def test_certain_risk_event_warns_but_saves():
    result = validate(good(p_occurrence=1.0))
    assert result.ok
    assert any(i.field == "is_variability" for i in result.warnings)


# -- shape/interpretation coherence ----------------------------------------------


def test_triangular_with_percentile_bounds_is_rejected():
    result = validate(good(bound_interpretation="p10_p90", cost=three_point("triangular")))
    assert not result.ok
    assert any("trigen" in i.message for i in result.errors)


def test_trigen_with_absolute_bounds_is_rejected():
    result = validate(good(cost=three_point("trigen")))
    assert not result.ok
    assert any("triangular" in i.message for i in result.errors)


def test_trigen_with_percentile_bounds_passes():
    assert validate(
        good(
            bound_interpretation="p10_p90",
            cost=three_point("trigen"),
            sched=three_point("trigen", lo=5.0, ml=15.0, hi=40.0),
        )
    ).ok


def test_pert_is_agnostic_to_interpretation():
    assert validate(good(bound_interpretation="p10_p90")).ok
    assert validate(good(bound_interpretation="absolute")).ok


def test_dimensions_may_take_different_shapes():
    est = good(
        cost=three_point("pert"),
        sched=DimensionInput(dist="uniform", lo=5.0, hi=40.0),
    )
    result = validate(est)
    assert result.ok
    out = summarise(est)
    assert out["cost"]["kind"] == "pert"
    assert out["sched"]["kind"] == "uniform"


# -- uniform ---------------------------------------------------------------------


def test_uniform_needs_only_bounds():
    assert validate(good(cost=DimensionInput(dist="uniform", lo=10.0, hi=90.0))).ok


def test_uniform_ignores_a_mode_and_says_so():
    result = validate(good(cost=DimensionInput(dist="uniform", lo=10.0, ml=40.0, hi=90.0)))
    assert result.ok
    assert any("ignored" in i.message for i in result.warnings)


def test_uniform_with_inverted_bounds_is_an_error():
    assert not validate(good(cost=DimensionInput(dist="uniform", lo=90.0, hi=10.0))).ok


# -- cumulative ------------------------------------------------------------------


def cumul(points) -> DimensionInput:
    return DimensionInput(dist="cumulative", points=points)


CURVE = [
    {"x": 0.0, "p": 0.0},
    {"x": 50.0, "p": 0.3},
    {"x": 120.0, "p": 0.8},
    {"x": 400.0, "p": 1.0},
]


def test_valid_cumulative_curve_passes():
    assert validate(good(cost=cumul(CURVE))).ok


def test_cumulative_must_start_at_zero_and_end_at_one():
    bad = [{"x": 0.0, "p": 0.1}, {"x": 10.0, "p": 0.9}]
    result = validate(good(cost=cumul(bad)))
    assert not result.ok
    assert any("unaccounted" in i.message for i in result.errors)


def test_cumulative_probability_must_increase():
    bad = [{"x": 0.0, "p": 0.0}, {"x": 10.0, "p": 0.5}, {"x": 20.0, "p": 0.4}, {"x": 30.0, "p": 1.0}]
    assert not validate(good(cost=cumul(bad))).ok


def test_cumulative_values_must_not_decrease():
    bad = [{"x": 0.0, "p": 0.0}, {"x": 30.0, "p": 0.5}, {"x": 10.0, "p": 0.8}, {"x": 40.0, "p": 1.0}]
    assert not validate(good(cost=cumul(bad))).ok


def test_sparse_cumulative_warns():
    result = validate(good(cost=cumul([{"x": 0.0, "p": 0.0}, {"x": 100.0, "p": 1.0}])))
    assert result.ok
    assert any("close to uniform" in i.message for i in result.warnings)


def test_single_point_is_not_a_distribution():
    assert not validate(good(cost=cumul([{"x": 1.0, "p": 1.0}]))).ok


def test_malformed_points_are_reported_not_raised():
    result = validate(good(cost=cumul([{"x": 1.0}, {"p": 1.0}])))
    assert not result.ok
    assert any("numeric" in i.message for i in result.errors)


# -- discrete --------------------------------------------------------------------


def disc(points) -> DimensionInput:
    return DimensionInput(dist="discrete", points=points)


def test_valid_discrete_passes():
    assert validate(
        good(cost=disc([{"x": 50.0, "p": 0.6}, {"x": 300.0, "p": 0.4}]))
    ).ok


def test_discrete_masses_must_sum_to_one():
    result = validate(good(cost=disc([{"x": 50.0, "p": 0.6}, {"x": 300.0, "p": 0.2}])))
    assert not result.ok
    assert any("sum to 1" in i.message for i in result.errors)


def test_discrete_rejects_non_positive_mass():
    assert not validate(good(cost=disc([{"x": 50.0, "p": 1.0}, {"x": 300.0, "p": 0.0}]))).ok


def test_discrete_rejects_duplicate_outcomes():
    assert not validate(good(cost=disc([{"x": 50.0, "p": 0.5}, {"x": 50.0, "p": 0.5}]))).ok


def test_discrete_zero_outcome_warns_about_double_counting():
    result = validate(good(cost=disc([{"x": 0.0, "p": 0.5}, {"x": 300.0, "p": 0.5}])))
    assert result.ok
    assert any("double-counts" in i.message for i in result.warnings)


# -- rationale -------------------------------------------------------------------


def test_rationale_is_accepted_on_each_point():
    est = good(
        cost=three_point(
            rationale={
                "min": {"text": "Best case if the permit lands first pass", "source": "sme"},
                "ml": {"text": "Two comparable jobs on this corridor", "source": "historical"},
                "max": {"text": "Full redesign plus remobilisation", "source": "sme"},
            }
        )
    )
    assert validate(est).ok


def test_unknown_rationale_key_is_rejected():
    est = good(cost=three_point(rationale={"median": {"text": "no such point"}}))
    assert not validate(est).ok


def test_agent_written_rationale_warns_until_a_human_owns_it():
    est = good(
        cost=three_point(rationale={"ml": {"text": "Drafted from the tender pack", "source": "agent_proposal"}})
    )
    result = validate(est)
    assert result.ok
    assert any("AI proposal" in i.message for i in result.warnings)


def test_overlong_rationale_is_rejected():
    est = good(cost=three_point(rationale={"ml": {"text": "x" * 5000}}))
    assert not validate(est).ok


def test_bad_rationale_source_is_rejected():
    est = good(cost=three_point(rationale={"ml": {"text": "hi", "source": "hearsay"}}))
    assert not validate(est).ok


# -- elicitation-quality warnings ------------------------------------------------


def test_low_confidence_absolute_bounds_nudges_toward_trigen():
    result = validate(good(confidence="low"))
    assert result.ok
    assert any("trigen" in i.message for i in result.warnings)


def test_rare_event_warns():
    result = validate(good(p_occurrence=0.01))
    assert result.ok and any("Rare" in i.message for i in result.warnings)


def test_wild_skew_warns():
    result = validate(good(cost=three_point(lo=100.0, ml=110.0, hi=500_000.0)))
    assert result.ok and any("skewed" in i.message for i in result.warnings)


def test_range_spanning_zero_warns():
    result = validate(good(cost=three_point(lo=-50_000.0, ml=10_000.0, hi=200_000.0)))
    assert result.ok and any("spans zero" in i.message for i in result.warnings)


@pytest.mark.parametrize(
    "field_name,bad",
    [
        ("bound_interpretation", "p1_p99"),
        ("cost_basis", "guess"),
        ("sched_day_basis", "elapsed"),
        ("source", "vibes"),
        ("confidence", "certain"),
    ],
)
def test_unknown_vocabulary_rejected(field_name, bad):
    result = validate(good(**{field_name: bad}))
    assert not result.ok
    assert any(i.field == field_name for i in result.errors)


def test_unknown_distribution_rejected():
    assert not validate(good(cost=three_point("lognormal"))).ok


def test_non_finite_value_rejected():
    assert not validate(good(cost=three_point(hi=math.inf))).ok


# --------------------------------------------------------------------------- summarise


def test_summarise_covers_both_dimensions():
    out = summarise(good())
    assert out["cost"]["expected_value"] == pytest.approx(out["cost"]["mean"] * 0.3)
    assert out["sched"]["alpha"] > 1


def test_summarise_flags_widening_so_the_form_can_show_it():
    plain = summarise(good())
    widened = summarise(good(bound_interpretation="p10_p90"))
    assert plain["cost"]["widened"] is False
    assert widened["cost"]["widened"] is True
    assert widened["cost"]["elicited_lo"] == 100.0
    assert widened["cost"]["lo"] < 100.0


def test_summarise_omits_an_absent_dimension():
    assert summarise(good(sched=DimensionInput(dist="none")))["sched"] is None
