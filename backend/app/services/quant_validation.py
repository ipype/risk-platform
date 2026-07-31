"""Validation and distribution derivation for quantitative risk estimates.

Pure module: no DB, no network, no framework imports. Everything here is a function of
its arguments, so the rules that decide whether a number is fit to simulate can be
property-tested in isolation from the API and the ORM.

Two jobs:

1. **Validation.** Reject estimates that cannot be sampled, and warn about the ones that
   can be sampled but usually mean the elicitation went wrong.
2. **Derivation.** Turn what the SME actually said into the parameters the sampler needs.
   These are not the same thing, and conflating them is the defect this module exists to
   prevent — see :func:`absolute_bounds`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------
# vocabularies
# --------------------------------------------------------------------------------------

SCENARIOS = ("pre_mitigation", "post_mitigation")
DIST_TYPES = ("pert", "triangular", "uniform", "discrete", "none")
BOUND_INTERPRETATIONS = ("absolute", "p10_p90", "p5_p95")
DAY_BASES = ("working", "calendar")
COST_BASES = ("absolute", "pct_of_base")
SOURCES = ("sme", "historical", "analyst", "agent_proposal")
CONFIDENCES = ("low", "medium", "high")

#: Tail mass excluded on each side by a given interpretation of the elicited bounds.
#: ``absolute`` excludes nothing, which makes it the identity case in
#: :func:`absolute_bounds` rather than a special case in the caller.
_TAIL_MASS: dict[str, tuple[float, float]] = {
    "absolute": (0.0, 0.0),
    "p10_p90": (0.10, 0.10),
    "p5_p95": (0.05, 0.05),
}

#: Ratio of the long side of the range to the short side, past which the estimate is
#: flagged for review. Wildly skewed three-points are usually a units error or a
#: misunderstood question, not a genuinely skewed risk.
SKEW_WARN_RATIO = 20.0

#: Below this occurrence probability, an estimate materially moves the tail only if its
#: impact is large. Worth a second look, never an error: rare-and-severe is real.
RARE_EVENT_P = 0.05


@dataclass(frozen=True)
class Issue:
    """One validation finding. ``error`` blocks persistence, ``warning`` does not."""

    severity: str  # "error" | "warning"
    field: str
    message: str


@dataclass(frozen=True)
class Moments:
    """Beta-PERT parameters and moments for one dimension of one estimate."""

    lo: float  # absolute lower bound after interpretation is applied
    ml: float
    hi: float  # absolute upper bound after interpretation is applied
    mean: float
    variance: float
    sd: float
    alpha: float
    beta: float

    @property
    def conditional_mean(self) -> float:
        """Mean given the risk occurs. Multiply by p_occurrence for the unconditional."""
        return self.mean


@dataclass
class EstimateInput:
    """Everything the rules need, decoupled from both the ORM model and the API schema."""

    p_occurrence: float
    is_variability: bool = False
    bound_interpretation: str = "absolute"
    dist_type: str = "pert"
    pert_lambda: float = 4.0

    cost_min: float | None = None
    cost_ml: float | None = None
    cost_max: float | None = None
    cost_basis: str = "absolute"

    sched_min: float | None = None
    sched_ml: float | None = None
    sched_max: float | None = None
    sched_day_basis: str = "working"

    source: str = "sme"
    confidence: str = "medium"

    @property
    def has_cost(self) -> bool:
        return any(v is not None for v in (self.cost_min, self.cost_ml, self.cost_max))

    @property
    def has_sched(self) -> bool:
        return any(v is not None for v in (self.sched_min, self.sched_ml, self.sched_max))


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
# derivation
# --------------------------------------------------------------------------------------


def absolute_bounds(
    lo: float, ml: float, hi: float, interpretation: str
) -> tuple[float, float]:
    """Recover absolute distribution bounds from elicited values.

    An SME asked for a "minimum" and a "maximum" almost never gives the true support of
    the distribution — they give something close to a P10 and a P90, because the genuine
    extremes feel absurd to say out loud. Feeding those in as hard bounds truncates
    exactly the tail that contingency exists to cover, and the resulting P80 is
    confidently too low.

    So the elicited triple is treated as two quantiles of a triangular distribution with
    mode ``ml``, and the true bounds are solved for. Writing ``d1 = ml - a`` and
    ``d2 = b - ml``, each of the two quantile conditions is a quadratic in one unknown
    given the other, so alternating between the two closed forms converges in a handful
    of passes with no solver and no float64 drama.

    ``absolute`` carries zero tail mass and falls out of the same arithmetic as the
    identity, which is why there is no branch for it.

    Triangular rather than Beta-PERT because the inverse incomplete beta would drag
    scipy into a module that is otherwise dependency-free, and the widening is a
    correction to an SME's framing, not a physical constant — the third significant
    figure is not doing any work.

    Returns ``(a, b)`` with ``a <= lo`` and ``b >= hi``.
    """
    if interpretation not in _TAIL_MASS:
        raise ValueError(f"unknown bound interpretation: {interpretation!r}")

    p, q = _TAIL_MASS[interpretation]
    u = ml - lo  # elicited distance below the mode
    v = hi - ml  # elicited distance above the mode

    if u < 0 or v < 0:
        raise ValueError("bounds must satisfy lo <= ml <= hi")
    if u == 0 and v == 0:
        return lo, hi  # degenerate: deterministic, nothing to widen
    if p == 0.0 and q == 0.0:
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


def pert_moments(
    lo: float, ml: float, hi: float, interpretation: str = "absolute", lam: float = 4.0
) -> Moments:
    """Beta-PERT parameters and exact moments, after bound interpretation is applied.

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
        return Moments(a, ml, b, mean=a, variance=0.0, sd=0.0, alpha=1.0, beta=1.0)

    mean = (a + lam * ml + b) / (lam + 2.0)
    variance = (mean - a) * (b - mean) / (lam + 3.0)
    alpha = 1.0 + lam * (ml - a) / span
    beta = 1.0 + lam * (b - ml) / span

    return Moments(
        lo=a,
        ml=ml,
        hi=b,
        mean=mean,
        variance=variance,
        sd=math.sqrt(max(variance, 0.0)),
        alpha=alpha,
        beta=beta,
    )


