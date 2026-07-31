"""Uniform generation: Latin hypercube by default, plain Monte Carlo when asked.

The engine samples uniforms first and transforms them last, so everything that shapes a
run — stratification here, rank correlation in :mod:`app.sim.correlation` — operates on
one rectangular ``(iterations, variables)`` matrix of numbers in ``(0, 1)``.

Latin hypercube is the default because it buys convergence for nothing. Each variable's
range is cut into ``n`` equal-probability strata and each stratum is visited exactly once,
so the marginal is reproduced almost exactly at any ``n`` instead of only in the limit. On
a contingency curve that shows up where it matters: the P80 of a plain Monte Carlo run
wanders by a percent or so between seeds at ten thousand iterations, which is enough for
two runs of the same register to disagree in review.

What it does not do is fix the joint distribution — stratifying each margin says nothing
about how they move together. That is Iman-Conover's job, and it reorders these columns
without disturbing a single stratum, because a permutation of a column's values is still
one draw from every stratum.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

__all__ = ["uniform_matrix", "spawn_generator"]


def spawn_generator(seed: int, *key: int) -> np.random.Generator:
    """A generator addressed by a fixed key rather than by draw order.

    Reproducibility (invariant 6) means the same inputs give the same answer, and that has
    to survive the engine processing iterations in chunks to stay inside a memory budget.
    Drawing from one running stream would tie the numbers to how the work happened to be
    divided; addressing a spawned stream by ``(seed, key)`` ties them to the run
    definition, which is the thing that gets hashed and stored.
    """
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=tuple(key)))


def uniform_matrix(
    rng: np.random.Generator,
    n: int,
    v: int,
    *,
    method: str = "lhs",
    centered: bool = False,
) -> NDArray[np.float64]:
    """An ``(n, v)`` matrix of uniforms in the open interval ``(0, 1)``.

    ``centered`` places each Latin hypercube draw at its stratum midpoint instead of
    randomly inside it. That removes the last of the sampling noise and makes a run fully
    deterministic in its marginals, which is what a statistical regression test wants; it
    is wrong for production because the strata stop being a sample and the variance of the
    result is no longer estimable.
    """
    if n <= 0 or v < 0:
        raise ValueError("iterations must be positive and variables non-negative")
    if v == 0:
        return np.empty((n, 0), dtype=np.float64)

    if method == "mc":
        u = rng.random((n, v))
    elif method == "lhs":
        strata = np.arange(n, dtype=np.float64)
        u = np.empty((n, v), dtype=np.float64)
        jitter = np.full((n, v), 0.5) if centered else rng.random((n, v))
        for j in range(v):
            u[:, j] = (rng.permutation(strata) + jitter[:, j]) / n
    else:
        raise ValueError(f"unknown sampling method: {method!r}")

    # Nudge off the endpoints: an inverse CDF at exactly 0 or 1 is an infinity for any
    # unbounded shape, and the ones here would return their bound and quietly bias it.
    return np.clip(u, 1e-12, 1.0 - 1e-12)
