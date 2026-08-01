"""The run itself.

Order of operations, and why it is this order:

1. **Lay out the stochastic variables.** One column per thing that is drawn: a risk's
   occurrence, its cost magnitude, its schedule magnitude. Activity background variability
   is not in this matrix — see the note on chunking below.
2. **Build the target correlation matrix** over those columns from driver tags and
   explicit pairs.
3. **Draw the uniforms once, for every iteration at once**, and reorder them by
   Iman-Conover. Both steps have to happen before anything is transformed, because
   stratification and rank correlation are properties of the uniforms (invariant 2).
4. **Transform to magnitudes**, gating each risk on a single occurrence draw so a risk
   that missed cannot hit the programme while sparing the budget.
5. **Run the network in chunks**, sampling activity durations, adding the driven delays,
   and taking a forward and backward CPM pass per chunk.
6. **Integrate inside the iteration**::

       total_i = base_cost + risk_cost_i + burn_rate * max(delay_i, 0)

   then percentile once, at the very end (invariant 1).

The chunking is not an optimisation, it is what makes step 5 possible at all: the CPM
needs four ``(iterations, activities)`` arrays live at once, which is 1.6 GB for a
five-thousand-activity schedule at ten thousand iterations. Risk variables stay full-length
because Iman-Conover reorders whole columns and cannot be done a slice at a time; activity
background variability is drawn per chunk from a stream addressed by ``(seed, chunk)``, so
the numbers depend on the run definition rather than on the order the work happened to be
scheduled in. The resolved chunk size travels in the manifest for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from app.sim.correlation import CorrelationReport, induce_rank_correlation
from app.sim.errors import RunTooLarge, SimulationInputInvalid
from app.sim.distributions import DistributionSpec
from app.sim.inputs import RiskInput, RunConfig, SimulationRequest
from app.sim.joint import JointConfidence, joint_confidence
from app.sim.network import CRITICAL_TOLERANCE, CompiledNetwork
from app.sim.results import (
    ContingencyView,
    DeterministicView,
    PercentilePoint,
    RunManifest,
    SeriesSummary,
    summarise,
)
from app.sim.sampling import spawn_generator, uniform_matrix
from app.sim.sensitivity import (
    ActivityCriticality,
    DurationAccumulator,
    RiskSensitivity,
    rank_correlation_with,
    variance_shares,
)
from pydantic import BaseModel, ConfigDict

__all__ = ["ENGINE_VERSION", "SimulationResult", "RunArrays", "Outcome", "run"]

#: Bumped whenever a change moves the numbers. Part of every manifest, because "same
#: inputs, same answer" is only meaningful next to the code that produced it.
#:
#: 1.1.0 moved no number. It added the joint cost-schedule view and the schedule
#: sensitivity index to the reported result, and the minor bump is what lets a stored run
#: be told apart from one that simply had nothing joint to report. Every 1.0.0 percentile
#: reproduces exactly under 1.1.0, and the request fingerprint is untouched because
#: neither addition took a new config field.
ENGINE_VERSION = "1.1.0"

#: Below this many expected occurrences a risk's own tail is too thinly sampled to read.
_THIN_TAIL_OCCURRENCES = 30


class SimulationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: RunManifest
    deterministic: DeterministicView
    contingency: ContingencyView

    #: Risk cost alone, before the base and before the burn-rate term.
    risk_cost: SeriesSummary
    total_cost: SeriesSummary
    #: Working days against the engine's own deterministic finish. Unclamped: an iteration
    #: that finishes early is a real outcome and hiding it would misreport the spread.
    delay_days: SeriesSummary | None = None
    finish_day: SeriesSummary | None = None
    #: The burn-rate term, per iteration. Carried separately so the schedule's share of
    #: the contingency can be quoted without re-running anything.
    schedule_driven_cost: SeriesSummary | None = None

    risk_sensitivity: tuple[RiskSensitivity, ...] = ()
    #: The burn-rate term's own share of the total-cost variance. Reported beside the
    #: risk shares rather than folded into them because it is not a risk: it is what the
    #: whole network did to the budget. With it the shares decompose the total, and the
    #: split between "cost risk" and "schedule risk" is readable straight off the two
    #: subtotals.
    schedule_variance_share: float = 0.0
    activity_criticality: tuple[ActivityCriticality, ...] = ()
    #: The cost and date distributions read together rather than side by side. ``None`` on
    #: a cost-only run, and on a run too short to place a joint quantile in.
    joint: JointConfidence | None = None
    correlation: CorrelationReport = CorrelationReport(variables=0)
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class RunArrays:
    """Raw per-iteration output, for a caller that wants to persist or re-cut it.

    Kept out of :class:`SimulationResult` because that model is serialised into an API
    response and a JSON array of ten thousand floats per series is not a payload anyone
    wants by default.
    """

    risk_cost: NDArray[np.float64]
    total_cost: NDArray[np.float64]
    delay_days: NDArray[np.float64] | None = None
    finish_day: NDArray[np.float64] | None = None
    #: ``(iterations, risks)`` — each risk's realised cost contribution.
    contributions: NDArray[np.float64] = field(
        default_factory=lambda: np.empty((0, 0), dtype=np.float64)
    )
    risk_ids: tuple[int, ...] = ()


@dataclass(slots=True)
class Outcome:
    result: SimulationResult
    arrays: RunArrays


# --------------------------------------------------------------------------------------
# variable layout
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class _Layout:
    """Which column of the uniform matrix holds what."""

    occ: dict[int, int] = field(default_factory=dict)
    cost: dict[int, int] = field(default_factory=dict)
    sched: dict[int, int] = field(default_factory=dict)
    width: int = 0


def _build_layout(risks: tuple[RiskInput, ...]) -> _Layout:
    lay = _Layout()
    col = 0
    for r in risks:
        if r.p_occurrence < 1.0:
            lay.occ[r.risk_id] = col
            col += 1
        if r.cost is not None:
            lay.cost[r.risk_id] = col
            col += 1
        if r.sched is not None:
            lay.sched[r.risk_id] = col
            col += 1
    lay.width = col
    return lay


def _target_matrix(req: SimulationRequest, lay: _Layout) -> NDArray[np.float64]:
    """Expand risk-level correlation onto the variable columns.

    Like correlates with like: a pair's coefficient is applied between the two risks' cost
    columns, between their schedule columns, and between their occurrence columns. Cost
    against another risk's schedule is left at zero because nobody elicits it and a
    plausible-looking guess would move the answer without anyone having said so.

    Within a single risk, cost and schedule are independent unless
    ``intra_risk_cost_sched_correlation`` says otherwise. Occurrence is a separate draw
    from magnitude by construction: the magnitude distributions were elicited *given*
    occurrence.
    """
    v = lay.width
    m = np.eye(v, dtype=np.float64)
    if v < 2:
        return m

    coeff = {d.name: d.coefficient for d in req.correlation.drivers}
    tags = {r.risk_id: set(r.drivers) for r in req.risks}

    pair: dict[tuple[int, int], float] = {}
    ids = [r.risk_id for r in req.risks]
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            shared = tags[a] & tags[b]
            if shared:
                # Strongest shared driver, never a sum: correlations do not add, and a
                # sum runs past 1.0 on the third shared tag.
                best = max(coeff[s] for s in shared)
                pair[(a, b)] = best
    for p in req.correlation.pairs:
        key = (p.risk_a, p.risk_b) if p.risk_a < p.risk_b else (p.risk_b, p.risk_a)
        pair[key] = p.coefficient

    blocks = [lay.cost, lay.sched]
    if req.config.correlate_occurrence:
        blocks.append(lay.occ)

    for (a, b), rho in pair.items():
        if rho == 0.0:
            continue
        for block in blocks:
            ia, ib = block.get(a), block.get(b)
            if ia is not None and ib is not None:
                m[ia, ib] = m[ib, ia] = rho

    rho_intra = req.config.intra_risk_cost_sched_correlation
    if rho_intra != 0.0:
        for r in req.risks:
            ic, is_ = lay.cost.get(r.risk_id), lay.sched.get(r.risk_id)
            if ic is not None and is_ is not None:
                m[ic, is_] = m[is_, ic] = rho_intra

    return m


# --------------------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------------------


def _resolve_chunk(req: SimulationRequest, n_activities: int) -> int:
    cfg = req.config
    if cfg.chunk_size is not None:
        return min(cfg.chunk_size, cfg.iterations)
    if n_activities == 0:
        return cfg.iterations
    # The CPM holds ES, EF, LS, LF and the durations simultaneously.
    live_arrays = 5
    budget = cfg.memory_budget_mb * 1e6 * 0.25
    c = int(budget // (live_arrays * 8 * n_activities))
    return int(min(max(c, 64), 4096, cfg.iterations))


def _guard(bytes_needed: float, budget_mb: float, what: str, remedy: str) -> None:
    if bytes_needed > budget_mb * 1e6:
        raise RunTooLarge(what, bytes_needed / 1e6, budget_mb, remedy)


def run(req: SimulationRequest) -> Outcome:
    """Execute a run. Pure: same request in, same numbers out, no side effects."""
    cfg = req.config
    n = cfg.iterations
    warnings: list[str] = []

    lay = _build_layout(req.risks)
    _guard(
        n * lay.width * 8 * 3,
        cfg.memory_budget_mb,
        f"The correlated sample matrix for {len(req.risks)} risks at {n:,} iterations",
        "Reduce iterations, or raise memory_budget_mb.",
    )

    # -- 1. uniforms, correlated ------------------------------------------------
    u = uniform_matrix(
        spawn_generator(cfg.seed, 0),
        n,
        lay.width,
        method=cfg.sampling,
        centered=cfg.centered_lhs,
    )
    target = _target_matrix(req, lay)
    u, corr_report = induce_rank_correlation(u, target, spawn_generator(cfg.seed, 1))
    warnings.extend(corr_report.notes)

    # -- 2. occurrence and magnitudes -------------------------------------------
    occurred: dict[int, NDArray[np.bool_]] = {}
    cost_contrib: dict[int, NDArray[np.float64]] = {}
    sched_impact: dict[int, NDArray[np.float64]] = {}

    for r in req.risks:
        if r.p_occurrence >= 1.0:
            hit = np.ones(n, dtype=bool)
        else:
            hit = u[:, lay.occ[r.risk_id]] < r.p_occurrence
            expected = r.p_occurrence * n
            if expected < _THIN_TAIL_OCCURRENCES:
                warnings.append(
                    f"Risk {r.code or r.risk_id} is expected to occur about "
                    f"{expected:.0f} time(s) in {n:,} iterations. Its own impact tail is "
                    "too thinly sampled to read; raise the iteration count before "
                    "quoting a percentile that depends on it."
                )
        occurred[r.risk_id] = hit

        if r.cost is not None:
            magnitude = r.cost.ppf(u[:, lay.cost[r.risk_id]])
            if r.cost_basis == "pct_of_base":
                ref = r.cost_base_reference
                ref = cfg.base_cost if ref is None else ref
                if ref <= 0.0:
                    warnings.append(
                        f"Risk {r.code or r.risk_id} is quantified as a percentage of a "
                        "base of zero, so it contributes nothing. Set "
                        "cost_base_reference on the risk or base_cost on the run."
                    )
                magnitude = magnitude * (ref / 100.0)
            cost_contrib[r.risk_id] = np.where(hit, magnitude, 0.0)

        if r.sched is not None:
            sched_impact[r.risk_id] = np.where(
                hit, r.sched.ppf(u[:, lay.sched[r.risk_id]]), 0.0
            )

    risk_cost = np.zeros(n, dtype=np.float64)
    for c in cost_contrib.values():
        risk_cost += c

    if not cost_contrib and not sched_impact:
        warnings.append(
            "No risk carries a cost or schedule distribution, so the run has nothing to "
            "simulate beyond the base."
        )

    # -- 3. the network ---------------------------------------------------------
    delay = None
    finish = None
    criticality: tuple[ActivityCriticality, ...] = ()
    det = DeterministicView(base_cost=cfg.base_cost)
    chunk_used = n

    if req.schedule is not None:
        finish, delay, criticality, det, sched_warnings, chunk_used = _run_network(
            req, sched_impact, occurred
        )
        warnings.extend(sched_warnings)

    # -- 4. integrate, inside the iteration -------------------------------------
    if delay is None:
        schedule_cost = np.zeros(n, dtype=np.float64)
    elif cfg.allow_negative_delay_credit:
        schedule_cost = cfg.burn_rate_per_day * delay
    else:
        schedule_cost = cfg.burn_rate_per_day * np.maximum(delay, 0.0)

    total_cost = cfg.base_cost + risk_cost + schedule_cost

    # -- 5. summarise -----------------------------------------------------------
    def _sum(values: NDArray[np.float64], label: str, units: str) -> SeriesSummary:
        return summarise(
            values,
            label=label,
            units=units,
            percentiles=cfg.percentiles,
            s_curve_points=cfg.s_curve_points,
            histogram_bins=cfg.histogram_bins,
        )

    total_summary = _sum(total_cost, "Total cost", "currency")
    contingency = _contingency(
        cfg, total_summary, total_cost, risk_cost, delay, schedule_cost, warnings
    )

    joint = _joint(cfg, total_cost, delay, det.baseline_finish_day, warnings)

    ids = tuple(r.risk_id for r in req.risks)
    contributions = (
        np.column_stack([cost_contrib.get(i, np.zeros(n)) for i in ids])
        if ids
        else np.empty((n, 0), dtype=np.float64)
    )

    result = SimulationResult(
        manifest=RunManifest(
            engine_version=ENGINE_VERSION,
            seed=cfg.seed,
            iterations=n,
            sampling=cfg.sampling,
            centered_lhs=cfg.centered_lhs,
            chunk_size=chunk_used,
            inputs_sha256=req.fingerprint(),
            calendar_id=None if req.schedule is None else req.schedule.calendar_id,
        ),
        deterministic=det,
        contingency=contingency,
        risk_cost=_sum(risk_cost, "Risk cost", "currency"),
        total_cost=total_summary,
        delay_days=None if delay is None else _sum(delay, "Schedule delay", "days"),
        finish_day=None if finish is None else _sum(finish, "Project finish", "days"),
        schedule_driven_cost=(
            None if delay is None else _sum(schedule_cost, "Burn-rate cost", "currency")
        ),
        risk_sensitivity=_risk_sensitivity(
            req,
            contributions,
            total_cost,
            schedule_cost,
            contingency.schedule_variance_share,
            occurred,
            sched_impact,
            delay,
        ),
        schedule_variance_share=float(
            variance_shares(schedule_cost[:, None], total_cost)[0]
        ),
        activity_criticality=criticality,
        joint=joint,
        correlation=corr_report,
        warnings=tuple(dict.fromkeys(warnings)),
    )

    return Outcome(
        result=result,
        arrays=RunArrays(
            risk_cost=risk_cost,
            total_cost=total_cost,
            delay_days=delay,
            finish_day=finish,
            contributions=contributions,
            risk_ids=ids,
        ),
    )


def _joint(
    cfg: RunConfig,
    total_cost: NDArray[np.float64],
    delay: NDArray[np.float64] | None,
    baseline_finish: float | None,
    warnings: list[str],
) -> JointConfidence | None:
    """The joint cost-date view, and the warning that is the point of having it.

    Frontier targets come from the run's own percentile grid, filtered to the half of it
    anyone commits against: a joint P5 curve is arithmetic without a use. No new setting,
    so the request fingerprint is untouched and every run recorded before this existed
    still verifies against its stored hash.
    """
    if delay is None:
        return None

    targets = tuple(p for p in sorted(cfg.percentiles) if p >= 50.0) or (80.0,)
    view = joint_confidence(
        total_cost,
        delay,
        targets=targets,
        baseline_finish=0.0 if baseline_finish is None else baseline_finish,
        burn_rate_coupled=cfg.burn_rate_per_day > 0.0,
    )
    if view is None:
        return None

    target = view.marginal_pair_target / 100.0
    achieved = view.joint_at_marginal_pair
    if achieved < target - 0.02:
        frontier = next(
            (f for f in view.frontiers if f.target == view.marginal_pair_target), None
        )
        balanced = frontier.balanced if frontier is not None else None
        tail = (
            ""
            if balanced is None
            else (
                f" Holding both to P{balanced.cost_p:.0f} instead — "
                f"{balanced.total_cost:,.0f} and {balanced.delay_days:,.0f} days — is "
                f"the pair that is actually {view.marginal_pair_target:.0f}% confident."
            )
        )
        warnings.append(
            f"Quoting the P{view.marginal_pair_target:.0f} cost beside the "
            f"P{view.marginal_pair_target:.0f} date describes a package that is only "
            f"{achieved:.0%} likely to be met on both, not "
            f"{view.marginal_pair_target:.0f}%. The two tails are not the same "
            f"iteration.{tail}"
        )
    return view


def _run_network(
    req: SimulationRequest,
    sched_impact: dict[int, NDArray[np.float64]],
    occurred: dict[int, NDArray[np.bool_]],
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    tuple[ActivityCriticality, ...],
    DeterministicView,
    list[str],
    int,
]:
    cfg = req.config
    sched = req.schedule
    assert sched is not None
    n = cfg.iterations
    notes: list[str] = []

    real_ids = [a.activity_id for a in sched.activities]
    ids: list[str] = list(real_ids)
    edges: list[tuple[str, str, str, float]] = [
        (r.predecessor_id, r.successor_id, r.type, r.lag_days)
        for r in sched.relationships
    ]

    # Inserted activities: work the risk adds that the schedule does not contain. One
    # synthetic node per insertion point, duration zero when the risk misses.
    inserted: list[tuple[str, int, float]] = []  # (synthetic id, risk id, allocation)
    for r in req.risks:
        insertions = [m for m in r.mappings if m.mapping_type == "inserted_activity"]
        for k, m in enumerate(insertions):
            sid = f"__risk{r.risk_id}_ins{k}"
            if sid in set(ids):
                raise SimulationInputInvalid(
                    [f"synthetic activity id {sid!r} collides with a real activity"]
                )
            alloc = (
                1.0 / len(insertions)
                if m.allocation_pct is None
                else m.allocation_pct / 100.0
            )
            ids.append(sid)
            inserted.append((sid, r.risk_id, alloc))
            assert m.predecessor_id is not None and m.successor_id is not None
            edges.append((m.predecessor_id, sid, "FS", 0.0))
            edges.append((sid, m.successor_id, "FS", 0.0))

    a_total = len(ids)
    idx = {a: i for i, a in enumerate(ids)}

    min_start = np.zeros(a_total, dtype=np.float64)
    base_dur = np.zeros(a_total, dtype=np.float64)
    uncertainty: list[tuple[int, DistributionSpec]] = []
    hard_constraints = 0
    for a in sched.activities:
        i = idx[a.activity_id]
        base_dur[i] = a.duration_days
        if a.min_start_day is not None:
            min_start[i] = a.min_start_day
        if a.uncertainty is not None and not a.uncertainty.is_degenerate:
            uncertainty.append((i, a.uncertainty))
        if a.has_hard_constraint:
            hard_constraints += 1

    if hard_constraints:
        notes.append(
            f"{hard_constraints} activity/activities carry a hard date constraint. The "
            "forward pass applies network logic and min_start_day only, so a mandatory "
            "date is not enforced and the simulated finish may be earlier than the "
            "schedule would allow."
        )
    if not uncertainty:
        notes.append(
            "No activity carries duration uncertainty. Only discrete risk events are "
            "driving the schedule, which produces an unrealistically tight base "
            "distribution and a criticality index that barely moves."
        )

    net = CompiledNetwork(
        ids,
        edges,
        min_start=min_start,
        finish_activity_ids=sched.finish_activity_ids,
    )

    det_es, det_ef = net.forward(base_dur.reshape(1, -1))
    det_pf = net.project_finish(det_ef)
    baseline = float(det_pf[0])
    det_ls, _ = net.backward(base_dur.reshape(1, -1), det_pf)
    det_critical = int(((det_ls - det_es)[0] <= CRITICAL_TOLERANCE).sum())

    # Which risks drive which activities, resolved once.
    drivers: list[tuple[int, NDArray[np.int64]]] = []
    for r in req.risks:
        driven = sorted(
            {
                idx[a]
                for m in r.mappings
                if m.mapping_type in ("duration_driver", "scoped_driver")
                for a in m.activity_ids
            }
        )
        if driven and r.risk_id in sched_impact:
            drivers.append((r.risk_id, np.array(driven, dtype=np.int64)))

    chunk = _resolve_chunk(req, a_total)
    finish = np.empty(n, dtype=np.float64)
    crit_count = np.zeros(a_total, dtype=np.int64)
    float_sum = np.zeros(a_total, dtype=np.float64)
    accum = DurationAccumulator(base_dur, baseline)

    for start in range(0, n, chunk):
        stop = min(start + chunk, n)
        rows = stop - start
        c_idx = start // chunk

        dur = np.empty((rows, a_total), dtype=np.float64, order="F")
        dur[:] = base_dur[None, :]

        if uncertainty:
            cu = uniform_matrix(
                spawn_generator(cfg.seed, 2, c_idx),
                rows,
                len(uncertainty),
                method=cfg.sampling,
                centered=cfg.centered_lhs,
            )
            for k, (i, spec) in enumerate(uncertainty):
                dur[:, i] = spec.ppf(cu[:, k])

        for rid, cols in drivers:
            dur[:, cols] += sched_impact[rid][start:stop, None]

        for sid, rid, alloc in inserted:
            i = idx[sid]
            impact = sched_impact.get(rid)
            dur[:, i] = (
                0.0 if impact is None else impact[start:stop] * alloc
            ) * occurred[rid][start:stop]

        np.maximum(dur, 0.0, out=dur)

        es, ef = net.forward(dur)
        pf = net.project_finish(ef)
        ls, _ = net.backward(dur, pf)

        tf = ls - es
        crit_count += (tf <= CRITICAL_TOLERANCE).sum(axis=0)
        float_sum += tf.sum(axis=0)
        accum.add(dur, pf)
        finish[start:stop] = pf

    delay = finish - baseline

    sens = accum.correlation()
    dur_sd, finish_sd = accum.spreads()
    inserted_ids = {sid for sid, _, _ in inserted}
    labels = {a.activity_id: a for a in sched.activities}
    rows_out: list[ActivityCriticality] = []
    for i, aid in enumerate(ids):
        ci = float(crit_count[i]) / n
        s = sens[i]
        sd_i = float(dur_sd[i])
        meta = labels.get(aid)
        rows_out.append(
            ActivityCriticality(
                activity_id=aid,
                code="" if meta is None else meta.code,
                name="" if meta is None else meta.name,
                criticality_index=ci,
                mean_total_float_days=float(float_sum[i]) / n,
                duration_sensitivity=None if np.isnan(s) else float(s),
                cruciality=0.0 if np.isnan(s) else ci * abs(float(s)),
                duration_sd_days=sd_i,
                schedule_sensitivity_index=(
                    0.0 if finish_sd <= 0.0 else ci * sd_i / finish_sd
                ),
                is_inserted=aid in inserted_ids,
            )
        )
    rows_out.sort(key=lambda x: (-x.cruciality, -x.criticality_index, x.activity_id))
    keep = cfg.max_sensitivity_activities
    if len(rows_out) > keep:
        # Retained on whichever of the two rankings rates the activity higher, because
        # they disagree on exactly the activities worth arguing about and truncating on
        # one of them would delete the other's answer before anyone saw it.
        rank = sorted(
            rows_out,
            key=lambda x: (
                -max(x.cruciality, x.schedule_sensitivity_index),
                -x.criticality_index,
                x.activity_id,
            ),
        )
        survivors = {x.activity_id for x in rank[:keep]}
        notes.append(
            f"Activity results are truncated to the top {keep} of {len(rows_out)} by "
            "cruciality and schedule sensitivity index. Raise "
            "max_sensitivity_activities to see more."
        )
        rows_out = [x for x in rows_out if x.activity_id in survivors]

    negative = float((delay < 0).mean())
    if negative > 0.5 and not cfg.allow_negative_delay_credit:
        notes.append(
            f"{negative:.0%} of iterations finish earlier than the deterministic "
            "baseline, so the burn-rate term is zero in most of them. That usually means "
            "the elicited activity durations sit below the schedule's own, which is a "
            "finding about the estimate rather than about the risk."
        )

    det = DeterministicView(
        base_cost=cfg.base_cost,
        activities=len(sched.activities),
        relationships=len(sched.relationships),
        inserted_activities=len(inserted),
        baseline_finish_day=baseline,
        critical_activities=det_critical,
    )
    return finish, delay, tuple(rows_out), det, notes, chunk


def _contingency(
    cfg: RunConfig,
    total_summary: SeriesSummary,
    total_cost: NDArray[np.float64],
    risk_cost: NDArray[np.float64],
    delay: NDArray[np.float64] | None,
    schedule_cost: NDArray[np.float64],
    warnings: list[str],
) -> ContingencyView:
    points = tuple(
        PercentilePoint(p=p.p, value=p.value - cfg.base_cost)
        for p in total_summary.percentiles
    )

    additive = None
    integrated = None
    error = None
    if delay is not None and cfg.burn_rate_per_day > 0:
        integrated = float(np.percentile(total_cost, 80.0))
        additive = (
            cfg.base_cost
            + float(np.percentile(risk_cost, 80.0))
            + cfg.burn_rate_per_day * max(float(np.percentile(delay, 80.0)), 0.0)
        )
        error = additive - integrated
        # Measured against the contingency, not the total. A 165k error inside a 27m
        # total is 0.6% and reads as noise; against the 1.8m contingency it is 9%, and
        # the contingency is the number the error actually corrupts.
        contingency_p80 = integrated - cfg.base_cost
        scale = contingency_p80 if contingency_p80 > 0 else integrated
        if scale > 0 and abs(error) / scale > 0.01:
            warnings.append(
                f"Adding the P80s instead of integrating inside each iteration would "
                f"report {additive:,.0f} against the correct {integrated:,.0f}, a "
                f"difference of {error:,.0f} ({abs(error) / scale:.0%} of the "
                "contingency). The figure to quote is the integrated one."
            )

    tv = float(total_cost.var())
    if tv > 0.0:
        centred = total_cost - total_cost.mean()
        sched_share = float(
            ((schedule_cost - schedule_cost.mean()) * centred).mean() / tv
        )
    else:
        sched_share = 0.0

    return ContingencyView(
        base_cost=cfg.base_cost,
        mean_total_cost=total_summary.mean,
        contingency=points,
        additive_p80_total=additive,
        integrated_p80_total=integrated,
        additive_error_at_p80=error,
        cost_variance_share=1.0 - sched_share,
        schedule_variance_share=sched_share,
    )


def _risk_sensitivity(
    req: SimulationRequest,
    contributions: NDArray[np.float64],
    total_cost: NDArray[np.float64],
    schedule_cost: NDArray[np.float64],
    schedule_share: float,
    occurred: dict[int, NDArray[np.bool_]],
    sched_impact: dict[int, NDArray[np.float64]],
    delay: NDArray[np.float64] | None,
) -> tuple[RiskSensitivity, ...]:
    """Rank the risks by how much of the answer each one owns.

    The cost side is an exact variance decomposition. The schedule side cannot be: delay
    is a maximum over network paths, so there is no additive split of it into per-risk
    terms, and any claim otherwise is a fiction dressed as arithmetic. What is done
    instead is to take the burn-rate term's *own* share — which is exact — and divide it
    among the risks that drive the network in proportion to how each one covaries with it.
    The total stays right; only the attribution is approximate, and it is labelled as
    such on the field.

    Ranking on the cost share alone was the alternative and it is worse than approximate,
    it is wrong: on a schedule-driven project the risk that owns the answer often carries
    no direct cost at all, and it would sort to the bottom of its own tornado.
    """
    if not req.risks:
        return ()

    cost_shares = variance_shares(contributions, total_cost)
    rho_cost = rank_correlation_with(contributions, total_cost)

    sched_cols = np.column_stack(
        [sched_impact.get(r.risk_id, np.zeros(total_cost.size)) for r in req.risks]
    )
    rho_delay = rank_correlation_with(sched_cols, delay) if delay is not None else None

    # Weights for apportioning the burn term: how each risk's sampled delay moves with
    # the cost that delay produced. Non-negative in every realistic case, since more
    # delay on a driven activity can only push the finish out.
    weights = np.zeros(len(req.risks), dtype=np.float64)
    if schedule_cost.var() > 0.0:
        sc = schedule_cost - schedule_cost.mean()
        cc = sched_cols - sched_cols.mean(axis=0)
        weights = np.maximum((cc * sc[:, None]).mean(axis=0), 0.0)
    total_weight = float(weights.sum())

    out: list[RiskSensitivity] = []
    for j, r in enumerate(req.risks):
        col = contributions[:, j]
        drives = r.risk_id in sched_impact and bool(r.mappings)
        if drives and total_weight > 0.0:
            s_share: float | None = schedule_share * float(weights[j]) / total_weight
        elif drives:
            s_share = 0.0
        else:
            s_share = None
        cost_share = float(cost_shares[j])
        out.append(
            RiskSensitivity(
                risk_id=r.risk_id,
                code=r.code,
                title=r.title,
                cost_variance_share=cost_share,
                schedule_variance_share=s_share,
                combined_variance_share=cost_share + (s_share or 0.0),
                spearman_total_cost=float(rho_cost[j]),
                spearman_delay=(
                    None
                    if rho_delay is None or r.risk_id not in sched_impact
                    else float(rho_delay[j])
                ),
                mean_contribution=float(col.mean()),
                p80_contribution=float(np.percentile(col, 80.0)),
                realised_frequency=float(occurred[r.risk_id].mean()),
            )
        )
    out.sort(
        key=lambda x: (-abs(x.combined_variance_share), -abs(x.spearman_total_cost))
    )
    return tuple(out)