def expected_value(m: Moments, p_occurrence: float) -> float:
    """Unconditional expected impact: the conditional mean scaled by occurrence.

    Never a contingency figure on its own. It is the sanity check an analyst runs
    against the simulation mean, and the sort order for a first-pass tornado.
    """
    return m.mean * p_occurrence


# --------------------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------------------


def _triple(
    prefix: str,
    lo: float | None,
    ml: float | None,
    hi: float | None,
    issues: list[Issue],
) -> bool:
    """Validate one dimension's three-point. Returns True if it is complete and usable."""
    present = [v is not None for v in (lo, ml, hi)]

    if not any(present):
        return False
    if not all(present):
        missing = [
            name
            for name, v in ((f"{prefix}_min", lo), (f"{prefix}_ml", ml), (f"{prefix}_max", hi))
            if v is None
        ]
        issues.append(
            Issue(
                "error",
                f"{prefix}_ml",
                f"Incomplete three-point estimate; missing {', '.join(missing)}. "
                "Give all three values or none.",
            )
        )
        return False

    assert lo is not None and ml is not None and hi is not None
    for name, v in ((f"{prefix}_min", lo), (f"{prefix}_ml", ml), (f"{prefix}_max", hi)):
        if not math.isfinite(v):
            issues.append(Issue("error", name, "Value must be finite."))
            return False

    if not (lo <= ml <= hi):
        issues.append(
            Issue(
                "error",
                f"{prefix}_ml",
                f"Values must satisfy min <= most likely <= max (got {lo}, {ml}, {hi}).",
            )
        )
        return False

    if lo == ml == hi:
        issues.append(
            Issue(
                "warning",
                f"{prefix}_ml",
                "All three values are equal, so this dimension is deterministic and "
                "contributes no spread to the simulation.",
            )
        )
        return True

    below, above = ml - lo, hi - ml
    if below > 0 and above > 0:
        ratio = max(below / above, above / below)
        if ratio > SKEW_WARN_RATIO:
            issues.append(
                Issue(
                    "warning",
                    f"{prefix}_ml",
                    f"Range is {ratio:.0f}x more skewed to one side of the most likely "
                    "value. Check the units and that the question was understood.",
                )
            )

    if lo < 0 < hi:
        issues.append(
            Issue(
                "warning",
                f"{prefix}_min",
                "Range spans zero, so this is modelled as both a threat and an "
                "opportunity. Confirm that is intended.",
            )
        )

    return True


