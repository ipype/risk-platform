"""Turning a column of iterations into something a report can carry.

Percentiles are taken once, at the end, over the integrated total. That sentence is
invariant 1 and it is the reason this module exists at all rather than being three lines
inside the engine: the temptation to percentile the parts and add them up is strongest
exactly here, where the parts are all conveniently to hand.

:class:`ContingencyView` states the case by carrying the wrong answer alongside the right
one. ``P80(risk cost) + burn rate * P80(delay)`` assumes the cost tail and the schedule
tail are the same iteration, which is a claim of perfect rank correlation that nobody
made. On a real register it runs several percent high, and it is the single most common
finding in a QSRA review. Showing the gap costs three lines and settles the argument
before it starts.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

__all__ = [
    "ContingencyView",
    "CurvePoint",
    "DeterministicView",
    "Histogram",
    "PercentilePoint",
    "RunManifest",
    "SeriesSummary",
    "summarise",
]


class PercentilePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    p: float
    value: float


class CurvePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: float
    p: float


class Histogram(BaseModel):
    model_config = ConfigDict(frozen=True)

    edges: tuple[float, ...]
    counts: tuple[int, ...]


class SeriesSummary(BaseModel):
    """One simulated quantity, described.

    Carries the S-curve as well as the percentile list because the two are read for
    different things — a number to put in a budget, and the shape that says how much
    confidence that number deserves.
    """

    model_config = ConfigDict(frozen=True)

    label: str
    units: str
    iterations: int
    mean: float
    sd: float
    minimum: float
    maximum: float
    percentiles: tuple[PercentilePoint, ...]
    s_curve: tuple[CurvePoint, ...]
    histogram: Histogram


class RunManifest(BaseModel):
    """Everything needed to reproduce the run, and nothing that varies between runs.

    No timestamps. A pure function that stamped the clock would hash differently on every
    call, and when it was recorded is the persistence layer's business anyway.
    """

    model_config = ConfigDict(frozen=True)

    engine_version: str
    seed: int
    iterations: int
    sampling: str
    centered_lhs: bool
    #: Resolved, not requested. Chunking changes which spawned stream each iteration draws
    #: from, so replaying a run means replaying its chunk size too.
    chunk_size: int
    inputs_sha256: str
    calendar_id: str | None = None


class DeterministicView(BaseModel):
    """The unsimulated baseline every result is measured against.

    The baseline finish comes from this engine's own forward pass over the deterministic
    durations, not from the dates in the imported schedule. Those dates came out of P6
    under constraints, calendars and progress overrides this pass does not model, so
    subtracting them would report the difference between two CPM engines as risk. Both
    numbers are carried so the gap stays visible.
    """

    model_config = ConfigDict(frozen=True)

    base_cost: float
    activities: int = 0
    relationships: int = 0
    inserted_activities: int = 0
    baseline_finish_day: float | None = None
    critical_activities: int = 0


class ContingencyView(BaseModel):
    """The headline numbers, plus the wrong way of getting them."""

    model_config = ConfigDict(frozen=True)

    base_cost: float
    mean_total_cost: float
    #: Total cost at each requested percentile, less the base. What goes in the budget.
    contingency: tuple[PercentilePoint, ...]
    #: The same figure computed by percentiling the parts and adding them. Reported so a
    #: reviewer can see what the correct arithmetic bought, never to be used.
    additive_error_at_p80: float | None = None
    additive_p80_total: float | None = None
    integrated_p80_total: float | None = None
    #: Exact split of total-cost variance between the risks' own cost draws and the
    #: burn-rate term. These sum to one; anything else means a bug, which is most of why
    #: they are reported rather than left implicit in the tornado.
    cost_variance_share: float = 1.0
    schedule_variance_share: float = 0.0


def summarise(
    values: NDArray[np.float64],
    *,
    label: str,
    units: str,
    percentiles: tuple[float, ...],
    s_curve_points: int,
    histogram_bins: int,
) -> SeriesSummary:
    """Describe one simulated quantity.

    Percentiles use linear interpolation between order statistics, which is NumPy's
    default and the convention every commercial risk tool reports. It matters only in the
    far tail at low iteration counts, and mentioning it is cheaper than having someone
    reconcile a P95 against a spreadsheet by hand.
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    n = v.size
    if n == 0:
        raise ValueError(f"cannot summarise an empty series ({label})")

    qs = np.asarray(percentiles, dtype=np.float64)
    pv = np.percentile(v, qs)

    grid = np.linspace(0.0, 100.0, s_curve_points)
    curve = np.percentile(v, grid)

    lo = float(v.min())
    hi = float(v.max())
    if hi <= lo:
        # A degenerate series still has to produce a drawable histogram rather than a
        # single zero-width bin that every renderer treats differently.
        hi = lo + 1.0
    counts, edges = np.histogram(v, bins=histogram_bins, range=(lo, hi))

    return SeriesSummary(
        label=label,
        units=units,
        iterations=n,
        mean=float(v.mean()),
        sd=float(v.std(ddof=1)) if n > 1 else 0.0,
        minimum=float(v.min()),
        maximum=float(v.max()),
        percentiles=tuple(
            PercentilePoint(p=float(q), value=float(x)) for q, x in zip(qs, pv)
        ),
        s_curve=tuple(
            CurvePoint(x=float(x), p=float(g) / 100.0) for g, x in zip(grid, curve)
        ),
        histogram=Histogram(
            edges=tuple(float(e) for e in edges),
            counts=tuple(int(c) for c in counts),
        ),
    )
