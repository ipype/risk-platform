"""What a mitigation package bought, measured as the difference between two runs.

4.4 built the residual register and deliberately claimed nothing about it. This module is
where the claim finally gets made, and it is made the only way it can be defended: run the
baseline register, run the residual register, subtract. Nothing here multiplies factors
together, because the interaction between correlated risks and the critical path does not
decompose into per-risk factors — that is the whole reason the platform owns a sampler.

Four rules shape the module.

**One sign convention, stated once.** Every figure is ``reduction = before - after``:
positive means the package took something away, which is what a mitigation is supposed to
do. A negative reduction is a real finding, not an error, and it is surfaced rather than
clamped.

**A pair is refused before it is computed.** Two runs are comparable only if they differ in
the scenario and in nothing else — same scope, same schedule version, same iterations, same
seed, same sampling plan, same base cost, same burn rate. :func:`pairing_issues` is the
whole guard, and it names every field that moved rather than saying no. A delta across a
changed seed is Monte Carlo noise wearing a result's clothes, and a delta across a changed
burn rate is measuring the burn rate.

**The delta carries its own error bar.** A P80 estimated from ten thousand iterations has a
standard error, and on a small register the difference between two runs is routinely
smaller than it. :func:`percentile_standard_error` estimates it from the stored S-curve's
local slope; :func:`compare` reports the pair's combined error as an explicit **upper
bound** and warns when the reduction does not clear it. The alternative — a confident
"saves 1.2m" that is noise — is the single most expensive thing this module could produce.

**Plan cost stays outside the contingency.** Action budgets are deterministic and additive;
contingency is a percentile and is not (invariant 1). The two sit side by side. The one
place they meet is ``net_at_percentile``, which subtracts rather than sums, is named for
the percentile it was taken at, and carries a line in ``basis`` saying so. Everything in
``basis`` is an approximation declared on the face of the result rather than buried here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

__all__ = [
    "Comparison",
    "CurveRow",
    "CriticalityMover",
    "Reduction",
    "RiskMover",
    "SeriesReduction",
    "compare",
    "contingency_at",
    "pairing_issues",
    "percentile_at",
    "percentile_standard_error",
]

#: Run fields that must match for a pair to mean anything. ``scenario`` is deliberately
#: absent: it is the one thing that is *supposed* to differ.
PAIRED_FIELDS = (
    "scope_id",
    "schedule_version_id",
    "iterations",
    "seed",
    "sampling",
    "base_cost",
    "burn_rate_per_day",
)

#: Human labels for the above, for a message an analyst can act on.
_FIELD_LABELS = {
    "scope_id": "project",
    "schedule_version_id": "schedule version",
    "iterations": "iteration count",
    "seed": "seed",
    "sampling": "sampling plan",
    "base_cost": "base cost",
    "burn_rate_per_day": "burn rate",
}

#: Below this the two figures are treated as the same number rather than a movement.
_EPS = 1e-9


# --------------------------------------------------------------------------------------
# the pure half
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reduction:
    """One quantity, before and after, and what the package took off it."""

    before: float | None = None
    after: float | None = None
    #: ``before - after``. Positive means the package reduced it.
    reduction: float | None = None
    #: ``reduction / before``, ``None`` when the baseline is zero or missing.
    reduction_pct: float | None = None

    @classmethod
    def of(cls, before: float | None, after: float | None) -> "Reduction":
        if before is None or after is None:
            return cls(before=before, after=after)
        cut = before - after
        pct = None if abs(before) < _EPS else cut / before
        return cls(before=before, after=after, reduction=cut, reduction_pct=pct)


@dataclass(frozen=True)
class SeriesReduction:
    """A simulated quantity compared at its mean and at the chosen percentile."""

    label: str
    units: str
    mean: Reduction
    at_percentile: Reduction
    #: Estimated standard error of ``at_percentile.reduction``, as an upper bound.
    #: ``None`` when either S-curve is too flat or too coarse to read a density off.
    standard_error: float | None = None
    #: The reduction is smaller than the error bar, so it is not distinguishable from
    #: Monte Carlo noise at this iteration count.
    within_noise: bool = False


@dataclass(frozen=True)
class CurveRow:
    """One percentile of the two S-curves, drawn together."""

    p: float
    before: float
    after: float
    reduction: float


@dataclass(frozen=True)
class RiskMover:
    """One risk's share of the answer, before and after."""

    risk_id: int
    code: str
    title: str
    #: ``retired`` / ``reduced`` / ``unchanged`` / ``increased`` / ``entered``.
    movement: str
    share_before: float | None
    share_after: float | None
    contribution_before: float | None
    contribution_after: float | None
    contribution_reduction: float | None
    rank_before: int | None
    rank_after: int | None