def validate(est: EstimateInput) -> ValidationResult:
    """Apply every rule. Errors block persistence; warnings are surfaced, not enforced."""
    issues: list[Issue] = []

    if est.bound_interpretation not in BOUND_INTERPRETATIONS:
        issues.append(
            Issue(
                "error",
                "bound_interpretation",
                f"Must be one of {', '.join(BOUND_INTERPRETATIONS)}.",
            )
        )
    if est.dist_type not in DIST_TYPES:
        issues.append(Issue("error", "dist_type", f"Must be one of {', '.join(DIST_TYPES)}."))
    if est.cost_basis not in COST_BASES:
        issues.append(Issue("error", "cost_basis", f"Must be one of {', '.join(COST_BASES)}."))
    if est.sched_day_basis not in DAY_BASES:
        issues.append(
            Issue("error", "sched_day_basis", f"Must be one of {', '.join(DAY_BASES)}.")
        )
    if est.source not in SOURCES:
        issues.append(Issue("error", "source", f"Must be one of {', '.join(SOURCES)}."))
    if est.confidence not in CONFIDENCES:
        issues.append(Issue("error", "confidence", f"Must be one of {', '.join(CONFIDENCES)}."))

    if not math.isfinite(est.pert_lambda) or est.pert_lambda <= 0:
        issues.append(Issue("error", "pert_lambda", "Lambda must be a positive number."))

    # -- occurrence ---------------------------------------------------------------
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
                "Estimate variability is always present, so probability must be 1.0. "
                "If this only happens sometimes, it is a risk event, not variability.",
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

    # -- dimensions ---------------------------------------------------------------
    cost_ok = _triple("cost", est.cost_min, est.cost_ml, est.cost_max, issues)
    sched_ok = _triple("sched", est.sched_min, est.sched_ml, est.sched_max, issues)

    if not cost_ok and not sched_ok and not any(
        i.severity == "error" and i.field.endswith("_ml") for i in issues
    ):
        issues.append(
            Issue(
                "error",
                "cost_ml",
                "Give a cost impact, a schedule impact, or both. An estimate with "
                "neither cannot be simulated.",
            )
        )

    # -- elicitation-quality warnings ---------------------------------------------
    if (
        est.bound_interpretation == "absolute"
        and est.confidence == "low"
        and (cost_ok or sched_ok)
    ):
        issues.append(
            Issue(
                "warning",
                "bound_interpretation",
                "Low-confidence bounds are recorded as absolute extremes. SMEs asked for "
                "a min and max usually give something nearer P10 and P90; consider "
                "recording it as such so the tails are not truncated.",
            )
        )

    if 0.0 < p < RARE_EVENT_P:
        issues.append(
            Issue(
                "warning",
                "p_occurrence",
                f"Probability below {RARE_EVENT_P:.0%}. Rare, severe risks drive the tail "
                "and are worth a second opinion before they shape the contingency.",
            )
        )

    if est.source == "agent_proposal":
        issues.append(
            Issue(
                "warning",
                "source",
                "Recorded as an AI proposal. It needs an SME or analyst to own it before "
                "it feeds a simulation.",
            )
        )

    return ValidationResult(issues)


def summarise(est: EstimateInput) -> dict:
    """Moments for both dimensions, for the entry-form preview and the tornado seed.

    Assumes ``validate`` already passed; dimensions that are absent come back ``None``.
    """
    out: dict = {"cost": None, "sched": None}

    if est.has_cost and None not in (est.cost_min, est.cost_ml, est.cost_max):
        m = pert_moments(
            float(est.cost_min),  # type: ignore[arg-type]
            float(est.cost_ml),  # type: ignore[arg-type]
            float(est.cost_max),  # type: ignore[arg-type]
            est.bound_interpretation,
            est.pert_lambda,
        )
        out["cost"] = {
            "lo": m.lo,
            "ml": m.ml,
            "hi": m.hi,
            "mean": m.mean,
            "sd": m.sd,
            "alpha": m.alpha,
            "beta": m.beta,
            "expected_value": expected_value(m, est.p_occurrence),
        }

    if est.has_sched and None not in (est.sched_min, est.sched_ml, est.sched_max):
        m = pert_moments(
            float(est.sched_min),  # type: ignore[arg-type]
            float(est.sched_ml),  # type: ignore[arg-type]
            float(est.sched_max),  # type: ignore[arg-type]
            est.bound_interpretation,
            est.pert_lambda,
        )
        out["sched"] = {
            "lo": m.lo,
            "ml": m.ml,
            "hi": m.hi,
            "mean": m.mean,
            "sd": m.sd,
            "alpha": m.alpha,
            "beta": m.beta,
            "expected_value": expected_value(m, est.p_occurrence),
        }

    return out
