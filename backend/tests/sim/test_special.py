"""Numerics that would otherwise come from SciPy.

Checked against closed forms rather than against another implementation: the point of
owning these is that there is no second library in the image to compare with at runtime.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.sim.special import beta_pdf, beta_ppf, betainc, norm_ppf


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class TestNormPpf:
    @pytest.mark.parametrize(
        ("p", "expected"),
        [
            (0.5, 0.0),
            (0.975, 1.959963984540054),
            (0.99, 2.3263478740408408),
            (0.001, -3.090232306167813),
        ],
    )
    def test_known_quantiles(self, p: float, expected: float) -> None:
        assert norm_ppf(np.array([p]))[0] == pytest.approx(expected, abs=1e-10)

    def test_round_trips_through_the_cdf(self) -> None:
        u = np.linspace(1e-9, 1.0 - 1e-9, 20001)
        back = np.array([_phi(x) for x in norm_ppf(u)])
        assert np.max(np.abs(back - u)) < 1e-12

    def test_far_tail_keeps_its_digits(self) -> None:
        # Where a 10,000-iteration Latin hypercube puts its outermost stratum. The cheap
        # rational approximations this replaces lose accuracy from about here on.
        assert norm_ppf(np.array([5e-5]))[0] == pytest.approx(-3.8905918864, abs=1e-8)

    def test_is_monotone_and_antisymmetric(self) -> None:
        u = np.linspace(0.001, 0.999, 999)
        x = norm_ppf(u)
        assert np.all(np.diff(x) > 0)
        assert np.allclose(norm_ppf(u), -norm_ppf(1.0 - u), atol=1e-12)

    def test_endpoints_and_nonsense(self) -> None:
        out = norm_ppf(np.array([0.0, 1.0, -0.5, 1.5]))
        assert out[0] == -np.inf
        assert out[1] == np.inf
        assert np.isnan(out[2]) and np.isnan(out[3])


class TestBetainc:
    def test_uniform_case_is_the_identity(self) -> None:
        x = np.array([0.01, 0.3, 0.77, 0.999])
        assert np.allclose(betainc(1, 1, x), x, atol=1e-15)

    def test_closed_forms(self) -> None:
        x = np.array([0.05, 0.4, 0.95])
        assert np.allclose(betainc(1, 7.3, x), 1 - (1 - x) ** 7.3, atol=1e-14)
        assert np.allclose(betainc(3.7, 1, x), x**3.7, atol=1e-14)
        # I_0.5(2, 3) is exactly 11/16.
        assert betainc(2, 3, np.array([0.5]))[0] == pytest.approx(0.6875, abs=1e-14)

    def test_symmetry(self) -> None:
        x = np.array([0.13, 0.5, 0.88])
        assert np.allclose(
            betainc(2.5, 4.5, x), 1 - betainc(4.5, 2.5, 1 - x), atol=1e-14
        )

    def test_rejects_bad_shapes(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            betainc(0, 1, np.array([0.5]))


class TestBetaPpf:
    @pytest.mark.parametrize(
        ("a", "b"),
        [(1, 1), (1, 5), (5, 1), (2, 3), (4.2, 1.8), (1.0001, 9.9), (20, 3), (50, 50)],
    )
    def test_inverts_betainc(self, a: float, b: float) -> None:
        u = np.linspace(1e-6, 1 - 1e-6, 5000)
        assert np.max(np.abs(betainc(a, b, beta_ppf(a, b, u)) - u)) < 1e-11

    def test_is_monotone(self) -> None:
        u = np.linspace(1e-6, 1 - 1e-6, 5000)
        assert np.all(np.diff(beta_ppf(2.6, 5.4, u)) >= 0)

    @pytest.mark.parametrize(("a", "b"), [(2.6, 5.4), (4.0, 4.0), (1.2, 9.0)])
    def test_reproduces_exact_moments(self, a: float, b: float) -> None:
        n = 100_000
        x = beta_ppf(a, b, (np.arange(n) + 0.5) / n)
        assert x.mean() == pytest.approx(a / (a + b), abs=1e-6)
        expected_var = a * b / ((a + b) ** 2 * (a + b + 1))
        assert x.var() == pytest.approx(expected_var, abs=1e-6)

    def test_endpoints_clamp_to_the_support(self) -> None:
        out = beta_ppf(2, 3, np.array([0.0, 1.0]))
        assert out[0] == 0.0
        assert out[1] == 1.0

    def test_density_integrates_to_one(self) -> None:
        x = np.linspace(0, 1, 200_001)
        assert np.trapezoid(beta_pdf(3.0, 5.0, x), x) == pytest.approx(1.0, abs=1e-6)