@dataclass(frozen=True)
class CriticalityMover:
    """One activity's criticality index, before and after."""

    activity_id: str
    code: str
    name: str
    index_before: float | None
    index_after: float | None
    #: ``after - before``: a path that got *more* critical is the interesting direction
    #: here, so this one is not a reduction and is not named like one.
    index_change: float | None


@dataclass
class Comparison:
    """The whole before/after answer, plus everything it rests on."""

    percentile: float = 80.0
    #: The headline: contingency, base cost excluded, at ``percentile``.
    contingency: SeriesReduction | None = None
    total_cost: SeriesReduction | None = None
    risk_cost: SeriesReduction | None = None
    delay_days: SeriesReduction | None = None
    finish_day: SeriesReduction | None = None
    schedule_driven_cost: SeriesReduction | None = None

    #: The two S-curves on one percentile grid.
    curve: list[CurveRow] = field(default_factory=list)

    #: Deterministic, additive, and never inside a contingency figure.
    plan_budget: float = 0.0
    plan_sched_days: float = 0.0
    plan_unpriced_count: int = 0

    #: ``contingency reduction / plan budget``. ``None`` when the package has no priced
    #: action, because a ratio over zero is not infinity, it is unanswered.
    benefit_cost_ratio: float | None = None
    #: ``contingency reduction - plan budget``, at ``percentile`` and nowhere else.
    net_at_percentile: float | None = None

    risk_movers: list[RiskMover] = field(default_factory=list)
    criticality_movers: list[CriticalityMover] = field(default_factory=list)

    risk_count_before: int = 0
    risk_count_after: int = 0
    retired_count: int = 0

    #: Approximations and conventions, stated on the face of the result.
    basis: list[str] = field(default_factory=list)
    #: Things that should stop a reader from quoting the number as it stands.
    warnings: list[str] = field(default_factory=list)


def percentile_at(points: Iterable[Any], p: float) -> float | None:
    """Read one percentile out of a stored ``percentiles`` tuple.

    Exact match only. The grid is the run's own and interpolating between two of its
    entries would invent a figure the engine never computed, which is precisely the habit
    this codebase refuses everywhere else.
    """
    for point in points or ():
        value = _get(point, "p")
        if value is not None and abs(float(value) - p) < 1e-6:
            return _float(_get(point, "value"))
    return None


def contingency_at(contingency_view: Any, p: float) -> float | None:
    """Contingency at ``p``: total cost less the base, as the engine already computed it."""
    if contingency_view is None:
        return None
    return percentile_at(_get(contingency_view, "contingency") or (), p)


