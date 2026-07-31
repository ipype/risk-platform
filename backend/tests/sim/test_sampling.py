"""Latin hypercube stratification, and the seeding that makes a run replayable."""

from __future__ import annotations

import numpy as np
import pytest

from app.sim.sampling import spawn_generator, uniform_matrix


def _strata_hit_once(u: np.ndarray) -> bool:
    n = u.shape[0]
    return all(
        np.array_equal(np.sort((u[:, j] * n).astype(int)), np.arange(n))
        for j in range(u.shape[1])
    )


class TestLatinHypercube:
    def test_every_stratum_is_visited_exactly_once(self) -> None:
        u = uniform_matrix(spawn_generator(1, 0), 2000, 6)
        assert _strata_hit_once(u)

    def test_marginals_are_reproduced_almost_exactly(self) -> None:
        u = uniform_matrix(spawn_generator(1, 0), 5000, 4)
        assert np.allclose(u.mean(axis=0), 0.5, atol=1e-3)

    def test_beats_plain_monte_carlo_on_percentile_stability(self) -> None:
        # The reason it is the default: a P80 that wanders between seeds is a P80 two
        # analysts will disagree about in review.
        lhs = [
            np.percentile(uniform_matrix(spawn_generator(s, 0), 2000, 1), 80)
            for s in range(12)
        ]
        mc = [
            np.percentile(
                uniform_matrix(spawn_generator(s, 0), 2000, 1, method="mc"), 80
            )
            for s in range(12)
        ]
        assert np.std(lhs) < np.std(mc) / 3

    def test_centered_removes_the_within_stratum_noise(self) -> None:
        u = uniform_matrix(spawn_generator(1, 0), 1000, 3, centered=True)
        assert np.allclose(np.sort(u[:, 0]), (np.arange(1000) + 0.5) / 1000)

    def test_never_returns_an_endpoint(self) -> None:
        u = uniform_matrix(spawn_generator(3, 0), 500, 2)
        assert u.min() > 0.0 and u.max() < 1.0

    def test_zero_variables_is_a_valid_empty_matrix(self) -> None:
        assert uniform_matrix(spawn_generator(1, 0), 100, 0).shape == (100, 0)

    def test_rejects_an_unknown_method(self) -> None:
        with pytest.raises(ValueError, match="unknown sampling method"):
            uniform_matrix(spawn_generator(1, 0), 10, 1, method="sobol")


class TestSpawning:
    def test_same_key_gives_the_same_stream(self) -> None:
        a = uniform_matrix(spawn_generator(99, 2, 5), 200, 3)
        b = uniform_matrix(spawn_generator(99, 2, 5), 200, 3)
        assert np.array_equal(a, b)

    def test_different_keys_are_independent_streams(self) -> None:
        a = uniform_matrix(spawn_generator(99, 2, 5), 200, 3)
        b = uniform_matrix(spawn_generator(99, 2, 6), 200, 3)
        assert not np.array_equal(a, b)

    def test_chunk_streams_do_not_depend_on_evaluation_order(self) -> None:
        # What makes chunked activity sampling reproducible: chunk 7's numbers are the
        # same whether or not chunks 0 to 6 were drawn first.
        forwards = [uniform_matrix(spawn_generator(5, 2, c), 64, 2) for c in range(8)]
        alone = uniform_matrix(spawn_generator(5, 2, 7), 64, 2)
        assert np.array_equal(forwards[7], alone)
