"""The joint cost-date view.

Every assertion here is against a closed form or against the sample the frontier was built
from, never against another implementation. The frontier is a level set of an empirical
joint CDF, so "is this point actually ``t`` percent likely" is a counting question with an
exact answer, and that is what is checked.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.sim import (
    DistributionSpec,
    RiskInput,
    RunConfig,
    SimulationRequest,
    joint_confidence,
    run,
)

TARGETS = (50.0, 80.0, 95.0)


def _independent(n: int = 4000, seed: int = 3) -> tuple[np.ndarray, np.ndarray]:
    g = np.random.default_rng(seed)
    return g.normal(10_000_000, 1_000_000, n), g.normal(60.0, 20.0, n)


def _achieved(
    cost: np.ndarray, delay: np.ndarray, c: float, d: float
) -> float:
    return float(np.mean((cost <= c) & (delay <= d)))


class TestFrontier:
    def test_every_point_delivers_the_confidence_it_claims(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=TARGETS)
        assert view is not None
        for f in view.frontiers:
            for p in f.points:
                got = _achieved(cost, delay, p.total_cost, p.delay_days)
                # At or above the target by construction, and tight: the cost is the
                # k-th order statistic of the admitted subset, so overshoot is one
                # iteration's worth plus ties.
                assert got >= f.target / 100.0 - 1e-12
                assert got <= f.target / 100.0 + 0.01

    def test_accepting_more_delay_lowers_the_cost_that_must_be_carried(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=TARGETS)
        assert view is not None
        for f in view.frontiers:
            delays = [p.delay_days for p in f.points]
            costs = [p.total_cost for p in f.points]
            assert delays == sorted(delays)
            assert all(b <= a + 1e-9 for a, b in zip(costs, costs[1:]))

    def test_the_open_ended_point_is_just_the_marginal_cost(self) -> None:
        """With every delay admitted, the joint constraint collapses to the cost one."""
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=TARGETS)
        assert view is not None
        for f in view.frontiers:
            last = f.points[-1]
            assert last.delay_p == pytest.approx(100.0)
            assert last.cost_p == pytest.approx(f.target, abs=0.5)
            assert last.total_cost == pytest.approx(
                float(np.percentile(cost, f.target)), rel=1e-3
            )

    def test_the_balanced_point_is_stricter_on_both_axes_than_the_target(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=TARGETS)
        assert view is not None
        for f in view.frontiers:
            b = f.balanced
            assert b is not None
            assert b.cost_p >= f.target - 0.5
            assert b.delay_p >= f.target - 0.5
            assert abs(b.cost_p - b.delay_p) < 5.0


class TestTheMarginalTrap:
    def test_independent_series_multiply(self) -> None:
        """The whole reason this module exists: 0.8 x 0.8, not 0.8."""
        cost, delay = _independent(n=20_000, seed=11)
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None
        assert view.joint_at_marginal_pair == pytest.approx(0.64, abs=0.02)

    def test_perfect_dependence_gives_the_marginal_back(self) -> None:
        cost, _ = _independent(n=4000, seed=5)
        delay = cost / 1_000.0  # strictly monotone in cost
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None
        assert view.joint_at_marginal_pair == pytest.approx(0.80, abs=0.002)
        assert view.cost_delay_correlation == pytest.approx(1.0, abs=1e-9)

    def test_the_pair_is_never_more_likely_than_either_marginal(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None
        assert view.joint_at_marginal_pair <= 0.80 + 1e-9


class TestTransport:
    def test_the_scatter_is_thinned_not_truncated(self) -> None:
        cost, delay = _independent(n=10_000)
        view = joint_confidence(cost, delay, targets=(80.0,), scatter_cap=1000)
        assert view is not None
        assert len(view.scatter) <= 1000
        assert view.scatter_stride > 1
        assert view.scatter[0] == (round(float(delay[0]), 3), round(float(cost[0]), 2))
        # Thinning must span the run, not stop early: the widest delay in the thinned
        # cloud has to be within a stride of the widest overall.
        assert max(p[0] for p in view.scatter) > float(np.percentile(delay, 99))

    def test_a_short_run_is_refused_rather_than_answered_thinly(self) -> None:
        cost, delay = _independent(n=120)
        assert joint_confidence(cost, delay, targets=(80.0,)) is None

    def test_mismatched_series_are_a_programming_error(self) -> None:
        with pytest.raises(ValueError):
            joint_confidence(np.zeros(400), np.zeros(300), targets=(80.0,))

    def test_the_finish_axis_is_the_baseline_plus_the_delay(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=(80.0,), baseline_finish=250.0)
        assert view is not None
        assert view.marginal_finish_day == pytest.approx(
            250.0 + view.marginal_delay_days
        )
        for p in view.frontiers[0].points:
            assert p.finish_day == pytest.approx(250.0 + p.delay_days)


class TestThroughTheEngine:
    def test_an_integrated_run_reports_a_joint_view(self, simple_request) -> None:
        r = run(simple_request).result
        assert r.joint is not None
        assert r.joint.iterations == simple_request.config.iterations
        assert r.joint.burn_rate_coupled is True
        assert [f.target for f in r.joint.frontiers] == [50, 60, 70, 80, 90, 95]

    def test_a_cost_only_run_has_no_joint_to_report(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="C",
                    p_occurrence=0.5,
                    cost=DistributionSpec(kind="uniform", lo=0.0, hi=1_000_000.0),
                ),
            ),
            config=RunConfig(iterations=1000, seed=2),
        )
        assert run(req).result.joint is None

    def test_the_marginal_pair_is_called_out_when_it_falls_short(
        self, simple_request
    ) -> None:
        r = run(simple_request).result
        assert r.joint is not None
        assert r.joint.joint_at_marginal_pair < 0.80
        assert any("not the same iteration" in w for w in r.warnings)

    def test_the_joint_view_survives_serialisation(self, simple_request) -> None:
        """It is persisted as ``result_json`` and read back by the UI, not re-derived."""
        r = run(simple_request).result
        blob = r.model_dump()
        assert blob["joint"]["frontiers"][0]["points"][0]["cost_p"] > 0
        assert len(blob["joint"]["scatter"][0]) == 2


class TestGrid:
    """The mesh a reader prices their own target pair against.

    Its whole claim is that it is *counted*, not fitted: every assertion here is against
    a brute-force count over the same sample, because anything softer would let an
    interpolation creep into the construction without failing a test.
    """

    def test_every_node_is_an_exact_count(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None
        grid = view.grid
        assert grid is not None
        for i in (0, 1, 17, 40, len(grid.delay_days) - 1):
            for j in (0, 2, 25, 49, len(grid.total_cost) - 1):
                truth = int(
                    np.sum((delay <= grid.delay_days[i]) & (cost <= grid.total_cost[j]))
                )
                assert grid.counts[i][j] == truth

    def test_the_mesh_is_the_marginal_quantiles(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None and view.grid is not None
        nodes = len(view.grid.delay_days)
        qs = np.linspace(0.0, 1.0, nodes)
        assert view.grid.delay_days == pytest.approx(tuple(np.quantile(delay, qs)))
        assert view.grid.total_cost == pytest.approx(tuple(np.quantile(cost, qs)))

    def test_it_is_non_decreasing_along_both_axes(self) -> None:
        """A reader brackets a mid-cell target between two nodes. That is only a bound
        if the surface cannot dip between them."""
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None and view.grid is not None
        counts = np.array(view.grid.counts)
        assert np.all(np.diff(counts, axis=0) >= 0)
        assert np.all(np.diff(counts, axis=1) >= 0)

    def test_the_corners_are_none_and_all(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None and view.grid is not None
        assert view.grid.counts[-1][-1] == view.grid.iterations == cost.size
        # The origin is the single cheapest, earliest iteration at most — never the
        # whole sample, which would mean the mesh had collapsed.
        assert view.grid.counts[0][0] <= 1

    def test_the_last_row_and_column_are_the_marginals(self) -> None:
        cost, delay = _independent()
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None and view.grid is not None
        grid = view.grid
        for j, c in enumerate(grid.total_cost):
            assert grid.counts[-1][j] == int(np.sum(cost <= c))
        for i, d in enumerate(grid.delay_days):
            assert grid.counts[i][-1] == int(np.sum(delay <= d))

    def test_a_degenerate_axis_still_produces_a_readable_mesh(self) -> None:
        """Every iteration finishing on the same day collapses the delay nodes onto one
        value. The mesh must still count, because a schedule with no risk mapped to it
        is a real run, not a malformed one."""
        g = np.random.default_rng(11)
        cost = g.normal(1_000_000, 100_000, 2000)
        delay = np.zeros(2000)
        view = joint_confidence(cost, delay, targets=(80.0,))
        assert view is not None and view.grid is not None
        assert set(view.grid.delay_days) == {0.0}
        assert view.grid.counts[-1][-1] == 2000
        assert view.grid.counts[0][-1] == 2000

    def test_the_grid_survives_serialisation(self, simple_request) -> None:
        blob = run(simple_request).result.model_dump()
        grid = blob["joint"]["grid"]
        assert len(grid["counts"]) == len(grid["delay_days"]) == len(grid["total_cost"])
        assert grid["counts"][-1][-1] == grid["iterations"]
