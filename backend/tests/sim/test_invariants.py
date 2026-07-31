"""The invariants, as executable statements.

`REFERENCE.md` requires a statistical regression test behind any change to sampling,
correlation or percentile logic. This file is that test. It is deliberately about
properties and pinned numbers rather than about mechanics — mechanics live in
``test_engine.py``, and a mechanics test would happily keep passing while the answer
drifted.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.sim import (
    CorrelationInput,
    DistributionSpec,
    DriverSpec,
    PairCorrelation,
    RiskInput,
    RiskMappingInput,
    RunConfig,
    SimulationRequest,
    run,
)
from app.sim.correlation import spearman

from .conftest import chain, pert, tri


def p_of(points, p: float) -> float:
    return next(x.value for x in points if x.p == p)


class TestInvariantOneNeverAddPercentiles:
    def test_the_additive_answer_is_higher_and_is_reported(
        self, simple_request
    ) -> None:
        c = run(simple_request).result.contingency
        assert c.additive_p80_total > c.integrated_p80_total
        assert c.additive_error_at_p80 == pytest.approx(
            c.additive_p80_total - c.integrated_p80_total
        )

    def test_the_gap_is_material_enough_to_warn_about(self, simple_request) -> None:
        r = run(simple_request).result
        gap = r.contingency.additive_error_at_p80
        contingency_p80 = p_of(r.contingency.contingency, 80)
        assert gap / contingency_p80 > 0.01
        assert any("integrated one" in w for w in r.warnings)

    def test_the_two_agree_only_under_perfect_rank_correlation(self) -> None:
        # Adding P80s assumes the cost tail and the delay tail are the same iteration.
        # Force that to be true and the error collapses; it is the *assumption* that is
        # wrong, not the arithmetic.
        one_risk = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="ONLY",
                    p_occurrence=1.0,
                    cost=DistributionSpec(kind="uniform", lo=0.0, hi=1_000_000.0),
                    sched=tri(0, 20, 40),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="duration_driver", activity_ids=("A001",)
                        ),
                    ),
                ),
            ),
            schedule=chain([50], uncertain=False),
            correlation=CorrelationInput(pairs=()),
            config=RunConfig(
                iterations=6000,
                seed=5,
                burn_rate_per_day=10_000.0,
                intra_risk_cost_sched_correlation=1.0,
            ),
        )
        c = run(one_risk).result.contingency
        assert abs(c.additive_error_at_p80) / c.integrated_p80_total < 0.005

    def test_the_burn_rate_term_is_priced_inside_the_iteration(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="D",
                    p_occurrence=0.5,
                    sched=DistributionSpec(kind="point", lo=20.0, hi=20.0),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="duration_driver", activity_ids=("A001",)
                        ),
                    ),
                ),
            ),
            schedule=chain([30], uncertain=False),
            config=RunConfig(iterations=4000, seed=6, burn_rate_per_day=1_000.0),
        )
        out = run(req)
        expected = 1_000.0 * np.maximum(out.arrays.delay_days, 0.0)
        assert np.allclose(out.arrays.total_cost, expected)

    def test_an_early_finish_earns_no_credit_by_default(self) -> None:
        req = SimulationRequest(
            schedule=chain([100], uncertain=True),
            config=RunConfig(iterations=2000, seed=8, burn_rate_per_day=1_000.0),
        )
        out = run(req)
        assert out.arrays.delay_days.min() < 0
        assert out.arrays.total_cost.min() == pytest.approx(0.0)

    def test_credit_is_available_when_asked_for_explicitly(self) -> None:
        req = SimulationRequest(
            schedule=chain([100], uncertain=True),
            config=RunConfig(
                iterations=2000,
                seed=8,
                burn_rate_per_day=1_000.0,
                allow_negative_delay_credit=True,
            ),
        )
        assert run(req).arrays.total_cost.min() < 0


class TestInvariantTwoCorrelationBeforeSampling:
    def _register(self, coefficient: float, seed: int = 21) -> SimulationRequest:
        return SimulationRequest(
            risks=tuple(
                RiskInput(
                    risk_id=i,
                    code=f"R{i}",
                    p_occurrence=1.0,
                    cost=pert(0, 500_000, 2_000_000),
                    drivers=("shared",),
                )
                for i in range(1, 9)
            ),
            correlation=CorrelationInput(
                drivers=(DriverSpec(name="shared", coefficient=coefficient),)
            ),
            config=RunConfig(iterations=8000, seed=seed),
        )

    def test_independent_sampling_understates_the_tail(self) -> None:
        independent = run(self._register(0.0)).result
        correlated = run(self._register(0.7)).result
        assert correlated.risk_cost.mean == pytest.approx(
            independent.risk_cost.mean, rel=0.02
        )
        # Same mean, fatter tail. This is the entire argument for the invariant.
        assert p_of(correlated.risk_cost.percentiles, 90) > p_of(
            independent.risk_cost.percentiles, 90
        )
        assert correlated.risk_cost.sd > independent.risk_cost.sd * 1.5

    def test_the_target_arrives_on_the_sampled_contributions(self) -> None:
        out = run(self._register(0.6))
        achieved = spearman(out.arrays.contributions)
        off = achieved[~np.eye(8, dtype=bool)]
        assert np.allclose(off, 0.6, atol=0.03)
        assert out.result.correlation.max_pair_error < 0.03

    def test_an_explicit_pair_overrides_the_shared_driver(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(risk_id=1, code="A", cost=pert(0, 1, 2), drivers=("s",)),
                RiskInput(risk_id=2, code="B", cost=pert(0, 1, 2), drivers=("s",)),
            ),
            correlation=CorrelationInput(
                drivers=(DriverSpec(name="s", coefficient=0.8),),
                pairs=(PairCorrelation(risk_a=1, risk_b=2, coefficient=-0.5),),
            ),
            config=RunConfig(iterations=6000, seed=9),
        )
        achieved = spearman(run(req).arrays.contributions)
        assert achieved[0, 1] == pytest.approx(-0.5, abs=0.03)

    def test_the_strongest_shared_driver_wins_rather_than_the_sum(self) -> None:
        # Correlations do not add. A sum would run past 1.0 on the third shared tag.
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1, code="A", cost=pert(0, 1, 2), drivers=("x", "y", "z")
                ),
                RiskInput(
                    risk_id=2, code="B", cost=pert(0, 1, 2), drivers=("x", "y", "z")
                ),
            ),
            correlation=CorrelationInput(
                drivers=(
                    DriverSpec(name="x", coefficient=0.3),
                    DriverSpec(name="y", coefficient=0.5),
                    DriverSpec(name="z", coefficient=0.4),
                )
            ),
            config=RunConfig(iterations=6000, seed=9),
        )
        achieved = spearman(run(req).arrays.contributions)
        assert achieved[0, 1] == pytest.approx(0.5, abs=0.03)


class TestInvariantSixReproducibility:
    def test_the_same_request_gives_identical_numbers(self, simple_request) -> None:
        a = run(simple_request)
        b = run(simple_request)
        assert np.array_equal(a.arrays.total_cost, b.arrays.total_cost)
        assert np.array_equal(a.arrays.delay_days, b.arrays.delay_days)
        assert a.result.manifest.inputs_sha256 == b.result.manifest.inputs_sha256

    def test_a_different_seed_gives_different_numbers(self, simple_request) -> None:
        other = simple_request.model_copy(
            update={"config": simple_request.config.model_copy(update={"seed": 8})}
        )
        assert not np.array_equal(
            run(simple_request).arrays.total_cost, run(other).arrays.total_cost
        )

    def test_the_fingerprint_moves_when_any_input_moves(self, simple_request) -> None:
        base = simple_request.fingerprint()
        moved = simple_request.model_copy(
            update={
                "config": simple_request.config.model_copy(
                    update={"burn_rate_per_day": 45_001.0}
                )
            }
        )
        assert moved.fingerprint() != base

    def test_the_manifest_records_the_resolved_chunk_size(self, simple_request) -> None:
        m = run(simple_request).result.manifest
        assert m.chunk_size > 0
        assert m.iterations == simple_request.config.iterations
        assert m.calendar_id == "CAL-5x8"

    def test_chunking_changes_the_draws_but_not_the_distribution(self) -> None:
        # Activity uniforms are drawn per chunk from a stream addressed by chunk index,
        # so the chunk size is part of the run definition and travels in the manifest.
        # What must not move is the answer, to within sampling error.
        def at(chunk: int) -> float:
            req = SimulationRequest(
                schedule=chain([30, 40, 50]),
                config=RunConfig(iterations=6000, seed=12, chunk_size=chunk),
            )
            return run(req).result.delay_days.mean

        assert at(500) == pytest.approx(at(6000), abs=0.5)


class TestOutputCoherence:
    def test_percentiles_and_the_s_curve_agree(self, simple_request) -> None:
        s = run(simple_request).result.total_cost
        values = [p.value for p in s.percentiles]
        assert values == sorted(values)
        curve = [c.x for c in s.s_curve]
        assert curve == sorted(curve)
        assert curve[0] == pytest.approx(s.minimum)
        assert curve[-1] == pytest.approx(s.maximum)

    def test_contingency_is_the_total_less_the_base(self, simple_request) -> None:
        r = run(simple_request).result
        assert p_of(r.contingency.contingency, 80) == pytest.approx(
            p_of(r.total_cost.percentiles, 80) - r.deterministic.base_cost
        )

    def test_the_variance_decomposition_is_exhaustive(self, simple_request) -> None:
        r = run(simple_request).result
        assert (
            r.contingency.cost_variance_share + r.contingency.schedule_variance_share
            == pytest.approx(1.0)
        )
        assert sum(s.combined_variance_share for s in r.risk_sensitivity) == (
            pytest.approx(1.0, abs=1e-9)
        )

    def test_the_tornado_ranks_a_schedule_only_risk(self) -> None:
        # A risk with no cost of its own reaches the budget through the burn rate alone.
        # Ranking on the cost share would sort it to the bottom of its own tornado.
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="SCHED",
                    p_occurrence=0.8,
                    sched=pert(20, 60, 140),
                    mappings=(
                        RiskMappingInput(
                            mapping_type="duration_driver", activity_ids=("A001",)
                        ),
                    ),
                ),
                RiskInput(risk_id=2, code="COST", cost=pert(0, 10_000, 30_000)),
            ),
            schedule=chain([100], uncertain=False),
            config=RunConfig(iterations=5000, seed=13, burn_rate_per_day=50_000.0),
        )
        top = run(req).result.risk_sensitivity[0]
        assert top.code == "SCHED"
        assert top.cost_variance_share == pytest.approx(0.0)
        assert top.schedule_variance_share > 0.9

    def test_the_histogram_covers_the_whole_series(self, simple_request) -> None:
        h = run(simple_request).result.total_cost.histogram
        assert sum(h.counts) == simple_request.config.iterations
        assert len(h.edges) == len(h.counts) + 1


class TestStatisticalRegression:
    """Pinned numbers. A change that moves these is a change to the answer."""

    def test_a_uniform_cost_reproduces_its_analytic_percentiles(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="U",
                    cost=DistributionSpec(kind="uniform", lo=0.0, hi=1_000_000.0),
                ),
            ),
            config=RunConfig(iterations=10_000, seed=1, centered_lhs=True),
        )
        s = run(req).result.total_cost
        # Centred strata put the k-th order statistic at exactly (k + 0.5) / n, and
        # np.percentile interpolates linearly between them. Pinning that arithmetic is
        # the point: it fixes the sampler and the percentile convention together, and the
        # convention is what someone reconciling a P80 against a spreadsheet will hit.
        n = 10_000
        for p in (10, 50, 80, 95):
            k = (n - 1) * p / 100.0
            assert p_of(s.percentiles, p) == pytest.approx(
                (k + 0.5) / n * 1_000_000.0, abs=1e-6
            )

    def test_a_pert_cost_reproduces_its_exact_mean(self) -> None:
        req = SimulationRequest(
            risks=(RiskInput(risk_id=1, code="P", cost=pert(100, 300, 900)),),
            config=RunConfig(iterations=20_000, seed=1, centered_lhs=True),
        )
        # The standard Beta-PERT mean, (lo + 4*ml + hi) / 6.
        assert run(req).result.risk_cost.mean == pytest.approx(2200.0 / 6.0, rel=1e-4)

    def test_a_bernoulli_risk_reproduces_its_expected_value(self) -> None:
        req = SimulationRequest(
            risks=(
                RiskInput(
                    risk_id=1,
                    code="B",
                    p_occurrence=0.3,
                    cost=DistributionSpec(kind="point", lo=1_000_000.0, hi=1_000_000.0),
                ),
            ),
            config=RunConfig(iterations=10_000, seed=1, centered_lhs=True),
        )
        assert run(req).result.risk_cost.mean == pytest.approx(300_000.0, rel=1e-6)

    def test_the_headline_figure_is_pinned(self, simple_request) -> None:
        # Golden value. If this moves, the engine version must move with it and the
        # reason must land in REFERENCE.md.
        r = run(simple_request).result
        assert p_of(r.contingency.contingency, 80) == pytest.approx(
            3_576_700.294165, abs=1e-4
        )
        assert r.delay_days.mean == pytest.approx(35.846612, abs=1e-6)
        assert p_of(r.total_cost.percentiles, 50) == pytest.approx(
            27_020_995.346093, abs=1e-4
        )
        assert (
            r.manifest.inputs_sha256
            == "b8087ed407aaf42bd42ccfcd1f8fa9bd2843e00bef33eb29a464d51224c33e85"
        )
