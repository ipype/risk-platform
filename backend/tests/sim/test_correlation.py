"""Iman-Conover: invariant 2, and the repair real driver tagging needs."""

from __future__ import annotations

import numpy as np
import pytest

from app.sim.correlation import (
    induce_rank_correlation,
    nearest_correlation,
    spearman,
    spearman_to_pearson,
)
from app.sim.sampling import spawn_generator, uniform_matrix

TARGET = np.array(
    [
        [1.0, 0.8, 0.5, 0.0, -0.3],
        [0.8, 1.0, 0.4, 0.0, 0.0],
        [0.5, 0.4, 1.0, 0.2, 0.0],
        [0.0, 0.0, 0.2, 1.0, 0.6],
        [-0.3, 0.0, 0.0, 0.6, 1.0],
    ]
)


def _sample(n: int = 8000, v: int = 5) -> np.ndarray:
    return uniform_matrix(spawn_generator(11, 0), n, v)


class TestInducedCorrelation:
    def test_lands_close_to_the_target(self) -> None:
        out, rep = induce_rank_correlation(_sample(), TARGET, spawn_generator(11, 1))
        assert rep.max_pair_error < 0.02
        assert np.allclose(spearman(out), TARGET, atol=0.02)

    def test_preserves_every_marginal_exactly(self) -> None:
        u = _sample()
        out, _ = induce_rank_correlation(u, TARGET, spawn_generator(11, 1))
        assert np.array_equal(np.sort(out, axis=0), np.sort(u, axis=0))

    def test_preserves_latin_hypercube_stratification(self) -> None:
        n = 4000
        u = uniform_matrix(spawn_generator(11, 0), n, 5)
        out, _ = induce_rank_correlation(u, TARGET, spawn_generator(11, 1))
        assert all(
            np.array_equal(np.sort((out[:, j] * n).astype(int)), np.arange(n))
            for j in range(5)
        )

    def test_identity_target_is_a_no_op(self) -> None:
        u = _sample()
        out, rep = induce_rank_correlation(u, np.eye(5), spawn_generator(11, 1))
        assert out is u
        assert rep.max_pair_error == 0.0

    def test_single_column_is_a_no_op(self) -> None:
        u = _sample(200, 1)
        out, _ = induce_rank_correlation(u, np.eye(1), spawn_generator(11, 1))
        assert out is u

    def test_strong_correlation_is_reached_not_undershot(self) -> None:
        # Feeding a Spearman target straight to the Cholesky undershoots at the strong
        # end, always in the direction of a thinner tail. The conversion is why it does
        # not here.
        target = np.array([[1.0, 0.9], [0.9, 1.0]])
        out, _ = induce_rank_correlation(
            _sample(8000, 2), target, spawn_generator(11, 1)
        )
        assert spearman(out)[0, 1] == pytest.approx(0.9, abs=0.01)


class TestRepair:
    def test_reports_a_matrix_that_was_already_valid(self) -> None:
        fixed, min_eig, delta = nearest_correlation(TARGET)
        assert delta == 0.0 and min_eig > 0

    def test_repairs_a_contradictory_matrix_and_says_so(self) -> None:
        # Three risks pairwise correlated at 0.9 through different drivers is not a
        # statement about any joint distribution, and nothing in the tagging UI stops it.
        bad = np.array([[1, 0.9, 0.9], [0.9, 1, -0.9], [0.9, -0.9, 1]], dtype=float)
        fixed, min_eig, delta = nearest_correlation(bad)
        assert min_eig < 0 and delta > 0
        assert np.all(np.linalg.eigvalsh(fixed) > -1e-12)
        assert np.allclose(np.diag(fixed), 1.0)

    def test_run_surfaces_the_repair_rather_than_swallowing_it(self) -> None:
        bad = np.array([[1, 0.9, 0.9], [0.9, 1, -0.9], [0.9, -0.9, 1]], dtype=float)
        _, rep = induce_rank_correlation(_sample(3000, 3), bad, spawn_generator(11, 1))
        assert rep.repaired and rep.repair_max_delta > 0
        assert rep.notes and "positive definite" in rep.notes[0]


class TestTransform:
    def test_is_the_gaussian_copula_identity(self) -> None:
        rho_s = np.array([[1.0, 0.5], [0.5, 1.0]])
        rho_p = spearman_to_pearson(rho_s)
        back = (6.0 / np.pi) * np.arcsin(rho_p / 2.0)
        assert back[0, 1] == pytest.approx(0.5, abs=1e-12)

    def test_leaves_the_diagonal_alone(self) -> None:
        assert np.allclose(np.diag(spearman_to_pearson(TARGET)), 1.0)
