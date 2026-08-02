"""Turning a mitigation package into a post-mitigation register.

The pure half is :func:`residual_fields`: elicited numbers in, residual numbers out, no
session, no clock. Everything a reviewer would argue about lives there and is unit-tested
without a database. The rest of the module is the part that has to touch rows.

Three rules carry the correctness of this module, and each one exists because the
alternative produces a clean, confident, wrong answer:

**A risk the plan says nothing about is carried through unchanged.** The residual register
is the *whole* register, not the treated subset. Materialising only the risks named in the
plan would leave a post-mitigation run silently missing everything nobody got round to
treating, and a residual contingency computed over a subset of the register understates it
in exactly the direction that gets a project into trouble.

**A declared residual is an input, not a result.** Nothing here reports a benefit. What a
package buys is the difference between two simulations of two registers (4.5), because
the interaction between correlated risks and the critical path is not something a set of
per-risk factors can be multiplied out into. Factors say what to simulate.

**Plan cost is never folded into a contingency figure.** Action budgets are deterministic
and additive; contingency is a percentile of a distribution and is not (invariant 1). The
two belong side by side in the same report and never inside the same number.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.history import RiskHistory
from app.models.mitigation import (
    MitigationAction,
    MitigationPlan,
    MitigationPlanRisk,
)
from app.models.quant import RiskQuantEstimate, quant_diff, quant_snapshot
from app.models.risk import Risk
from app.services import quant_validation as qv
from app.services.scope import resolve_read_scope

#: The scenario a plan writes into. ``pre_mitigation`` is the elicited baseline and is
#: never written by this module.
RESIDUAL_SCENARIO = "post_mitigation"

#: How a materialised estimate describes its own provenance. ``analyst`` rather than
#: ``agent_proposal``: the numbers come from a human's declared factors, applied
#: mechanically. Nothing in this module proposes anything.
RESIDUAL_SOURCE = "analyst"

#: Statuses whose actions do not count toward what the package costs.
_UNCOSTED_ACTION_STATUSES = ("Cancelled",)

#: Estimate fields the residual carries over from the baseline untouched. Shape, lambda,
#: basis and interpretation describe how the session was run, not how big the number is,
#: and a mitigation does not change how the session was run.
_CARRIED_FIELDS = (
    "is_variability",
    "bound_interpretation",
    "cost_dist",
    "cost_pert_lambda",
    "cost_basis",
    "sched_dist",
    "sched_pert_lambda",
    "sched_day_basis",
    "confidence",
)

#: Fields the residual may move.
_MOVED_FIELDS = (
    "p_occurrence",
    "cost_min",
    "cost_ml",
    "cost_max",
    "cost_points",
    "sched_min",
    "sched_ml",
    "sched_max",
    "sched_points",
)

BASE_FIELDS = _CARRIED_FIELDS + _MOVED_FIELDS


# --------------------------------------------------------------------------------------
# the pure half
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Treatment:
    """One plan's declared position on one risk, free of SQLAlchemy."""

    treatment: str = "reduce"
    mode: str = "factor"
    p_factor: float = 1.0
    cost_factor: float = 1.0
    sched_factor: float = 1.0
    residual_p: float | None = None
    residual_cost_min: float | None = None
    residual_cost_ml: float | None = None
    residual_cost_max: float | None = None
    residual_sched_min: float | None = None
    residual_sched_ml: float | None = None
    residual_sched_max: float | None = None

    @classmethod
    def from_row(cls, row: MitigationPlanRisk) -> "Treatment":
        return cls(
            treatment=row.treatment,
            mode=row.mode,
            p_factor=row.p_factor,
            cost_factor=row.cost_factor,
            sched_factor=row.sched_factor,
            residual_p=row.residual_p,
            residual_cost_min=row.residual_cost_min,
            residual_cost_ml=row.residual_cost_ml,
            residual_cost_max=row.residual_cost_max,
            residual_sched_min=row.residual_sched_min,
            residual_sched_ml=row.residual_sched_ml,
            residual_sched_max=row.residual_sched_max,
        )


