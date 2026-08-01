"""Where cost and date land *together*.

The marginal P80 cost and the marginal P80 date are each 80% confident. The pair is not.
``P(cost <= c80 and delay <= d80)`` runs around 65-70% on a typical register, because the
two tails are not the same iteration — which is the same mistake as adding percentiles
(invariant 1), one dimension further out. A sanction paper that quotes a P80 budget beside
a P80 date and calls the package "P80" is overstating the commitment it is asking for, and
nothing in the marginal S-curves shows it.

So this module reports the joint distribution instead of the two marginals:

**The frontier.** For a target confidence ``t`` there is no single (cost, date) point, there
is a curve: every pair whose joint cumulative probability is exactly ``t``. Accept more
delay and the cost you must carry falls; commit to an earlier date and it rises. The curve
is the trade-off, and it is the thing a sponsor actually chooses a point on.

**The balanced point.** The one place on the frontier where the cost and the date are held
to the same marginal stringency. It is the honest answer to "what is our P80 package" when
nobody wants to choose a trade-off, and it always sits at a marginal percentile *above* the
target — around P88 on each axis for a joint P80.

**The scatter.** Every iteration is a realised (delay, cost) pair, so the cloud is the joint
distribution itself and the frontier is a level set of it. Thinned for transport, never
smoothed: the shape of the cloud carries the correlation, and with a burn rate in play its
lower-left edge is a straight line of that slope, which is worth being able to see.

Exact by construction, not fitted. No copula, no bivariate normal, no kernel — the sample
*is* the joint distribution and reading a quantile off it costs one partition. Fitting a
parametric joint to a sample we already hold would add an assumption to buy nothing.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

from app.sim.sensitivity import rank_correlation_with

__all__ = [
    "JointConfidence",
    "JointFrontier",
    "JointPoint",
    "joint_confidence",
]

#: Most points carried on one frontier. The curve is smooth and monotone; past about this
#: many the extra points are indistinguishable on any screen and cost payload.
_FRONTIER_POINTS = 33

#: Most scatter points serialised. Ten thousand pairs is roughly a quarter-megabyte of
#: JSON on every run detail fetch and renders as a solid block of ink anyway.
_SCATTER_CAP = 1200

#: Below this the joint reading is noise: a frontier at P95 needs at least a few hundred
#: iterations above the threshold to place a quantile at all.
_MIN_ITERATIONS = 200


class JointPoint(BaseModel):
    """One (cost, date) pair on a frontier.

    Both marginal percentiles are carried because the gap between them and the target is
    the finding. A joint P80 point sitting at P88 cost and P88 date says the eight points
    of extra stringency on each axis are what buying joint P80 costs.
    """

    model_config = ConfigDict(frozen=True)

    delay_days: float
    finish_day: float
    total_cost: float
    #: Marginal percentile of this delay in the delay distribution, 0-100.
    delay_p: float
    #: Marginal percentile of this cost in the total-cost distribution, 0-100.
    cost_p: float


class JointFrontier(BaseModel):
    """Every (cost, date) pair the run is ``target`` percent confident of meeting."""

    model_config = ConfigDict(frozen=True)

    target: float
    points: tuple[JointPoint, ...]
    #: The point on this frontier where cost and date carry equal marginal stringency.
    #: ``None`` only when the frontier is empty.
    balanced: JointPoint | None = None


class JointConfidence(BaseModel):
    """The joint cost-schedule picture: frontiers, the marginal trap, and the cloud."""

    model_config = ConfigDict(frozen=True)

    iterations: int
    frontiers: tuple[JointFrontier, ...] = ()

    #: The percentile at which the marginal pair is quoted for the headline comparison.
    marginal_pair_target: float = 80.0
    marginal_cost: float = 0.0
    marginal_delay_days: float = 0.0
    marginal_finish_day: float = 0.0
    #: ``P(cost <= marginal_cost and delay <= marginal_delay_days)``, 0-1. The number that
    #: says what quoting the two marginals side by side is actually worth.
    joint_at_marginal_pair: float = 0.0

    #: Spearman correlation between total cost and delay. Positive by construction when a
    #: burn rate is set; positive without one whenever risks carry both impacts.
    cost_delay_correlation: float = 0.0
    #: True when a burn rate priced the delay into the cost, which makes part of the
    #: dependence mechanical rather than elicited and puts a straight lower-left edge of
    #: that slope on the cloud.
    burn_rate_coupled: bool = False

    #: ``(delay_days, total_cost)`` per retained iteration. Thinned by a fixed stride, not
    #: sampled: no generator, nothing to reproduce, and the rows are exchangeable anyway.
    scatter: tuple[tuple[float, float], ...] = ()
    scatter_stride: int = 1


def _percentile_rank(sorted_values: NDArray[np.float64], x: float) -> float:
    """Marginal percentile of ``x`` against an ascending sample, 0-100."""
    n = sorted_values.size
    if n == 0:
        return 0.0
    return 100.0 * float(np.searchsorted(sorted_values, x, side="right")) / float(n)


def _frontier(
    cost_by_delay: NDArray[np.float64],
    delay_sorted: NDArray[np.float64],
    cost_sorted: NDArray[np.float64],
    baseline_finish: float,
    target: float,
) -> JointFrontier:
    """One level set of the joint CDF, walked along the delay axis.

    For a delay threshold admitting the ``m`` earliest-finishing iterations, the joint
    probability of also landing under a cost ``c`` is the count of those ``m`` whose cost is
    at or below ``c``, over the full iteration count. Hitting the target therefore means
    reaching the ``k``-th smallest cost among those ``m``, where ``k = ceil(target * n)`` —
    exact, and undefined for ``m < k`` because no cost threshold, however generous, can make
    a delay threshold that rare more likely than it is.

    Walking ``m`` upward walks the frontier down: the ``k``-th smallest over a superset can
    only fall. The curve is monotone by construction rather than by smoothing.
    """
    n = cost_by_delay.size
    k = int(math.ceil(target / 100.0 * n))
    k = max(1, min(k, n))
    if k > n:
        return JointFrontier(target=target, points=())

    span = n - k + 1
    count = min(_FRONTIER_POINTS, span)
    ms = np.unique(np.linspace(k, n, count).astype(np.int64))

    points: list[JointPoint] = []
    for m in ms:
        window = cost_by_delay[:m]
        cost = float(np.partition(window, k - 1)[k - 1])
        delay = float(delay_sorted[m - 1])
        points.append(
            JointPoint(
                delay_days=delay,
                finish_day=baseline_finish + delay,
                total_cost=cost,
                delay_p=100.0 * float(m) / float(n),
                cost_p=_percentile_rank(cost_sorted, cost),
            )
        )

    balanced = None
    if points:
        balanced = min(points, key=lambda p: abs(p.cost_p - p.delay_p))
    return JointFrontier(target=target, points=tuple(points), balanced=balanced)


def joint_confidence(
    total_cost: NDArray[np.float64],
    delay: NDArray[np.float64],
    *,
    targets: tuple[float, ...],
    baseline_finish: float = 0.0,
    marginal_pair_target: float = 80.0,
    burn_rate_coupled: bool = False,
    scatter_cap: int = _SCATTER_CAP,
) -> JointConfidence | None:
    """Build the joint view from the two per-iteration series.

    ``targets`` comes from the run's own percentile grid rather than from a new setting.
    A frontier below the median is a curve nobody commits against, so the caller is
    expected to filter; this function honours whatever it is given.

    Returns ``None`` for a run too short to place a joint quantile in — which is a refusal,
    not an empty result, because a frontier drawn from forty iterations looks exactly like
    one drawn from ten thousand.
    """
    c = np.asarray(total_cost, dtype=np.float64).ravel()
    d = np.asarray(delay, dtype=np.float64).ravel()
    if c.size != d.size:
        raise ValueError("cost and delay series must be the same length")
    n = c.size
    if n < _MIN_ITERATIONS:
        return None

    order = np.argsort(d, kind="stable")
    delay_sorted = d[order]
    cost_by_delay = c[order]
    cost_sorted = np.sort(c)

    live = tuple(t for t in sorted(targets) if 0.0 < t < 100.0)
    frontiers = tuple(
        _frontier(cost_by_delay, delay_sorted, cost_sorted, baseline_finish, t)
        for t in live
    )

    mc = float(np.percentile(c, marginal_pair_target))
    md = float(np.percentile(d, marginal_pair_target))
    joint = float(np.mean((c <= mc) & (d <= md)))

    stride = max(1, int(math.ceil(n / scatter_cap)))
    idx = np.arange(0, n, stride)
    scatter = tuple(
        (round(float(dv), 3), round(float(cv), 2))
        for dv, cv in zip(d[idx], c[idx], strict=True)
    )

    rho = float(rank_correlation_with(d[:, None], c)[0])

    return JointConfidence(
        iterations=n,
        frontiers=frontiers,
        marginal_pair_target=marginal_pair_target,
        marginal_cost=mc,
        marginal_delay_days=md,
        marginal_finish_day=baseline_finish + md,
        joint_at_marginal_pair=joint,
        cost_delay_correlation=rho,
        burn_rate_coupled=burn_rate_coupled,
        scatter=scatter,
        scatter_stride=stride,
    )
