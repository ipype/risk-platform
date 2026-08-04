"""The before/after arithmetic, with no database anywhere near it.

``app.services.roi`` takes two serialised results and returns a comparison, so every rule
worth arguing about is testable from two dictionaries. What is under test here is the
judgement, not the subtraction: the sign convention, the refusals that stop two
incomparable runs being subtracted at all, the error bar that decides whether a reduction
is worth quoting, and the wall between a deterministic plan cost and a percentile.
"""

from __future__ import annotations

import math

import pytest

from app.services import roi


# --------------------------------------------------------------------------------------
# fixtures: the smallest thing shaped like a stored SimulationResult
# --------------------------------------------------------------------------------------


def series(mean: float, scale: float, *, iterations: int = 10_000, units: str = "currency"):
    """A summary whose S-curve is a straight line from ``0`` to ``2 * mean``.

    Linear on purpose. A uniform distribution has a known density, so the standard-error
    estimate has an analytic answer to be checked against rather than a plausible-looking
    number to be eyeballed.
    """
    lo, hi = mean - scale, mean + scale
    points = tuple(
        {"p": float(p), "value": lo + (hi - lo) * p / 100.0} for p in (10, 50, 80, 90, 95)
    )
    curve = tuple(
        {"x": lo + (hi - lo) * i / 100.0, "p": i / 100.0} for i in range(101)
    )
    return {
        "label": "x",
        "units": units,
        "iterations": iterations,
        "mean": mean,
        "sd": scale / math.sqrt(3.0),
        "minimum": lo,
        "maximum": hi,
        "percentiles": points,
        "s_curve": curve,
        "histogram": {"edges": (lo, hi), "counts": (iterations,)},
    }


def result(
    *,
    contingency_p80: float,
    mean_total: float = 1_000_000.0,
    sensitivity=(),
    criticality=(),
    total_scale: float = 500_000.0,
):
    return {
        "contingency": {
            "base_cost": 0.0,
            "mean_total_cost": mean_total,
            "contingency": (
                {"p": 50.0, "value": contingency_p80 * 0.6},
                {"p": 80.0, "value": contingency_p80},
                {"p": 90.0, "value": contingency_p80 * 1.2},
            ),
        },
        "total_cost": series(mean_total, total_scale),
        "risk_cost": series(mean_total * 0.3, total_scale * 0.5),
        "delay_days": None,
        "finish_day": None,
        "schedule_driven_cost": None,
        "risk_sensitivity": list(sensitivity),
        "activity_criticality": list(criticality),
        "warnings": [],
    }