def estimate_base(est: RiskQuantEstimate) -> dict[str, Any]:
    """The subset of an elicited estimate a residual is built from.

    Point lists are copied. A JSON column hands back a live list, and letting a residual
    row and its baseline share one object means editing either edits both — a corruption
    that only shows up after a flush, in the other scenario.
    """
    base = {f: getattr(est, f) for f in BASE_FIELDS}
    for key in ("cost_points", "sched_points"):
        if base[key] is not None:
            base[key] = [dict(pt) for pt in base[key]]
    return base


def _scale_points(points: list | None, factor: float) -> list | None:
    """Scale a cumulative or discrete shape along its value axis.

    Only ``x`` moves. ``p`` is a probability — cumulative for one shape, a mass for the
    other — and multiplying it by a cost factor would produce a distribution that does not
    integrate to one.
    """
    if not points:
        return points
    return [{"x": float(pt["x"]) * factor, "p": pt["p"]} for pt in points]


def _ordered(lo: float | None, ml: float | None, hi: float | None) -> bool:
    seq = [v for v in (lo, ml, hi) if v is not None]
    return all(a <= b for a, b in zip(seq, seq[1:]))


def _apply_dimension(
    out: dict[str, Any],
    base: dict[str, Any],
    prefix: str,
    t: Treatment,
    issues: list[str],
) -> None:
    """One dimension of a ``reduce``, in place.

    Every failure path here restores the baseline numbers rather than dropping the
    dimension. A residual that is accidentally *larger* than it should be is a
    conservative error a reviewer can spot; one that is accidentally absent is invisible.
    """
    dist = base[f"{prefix}_dist"]
    if dist == "none":
        return

    if t.mode == "factor":
        factor = t.cost_factor if prefix == "cost" else t.sched_factor
        if dist in qv.POINT_DISTS:
            out[f"{prefix}_points"] = _scale_points(base[f"{prefix}_points"], factor)
        else:
            for suffix in ("min", "ml", "max"):
                value = base[f"{prefix}_{suffix}"]
                out[f"{prefix}_{suffix}"] = None if value is None else value * factor
        return

    # absolute
    if dist in qv.POINT_DISTS:
        issues.append(
            f"{prefix}: an absolute residual cannot replace a {dist} shape point by "
            "point, so the elicited curve was carried through unchanged. Use a factor, "
            "or edit the residual estimate directly."
        )
        return

    declared = {
        suffix: getattr(t, f"residual_{prefix}_{suffix}") for suffix in ("min", "ml", "max")
    }
    candidate = {
        suffix: (declared[suffix] if declared[suffix] is not None else base[f"{prefix}_{suffix}"])
        for suffix in ("min", "ml", "max")
    }
    if not _ordered(candidate["min"], candidate["ml"], candidate["max"]):
        issues.append(
            f"{prefix}: the absolute residual is not ordered min <= most likely <= max "
            f"({candidate['min']}, {candidate['ml']}, {candidate['max']}), so the "
            "elicited numbers were carried through unchanged."
        )
        return
    for suffix in ("min", "ml", "max"):
        out[f"{prefix}_{suffix}"] = candidate[suffix]


def residual_fields(
    base: dict[str, Any], t: Treatment | None
) -> tuple[dict[str, Any] | None, list[str]]:
    """The residual estimate this treatment implies, or ``None`` when the risk is retired.

    ``None`` for the treatment and ``treatment="accept"`` both return the baseline
    verbatim. They are not the same thing to a reader — one is an omission and one is a
    decision — but they are the same thing to a sampler, and this function's job is to
    describe what gets sampled.
    """
    issues: list[str] = []
    out = dict(base)

    if t is None or t.treatment == "accept":
        return out, issues
    if t.treatment == "retire":
        return None, issues

    if base["is_variability"]:
        # A variability row is inherent spread on a base estimate, not an event, and its
        # occurrence is pinned at 1.0 by the schema. Reducing its likelihood is a category
        # error; reducing its spread is not.
        out["p_occurrence"] = 1.0
        if t.mode == "factor" and t.p_factor != 1.0:
            issues.append(
                "p_occurrence: this is a variability estimate, whose occurrence is "
                "always certain, so the probability factor was ignored. Reduce the range "
                "instead."
            )
        elif t.mode == "absolute" and t.residual_p is not None:
            issues.append(
                "p_occurrence: this is a variability estimate, whose occurrence is "
                "always certain, so the residual probability was ignored."
            )
    elif t.mode == "factor":
        out["p_occurrence"] = base["p_occurrence"] * t.p_factor
    elif t.residual_p is not None:
        out["p_occurrence"] = t.residual_p

    _apply_dimension(out, base, "cost", t, issues)
    _apply_dimension(out, base, "sched", t, issues)
    return out, issues


