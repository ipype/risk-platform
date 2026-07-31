"""Tests for the pure quantitative validation and derivation core.

The bound-widening solver is checked against the triangular CDF it claims to invert
rather than against stored expected values. A regression that quietly changed the
widening would sail past a golden-number test with the numbers updated to match.
"""

import math

import pytest

from app.services.quant_validation import (
    EstimateInput,
    absolute_bounds,
    expected_value,
    pert_moments,
    summarise,
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


# ---------------------------------------------------------------------------- bounds


def test_absolute_interpretation_is_identity():
    assert absolute_bounds(10.0, 20.0, 60.0, "absolute") == (10.0, 60.0)


@pytest.mark.parametrize(
    "lo,ml,hi",
    [
        (10.0, 20.0, 60.0),
        (0.0, 5.0, 100.0),
        (-50.0, 10.0, 25.0),
        (100.0, 100.0, 400.0),  # mode at the low bound
        (100.0, 400.0, 400.0),  # mode at the high bound
        (1e6, 2.5e6, 9e6),
    ],
)
@pytest.mark.parametrize("interp,p_lo,p_hi", [("p10_p90", 0.10, 0.90), ("p5_p95", 0.05, 0.95)])
def test_solved_bounds_reproduce_the_elicited_quantiles(lo, ml, hi, interp, p_lo, p_hi):
    a, b = absolute_bounds(lo, ml, hi, interp)

    assert a <= lo, "widened lower bound must sit at or below the elicited low"
    assert b >= hi, "widened upper bound must sit at or above the elicited high"

    assert tri_cdf(lo, a, ml, b) == pytest.approx(p_lo, abs=1e-9)
    assert tri_cdf(hi, a, ml, b) == pytest.approx(p_hi, abs=1e-9)


def test_p10_p90_widens_further_than_p5_p95_does_not_hold_backwards():
    """P5/P95 excludes less tail, so it must widen *less* than P10/P90."""
    a90, b90 = absolute_bounds(10.0, 20.0, 60.0, "p10_p90")
    a95, b95 = absolute_bounds(10.0, 20.0, 60.0, "p5_p95")
    assert a90 < a95 < 10.0
    assert b90 > b95 > 60.0


def test_degenerate_estimate_is_not_widened():
    assert absolute_bounds(7.0, 7.0, 7.0, "p10_p90") == (7.0, 7.0)


def test_unknown_interpretation_rejected():
    with pytest.raises(ValueError):
        absolute_bounds(1.0, 2.0, 3.0, "p1_p99")


def test_bounds_out_of_order_rejected():
    with pytest.raises(ValueError):
        absolute_bounds(5.0, 2.0, 9.0, "absolute")


# ---------------------------------------------------------------------------- moments


def test_symmetric_standard_pert_uses_exact_variance_not_malcolm():
    m = pert_moments(0.0, 50.0, 100.0, "absolute", 4.0)
    assert m.mean == pytest.approx(50.0)
    # exact Beta-PERT: (b - a)^2 / 28, not Malcolm's / 36
    assert m.variance == pytest.approx(100.0**2 / 28.0)
    assert m.variance != pytest.approx(100.0**2 / 36.0)


def test_alpha_beta_symmetric_case():
    m = pert_moments(0.0, 50.0, 100.0, "absolute", 4.0)
    assert m.alpha == pytest.approx(3.0)
    assert m.beta == pytest.approx(3.0)


def test_skewed_mean_leans_toward_the_mode():
    m = pert_moments(10.0, 20.0, 100.0, "absolute", 4.0)
    assert m.mean == pytest.approx((10.0 + 4 * 20.0 + 100.0) / 6.0)
    assert m.mean < (10.0 + 100.0) / 2.0


def test_higher_lambda_tightens_the_distribution():
    loose = pert_moments(0.0, 50.0, 100.0, "absolute", 2.0)
    tight = pert_moments(0.0, 50.0, 100.0, "absolute", 8.0)
    assert tight.sd < loose.sd


def test_widened_bounds_raise_the_spread():
    hard = pert_moments(10.0, 20.0, 60.0, "absolute")
    widened = pert_moments(10.0, 20.0, 60.0, "p10_p90")
    assert widened.sd > hard.sd
    assert widened.lo < hard.lo and widened.hi > hard.hi


def test_deterministic_estimate_has_zero_spread():
    m = pert_moments(5.0, 5.0, 5.0, "absolute")
    assert m.sd == 0.0 and m.variance == 0.0


def test_negative_range_is_supported_for_opportunities():
    m = pert_moments(-100.0, -60.0, -10.0, "absolute")
    assert m.mean < 0
    assert m.sd > 0


def test_expected_value_scales_by_occurrence():
    m = pert_moments(0.0, 50.0, 100.0, "absolute")
    assert expected_value(m, 0.25) == pytest.approx(12.5)


def test_bad_lambda_rejected():
    with pytest.raises(ValueError):
        pert_moments(0.0, 1.0, 2.0, "absolute", 0.0)


# ------------------------------------------------------------------------- validation


def good() -> EstimateInput:
    return EstimateInput(
        p_occurrence=0.3,
        cost_min=100_000.0,
        cost_ml=250_000.0,
        cost_max=900_000.0,
        sched_min=5.0,
        sched_ml=15.0,
        sched_max=40.0,
    )


def test_well_formed_estimate_passes():
    assert validate(good()).ok


def test_out_of_order_three_point_is_an_error():
    est = good()
    est.cost_ml = 50_000.0  # below the min
    result = validate(est)
    assert not result.ok
    assert any(i.field == "cost_ml" for i in result.errors)


def test_partial_three_point_is_an_error():
    est = good()
    est.cost_max = None
    result = validate(est)
    assert not result.ok
    assert any("missing" in i.message for i in result.errors)


def test_estimate_with_no_dimension_is_an_error():
    est = EstimateInput(p_occurrence=0.5)
    result = validate(est)
    assert not result.ok
    assert any("cannot be simulated" in i.message for i in result.errors)


@pytest.mark.parametrize("p", [0.0, -0.1, 1.5, float("nan")])
def test_probability_outside_range_is_an_error(p):
    est = good()
    est.p_occurrence = p
    assert not validate(est).ok


def test_variability_must_be_certain():
    est = good()
    est.is_variability = True
    est.p_occurrence = 0.4
    result = validate(est)
    assert not result.ok
    assert any(i.field == "p_occurrence" for i in result.errors)


def test_variability_at_probability_one_passes():
    est = good()
    est.is_variability = True
    est.p_occurrence = 1.0
    assert validate(est).ok


def test_certain_risk_event_warns_but_saves():
    est = good()
    est.p_occurrence = 1.0
    result = validate(est)
    assert result.ok
    assert any(i.field == "is_variability" for i in result.warnings)


def test_deterministic_dimension_warns():
    est = good()
    est.cost_min = est.cost_ml = est.cost_max = 1000.0
    result = validate(est)
    assert result.ok
    assert any("deterministic" in i.message for i in result.warnings)


def test_wild_skew_warns():
    est = good()
    est.cost_min, est.cost_ml, est.cost_max = 100.0, 110.0, 500_000.0
    result = validate(est)
    assert result.ok
    assert any("skewed" in i.message for i in result.warnings)


def test_range_spanning_zero_warns():
    est = good()
    est.cost_min, est.cost_ml, est.cost_max = -50_000.0, 10_000.0, 200_000.0
    result = validate(est)
    assert result.ok
    assert any("spans zero" in i.message for i in result.warnings)


def test_low_confidence_absolute_bounds_nudges_toward_p10_p90():
    est = good()
    est.confidence = "low"
    est.bound_interpretation = "absolute"
    result = validate(est)
    assert result.ok
    assert any(i.field == "bound_interpretation" for i in result.warnings)


def test_rare_event_warns():
    est = good()
    est.p_occurrence = 0.01
    result = validate(est)
    assert result.ok
    assert any("Rare" in i.message for i in result.warnings)


def test_agent_proposal_is_flagged_for_human_ownership():
    est = good()
    est.source = "agent_proposal"
    result = validate(est)
    assert result.ok
    assert any(i.field == "source" for i in result.warnings)


@pytest.mark.parametrize(
    "field_name,bad",
    [
        ("bound_interpretation", "p1_p99"),
        ("dist_type", "lognormal"),
        ("cost_basis", "guess"),
        ("sched_day_basis", "elapsed"),
        ("source", "vibes"),
        ("confidence", "certain"),
    ],
)
def test_unknown_vocabulary_rejected(field_name, bad):
    est = good()
    setattr(est, field_name, bad)
    result = validate(est)
    assert not result.ok
    assert any(i.field == field_name for i in result.errors)


def test_non_finite_value_rejected():
    est = good()
    est.cost_max = math.inf
    assert not validate(est).ok


# --------------------------------------------------------------------------- summarise


def test_summarise_covers_both_dimensions():
    out = summarise(good())
    assert out["cost"] is not None and out["sched"] is not None
    assert out["cost"]["expected_value"] == pytest.approx(out["cost"]["mean"] * 0.3)
    assert out["sched"]["alpha"] > 1 and out["sched"]["beta"] > 1


def test_summarise_omits_an_absent_dimension():
    est = good()
    est.sched_min = est.sched_ml = est.sched_max = None
    out = summarise(est)
    assert out["cost"] is not None
    assert out["sched"] is None
