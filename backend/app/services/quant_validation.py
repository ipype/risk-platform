"""Validation and distribution derivation for quantitative risk estimates.

Pure module: no DB, no network, no framework imports. Everything here is a function of
its arguments, so the rules that decide whether a number is fit to simulate can be
property-tested in isolation from the API and the ORM.

Shape is per dimension. A risk's cost impact and its schedule impact routinely have
different shapes — a delay capped by a contractual milestone is triangular with a bound
that means something, while the cost it drags along is unbounded and PERT. One shared
``dist_type`` was expedient and wrong, and it becomes impossible once one dimension is a
cumulative curve and the other is a three-point.

``bound_interpretation`` stays shared. It records how the SME was questioned in that
session rather than a property of the number, and splitting it per dimension would only
invite combinations nobody meant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------
# vocabularies
# --------------------------------------------------------------------------------------

SCENARIOS = ("pre_mitigation", "post_mitigation")
DIST_TYPES = ("pert", "triangular", "trigen", "uniform", "cumulative", "discrete", "none")
BOUND_INTERPRETATIONS = ("absolute", "p10_p90", "p5_p95")
DAY_BASES = ("working", "calendar")
COST_BASES = ("absolute", "pct_of_base")
SOURCES = ("sme", "historical", "analyst", "agent_proposal")
CONFIDENCES = ("low", "medium", "high")
RATIONALE_KEYS = ("min", "ml", "max")

#: Shapes built from a three-point estimate.
THREE_POINT_DISTS = ("pert", "triangular", "trigen")
#: Shapes built from a list of points rather than a three-point.
POINT_DISTS = ("cumulative", "discrete")

#: Tail mass excluded on each side by a given interpretation of the elicited bounds.
#: ``absolute`` excludes nothing, which makes it the identity case in
#: :func:`absolute_bounds` rather than a special case in the caller.
_TAIL_MASS: dict[str, tuple[float, float]] = {
    "absolute": (0.0, 0.0),
    "p10_p90": (0.10, 0.10),
    "p5_p95": (0.05, 0.05),
}

SKEW_WARN_RATIO = 20.0
RARE_EVENT_P = 0.05
MAX_RATIONALE_CHARS = 4000
MASS_TOLERANCE = 1e-6


#: When to reach for each shape, and what it does to the answer if the choice is wrong.
#: Served from the API so the picker, the docs, and the validator cannot drift apart.
DISTRIBUTION_GUIDANCE: dict[str, dict] = {
    "pert": {
        "label": "PERT",
        "inputs": "three_point",
        "summary": "Smooth curve that gathers weight around the most likely value.",
        "use_when": (
            "The default for an SME three-point. Use it when the most likely value is a "
            "real judgement and the extremes are genuinely unlikely rather than merely "
            "possible."
        ),
        "avoid_when": (
            "The bounds are hard limits you want treated as reachable, or you have actual "
            "data — a cumulative curve will say more than three numbers can."
        ),
        "caution": (
            "Lambda controls how tightly weight gathers on the mode. Four is standard; "
            "raising it narrows the spread and lowers contingency. Never tune it to reach "
            "a number you already had in mind."
        ),
    },
    "triangular": {
        "label": "Triangular",
        "inputs": "three_point",
        "summary": "Straight lines from a hard minimum, through the mode, to a hard maximum.",
        "use_when": (
            "The bounds are real limits — a contract cap, a physical constraint, a "
            "regulatory floor — and you want them respected exactly."
        ),
        "avoid_when": (
            "The min and max are soft judgements. Triangular carries far more weight near "
            "the extremes than PERT does, so soft bounds get simulated as live outcomes "
            "and contingency inflates."
        ),
        "caution": (
            "The mean is the plain average of the three points, so each extreme pulls as "
            "hard as the mode. That is usually more tail than the SME intended."
        ),
    },
    "trigen": {
        "label": "Trigen",
        "inputs": "three_point",
        "summary": "Triangular whose min and max are percentiles, with true bounds solved for.",
        "use_when": (
            "The honest default for anything an SME calls a minimum and a maximum. People "
            "give numbers they would defend — roughly a P10 and a P90 — because the real "
            "extremes feel absurd to say out loud. Trigen takes them at their word and "
            "recovers the bounds those percentiles imply."
        ),
        "avoid_when": "The bounds really are absolute. Use triangular and mean it.",
        "caution": (
            "It deliberately widens past the numbers you typed, which is the point: it "
            "restores the tail contingency exists to cover. Expect the P80 to rise. Needs a "
            "percentile interpretation — trigen on absolute bounds is just triangular."
        ),
    },
    "uniform": {
        "label": "Uniform",
        "inputs": "bounds_only",
        "summary": "Every value in the range equally likely. No most likely value.",
        "use_when": (
            "You know the range and have genuinely no basis for preferring any value inside "
            "it — an unpriced scope with a known envelope, an outcome bounded by two "
            "published figures."
        ),
        "avoid_when": (
            "Anyone has an opinion about the likely value. Uniform asserts total ignorance "
            "within the range, and in practice it is usually a placeholder nobody revisited."
        ),
        "caution": (
            "The most likely field is ignored. If you want to fill it in, you have more "
            "information than uniform can carry."
        ),
    },
    "cumulative": {
        "label": "Cumulative",
        "inputs": "points",
        "summary": "A curve you supply: values against cumulative probability.",
        "use_when": (
            "You have data or a fully elicited curve — reference-class history, a bid "
            "spread, a set of analogous outcomes. It is also the only shape here that can "
            "express something other than a single peak."
        ),
        "avoid_when": (
            "You have three points and are tempted to invent the ones in between. Inventing "
            "interior points manufactures confidence you do not have."
        ),
        "caution": (
            "Points define a piecewise-linear curve, so the density underneath is a step "
            "function. Sparse points look blocky, and that blockiness is honest — it is "
            "exactly as much as you actually know."
        ),
    },
    "discrete": {
        "label": "Discrete",
        "inputs": "points",
        "summary": "A fixed set of outcomes, each with its own probability.",
        "use_when": (
            "The outcomes are genuinely countable — a claim settles at one of three figures, "
            "a permit is granted or refused with a known cost either way."
        ),
        "avoid_when": (
            "The outcome is continuous and you are chopping it into buckets for convenience. "
            "That discards everything between the buckets."
        ),
        "caution": (
            "Probabilities must sum to one, and they describe the magnitude given the risk "
            "occurs. Do not add a zero-impact outcome to mean 'it does not happen' — that is "
            "what the occurrence probability is for, and encoding it twice understates the "
            "risk."
        ),
    },
    "none": {
        "label": "Not assessed",
        "inputs": "none",
        "summary": "This dimension is not quantified.",
        "use_when": "The risk has no impact on this dimension, or it has not been elicited yet.",
        "avoid_when": "You have numbers. Record them.",
        "caution": (
            "A risk with neither dimension assessed cannot be simulated and will not reach "
            "the contingency."
        ),
    },
}


@dataclass(frozen=True)
class Issue:
    """One validation finding. ``error`` blocks persistence, ``warning`` does not."""

    severity: str  # "error" | "warning"
    field: str
    message: str


@dataclass(frozen=True)
class Moments:
    """Support and moments for one dimension, after interpretation is applied."""

    kind: str
    lo: float
    hi: float
    mean: float
    variance: float
    sd: float
    ml: float | None = None
    alpha: float | None = None
    beta: float | None = None


@dataclass
class DimensionInput:
    """One dimension — cost or schedule — of one estimate."""

    dist: str = "none"
    lo: float | None = None
    ml: float | None = None
    hi: float | None = None
    pert_lambda: float = 4.0
    points: list[dict] | None = None
    rationale: dict | None = None

    @property
    def assessed(self) -> bool:
        return self.dist != "none"


@dataclass
class EstimateInput:
    p_occurrence: float = 1.0
    is_variability: bool = False
    bound_interpretation: str = "absolute"
    cost: DimensionInput = field(default_factory=DimensionInput)
    sched: DimensionInput = field(default_factory=DimensionInput)
    cost_basis: str = "absolute"
    sched_day_basis: str = "working"
    source: str = "sme"
    confidence: str = "medium"


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------------------
# bound recovery
# --------------------------------------------------------------------------------------


def absolute_bounds(
    lo: float, ml: float, hi: float, interpretation: str
) -> tuple[float, float]:
    """Recover absolute triangular bounds from elicited values.

    An SME asked for a minimum and a maximum almost never gives the true support of the
    distribution — they give something close to a P10 and a P90, because the genuine
    extremes feel absurd to say out loud. Feeding those in as hard bounds truncates
    exactly the tail contingency exists to cover, and the resulting P80 is confidently
    too low.

    So the elicited triple is treated as two quantiles of a triangular distribution with
    mode ``ml``, and the true bounds are solved for. Writing ``d1 = ml - a`` and
    ``d2 = b - ml``, each quantile condition is a quadratic in one unknown given the
    other, so alternating between the two closed forms converges in a handful of passes
    with no solver and no float64 drama.

    ``absolute`` carries zero tail mass and falls out of the same arithmetic as the
    identity, which is why there is no branch for it.

    Triangular rather than Beta-PERT because the inverse incomplete beta would drag scipy
    into a module that is otherwise dependency-free, and the widening corrects an SME's
    framing rather than measuring a constant — the third significant figure is not doing
    any work.
    """
    if interpretation not in _TAIL_MASS:
        raise ValueError(f"unknown bound interpretation: {interpretation!r}")

    p, q = _TAIL_MASS[interpretation]
    u = ml - lo
    v = hi - ml

    if u < 0 or v < 0:
        raise ValueError("bounds must satisfy lo <= ml <= hi")
    if (u == 0 and v == 0) or (p == 0.0 and q == 0.0):
        return lo, hi

    d1, d2 = u, v
    for _ in range(200):
        prev = (d1, d2)
        if p > 0.0:
            b_ = 2.0 * u + p * d2
            disc = max(b_ * b_ - 4.0 * (1.0 - p) * u * u, 0.0)
            d1 = (b_ + math.sqrt(disc)) / (2.0 * (1.0 - p))
        if q > 0.0:
            b_ = 2.0 * v + q * d1
            disc = max(b_ * b_ - 4.0 * (1.0 - q) * v * v, 0.0)
            d2 = (b_ + math.sqrt(disc)) / (2.0 * (1.0 - q))
        if abs(d1 - prev[0]) < 1e-12 and abs(d2 - prev[1]) < 1e-12:
            break

    return ml - d1, ml + d2


def uniform_bounds(lo: float, hi: float, interpretation: str) -> tuple[float, float]:
    """Recover absolute bounds for a uniform whose elicited edges are percentiles.

    Closed form: if the given values are the ``p`` and ``1-q`` quantiles of a uniform on
    ``[a, b]``, the span is ``(hi - lo) / (1 - p - q)`` and the rest follows.
    """
    if interpretation not in _TAIL_MASS:
        raise ValueError(f"unknown bound interpretation: {interpretation!r}")
    if hi < lo:
        raise ValueError("bounds must satisfy lo <= hi")

    p, q = _TAIL_MASS[interpretation]
    kept = 1.0 - p - q
    span = (hi - lo) / kept
    return lo - p * span, hi + q * span


# --------------------------------------------------------------------------------------
# moments
# --------------------------------------------------------------------------------------


def pert_moments(
    lo: float, ml: float, hi: float, interpretation: str = "absolute", lam: float = 4.0
) -> Moments:
    """Beta-PERT parameters and exact moments.

    The variance is the exact Beta-PERT value ``(mean - a)(b - mean) / (lambda + 3)``,
    not Malcolm's ``((b - a) / 6)**2`` approximation. For a symmetric standard PERT the
    two differ by ``(b - a)**2 / 28`` against ``(b - a)**2 / 36`` — the approximation
    understates the spread by roughly a sixth, which is not something to inherit by
    accident in a tool whose output is a contingency number.
    """
    if lam <= 0:
        raise ValueError("pert lambda must be positive")

    a, b = absolute_bounds(lo, ml, hi, interpretation)
    span = b - a
    if span == 0:
        return Moments("pert", a, b, a, 0.0, 0.0, ml=ml, alpha=1.0, beta=1.0)

    mean = (a + lam * ml + b) / (lam + 2.0)
    variance = (mean - a) * (b - mean) / (lam + 3.0)
    return Moments(
        kind="pert",
        lo=a,
        hi=b,
        mean=mean,
        variance=variance,
        sd=math.sqrt(max(variance, 0.0)),
        ml=ml,
        alpha=1.0 + lam * (ml - a) / span,
        beta=1.0 + lam * (b - ml) / span,
    )


def triangular_moments(
    lo: float, ml: float, hi: float, interpretation: str = "absolute"
) -> Moments:
    """Exact triangular moments. ``trigen`` is this shape with percentile bounds."""
    a, b = absolute_bounds(lo, ml, hi, interpretation)
    mean = (a + ml + b) / 3.0
    variance = (a * a + ml * ml + b * b - a * ml - a * b - ml * b) / 18.0
    kind = "triangular" if interpretation == "absolute" else "trigen"
    return Moments(kind, a, b, mean, variance, math.sqrt(max(variance, 0.0)), ml=ml)


def uniform_moments(lo: float, hi: float, interpretation: str = "absolute") -> Moments:
    a, b = uniform_bounds(lo, hi, interpretation)
    variance = (b - a) ** 2 / 12.0
    return Moments("uniform", a, b, (a + b) / 2.0, variance, math.sqrt(variance))


def cumulative_moments(points: list[dict]) -> Moments:
    """Moments of a piecewise-linear CDF.

    Each segment carries uniform density, so the whole thing is a mixture of uniforms
    weighted by the probability each segment spans. Exact, and it degrades gracefully: a
    segment of zero width becomes a point mass and the same arithmetic still holds.
    """
    xs = [float(p["x"]) for p in points]
    ps = [float(p["p"]) for p in points]

    mean = 0.0
    second = 0.0
    for i in range(len(xs) - 1):
        w = ps[i + 1] - ps[i]
        if w <= 0:
            continue
        x0, x1 = xs[i], xs[i + 1]
        mean += w * (x0 + x1) / 2.0
        second += w * (x0 * x0 + x0 * x1 + x1 * x1) / 3.0

    variance = max(second - mean * mean, 0.0)
    return Moments("cumulative", xs[0], xs[-1], mean, variance, math.sqrt(variance))


def discrete_moments(points: list[dict]) -> Moments:
    xs = [float(p["x"]) for p in points]
    ps = [float(p["p"]) for p in points]
    total = sum(ps)
    mean = sum(x * p for x, p in zip(xs, ps)) / total
    variance = max(sum(p * (x - mean) ** 2 for x, p in zip(xs, ps)) / total, 0.0)
    return Moments("discrete", min(xs), max(xs), mean, variance, math.sqrt(variance))


def dimension_moments(dim: DimensionInput, interpretation: str) -> Moments | None:
    """Dispatch to the right shape. Returns ``None`` for an unassessed dimension."""
    if dim.dist == "none":
        return None
    if dim.dist == "pert":
        return pert_moments(
            float(dim.lo), float(dim.ml), float(dim.hi), interpretation, dim.pert_lambda
        )
    if dim.dist == "triangular":
        return triangular_moments(float(dim.lo), float(dim.ml), float(dim.hi), "absolute")
    if dim.dist == "trigen":
        return triangular_moments(float(dim.lo), float(dim.ml), float(dim.hi), interpretation)
    if dim.dist == "uniform":
        return uniform_moments(float(dim.lo), float(dim.hi), interpretation)
    if dim.dist == "cumulative":
        return cumulative_moments(dim.points or [])
    if dim.dist == "discrete":
        return discrete_moments(dim.points or [])
    raise ValueError(f"unknown distribution: {dim.dist!r}")


def expected_value(m: Moments, p_occurrence: float) -> float:
    """Unconditional expected impact: the conditional mean scaled by occurrence.

    Never a contingency figure on its own. It is the sanity check an analyst runs against
    the simulation mean, and the sort order for a first-pass tornado.
    """
    return m.mean * p_occurrence


# --------------------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------------------


def _validate_rationale(prefix: str, rationale: dict | None, issues: list[Issue]) -> None:
    """Rationale is narrative, but its provenance is not.

    The source on each entry is what will one day let an agent draft a justification
    without it silently becoming the analyst's own. Anything an agent wrote stays visibly
    an agent's until a human takes it.
    """
    if rationale is None:
        return
    if not isinstance(rationale, dict):
        issues.append(Issue("error", f"{prefix}.rationale", "Rationale must be an object."))
        return

    for key, entry in rationale.items():
        where = f"{prefix}.rationale.{key}"
        if key not in RATIONALE_KEYS:
            issues.append(
                Issue(
                    "error",
                    f"{prefix}.rationale",
                    f"Unknown rationale key '{key}'. Expected {', '.join(RATIONALE_KEYS)}.",
                )
            )
            continue
        if not isinstance(entry, dict):
            issues.append(Issue("error", where, "Each rationale must be an object."))
            continue

        text = entry.get("text")
        if text is not None and not isinstance(text, str):
            issues.append(Issue("error", where, "Rationale text must be a string."))
        elif isinstance(text, str) and len(text) > MAX_RATIONALE_CHARS:
            issues.append(
                Issue("error", where, f"Rationale exceeds {MAX_RATIONALE_CHARS} characters.")
            )

        src = entry.get("source")
        if src is not None and src not in SOURCES:
            issues.append(
                Issue("error", where, f"Source must be one of {', '.join(SOURCES)}.")
            )
        elif src == "agent_proposal" and isinstance(text, str) and text.strip():
            issues.append(
                Issue(
                    "warning",
                    where,
                    "This rationale came from an AI proposal. It needs a human to own it "
                    "before the estimate feeds a run.",
                )
            )


def _validate_points(prefix: str, dim: DimensionInput, issues: list[Issue]) -> None:
    pts = dim.points or []
    if len(pts) < 2:
        issues.append(
            Issue(
                "error",
                f"{prefix}.points",
                "At least two points are needed. One point is a constant, not a distribution.",
            )
        )
        return

    try:
        xs = [float(p["x"]) for p in pts]
        ps = [float(p["p"]) for p in pts]
    except (KeyError, TypeError, ValueError):
        issues.append(
            Issue("error", f"{prefix}.points", "Every point needs numeric 'x' and 'p' values.")
        )
        return

    if any(not math.isfinite(v) for v in xs + ps):
        issues.append(Issue("error", f"{prefix}.points", "Every point value must be finite."))
        return

    if dim.dist == "cumulative":
        if any(b < a for a, b in zip(xs, xs[1:])):
            issues.append(
                Issue("error", f"{prefix}.points", "Values must not decrease down the curve.")
            )
        if any(p < 0.0 or p > 1.0 for p in ps):
            issues.append(
                Issue("error", f"{prefix}.points", "Probabilities must lie between 0 and 1.")
            )
        elif any(b <= a for a, b in zip(ps, ps[1:])):
            issues.append(
                Issue(
                    "error",
                    f"{prefix}.points",
                    "Cumulative probability must strictly increase; a flat step carries no "
                    "probability and a fall is not a distribution.",
                )
            )
        if not (abs(ps[0]) < 1e-9 and abs(ps[-1] - 1.0) < 1e-9):
            issues.append(
                Issue(
                    "error",
                    f"{prefix}.points",
                    "The curve must start at probability 0 and end at 1. Anything else leaves "
                    "probability unaccounted for.",
                )
            )
        if len(pts) < 4:
            issues.append(
                Issue(
                    "warning",
                    f"{prefix}.points",
                    "Only a few points, so the curve is close to uniform between them. A "
                    "three-point shape would say the same thing more honestly.",
                )
            )
    else:  # discrete
        if any(p <= 0.0 for p in ps):
            issues.append(
                Issue(
                    "error",
                    f"{prefix}.points",
                    "Every outcome needs a probability above zero. Drop the ones that cannot "
                    "happen.",
                )
            )
        else:
            total = sum(ps)
            if abs(total - 1.0) > MASS_TOLERANCE:
                issues.append(
                    Issue(
                        "error",
                        f"{prefix}.points",
                        f"Probabilities must sum to 1, not {total:.4f}.",
                    )
                )
        if len(set(xs)) != len(xs):
            issues.append(
                Issue("error", f"{prefix}.points", "Each outcome value must appear only once.")
            )
        if any(abs(x) < 1e-12 for x in xs):
            issues.append(
                Issue(
                    "warning",
                    f"{prefix}.points",
                    "One outcome is zero impact. If that represents the risk not occurring, "
                    "it double-counts the occurrence probability and understates the risk.",
                )
            )


def _validate_dimension(
    prefix: str, dim: DimensionInput, interpretation: str, issues: list[Issue]
) -> None:
    if dim.dist not in DIST_TYPES:
        issues.append(
            Issue("error", f"{prefix}.dist", f"Must be one of {', '.join(DIST_TYPES)}.")
        )
        return

    _validate_rationale(prefix, dim.rationale, issues)

    if dim.dist == "none":
        return

    if not math.isfinite(dim.pert_lambda) or dim.pert_lambda <= 0:
        issues.append(Issue("error", f"{prefix}.pert_lambda", "Lambda must be positive."))

    if dim.dist in POINT_DISTS:
        _validate_points(prefix, dim, issues)
        return

    needs_mode = dim.dist in THREE_POINT_DISTS
    label = DISTRIBUTION_GUIDANCE[dim.dist]["label"]
    required: list[tuple[str, float | None]] = [("min", dim.lo), ("max", dim.hi)]
    if needs_mode:
        required.insert(1, ("ml", dim.ml))

    missing = [name for name, v in required if v is None]
    if missing:
        issues.append(
            Issue(
                "error",
                f"{prefix}.{'ml' if needs_mode else 'min'}",
                f"{label} needs {', '.join(n for n, _ in required)}; "
                f"missing {', '.join(missing)}.",
            )
        )
        return

    if any(not math.isfinite(float(v)) for _, v in required):  # type: ignore[arg-type]
        issues.append(Issue("error", f"{prefix}.min", "Values must be finite."))
        return

    lo, hi = float(dim.lo), float(dim.hi)  # type: ignore[arg-type]

    if dim.dist == "uniform":
        if lo > hi:
            issues.append(Issue("error", f"{prefix}.max", "Maximum must not be below minimum."))
            return
        if dim.ml is not None:
            issues.append(
                Issue(
                    "warning",
                    f"{prefix}.ml",
                    "Uniform has no most likely value, so this entry is ignored. If you have "
                    "one, a three-point shape will use it.",
                )
            )
        if lo == hi:
            issues.append(
                Issue(
                    "warning",
                    f"{prefix}.min",
                    "Minimum equals maximum, so this dimension is deterministic and adds no "
                    "spread.",
                )
            )
        return

    ml = float(dim.ml)  # type: ignore[arg-type]

    if dim.dist == "triangular" and interpretation != "absolute":
        issues.append(
            Issue(
                "error",
                f"{prefix}.dist",
                "Triangular treats its bounds as hard limits, but this estimate records them "
                "as percentiles. Use trigen, or set the interpretation to absolute.",
            )
        )
    if dim.dist == "trigen" and interpretation == "absolute":
        issues.append(
            Issue(
                "error",
                f"{prefix}.dist",
                "Trigen solves for the bounds behind elicited percentiles, so absolute bounds "
                "leave it nothing to do. Use triangular, or record the bounds as P10/P90.",
            )
        )

    if not (lo <= ml <= hi):
        issues.append(
            Issue(
                "error",
                f"{prefix}.ml",
                f"Values must satisfy min <= most likely <= max (got {lo}, {ml}, {hi}).",
            )
        )
        return

    if lo == ml == hi:
        issues.append(
            Issue(
                "warning",
                f"{prefix}.ml",
                "All three values are equal, so this dimension is deterministic and "
                "contributes no spread to the simulation.",
            )
        )
        return

    below, above = ml - lo, hi - ml
    if below > 0 and above > 0:
        ratio = max(below / above, above / below)
        if ratio > SKEW_WARN_RATIO:
            issues.append(
                Issue(
                    "warning",
                    f"{prefix}.ml",
                    f"Range is {ratio:.0f}x more skewed to one side of the most likely value. "
                    "Check the units and that the question was understood.",
                )
            )

    if lo < 0 < hi:
        issues.append(
            Issue(
                "warning",
                f"{prefix}.min",
                "Range spans zero, so this is modelled as both a threat and an opportunity. "
                "Confirm that is intended.",
            )
        )


def validate(est: EstimateInput) -> ValidationResult:
    """Apply every rule. Errors block persistence; warnings are surfaced, not enforced."""
    issues: list[Issue] = []

    for name, value, allowed in (
        ("bound_interpretation", est.bound_interpretation, BOUND_INTERPRETATIONS),
        ("cost_basis", est.cost_basis, COST_BASES),
        ("sched_day_basis", est.sched_day_basis, DAY_BASES),
        ("source", est.source, SOURCES),
        ("confidence", est.confidence, CONFIDENCES),
    ):
        if value not in allowed:
            issues.append(Issue("error", name, f"Must be one of {', '.join(allowed)}."))

    p = est.p_occurrence
    if not math.isfinite(p) or not (0.0 < p <= 1.0):
        issues.append(
            Issue(
                "error",
                "p_occurrence",
                "Probability must be greater than 0 and at most 1. A risk with zero "
                "probability does not belong in the simulation.",
            )
        )
    elif est.is_variability and p != 1.0:
        issues.append(
            Issue(
                "error",
                "p_occurrence",
                "Estimate variability is always present, so probability must be 1.0. If this "
                "only happens sometimes, it is a risk event, not variability.",
            )
        )
    elif p == 1.0 and not est.is_variability:
        issues.append(
            Issue(
                "warning",
                "is_variability",
                "Probability is 1.0 but this is flagged as a risk event. A certainty is "
                "estimate variability and belongs in the base, not the risk register.",
            )
        )

    if est.bound_interpretation in BOUND_INTERPRETATIONS:
        _validate_dimension("cost", est.cost, est.bound_interpretation, issues)
        _validate_dimension("sched", est.sched, est.bound_interpretation, issues)

    if not est.cost.assessed and not est.sched.assessed:
        issues.append(
            Issue(
                "error",
                "cost.dist",
                "Give a cost impact, a schedule impact, or both. An estimate with neither "
                "cannot be simulated.",
            )
        )

    if (
        est.bound_interpretation == "absolute"
        and est.confidence == "low"
        and any(d.dist in THREE_POINT_DISTS for d in (est.cost, est.sched))
    ):
        issues.append(
            Issue(
                "warning",
                "bound_interpretation",
                "Low-confidence bounds are recorded as absolute extremes. SMEs asked for a min "
                "and max usually give something nearer P10 and P90; trigen treats them that "
                "way and stops the tails being truncated.",
            )
        )

    if 0.0 < p < RARE_EVENT_P:
        issues.append(
            Issue(
                "warning",
                "p_occurrence",
                f"Probability below {RARE_EVENT_P:.0%}. Rare, severe risks drive the tail and "
                "are worth a second opinion before they shape the contingency.",
            )
        )

    if est.source == "agent_proposal":
        issues.append(
            Issue(
                "warning",
                "source",
                "Recorded as an AI proposal. It needs an SME or analyst to own it before it "
                "feeds a simulation.",
            )
        )

    return ValidationResult(issues)


def _dim_summary(dim: DimensionInput, interpretation: str, p: float) -> dict | None:
    m = dimension_moments(dim, interpretation)
    if m is None:
        return None
    return {
        "kind": m.kind,
        "lo": m.lo,
        "ml": m.ml,
        "hi": m.hi,
        "mean": m.mean,
        "sd": m.sd,
        "alpha": m.alpha,
        "beta": m.beta,
        "expected_value": expected_value(m, p),
        "elicited_lo": dim.lo,
        "elicited_hi": dim.hi,
        "widened": dim.lo is not None and (m.lo != dim.lo or m.hi != dim.hi),
    }


def summarise(est: EstimateInput) -> dict:
    """Moments for both dimensions, for the entry-form preview and the tornado seed.

    Assumes ``validate`` passed; unassessed dimensions come back ``None``.
    """
    return {
        "cost": _dim_summary(est.cost, est.bound_interpretation, est.p_occurrence),
        "sched": _dim_summary(est.sched, est.bound_interpretation, est.p_occurrence),
    }