def percentile_standard_error(summary: Any, p: float) -> float | None:
    """Approximate standard error of the ``p``-th percentile of a stored series.

    ``SE ≈ sqrt(p(1-p)/n) / f(x_p)``, the standard large-sample result for a sample
    quantile. The density ``f(x_p)`` is not available — per-iteration arrays are not
    persisted (see ``models/simulation.py``) — so it is estimated from the slope of the
    stored 101-point S-curve across a window either side of ``p``. That makes this an
    approximation of an approximation and it is labelled as one everywhere it surfaces.

    ``None`` rather than a number when the curve is flat across the window: a zero density
    would divide out to an infinite error, and "cannot tell" is the honest reading of a
    series whose S-curve does not move there.
    """
    curve = list(_get(summary, "s_curve") or ())
    iterations = _get(summary, "iterations")
    if len(curve) < 3 or not iterations:
        return None
    n = int(iterations)
    if n < 2:
        return None

    q = p / 100.0
    if not 0.0 < q < 1.0:
        return None

    # A window wide enough to survive the curve's own quantisation and narrow enough to
    # still be local. Five percentile points either side is one twentieth of the range.
    window = 0.05
    lo_q, hi_q = max(0.0, q - window), min(1.0, q + window)
    lo_x, hi_x = _curve_value(curve, lo_q), _curve_value(curve, hi_q)
    if lo_x is None or hi_x is None:
        return None

    spread = hi_x - lo_x
    if abs(spread) < _EPS:
        return None
    density = (hi_q - lo_q) / spread
    if density <= 0:
        return None
    return math.sqrt(q * (1.0 - q) / n) / density


def pairing_issues(before: Any, after: Any) -> list[str]:
    """Why these two runs cannot be subtracted, in the reader's terms. Empty means go.

    The scenario check is first because it is the one people get backwards: pairing a
    post-mitigation run as the baseline reports the package as making things worse by
    exactly the amount it improved them.
    """
    issues: list[str] = []

    if _get(before, "scenario") != "pre_mitigation":
        issues.append(
            "The baseline run must be a pre-mitigation run; this one is "
            f"{_get(before, 'scenario')!r}."
        )
    if _get(after, "scenario") != "post_mitigation":
        issues.append(
            "The treated run must be a post-mitigation run; this one is "
            f"{_get(after, 'scenario')!r}."
        )
    if _get(before, "id") is not None and _get(before, "id") == _get(after, "id"):
        issues.append("A run cannot be compared with itself.")

    for name in PAIRED_FIELDS:
        left, right = _get(before, name), _get(after, name)
        if left != right:
            label = _FIELD_LABELS.get(name, name)
            issues.append(
                f"The two runs used a different {label} ({left!r} and {right!r}). The "
                "difference between them would be measuring that, not the package."
            )

    for run, which in ((before, "baseline"), (after, "treated")):
        status = _get(run, "status")
        if status != "succeeded":
            issues.append(f"The {which} run has not succeeded (status {status!r}).")

    engines = {_get(before, "engine_version"), _get(after, "engine_version")}
    engines.discard(None)
    if len(engines) > 1:
        issues.append(
            "The two runs were produced by different engine versions "
            f"({', '.join(sorted(str(e) for e in engines))}), so the difference between "
            "them includes whatever changed in the engine."
        )

    return issues