def expected_impact(fields: dict[str, Any], prefix: str) -> float | None:
    """Unconditional expected impact for one dimension, for the before/after summary.

    An approximation stated as one: a mean is not a contingency and the summary says so on
    its face. It is here because a residual table with no numbers on it cannot be reviewed,
    and because means — unlike percentiles — are legitimately additive.
    """
    dim = qv.DimensionInput(
        dist=fields[f"{prefix}_dist"],
        lo=fields[f"{prefix}_min"],
        ml=fields[f"{prefix}_ml"],
        hi=fields[f"{prefix}_max"],
        pert_lambda=fields[f"{prefix}_pert_lambda"],
        points=fields[f"{prefix}_points"],
    )
    if not dim.assessed:
        return None
    try:
        moments = qv.dimension_moments(dim, fields["bound_interpretation"])
    except (ValueError, TypeError, ZeroDivisionError):
        return None
    if moments is None:
        return None
    return qv.expected_value(moments, float(fields["p_occurrence"]))


def fingerprint(lines: Sequence["ResidualLine"]) -> str:
    """A digest of exactly what a materialisation wrote.

    Attribution, not integrity: a post-mitigation run can be tied to the plan that
    produced its inputs only if there is something to compare. If the register no longer
    hashes to this, it was edited after the plan wrote it and the run is measuring
    something else.
    """
    payload = [
        [
            line.risk_code,
            line.treatment,
            None
            if line.residual is None
            else {k: line.residual[k] for k in sorted(line.residual)},
        ]
        for line in sorted(lines, key=lambda ln: ln.risk_code)
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# reading the plan
# --------------------------------------------------------------------------------------


@dataclass
class ResidualLine:
    """One risk's before and after, as the preview and the writer both see it."""

    risk_id: int
    risk_code: str
    title: str
    #: ``reduce`` / ``retire`` / ``accept`` / ``untreated``.
    treatment: str
    residual: dict[str, Any] | None
    base_p: float
    residual_p: float | None
    base_cost_ev: float | None
    residual_cost_ev: float | None
    base_sched_ev: float | None
    residual_sched_ev: float | None
    issues: list[str] = field(default_factory=list)
    #: A run has frozen this residual, so materialising will not touch it.
    locked: bool = False
    #: The residual on file differs from what this plan would write.
    edited_since: bool = False


@dataclass
class PlanCost:
    """What the package costs, before anything is said about what it buys."""

    action_count: int = 0
    costed_count: int = 0
    #: Actions with neither a budget nor a duration. A rollup that quietly treats these as
    #: zero is the cost-side twin of dropping a risk from a run.
    unpriced_count: int = 0
    cancelled_count: int = 0
    total_budget: float = 0.0
    total_sched_days: float = 0.0
    by_status: dict[str, int] = field(default_factory=dict)


@dataclass
class MaterializeResult:
    written: int = 0
    unchanged: int = 0
    retired: int = 0
    #: Residuals a run has frozen. Left exactly as they were (invariant 6).
    skipped_locked: list[str] = field(default_factory=list)
    #: Residuals that changed after this plan last wrote them, and were overwritten.
    replaced_edited: list[str] = field(default_factory=list)
    #: Post-mitigation rows with no pre-mitigation estimate behind them any more.
    orphans: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    fingerprint: str = ""


async def _scope_ids(db: AsyncSession, plan: MitigationPlan) -> list[int] | None:
    return await resolve_read_scope(db, plan.scope_id)


async def load_lines(db: AsyncSession, plan: MitigationPlan) -> list[ResidualLine]:
    """Every risk in the plan's scope that has a baseline, with its residual.

    Driven by the baseline register rather than by the plan's entries. That is the
    carried-through rule expressed as a query: a risk nobody treated still appears, and
    still contributes.
    """
    scope_ids = await _scope_ids(db, plan)

    risk_stmt = select(Risk)
    if scope_ids is not None:
        risk_stmt = risk_stmt.where(Risk.scope_id.in_(scope_ids))
    risks = {r.id: r for r in (await db.scalars(risk_stmt.order_by(Risk.risk_code))).all()}
    if not risks:
        return []

    estimates = list(
        (
            await db.scalars(
                select(RiskQuantEstimate)
                .where(
                    RiskQuantEstimate.scenario == "pre_mitigation",
                    RiskQuantEstimate.risk_id.in_(list(risks)),
                )
                .order_by(RiskQuantEstimate.risk_id)
            )
        ).all()
    )

    entries = {
        row.risk_id: row
        for row in (
            await db.scalars(
                select(MitigationPlanRisk).where(MitigationPlanRisk.plan_id == plan.id)
            )
        ).all()
    }

    existing = {
        row.risk_id: row
        for row in (
            await db.scalars(
                select(RiskQuantEstimate).where(
                    RiskQuantEstimate.scenario == RESIDUAL_SCENARIO,
                    RiskQuantEstimate.risk_id.in_(list(risks)),
                )
            )
        ).all()
    }

    lines: list[ResidualLine] = []
    for est in sorted(estimates, key=lambda e: risks[e.risk_id].risk_code):
        risk = risks[est.risk_id]
        entry = entries.get(risk.id)
        treatment = Treatment.from_row(entry) if entry is not None else None
        base = estimate_base(est)
        residual, issues = residual_fields(base, treatment)
        on_file = existing.get(risk.id)
        lines.append(
            ResidualLine(
                risk_id=risk.id,
                risk_code=risk.risk_code,
                title=risk.title,
                treatment="untreated" if entry is None else entry.treatment,
                residual=residual,
                base_p=float(est.p_occurrence),
                residual_p=None if residual is None else float(residual["p_occurrence"]),
                base_cost_ev=expected_impact(base, "cost"),
                residual_cost_ev=None if residual is None else expected_impact(residual, "cost"),
                base_sched_ev=expected_impact(base, "sched"),
                residual_sched_ev=None
                if residual is None
                else expected_impact(residual, "sched"),
                issues=issues,
                locked=bool(on_file is not None and on_file.locked),
                edited_since=_edited_since(plan, on_file),
            )
        )
    return lines


def _edited_since(plan: MitigationPlan, row: RiskQuantEstimate | None) -> bool:
    """Whether a residual on file was touched after this plan last wrote it.

    Covers a hand-elicited residual that predates the plan and a residual written by a
    *different* plan, which is why the flag is phrased as "changed since" rather than
    "hand-edited" everywhere it surfaces. Both mean the same thing to the analyst about to
    overwrite it.
    """
    if row is None:
        return False
    if plan.materialized_at is None or row.updated_at is None:
        return True
    return row.updated_at > plan.materialized_at


async def plan_cost(db: AsyncSession, plan_id: int) -> PlanCost:
    """Sum the package. Money and days, kept apart."""
    actions = list(
        (
            await db.scalars(
                select(MitigationAction)
                .where(MitigationAction.plan_id == plan_id)
                .order_by(MitigationAction.id)
            )
        ).all()
    )
    cost = PlanCost(action_count=len(actions))
    for a in actions:
        cost.by_status[a.status] = cost.by_status.get(a.status, 0) + 1
        if a.status in _UNCOSTED_ACTION_STATUSES:
            cost.cancelled_count += 1
            continue
        if a.budget is None and a.sched_days is None:
            cost.unpriced_count += 1
            continue
        cost.costed_count += 1
        cost.total_budget += float(a.budget or 0.0)
        cost.total_sched_days += float(a.sched_days or 0.0)
    return cost


# --------------------------------------------------------------------------------------
# writing the residual register
# --------------------------------------------------------------------------------------


async def materialize(
    db: AsyncSession,
    plan: MitigationPlan,
    *,
    actor: str,
    confirm_replace_edited: bool = False,
) -> MaterializeResult:
    """Project the plan into ``post_mitigation`` estimates.

    Does not commit. The caller owns the transaction, because the plan's own
    materialisation record has to land in the same one as the rows it describes.

    Raises :class:`ValueError` when residuals that changed since this plan last wrote them
    would be overwritten without an explicit confirmation. That is a guard against
    destroying elicited work, not a policy about who owns the number: confirm and it
    proceeds.
    """
    lines = await load_lines(db, plan)
    result = MaterializeResult(fingerprint=fingerprint(lines))

    at_risk = [ln.risk_code for ln in lines if ln.edited_since and not ln.locked]
    if at_risk and not confirm_replace_edited:
        raise ValueError(
            f"{len(at_risk)} residual estimate(s) have changed since this plan last wrote "
            f"them ({', '.join(at_risk[:8])}"
            f"{'…' if len(at_risk) > 8 else ''}). Re-send with confirm_replace_edited to "
            "overwrite them."
        )

    # Every residual in scope, not only the ones with a baseline behind them. The
    # difference is the orphan set: a post-mitigation estimate whose pre-mitigation
    # partner has since been deleted still sits in the scenario a run reads, and would go
    # on contributing to a residual contingency with nothing to justify it.
    scope_ids = await _scope_ids(db, plan)
    existing_stmt = (
        select(RiskQuantEstimate, Risk.risk_code)
        .join(Risk, Risk.id == RiskQuantEstimate.risk_id)
        .where(RiskQuantEstimate.scenario == RESIDUAL_SCENARIO)
    )
    if scope_ids is not None:
        existing_stmt = existing_stmt.where(Risk.scope_id.in_(scope_ids))
    existing: dict[int, RiskQuantEstimate] = {}
    codes: dict[int, str] = {ln.risk_id: ln.risk_code for ln in lines}
    for row, code in (await db.execute(existing_stmt)).all():
        existing[row.risk_id] = row
        codes.setdefault(row.risk_id, code)

    for line in lines:
        result.issues.extend(f"{line.risk_code}: {issue}" for issue in line.issues)
        row = existing.get(line.risk_id)

        if row is not None and row.locked:
            result.skipped_locked.append(line.risk_code)
            continue

        if line.residual is None:
            if row is not None:
                await db.delete(row)
                db.add(
                    RiskHistory(
                        risk_id=line.risk_id,
                        risk_code=line.risk_code,
                        action="residual cleared",
                        actor=actor,
                        changes=[
                            {"field": "scenario", "old": RESIDUAL_SCENARIO, "new": None}
                        ],
                    )
                )
            result.retired += 1
            continue

        if line.edited_since:
            result.replaced_edited.append(line.risk_code)

        payload = dict(line.residual)
        for key in ("cost_points", "sched_points"):
            if payload[key] is not None:
                payload[key] = [dict(pt) for pt in payload[key]]
        payload["notes"] = f"Residual under mitigation plan: {plan.name}"

        if row is None:
            row = RiskQuantEstimate(
                risk_id=line.risk_id,
                scenario=RESIDUAL_SCENARIO,
                source=RESIDUAL_SOURCE,
                estimated_by=actor,
            )
            for key, value in payload.items():
                setattr(row, key, value)
            db.add(row)
            result.written += 1
            db.add(
                RiskHistory(
                    risk_id=line.risk_id,
                    risk_code=line.risk_code,
                    action="residual set",
                    actor=actor,
                    changes=[
                        {"field": "scenario", "old": None, "new": RESIDUAL_SCENARIO},
                        {"field": "treatment", "old": None, "new": line.treatment},
                    ],
                )
            )
            continue

        before = quant_snapshot(row)
        for key, value in payload.items():
            setattr(row, key, value)
        row.source = RESIDUAL_SOURCE
        row.estimated_by = actor
        changes = quant_diff(before, quant_snapshot(row))
        if changes:
            result.written += 1
            db.add(
                RiskHistory(
                    risk_id=line.risk_id,
                    risk_code=line.risk_code,
                    action="residual set",
                    actor=actor,
                    changes=changes,
                )
            )
        else:
            result.unchanged += 1

    result.orphans = sorted(
        codes.get(risk_id) or f"risk {risk_id}"
        for risk_id in existing
        if risk_id not in {ln.risk_id for ln in lines}
    )

    plan.materialized_at = func.now()
    plan.materialized_by = actor
    plan.materialized_fingerprint = result.fingerprint
    plan.materialized_risk_count = result.written + result.unchanged
    plan.materialized_retired_count = result.retired
    return result
