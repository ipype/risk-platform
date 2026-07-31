"""Sampling shapes. Every check is against the shape's own exact moments."""

from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from app.sim.distributions import DistributionSpec, PointMass, spec_from_moments

from .conftest import pert, tri

GRID = (np.arange(200_000) + 0.5) / 200_000


class TestThreePoint:
    def test_pert_matches_its_exact_moments(self) -> None:
        lo, ml, hi, lam = 100.0, 300.0, 900.0, 4.0
        x = pert(lo, ml, hi).ppf(GRID)
        mean = (lo + lam * ml + hi) / (lam + 2)
        var = (mean - lo) * (hi - mean) / (lam + 3)
        assert x.mean() == pytest.approx(mean, rel=1e-6)
        assert x.var() == pytest.approx(var, rel=1e-4)
        assert x.min() >= lo and x.max() <= hi

    def test_triangular_matches_its_exact_moments(self) -> None:
        a, c, b = 5.0, 15.0, 40.0
        x = tri(a, c, b).ppf(GRID)
        var = (a * a + c * c + b * b - a * c - a * b - c * b) / 18.0
        assert x.mean() == pytest.approx((a + c + b) / 3.0, rel=1e-6)
        assert x.var() == pytest.approx(var, rel=1e-4)

    def test_triangular_mode_sits_where_the_cdf_splits(self) -> None:
        spec = tri(0.0, 2.0, 10.0)
        assert spec.ppf(np.array([0.2]))[0] == pytest.approx(2.0, abs=1e-12)

    def test_pert_carries_less_tail_than_triangular(self) -> None:
        # The whole reason both shapes exist. Triangular treats soft bounds as live
        # outcomes and inflates contingency; PERT gathers weight on the mode.
        p = np.percentile(pert(0, 10, 100).ppf(GRID), 90)
        t = np.percentile(tri(0, 10, 100).ppf(GRID), 90)
        assert p < t

    def test_lambda_narrows_the_spread(self) -> None:
        loose = pert(0, 50, 100, lam=2.0).ppf(GRID)
        tight = pert(0, 50, 100, lam=12.0).ppf(GRID)
        assert tight.var() < loose.var()


class TestSimpleShapes:
    def test_uniform(self) -> None:
        x = DistributionSpec(kind="uniform", lo=-200.0, hi=800.0).ppf(GRID)
        assert x.mean() == pytest.approx(300.0, rel=1e-9)
        assert x.var() == pytest.approx(1000.0**2 / 12.0, rel=1e-5)

    def test_point_ignores_the_uniform(self) -> None:
        x = DistributionSpec(kind="point", lo=42.0, hi=42.0).ppf(GRID)
        assert np.all(x == 42.0)

    def test_degenerate_pert_collapses_to_its_bound(self) -> None:
        spec = DistributionSpec(kind="pert", lo=7.0, ml=7.0, hi=7.0)
        assert spec.is_degenerate
        assert np.all(spec.ppf(GRID) == 7.0)


class TestPointShapes:
    def test_cumulative_is_piecewise_linear(self) -> None:
        spec = DistributionSpec(
            kind="cumulative",
            points=(
                PointMass(x=0.0, p=0.0),
                PointMass(x=100.0, p=0.5),
                PointMass(x=300.0, p=1.0),
            ),
        )
        x = spec.ppf(GRID)
        assert np.percentile(x, 50) == pytest.approx(100.0, abs=0.5)
        assert x.mean() == pytest.approx(0.5 * 50 + 0.5 * 200, rel=1e-3)

    def test_discrete_reproduces_its_masses(self) -> None:
        spec = DistributionSpec(
            kind="discrete",
            points=(
                PointMass(x=0.0, p=0.7),
                PointMass(x=1_000.0, p=0.2),
                PointMass(x=5_000.0, p=0.1),
            ),
        )
        x = spec.ppf(GRID)
        assert set(np.unique(x)) == {0.0, 1_000.0, 5_000.0}
        assert (x == 0.0).mean() == pytest.approx(0.7, abs=1e-3)
        assert (x == 5_000.0).mean() == pytest.approx(0.1, abs=1e-3)
        assert x.mean() == pytest.approx(spec.mean(), rel=1e-3)


class TestValidation:
    def test_rejects_inverted_bounds(self) -> None:
        with pytest.raises(ValidationError, match="below lo"):
            DistributionSpec(kind="uniform", lo=10.0, hi=1.0)

    def test_rejects_mode_outside_support(self) -> None:
        with pytest.raises(ValidationError, match="outside the support"):
            DistributionSpec(kind="triangular", lo=0.0, ml=99.0, hi=10.0)

    def test_rejects_cumulative_that_does_not_reach_one(self) -> None:
        with pytest.raises(ValidationError, match="p=0 to p=1"):
            DistributionSpec(
                kind="cumulative",
                points=(PointMass(x=0.0, p=0.0), PointMass(x=1.0, p=0.8)),
            )

    def test_rejects_discrete_masses_that_do_not_sum(self) -> None:
        with pytest.raises(ValidationError, match="not 1"):
            DistributionSpec(
                kind="discrete",
                points=(PointMass(x=0.0, p=0.3), PointMass(x=1.0, p=0.3)),
            )

    def test_is_frozen(self) -> None:
        with pytest.raises(ValidationError):
            tri(1, 2, 3).lo = 5.0


class TestSpecFromMoments:
    class _Moments:
        """Structurally what ``quant_validation.Moments`` is, without importing it."""

        def __init__(self, **kw: object) -> None:
            self.__dict__.update(kw)

    def test_trigen_collapses_to_triangular(self) -> None:
        m = self._Moments(
            kind="trigen", lo=-5.0, hi=60.0, ml=15.0, alpha=None, beta=None
        )
        spec = spec_from_moments(m)
        assert spec.kind == "triangular"
        assert spec.lo == -5.0 and spec.hi == 60.0

    def test_carries_pert_shape_parameters_through(self) -> None:
        m = self._Moments(kind="pert", lo=0.0, hi=10.0, ml=4.0, alpha=2.6, beta=3.4)
        spec = spec_from_moments(m)
        assert (spec.alpha, spec.beta) == (2.6, 3.4)

    def test_zero_width_support_becomes_a_point(self) -> None:
        m = self._Moments(kind="pert", lo=3.0, hi=3.0, ml=3.0, alpha=1.0, beta=1.0)
        assert spec_from_moments(m).kind == "point"

    def test_refuses_point_shapes_it_cannot_rebuild(self) -> None:
        m = self._Moments(kind="cumulative", lo=0.0, hi=1.0, ml=None)
        with pytest.raises(ValueError, match="cannot be rebuilt"):
            spec_from_moments(m)
