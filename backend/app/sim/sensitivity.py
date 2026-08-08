"""Which risks move the answer, and which activities decide the finish date.

Two different questions with two different right tools:

**Risks.** ``variance_share`` is the primary ranking, not rank correlation. The total is a
sum of the risk contributions plus the base and the burn-rate term, and for any sum
``sum_i cov(x_i, total) / var(total) == 1`` exactly — correlated or not. The risk shares
therefore add to one only once the burn-rate term is counted alongside them, which is why
the engine reports its share separately rather than leaving the remainder unexplained. So
the shares decompose the whole and can be read as "this risk owns eleven percent of the
spread". A
tornado built on correlation coefficients cannot say that: the bars do not add to
anything, and a rare high-impact risk ranks below a frequent trivial one because
correlation is blind to scale. Spearman is reported alongside because it answers the other
question people ask — how reliably this risk moves with the outcome, independent of size.

**Activities.** The criticality index is the fraction of iterations in which the activity
sat on the critical path, and it is exact and nearly free once the backward pass has run.
Duration sensitivity is the linear correlation between the activity's sampled duration and
the project finish, accumulated as running sums so that a five-thousand-activity network
never has to hold every sampled duration at once. Cruciality is the product of the two,
which is what separates "this activity is always critical but never varies" from "this
activity decides the date".

The schedule sensitivity index is the same idea with scale put back in: criticality index
times the ratio of the activity's duration spread to the project's. It is the metric
Primavera Risk Analysis reports and the one a reviewer coming from that tool will ask for
by name, and it disagrees with cruciality precisely where scale matters — a two-day
activity whose duration correlates perfectly with the finish is crucial and barely
sensitive, because its entire range is two days. Both are carried. Ranking on either alone
loses a real reading.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

__all__ = [
    "ActivityCriticality",
    "DurationAccumulator",
    "RiskSensitivity",
    "rank_correlation_with",
    "variance_shares",
]


class RiskSensitivity(BaseModel):
    model_config = ConfigDict(frozen=True)

    risk_id: int
    code: str = ""
    title: str = ""
    #: Share of total-cost variance owned by this risk's *cost* draw. Exact: the shares
    #: over all risks plus the burn-rate term's share sum to one, because covariance is
    #: linear over the sum that makes up the total. Signed, because a risk that moves
    #: against the total is worth seeing rather than hiding behind an absolute value.
    cost_variance_share: float = 0.0
    #: Share of total-cost variance reaching the total through the burn-rate term.
    #: Apportioned, not exact: delay is a max over network paths, so no exact additive
    #: split of it among risks exists. The burn term's own share *is* exact and these
    #: numbers sum to it; what is approximate is only how it divides. ``None`` when the
    #: risk drives no activity.
    schedule_variance_share: float | None = None
    #: The two above, added. What the tornado is ranked on — a risk that reaches the
    #: budget only through delay is invisible on the cost share alone, and on a
    #: schedule-driven project that is most of them.
    combined_variance_share: float = 0.0
    #: Share of the variance of *project delay* — not of cost — owned by this risk's own
    #: sampled schedule impact. ``cov(impact_j, delay) / var(delay)``, the same estimator
    #: as :func:`variance_shares` pointed at a different target, and the answer to "which
    #: risk drives the date" in the same units as "which risk drives the budget".
    #:
    #: These do **not** sum to one, and that is the point rather than a defect. Delay is a
    #: maximum over network paths, so it is not the sum of the per-risk impacts: the
    #: remainder is the schedule's own background duration uncertainty plus whatever the
    #: path switching contributes. Normalising to one would hide exactly that, and hiding
    #: it is how a register with three risks gets credited for a date driven mostly by an
    #: uncertain baseline. The unexplained remainder is reported on the face of the chart.
    #:
    #: ``None`` when the risk drives no activity, matching ``schedule_variance_share`` —
    #: a zero there would read as measured rather than as inapplicable.
    delay_variance_share: float | None = None
    spearman_total_cost: float = 0.0
    spearman_delay: float | None = None
    mean_contribution: float = 0.0
    p80_contribution: float = 0.0
    #: Fraction of iterations in which the risk occurred. Converges on ``p_occurrence``;
    #: a visible gap means too few iterations for a rare risk to be represented.
    realised_frequency: float = 0.0


class ActivityCriticality(BaseModel):
    model_config = ConfigDict(frozen=True)

    activity_id: str
    code: str = ""
    name: str = ""
    #: Fraction of iterations with total float at or below the critical tolerance.
    criticality_index: float = 0.0
    mean_total_float_days: float = 0.0
    #: Pearson correlation between this activity's sampled duration and the project
    #: finish. ``None`` when the duration never varied, which is the honest answer rather
    #: than a zero that reads like a measured result.
    duration_sensitivity: float | None = None
    #: Criticality index times absolute duration sensitivity. Hulett's cruciality: being
    #: on the critical path matters only if the duration also moves.
    cruciality: float = 0.0
    #: Standard deviation of this activity's sampled duration. Zero for a deterministic
    #: activity, which is a measured zero rather than a missing one.
    duration_sd_days: float = 0.0
    #: Schedule sensitivity index, the Primavera Risk Analysis definition: criticality
    #: index times the ratio of this activity's duration spread to the project finish
    #: spread. Scale-aware where cruciality is correlation-based, so the two disagree
    #: exactly where it matters — a short activity perfectly correlated with the finish
    #: ranks top on cruciality and low on SSI, because moving it by its whole range moves
    #: the date by very little. Both are reported; neither is a substitute for the other.
    schedule_sensitivity_index: float = 0.0
    is_inserted: bool = False


def rank_correlation_with(
    columns: NDArray[np.float64], target: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Spearman correlation of every column of ``columns`` against ``target``.

    Constant columns — a risk that occurred in every iteration with a point impact, an
    activity with no uncertainty — have no rank order and return zero rather than a
    divide-by-zero warning.
    """
    n = target.size
    if columns.size == 0:
        return np.zeros(columns.shape[1] if columns.ndim == 2 else 0)

    def _ranks(x: NDArray[np.float64]) -> NDArray[np.float64]:
        order = np.argsort(x, axis=0, kind="stable")
        out = np.empty_like(x)
        idx = np.arange(n, dtype=np.float64)
        if x.ndim == 1:
            out[order] = idx
        else:
            for j in range(x.shape[1]):
                out[order[:, j], j] = idx
        return out

    rc = _ranks(columns)
    rt = _ranks(target)
    rc = rc - rc.mean(axis=0)
    rt = rt - rt.mean()

    denom = np.sqrt((rc * rc).sum(axis=0)) * np.sqrt((rt * rt).sum())
    num = (rc * rt[:, None]).sum(axis=0)
    return np.divide(num, denom, out=np.zeros_like(num), where=denom > 0)