def run(**overrides):
    """A stand-in for a ``SimulationRun`` row; ``pairing_issues`` reads attributes or keys."""
    base = {
        "id": 1,
        "scenario": "pre_mitigation",
        "status": "succeeded",
        "scope_id": 1,
        "schedule_version_id": None,
        "iterations": 10_000,
        "seed": 7,
        "sampling": "lhs",
        "base_cost": 0.0,
        "burn_rate_per_day": 0.0,
        "engine_version": "1.1.0",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------------------
# the sign convention
# --------------------------------------------------------------------------------------


class TestReduction:
    def test_a_package_that_helps_reports_a_positive_reduction(self) -> None:
        r = roi.Reduction.of(1_000.0, 600.0)
        assert r.reduction == pytest.approx(400.0)
        assert r.reduction_pct == pytest.approx(0.4)

    def test_a_package_that_hurts_reports_a_negative_one_rather_than_zero(self) -> None:
        """Clamping here would hide the only finding worth acting on."""
        r = roi.Reduction.of(600.0, 1_000.0)
        assert r.reduction == pytest.approx(-400.0)

    def test_a_zero_baseline_has_no_percentage(self) -> None:
        assert roi.Reduction.of(0.0, 0.0).reduction_pct is None

    def test_a_missing_side_produces_no_arithmetic(self) -> None:
        assert roi.Reduction.of(None, 5.0).reduction is None


# --------------------------------------------------------------------------------------
# the guard
# --------------------------------------------------------------------------------------


class TestPairingIssues:
    def test_a_matched_pair_passes(self) -> None:
        assert roi.pairing_issues(run(), run(id=2, scenario="post_mitigation")) == []

    def test_the_scenarios_must_be_the_right_way_round(self) -> None:
        issues = roi.pairing_issues(
            run(scenario="post_mitigation"), run(id=2, scenario="pre_mitigation")
        )
        assert len(issues) == 2

    @pytest.mark.parametrize(
        "field,value",
        [
            ("seed", 8),
            ("iterations", 5_000),
            ("sampling", "mc"),
            ("base_cost", 1.0),
            ("burn_rate_per_day", 100.0),
            ("schedule_version_id", 3),
            ("scope_id", 2),
        ],
    )
    def test_every_field_that_would_contaminate_the_delta_is_refused(
        self, field, value
    ) -> None:
        issues = roi.pairing_issues(
            run(), run(id=2, scenario="post_mitigation", **{field: value})
        )
        assert len(issues) == 1
        # The message names the field, because "these runs are not comparable" is not
        # something an analyst can act on.
        assert str(value) in issues[0]

    def test_a_run_cannot_be_compared_with_itself(self) -> None:
        issues = roi.pairing_issues(run(), run(scenario="post_mitigation"))
        assert any("itself" in i for i in issues)

    def test_an_unfinished_run_is_not_comparable(self) -> None:
        issues = roi.pairing_issues(
            run(), run(id=2, scenario="post_mitigation", status="queued")
        )
        assert any("has not succeeded" in i for i in issues)

    def test_a_different_engine_version_is_named(self) -> None:
        issues = roi.pairing_issues(
            run(), run(id=2, scenario="post_mitigation", engine_version="1.2.0")
        )
        assert any("engine version" in i for i in issues)


# --------------------------------------------------------------------------------------
# the error bar
# --------------------------------------------------------------------------------------


class TestStandardError:
    def test_it_matches_the_analytic_value_for_a_known_density(self) -> None:
        """Uniform on [500k, 1.5m]: f = 1/1e6, so SE(P80) = sqrt(.8*.2/n) * 1e6."""
        s = series(1_000_000.0, 500_000.0, iterations=10_000)
        expected = math.sqrt(0.8 * 0.2 / 10_000) * 1_000_000.0
        assert roi.percentile_standard_error(s, 80.0) == pytest.approx(expected, rel=0.02)

    def test_more_iterations_narrow_it(self) -> None:
        few = roi.percentile_standard_error(series(1e6, 5e5, iterations=1_000), 80.0)
        many = roi.percentile_standard_error(series(1e6, 5e5, iterations=100_000), 80.0)
        assert many < few

    def test_a_flat_curve_returns_none_rather_than_infinity(self) -> None:
        """A degenerate series has no density to divide by, and saying so beats a number."""
        flat = series(1_000_000.0, 0.0)
        assert roi.percentile_standard_error(flat, 80.0) is None

    def test_a_result_without_a_curve_returns_none(self) -> None:
        assert roi.percentile_standard_error({"iterations": 10_000}, 80.0) is None


class TestPercentileAt:
    def test_it_reads_a_computed_percentile(self) -> None:
        points = ({"p": 50.0, "value": 10.0}, {"p": 80.0, "value": 20.0})
        assert roi.percentile_at(points, 80.0) == 20.0

    def test_it_refuses_to_invent_one_the_run_never_computed(self) -> None:
        """Interpolating the percentile grid would fabricate a figure and label it a result."""
        points = ({"p": 50.0, "value": 10.0}, {"p": 80.0, "value": 20.0})
        assert roi.percentile_at(points, 70.0) is None


# --------------------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------------------


class TestCompare:
    def test_the_headline_is_the_contingency_the_package_removed(self) -> None:
        c = roi.compare(
            result(contingency_p80=3_000_000.0),
            result(contingency_p80=1_800_000.0),
            percentile=80.0,
        )
        assert c.contingency.at_percentile.reduction == pytest.approx(1_200_000.0)
        assert c.contingency.at_percentile.reduction_pct == pytest.approx(0.4)

    def test_plan_cost_is_never_inside_the_contingency(self) -> None:
        """Invariant 1's cost-side twin: additive money, percentile money, kept apart."""
        c = roi.compare(
            result(contingency_p80=3_000_000.0),
            result(contingency_p80=1_800_000.0),
            plan_budget=400_000.0,
        )
        assert c.contingency.at_percentile.after == pytest.approx(1_800_000.0)
        assert c.plan_budget == pytest.approx(400_000.0)
        assert c.net_at_percentile == pytest.approx(800_000.0)
        assert c.benefit_cost_ratio == pytest.approx(3.0)

    def test_an_unpriced_package_has_no_ratio_rather_than_an_infinite_one(self) -> None:
        c = roi.compare(
            result(contingency_p80=3_000_000.0),
            result(contingency_p80=1_800_000.0),
            plan_budget=0.0,
        )
        assert c.benefit_cost_ratio is None
        assert c.net_at_percentile == pytest.approx(1_200_000.0)

    def test_a_package_that_makes_things_worse_is_reported_not_hidden(self) -> None:
        c = roi.compare(
            result(contingency_p80=1_800_000.0), result(contingency_p80=3_000_000.0)
        )
        assert c.contingency.at_percentile.reduction < 0
        assert any("higher" in w for w in c.warnings)

    def test_a_reduction_inside_the_error_bar_is_flagged(self) -> None:
        """The failure mode this whole module exists to prevent."""
        c = roi.compare(
            result(contingency_p80=3_000_000.0),
            result(contingency_p80=2_999_000.0),
        )
        assert c.contingency.within_noise is True
        assert any("distinguishable" in w for w in c.warnings)

    def test_a_reduction_clearing_the_error_bar_is_not_flagged(self) -> None:
        c = roi.compare(
            result(contingency_p80=3_000_000.0),
            result(contingency_p80=1_800_000.0),
        )
        assert c.contingency.within_noise is False
        assert not any("distinguishable" in w for w in c.warnings)

    def test_the_error_on_a_difference_is_the_declared_upper_bound(self) -> None:
        c = roi.compare(
            result(contingency_p80=3_000_000.0), result(contingency_p80=1_800_000.0)
        )
        one = roi.percentile_standard_error(series(1_000_000.0, 500_000.0), 80.0)
        assert c.contingency.standard_error == pytest.approx(math.sqrt(2) * one, rel=0.05)
        assert any("upper bound" in b for b in c.basis)

    def test_a_percentile_neither_run_computed_is_refused_not_interpolated(self) -> None:
        c = roi.compare(
            result(contingency_p80=3_000_000.0),
            result(contingency_p80=1_800_000.0),
            percentile=70.0,
        )
        assert c.contingency.at_percentile.reduction is None
        assert any("did not compute" in w for w in c.warnings)

    def test_the_two_curves_land_on_one_grid(self) -> None:
        c = roi.compare(
            result(contingency_p80=3_000_000.0),
            result(contingency_p80=1_800_000.0, mean_total=700_000.0),
        )
        assert len(c.curve) == 101
        assert c.curve[50].before > c.curve[50].after
        assert c.curve[50].reduction == pytest.approx(
            c.curve[50].before - c.curve[50].after
        )

    def test_a_missing_result_produces_a_warning_not_a_crash(self) -> None:
        c = roi.compare(None, result(contingency_p80=1.0))
        assert c.contingency is None
        assert c.warnings

    def test_the_basis_states_the_sign_convention_and_the_seed_position(self) -> None:
        shared = roi.compare(result(contingency_p80=3e6), result(contingency_p80=2e6))
        assert any("baseline minus treated" in b for b in shared.basis)
        assert any("same seed" in b for b in shared.basis)

        unshared = roi.compare(
            result(contingency_p80=3e6), result(contingency_p80=2e6), seed_shared=False
        )
        assert any("different seeds" in b for b in unshared.basis)

    def test_plan_cost_appears_in_the_basis_only_when_there_is_one(self) -> None:
        priced = roi.compare(
            result(contingency_p80=3e6), result(contingency_p80=2e6), plan_budget=1.0
        )
        free = roi.compare(result(contingency_p80=3e6), result(contingency_p80=2e6))
        assert any("never" in b and "added into one number" in b for b in priced.basis)
        assert not any("added into one number" in b for b in free.basis)

    def test_unpriced_actions_understate_every_ratio_and_it_says_so(self) -> None:
        c = roi.compare(
            result(contingency_p80=3e6),
            result(contingency_p80=2e6),
            plan_budget=100.0,
            plan_unpriced_count=3,
        )
        assert any("understated" in w for w in c.warnings)


# --------------------------------------------------------------------------------------
# who moved
# --------------------------------------------------------------------------------------


def sens(risk_id: int, code: str, share: float, contribution: float):
    return {
        "risk_id": risk_id,
        "code": code,
        "title": f"Risk {code}",
        "combined_variance_share": share,
        "p80_contribution": contribution,
    }


class TestMovers:
    def test_a_retired_risk_is_named_as_retired(self) -> None:
        c = roi.compare(
            result(contingency_p80=3e6, sensitivity=[sens(1, "A", 0.6, 900.0), sens(2, "B", 0.4, 400.0)]),
            result(contingency_p80=2e6, sensitivity=[sens(2, "B", 1.0, 400.0)]),
        )
        by_id = {m.risk_id: m for m in c.risk_movers}
        assert by_id[1].movement == "retired"
        assert by_id[1].contribution_after is None
        assert by_id[2].movement == "unchanged"
        assert c.retired_count == 1

    def test_a_risk_that_appeared_between_the_runs_is_named_and_warned_about(self) -> None:
        """Not the package's doing, and a comparison that hid it would credit it anyway."""
        c = roi.compare(
            result(contingency_p80=3e6, sensitivity=[sens(1, "A", 1.0, 900.0)]),
            result(
                contingency_p80=2e6,
                sensitivity=[sens(1, "A", 0.5, 400.0), sens(9, "Z", 0.5, 100.0)],
            ),
        )
        by_id = {m.risk_id: m for m in c.risk_movers}
        assert by_id[9].movement == "entered"
        assert any("more risks than the baseline" in w for w in c.warnings)

    def test_movers_are_ordered_by_what_the_package_took_off_them(self) -> None:
        c = roi.compare(
            result(
                contingency_p80=3e6,
                sensitivity=[sens(1, "A", 0.3, 100.0), sens(2, "B", 0.7, 900.0)],
            ),
            result(
                contingency_p80=2e6,
                sensitivity=[sens(1, "A", 0.5, 90.0), sens(2, "B", 0.5, 200.0)],
            ),
        )
        assert [m.code for m in c.risk_movers] == ["B", "A"]
        assert c.risk_movers[0].contribution_reduction == pytest.approx(700.0)

    def test_ranks_are_reported_on_both_sides(self) -> None:
        c = roi.compare(
            result(
                contingency_p80=3e6,
                sensitivity=[sens(1, "A", 0.3, 100.0), sens(2, "B", 0.7, 900.0)],
            ),
            result(
                contingency_p80=2e6,
                sensitivity=[sens(1, "A", 0.8, 90.0), sens(2, "B", 0.2, 20.0)],
            ),
        )
        by_id = {m.risk_id: m for m in c.risk_movers}
        assert (by_id[2].rank_before, by_id[2].rank_after) == (1, 2)
        assert (by_id[1].rank_before, by_id[1].rank_after) == (2, 1)

    def test_criticality_keeps_both_directions(self) -> None:
        """A package that clears one path usually promotes another; hiding it is the bug."""
        before = [
            {"activity_id": "A1", "code": "A1000", "name": "Design", "criticality_index": 0.9},
            {"activity_id": "A2", "code": "A2000", "name": "Build", "criticality_index": 0.2},
        ]
        after = [
            {"activity_id": "A1", "code": "A1000", "name": "Design", "criticality_index": 0.3},
            {"activity_id": "A2", "code": "A2000", "name": "Build", "criticality_index": 0.8},
        ]
        c = roi.compare(
            result(contingency_p80=3e6, criticality=before),
            result(contingency_p80=2e6, criticality=after),
        )
        moves = {m.activity_id: m.index_change for m in c.criticality_movers}
        assert moves["A1"] == pytest.approx(-0.6)
        assert moves["A2"] == pytest.approx(0.6)
        # Ordered by size of movement regardless of direction.
        assert abs(c.criticality_movers[0].index_change) >= abs(
            c.criticality_movers[-1].index_change
        )
