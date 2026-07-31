"""Rank correlation by the Iman-Conover method, plus the repair it needs to survive real
input.

Invariant 2: risks are not independent, and sampling them as if they were understates the
tail. Weather stops three trades at once; a labour market that is short stays short for
every package that needs it; escalation moves every commodity line together. Independent
draws let those cancel, and the P80 comes out confidently low.

Iman-Conover induces a target *rank* correlation by reordering each column's existing
values. Two properties follow from that and both matter here:

* the marginals survive untouched, so an elicited Beta-PERT is still exactly that
  Beta-PERT afterwards, and
* applied to the uniforms rather than the samples it leaves Latin hypercube
  stratification intact, since a permutation of a column still visits every stratum once.

Rank correlation rather than linear: it is invariant to the monotone transform each
uniform is about to pass through, so the number the analyst supplied is the number the
samples come out with, whatever shape sits on top.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from app.sim.errors import CorrelationNotRepairable
from app.sim.special import norm_ppf

__all__ = [
    "CorrelationReport",
    "induce_rank_correlation",
    "nearest_correlation",
    "spearman",
    "spearman_to_pearson",
]


class CorrelationReport(BaseModel):
    """What the correlation step actually did, as opposed to what it was asked to do.

    Reported rather than logged because a run has to be defensible after the fact: the
    difference between the requested matrix and the achieved one is a real property of the
    result, and a repair that moved a coefficient by 0.3 is a finding.
    """

    model_config = ConfigDict(frozen=True)

    variables: int
    #: Largest absolute change made by the positive-definite repair, 0.0 if none was
    #: needed.
    repair_max_delta: float = 0.0
    repaired: bool = False
    #: Smallest eigenvalue of the requested matrix. Negative means it was not a valid
    #: correlation matrix as supplied.
    min_eigenvalue: float = 1.0
    #: Largest gap between requested and achieved rank correlation across all pairs.
    max_pair_error: float = 0.0
    #: Mean absolute gap across all off-diagonal pairs.
    mean_pair_error: float = 0.0
    notes: tuple[str, ...] = ()


def spearman(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Spearman rank correlation matrix of the columns of ``x``.

    Ties are broken by position rather than averaged. Every caller here feeds continuous
    uniforms where exact ties do not occur, and average ranks would cost a sort per column
    to handle a case that cannot arise.
    """
    n, v = x.shape
    if v == 0:
        return np.zeros((0, 0))
    ranks = np.empty_like(x)
    order = np.argsort(x, axis=0, kind="stable")
    idx = np.arange(n, dtype=np.float64)
    for j in range(v):
        ranks[order[:, j], j] = idx
    centred = ranks - ranks.mean(axis=0)
    sd = np.sqrt((centred * centred).sum(axis=0))
    sd[sd == 0.0] = 1.0
    normed = centred / sd
    return normed.T @ normed


def spearman_to_pearson(rho: NDArray[np.float64]) -> NDArray[np.float64]:
    """Convert a target Spearman matrix to the Pearson matrix a normal copula needs.

    Iman-Conover builds its ordering from normal scores, so the correlation imposed on
    those scores is a *linear* one. Under a Gaussian copula the two are related by
    ``rho_S = (6/pi) * arcsin(rho_P / 2)``; this is that identity turned around. Feeding
    the Spearman target straight into the Cholesky step, as the original 1982 paper does,
    lands a little short at the strong end — about 0.02 low at a target of 0.9, which is
    small but systematic and always in the direction of understating the tail.
    """
    out = 2.0 * np.sin(np.pi * rho / 6.0)
    np.fill_diagonal(out, 1.0)
    return out