def compare(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    percentile: float = 80.0,
    plan_budget: float = 0.0,
    plan_sched_days: float = 0.0,
    plan_unpriced_count: int = 0,
    seed_shared: bool = True,
) -> Comparison:
    """Subtract two stored :class:`~app.sim.engine.SimulationResult` payloads.

    Takes the serialised results rather than the ORM rows so the whole of this function is
    testable from two dictionaries, which is what the unit tests do.
    """
    out = Comparison(
        percentile=percentile,
        plan_budget=plan_budget,
        plan_sched_days=plan_sched_days,
        plan_unpriced_count=plan_unpriced_count,
    )
    if before is None or after is None:
        out.warnings.append(
            "One of the two runs has no result yet, so there is nothing to compare."
        )
        return out

    # -- the headline -----------------------------------------------------------------
    cont_before = contingency_at(before.get("contingency"), percentile)
    cont_after = contingency_at(after.get("contingency"), percentile)
    if cont_before is None or cont_after is None:
        out.warnings.append(
            f"Neither run reports a P{percentile:g} contingency. The percentile grid is "
            "fixed at run time, so comparing at a percentile the runs did not compute "
            "would mean inventing it."
        )
    total_before = before.get("total_cost")
    total_after = after.get("total_cost")

    se = _combined_error(total_before, total_after, percentile)
    reduction = None if cont_before is None or cont_after is None else cont_before - cont_after
    out.contingency = SeriesReduction(
        label="Contingency",
        units="currency",
        mean=Reduction.of(
            _float((before.get("contingency") or {}).get("mean_total_cost")),
            _float((after.get("contingency") or {}).get("mean_total_cost")),
        ),
        at_percentile=Reduction.of(cont_before, cont_after),
        standard_error=se,
        within_noise=bool(se is not None and reduction is not None and abs(reduction) < se),
    )

    out.total_cost = _series(before.get("total_cost"), after.get("total_cost"), "Total cost", percentile)
    out.risk_cost = _series(before.get("risk_cost"), after.get("risk_cost"), "Risk cost", percentile)
    out.delay_days = _series(before.get("delay_days"), after.get("delay_days"), "Delay", percentile)
    out.finish_day = _series(before.get("finish_day"), after.get("finish_day"), "Finish", percentile)
    out.schedule_driven_cost = _series(
        before.get("schedule_driven_cost"),
        after.get("schedule_driven_cost"),
        "Schedule-driven cost",
        percentile,
    )

    # -- the two curves on one grid ---------------------------------------------------
    out.curve = _curve_rows(before.get("total_cost"), after.get("total_cost"))

    # -- what the package cost, kept structurally apart -------------------------------
    if reduction is not None:
        out.net_at_percentile = reduction - plan_budget
        if plan_budget > _EPS:
            out.benefit_cost_ratio = reduction / plan_budget

    # -- who moved --------------------------------------------------------------------
    out.risk_movers = _risk_movers(
        before.get("risk_sensitivity") or [], after.get("risk_sensitivity") or []
    )
    out.criticality_movers = _criticality_movers(
        before.get("activity_criticality") or [], after.get("activity_criticality") or []
    )
    out.risk_count_before = len(before.get("risk_sensitivity") or [])
    out.risk_count_after = len(after.get("risk_sensitivity") or [])
    out.retired_count = sum(1 for m in out.risk_movers if m.movement == "retired")

    # -- what the reader has to know to quote any of it -------------------------------
    out.basis.extend(_basis_lines(percentile, seed_shared, plan_budget))
    out.warnings.extend(_warning_lines(out, reduction, se, percentile))
    return out


# --------------------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------------------


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _curve_value(curve: Sequence[Any], q: float) -> float | None:
    """Linear read of an S-curve at cumulative probability ``q``.

    Interpolating *within* a stored curve is not the same act as inventing a percentile
    the run never computed: the curve is a 101-point sampling of a monotone function the
    engine did compute, and reading between two of its own points is what a curve is for.
    """
    points = sorted(
        (
            (float(_get(p, "p")), float(_get(p, "x")))
            for p in curve
            if _get(p, "p") is not None and _get(p, "x") is not None
        )
    )
    if not points:
        return None
    if q <= points[0][0]:
        return points[0][1]
    if q >= points[-1][0]:
        return points[-1][1]
    for (p0, x0), (p1, x1) in zip(points, points[1:]):
        if p0 <= q <= p1:
            if abs(p1 - p0) < _EPS:
                return x0
            t = (q - p0) / (p1 - p0)
            return x0 + t * (x1 - x0)
    return points[-1][1]


def _combined_error(before: Any, after: Any, p: float) -> float | None:
    """Upper bound on the error of the difference of two percentiles.

    ``sqrt(se_a^2 + se_b^2)`` is the independent case. The two runs share a seed and a
    sampling plan by construction, so they are positively correlated and the true error is
    smaller than this. Reporting the loose bound is the safe direction: it can only make a
    reduction look less certain than it is, never more.
    """
    left = percentile_standard_error(before, p)
    right = percentile_standard_error(after, p)
    if left is None or right is None:
        return None
    return math.sqrt(left * left + right * right)