def variance_shares(
    contributions: NDArray[np.float64], total: NDArray[np.float64]
) -> NDArray[np.float64]:
    """Each column's share of the variance of ``total``.

    ``cov(x_j, total) / var(total)``. Exact and additive over any decomposition of
    ``total`` into a sum, which is what makes a tornado built on it readable as a
    breakdown rather than as a ranking with arbitrary units.
    """
    if contributions.size == 0:
        return np.zeros(contributions.shape[1] if contributions.ndim == 2 else 0)
    tv = float(total.var())
    if tv <= 0.0:
        return np.zeros(contributions.shape[1])
    tc = total - total.mean()
    cc = contributions - contributions.mean(axis=0)
    return (cc * tc[:, None]).mean(axis=0) / tv


class DurationAccumulator:
    """Running sums for the duration-versus-finish correlation, one pass, chunked.

    Holding every sampled duration would be ``iterations x activities`` floats — 400 MB
    for a five-thousand-activity schedule at ten thousand iterations, and it is needed for
    nothing else. Five running sums per activity give the same Pearson coefficient in
    forty kilobytes.

    Sums are accumulated about a fixed offset (the deterministic duration and the
    deterministic finish) rather than about zero. On a schedule whose finish is in the
    thousands of days the raw second moment loses most of its significant digits to
    cancellation; centring on a value already close to the mean keeps them.
    """

    __slots__ = ("n", "sum_d", "sum_d2", "sum_df", "sum_f", "sum_f2", "_od", "_of")

    def __init__(
        self, offset_duration: NDArray[np.float64], offset_finish: float
    ) -> None:
        a = offset_duration.size
        self._od = np.asarray(offset_duration, dtype=np.float64)
        self._of = float(offset_finish)
        self.n = 0
        self.sum_d = np.zeros(a, dtype=np.float64)
        self.sum_d2 = np.zeros(a, dtype=np.float64)
        self.sum_df = np.zeros(a, dtype=np.float64)
        self.sum_f = 0.0
        self.sum_f2 = 0.0

    def add(self, dur: NDArray[np.float64], finish: NDArray[np.float64]) -> None:
        d = dur - self._od
        f = finish - self._of
        self.n += d.shape[0]
        self.sum_d += d.sum(axis=0)
        self.sum_d2 += (d * d).sum(axis=0)
        self.sum_df += d.T @ f
        self.sum_f += float(f.sum())
        self.sum_f2 += float((f * f).sum())

    def spreads(self) -> tuple[NDArray[np.float64], float]:
        """Duration standard deviation per activity, and the project finish's.

        Population moments, taken about the same offsets as everything else here. They
        differ from ``SeriesSummary.sd`` — which uses ``ddof=1`` — by one part in the
        iteration count, far below anything the ratio in an SSI is read to.
        """
        n = float(self.n)
        if n < 2:
            return np.zeros(self.sum_d.size, dtype=np.float64), 0.0
        vd = self.sum_d2 / n - (self.sum_d / n) ** 2
        vf = self.sum_f2 / n - (self.sum_f / n) ** 2
        return np.sqrt(np.maximum(vd, 0.0)), math.sqrt(max(vf, 0.0))

    def correlation(self) -> NDArray[np.float64]:
        """Pearson correlation per activity. NaN where the duration never varied."""
        n = float(self.n)
        if n < 2:
            return np.full(self.sum_d.size, np.nan)
        cov = self.sum_df / n - (self.sum_d / n) * (self.sum_f / n)
        vd = self.sum_d2 / n - (self.sum_d / n) ** 2
        vf = self.sum_f2 / n - (self.sum_f / n) ** 2
        denom = np.sqrt(np.maximum(vd, 0.0) * max(vf, 0.0))
        out = np.full(self.sum_d.size, np.nan)
        live = denom > 1e-12
        out[live] = np.clip(cov[live] / denom[live], -1.0, 1.0)
        return out