def nearest_correlation(
    c: NDArray[np.float64], *, floor: float = 1e-8
) -> tuple[NDArray[np.float64], float, float]:
    """Nearest usable correlation matrix by eigenvalue clipping.

    Returns the repaired matrix, the smallest eigenvalue of the input, and the largest
    absolute change made.

    A matrix assembled from shared-driver tagging is very often not positive definite —
    three risks pairwise correlated at 0.8 through different drivers is not a consistent
    statement about any joint distribution, and nothing in the tagging interface stops an
    analyst saying it. Clipping the negative eigenvalues to a small positive floor and
    rescaling to a unit diagonal gives the closest thing that *is* consistent. Higham's
    alternating projections would land marginally closer; the extra fidelity is well
    inside the elicitation noise on numbers that came from a workshop.
    """
    if c.shape[0] == 0:
        return c, 1.0, 0.0

    sym = 0.5 * (c + c.T)
    vals, vecs = np.linalg.eigh(sym)
    min_eig = float(vals.min())
    if min_eig > floor:
        return sym, min_eig, 0.0

    clipped = vecs @ np.diag(np.maximum(vals, floor)) @ vecs.T
    d = np.sqrt(np.diag(clipped))
    if np.any(d <= 0):
        raise CorrelationNotRepairable("a repaired variance came out non-positive")
    repaired = clipped / np.outer(d, d)
    np.fill_diagonal(repaired, 1.0)
    repaired = np.clip(repaired, -1.0, 1.0)
    return repaired, min_eig, float(np.abs(repaired - sym).max())


def induce_rank_correlation(
    u: NDArray[np.float64],
    target: NDArray[np.float64],
    rng: np.random.Generator,
    *,
    convert_to_pearson: bool = True,
) -> tuple[NDArray[np.float64], CorrelationReport]:
    """Reorder the columns of ``u`` to carry the target Spearman correlation.

    ``u`` is returned unchanged when there is nothing to correlate — fewer than two
    columns, or an identity target — so a register with no driver tagging pays nothing for
    the machinery and, more usefully, produces byte-identical output to a run with the
    correlation step compiled out.
    """
    n, v = u.shape
    if v < 2:
        return u, CorrelationReport(variables=v)

    off = target - np.eye(v)
    if not np.any(np.abs(off) > 1e-12):
        return u, CorrelationReport(variables=v)

    notes: list[str] = []
    wanted = spearman_to_pearson(target) if convert_to_pearson else target.copy()
    repaired, min_eig, delta = nearest_correlation(wanted)
    if delta > 0.0:
        notes.append(
            "The requested correlation matrix was not positive definite and was "
            "adjusted to the nearest matrix that is. Pairwise values that contradict "
            "each other cannot all be honoured at once."
        )

    try:
        p = np.linalg.cholesky(repaired)
    except (
        np.linalg.LinAlgError
    ) as exc:  # pragma: no cover - repair should prevent this
        raise CorrelationNotRepairable(str(exc)) from exc

    # van der Waerden scores: one fixed set of normal quantiles, independently shuffled
    # per column. Using the same score vector everywhere is what makes the achieved
    # correlation depend only on the ordering, not on which normals happened to be drawn.
    base = norm_ppf((np.arange(1, n + 1, dtype=np.float64)) / (n + 1.0))
    scores = np.empty((n, v), dtype=np.float64)
    for j in range(v):
        scores[:, j] = rng.permutation(base)

    # Strip the correlation the random shuffles happened to leave behind before imposing
    # the wanted one. Skipping this is the classic Iman-Conover bug: at n = 1000 the
    # accidental correlation between two shuffled columns is around 0.03, and it rides
    # straight through into the result.
    t = np.corrcoef(scores, rowvar=False)
    t = np.atleast_2d(t)
    t_fixed, _, _ = nearest_correlation(t)
    q = np.linalg.cholesky(t_fixed)
    shaped = scores @ np.linalg.solve(q, p.T)

    out = np.empty_like(u)
    for j in range(v):
        ordering = np.argsort(np.argsort(shaped[:, j], kind="stable"), kind="stable")
        out[:, j] = np.sort(u[:, j])[ordering]

    achieved = spearman(out)
    err = np.abs(achieved - target)
    np.fill_diagonal(err, 0.0)
    pairs = v * (v - 1)
    return out, CorrelationReport(
        variables=v,
        repair_max_delta=delta,
        repaired=delta > 0.0,
        min_eigenvalue=min_eig,
        max_pair_error=float(err.max()) if pairs else 0.0,
        mean_pair_error=float(err.sum() / pairs) if pairs else 0.0,
        notes=tuple(notes),
    )