def _series(before: Any, after: Any, label: str, p: float) -> SeriesReduction | None:
    if before is None or after is None:
        return None
    b_at = percentile_at(_get(before, "percentiles") or (), p)
    a_at = percentile_at(_get(after, "percentiles") or (), p)
    se = _combined_error(before, after, p)
    at = Reduction.of(b_at, a_at)
    return SeriesReduction(
        label=label,
        units=str(_get(before, "units") or ""),
        mean=Reduction.of(_float(_get(before, "mean")), _float(_get(after, "mean"))),
        at_percentile=at,
        standard_error=se,
        within_noise=bool(
            se is not None and at.reduction is not None and abs(at.reduction) < se
        ),
    )


def _curve_rows(before: Any, after: Any) -> list[CurveRow]:
    """Both S-curves on the baseline's grid, so they can be drawn on one axis."""
    if before is None or after is None:
        return []
    left = list(_get(before, "s_curve") or ())
    right = list(_get(after, "s_curve") or ())
    if not left or not right:
        return []
    rows: list[CurveRow] = []
    for point in left:
        q = _float(_get(point, "p"))
        x = _float(_get(point, "x"))
        if q is None or x is None:
            continue
        y = _curve_value(right, q)
        if y is None:
            continue
        rows.append(CurveRow(p=q * 100.0, before=x, after=y, reduction=x - y))
    return rows


def _rank(rows: Sequence[Any]) -> dict[int, int]:
    ordered = sorted(
        (r for r in rows if _get(r, "risk_id") is not None),
        key=lambda r: -abs(_float(_get(r, "combined_variance_share")) or 0.0),
    )
    return {int(_get(r, "risk_id")): i + 1 for i, r in enumerate(ordered)}


def _risk_movers(before: Sequence[Any], after: Sequence[Any]) -> list[RiskMover]:
    """Per-risk before and after, ranked by how much the package took off each.

    Driven by the union of the two runs, not by the treated set. A risk that only appears
    in the treated run is a real event — an estimate elicited between the two runs, or a
    residual with no baseline behind it — and calling it ``entered`` is more useful than
    dropping it.
    """
    b_by_id = {int(_get(r, "risk_id")): r for r in before if _get(r, "risk_id") is not None}
    a_by_id = {int(_get(r, "risk_id")): r for r in after if _get(r, "risk_id") is not None}
    b_rank, a_rank = _rank(before), _rank(after)

    movers: list[RiskMover] = []
    for risk_id in sorted(set(b_by_id) | set(a_by_id)):
        b_row, a_row = b_by_id.get(risk_id), a_by_id.get(risk_id)
        source = b_row if b_row is not None else a_row
        b_contrib = _float(_get(b_row, "p80_contribution"))
        a_contrib = _float(_get(a_row, "p80_contribution"))

        if a_row is None:
            movement = "retired"
        elif b_row is None:
            movement = "entered"
        elif b_contrib is None or a_contrib is None:
            movement = "unchanged"
        elif a_contrib < b_contrib - _EPS:
            movement = "reduced"
        elif a_contrib > b_contrib + _EPS:
            movement = "increased"
        else:
            movement = "unchanged"

        cut = None if b_contrib is None or a_contrib is None else b_contrib - a_contrib
        movers.append(
            RiskMover(
                risk_id=risk_id,
                code=str(_get(source, "code") or ""),
                title=str(_get(source, "title") or ""),
                movement=movement,
                share_before=_float(_get(b_row, "combined_variance_share")),
                share_after=_float(_get(a_row, "combined_variance_share")),
                contribution_before=b_contrib,
                contribution_after=a_contrib,
                contribution_reduction=cut,
                rank_before=b_rank.get(risk_id),
                rank_after=a_rank.get(risk_id),
            )
        )

    movers.sort(key=lambda m: -(m.contribution_reduction or 0.0))
    return movers


def _criticality_movers(before: Sequence[Any], after: Sequence[Any]) -> list[CriticalityMover]:
    """Activities whose criticality index moved, biggest movement first.

    Both directions are kept. A package that pulls one path off the critical path almost
    always pushes another one onto it, and a list that showed only the improvements would
    hide the activity that is about to become the problem.
    """
    b_by_id = {str(_get(r, "activity_id")): r for r in before if _get(r, "activity_id")}
    a_by_id = {str(_get(r, "activity_id")): r for r in after if _get(r, "activity_id")}

    movers: list[CriticalityMover] = []
    for activity_id in sorted(set(b_by_id) | set(a_by_id)):
        b_row, a_row = b_by_id.get(activity_id), a_by_id.get(activity_id)
        source = b_row if b_row is not None else a_row
        b_ci = _float(_get(b_row, "criticality_index"))
        a_ci = _float(_get(a_row, "criticality_index"))
        movers.append(
            CriticalityMover(
                activity_id=activity_id,
                code=str(_get(source, "code") or ""),
                name=str(_get(source, "name") or ""),
                index_before=b_ci,
                index_after=a_ci,
                index_change=None if b_ci is None or a_ci is None else a_ci - b_ci,
            )
        )

    movers.sort(key=lambda m: -abs(m.index_change or 0.0))
    return movers


def _basis_lines(percentile: float, seed_shared: bool, plan_budget: float) -> list[str]:
    lines = [
        "Every figure is stated as a reduction: baseline minus treated. A positive number "
        "means the package took that much off; a negative one means the residual register "
        "is worse than the baseline.",
        "Contingency is a percentile of an integrated distribution, not a sum of parts. "
        "The two runs are compared percentile to percentile, never by adding or "
        "subtracting components (invariant 1).",
        f"Standard errors are estimated from each run's stored S-curve at P{percentile:g}, "
        "because per-iteration samples are not retained. They are an approximation of the "
        "sampling error, not of model error, and they say nothing about whether the "
        "elicited inputs were right.",
        "The error shown on a difference treats the two runs as independent, which is an "
        "upper bound: they share a seed, so the true error is smaller. A reduction that "
        "clears this bar is real; one that does not may still be.",
    ]
    if seed_shared:
        lines.append(
            "Both runs used the same seed and sampling plan, so wherever the register is "
            "unchanged they draw the same numbers and the difference between them is the "
            "package rather than the sampler. Retiring a risk shifts that alignment for "
            "the risks after it in the register."
        )
    else:
        lines.append(
            "The two runs used different seeds, so part of the difference between them is "
            "sampling noise that a matched pair would have cancelled."
        )
    if plan_budget > _EPS:
        lines.append(
            f"Plan cost is deterministic and additive; contingency is not. The net figure "
            f"subtracts the package's budget from the contingency reduction at "
            f"P{percentile:g} and is meaningful only at that percentile. The two are never "
            "added into one number."
        )
    return lines


def _warning_lines(
    out: Comparison, reduction: float | None, se: float | None, percentile: float
) -> list[str]:
    warnings: list[str] = []
    if reduction is None:
        return warnings

    if reduction < -_EPS:
        warnings.append(
            f"The treated register produces a *higher* P{percentile:g} contingency than "
            f"the baseline, by {abs(reduction):,.0f}. Either a treatment widened a range, "
            "a residual was hand-edited upward, or the difference is sampling noise."
        )
    if se is not None and abs(reduction) < se:
        warnings.append(
            f"The reduction ({reduction:,.0f}) is smaller than the estimated error on the "
            f"difference ({se:,.0f}). At this iteration count the two runs are not "
            "distinguishable; raise iterations before quoting a figure."
        )
    if out.plan_unpriced_count:
        warnings.append(
            f"{out.plan_unpriced_count} action(s) in this package carry neither a budget "
            "nor a duration, so the cost side of every ratio below is understated."
        )
    if out.retired_count:
        warnings.append(
            f"{out.retired_count} risk(s) were retired outright. A retired risk leaves the "
            "register entirely, which shifts the sampler's alignment for the risks after "
            "it — the matched-seed cancellation is partial for those."
        )
    if out.risk_count_after > out.risk_count_before:
        warnings.append(
            "The treated run carries more risks than the baseline. Something was added to "
            "the register between the two runs, so the difference is not the package alone."
        )
    return warnings
