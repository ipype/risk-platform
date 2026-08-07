"""The sections a report can carry, and the rule for when each one has anything to say.

Every builder here is a pure function of :class:`~app.services.report.data.ReportData`.
No database, no clock, no formatting — blocks in, blocks out. The registry is ordered, and
that order is the order of the document.

A section is either *available* or it states why not. Nothing renders an empty heading and
nothing silently disappears: "Schedule outcome — this run carried no schedule" is a
finding, and a missing schedule section is a mystery.

Three things this file is careful about, all of them the kind of mistake that produces a
number a client will quote:

* The additive contingency is printed **next to** the integrated one, labelled as the
  wrong arithmetic (invariant 1). Not printing it does not stop anyone doing it in a
  spreadsheet afterwards; printing the gap does.
* A run that entered the engine over a failed DCMA gate says so at the top of the basis,
  with the reason the human gave (invariant 3).
* Excluded risks are listed. A contingency computed over a subset of the register without
  saying which subset is the most expensive kind of wrong this platform can produce.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from app.services.report.data import ReportData
from app.services.report.model import (
    AnyBlock,
    Callout,
    Cell,
    Column,
    KeyValue,
    KeyValues,
    MatrixBlock,
    Paragraph,
    Section,
    Table,
    format_value,
    text_cell,
    value_cell,
)

__all__ = [
    "SECTIONS",
    "SectionSpec",
    "available_ids",
    "build_sections",
    "section_by_id",
]

HEADLINE_P = 80.0


@dataclass(frozen=True)
class SectionSpec:
    id: str
    title: str
    #: One line for the picker. What this section answers, not what it contains.
    summary: str
    build: Callable[[ReportData], Section]
    #: ``None`` when the section can be built. Otherwise the reason, in a sentence a
    #: reader of the picker can act on.
    unavailable: Callable[[ReportData], str | None]


# ------------------------------------------------------------------------------ helpers


def _percentile(points: Iterable, p: float) -> float | None:
    for point in points:
        if abs(point.p - p) < 1e-9:
            return point.value
    return None


def _duration(ms: int | None) -> str:
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms} ms"
    seconds = ms / 1000
    if seconds < 90:
        return f"{seconds:.1f} s"
    return f"{seconds / 60:.1f} min"


def _kv(label: str, value: object, note: str | None = None, fmt: str = "text",
        currency: str = "") -> KeyValue:
    return KeyValue(
        label=label,
        value=format_value(value, fmt, currency),  # type: ignore[arg-type]
        note=note,
    )


def _table(
    caption: str | None,
    columns: Sequence[Column],
    rows: Sequence[Sequence[Cell]],
    note: str | None = None,
    empty_text: str = "Nothing to report.",
) -> Table:
    return Table(
        caption=caption,
        columns=tuple(columns),
        rows=tuple(tuple(row) for row in rows),
        note=note,
        empty_text=empty_text,
    )


def _counts(values: Iterable[str | None]) -> list[tuple[str, int]]:
    out: dict[str, int] = {}
    for value in values:
        key = value or "Unscored"
        out[key] = out.get(key, 0) + 1
    return sorted(out.items(), key=lambda item: (-item[1], item[0]))


# -------------------------------------------------------------------------------- cover


def _build_cover(data: ReportData) -> Section:
    run = data.run
    scope = data.scope

    items = [
        _kv("Report", data.title),
        _kv("Scope", data.subtitle or "All scopes"),
    ]
    if scope is not None:
        items.append(_kv("Scope type", scope.kind.title()))
        if scope.code:
            items.append(_kv("Scope code", scope.code))
    items.append(_kv("Prepared by", data.prepared_by or "—"))
    items.append(_kv("Report date", data.generated_on))
    items.append(_kv("Risks in register", len(data.risks), fmt="int"))

    if run is None:
        items.append(
            _kv(
                "Quantitative basis",
                "None — register and matrix only",
                note="No simulation run was attached to this report.",
            )
        )
    else:
        items.append(_kv("Simulation run", f"#{run.id} · {run.name}"))
        items.append(
            _kv(
                "Scenario",
                "Post-mitigation" if run.scenario == "post_mitigation" else "Pre-mitigation",
            )
        )

    blocks: list[AnyBlock] = [KeyValues(items=tuple(items))]
    if scope is not None and scope.description:
        blocks.append(Paragraph(text=scope.description))
    return Section(id="cover", title=data.title, blocks=tuple(blocks))


# -------------------------------------------------------------------------------- basis


def _build_basis(data: ReportData) -> Section:
    blocks: list[AnyBlock] = []
    run = data.run

    if run is None:
        blocks.append(
            Callout(
                tone="info",
                title="No simulation attached",
                text=(
                    "This report covers the register and the qualitative assessment only. "
                    "Contingency, sensitivity and criticality sections require a completed "
                    "Monte Carlo run."
                ),
            )
        )
    else:
        if run.gate_override:
            blocks.append(
                Callout(
                    tone="warning",
                    title="Schedule quality gate was overridden",
                    text=(
                        "This run simulated a schedule that did not pass the DCMA 14-point "
                        "assessment. A human accepted that explicitly. Reason given: "
                        f"{run.gate_override_reason or 'none recorded'}. Read every "
                        "schedule figure below against that."
                    ),
                )
            )
        elif run.schedule_version_id is not None and run.gate_passed is None:
            blocks.append(
                Callout(
                    tone="warning",
                    title="Gate status not recorded",
                    text=(
                        "A schedule was simulated but no DCMA assessment is recorded "
                        "against this run."
                    ),
                )
            )
        if run.result_error:
            blocks.append(
                Callout(
                    tone="warning",
                    title="Stored result unreadable",
                    text=run.result_error,
                )
            )

        manifest = run.result.manifest if run.result is not None else None
        blocks.append(
            KeyValues(
                caption="What was run",
                items=(
                    _kv("Run", f"#{run.id} · {run.name}"),
                    _kv("Status", run.status.title()),
                    _kv(
                        "Scenario",
                        "Post-mitigation"
                        if run.scenario == "post_mitigation"
                        else "Pre-mitigation",
                    ),
                    _kv("Run by", run.created_by),
                    _kv("Started", run.created_at),
                    _kv("Finished", run.finished_at),
                    _kv("Engine time", _duration(run.duration_ms)),
                ),
            )
        )
        blocks.append(
            KeyValues(
                caption="Reproducibility record",
                items=(
                    _kv("Engine version", run.engine_version),
                    _kv("Iterations", run.iterations, fmt="int"),
                    # An identifier, not a quantity. A seed printed as "4,242" is a seed
                    # somebody retypes wrong when they come to replay the run.
                    _kv("Seed", str(run.seed)),
                    _kv("Sampling", run.sampling.upper()),
                    _kv(
                        "Chunk size",
                        run.chunk_size if manifest is None else manifest.chunk_size,
                        note="Resolved by the engine, not requested. Replaying a run "
                        "replays its chunking.",
                        fmt="int",
                    ),
                    _kv("Inputs SHA-256", run.inputs_sha256 or "—"),
                    _kv(
                        "Schedule version",
                        "—"
                        if run.schedule_version_id is None
                        else f"#{run.schedule_version_id}",
                    ),
                ),
            )
        )
        blocks.append(
            KeyValues(
                caption="What went in",
                items=(
                    _kv("Base cost", run.base_cost, fmt="currency", currency=data.currency),
                    _kv(
                        "Burn rate",
                        run.burn_rate_per_day,
                        note="Applied to the delay inside each iteration, never to a "
                        "percentile afterwards.",
                        fmt="currency",
                        currency=data.currency,
                    ),
                    _kv("Risks simulated", run.risk_count, fmt="int"),
                    _kv("Risks mapped to activities", run.mapped_risk_count, fmt="int"),
                    _kv("Activities", run.activity_count, fmt="int"),
                    _kv("Risks in register", len(data.risks), fmt="int"),
                ),
            )
        )

        blocks.append(
            _table(
                "Risks excluded from the run",
                [
                    Column(label="Risk", width=16),
                    Column(label="Title", width=40),
                    Column(label="Why it was excluded", width=60),
                ],
                [
                    [
                        text_cell(str(item.get("risk_code", ""))),
                        text_cell(str(item.get("title", ""))),
                        text_cell(str(item.get("reason", ""))),
                    ]
                    for item in run.excluded
                ],
                note="Every excluded risk is named. A contingency computed over part of "
                "the register without saying which part is not a contingency.",
                empty_text="No risk was excluded. Every quantified risk entered the run.",
            )
        )

        if run.assembly_notes:
            blocks.append(
                _table(
                    "Assembly notes",
                    [Column(label="Note", width=110)],
                    [[text_cell(note)] for note in run.assembly_notes],
                    note="Raised while building the run from the register, the estimates "
                    "and the parsed schedule.",
                )
            )

    if data.notes:
        blocks.append(
            _table(
                "Notes on this report",
                [Column(label="Note", width=110)],
                [[text_cell(note)] for note in data.notes],
            )
        )

    return Section(id="basis", title="Basis of the analysis", blocks=tuple(blocks))


# ------------------------------------------------------------------------------- method


def _build_method(data: ReportData) -> Section:
    lines = [
        "Cost and schedule risk are simulated together. Each iteration draws every risk's "
        "occurrence and impact, runs the affected activities through the network, prices "
        "the resulting delay at the burn rate, and adds the result to that iteration's "
        "cost. Percentiles are taken once, at the end, over the integrated total.",
        "Risk draws pass through Iman-Conover rank correlation before sampling, on the "
        "uniform substrate. Sampling risks independently understates the tail, because "
        "the correlated risks are exactly the ones that show up together in a bad month.",
        "Quantitative impacts are elicited as ranges with a distribution family per "
        "dimension: a risk's cost shape and its schedule shape are set separately, "
        "because they routinely differ.",
    ]
    if data.has_schedule:
        lines.append(
            "The baseline finish is this engine's own forward pass over the deterministic "
            "durations, not the dates in the imported schedule. Those dates came out of "
            "the planning tool under constraints, calendars and progress overrides this "
            "pass does not model, so subtracting them would report the difference between "
            "two CPM engines as risk. Both figures are carried where they are available."
        )
        lines.append(
            "Delay is reported unclamped. An iteration that finishes early is a real "
            "outcome, and hiding it would understate the spread."
        )
    lines.append(
        "Percentiles interpolate linearly between order statistics — NumPy's default and "
        "the convention every commercial risk tool reports. It matters only in the far "
        "tail at low iteration counts."
    )

    blocks: list[AnyBlock] = [Paragraph(text=line) for line in lines]
    blocks.insert(
        0,
        Callout(
            tone="method",
            title="Percentiles are never added",
            text=(
                "The contingency below is the percentile of the integrated total. Adding a "
                "P80 cost to a P80 delay priced at the burn rate assumes the cost tail and "
                "the schedule tail land in the same iteration, which is a claim of perfect "
                "correlation nobody made. Where the engine measured that gap it is printed "
                "in the contingency section."
            ),
        ),
    )
    if data.has_schedule:
        blocks.append(
            Callout(
                tone="method",
                title="Approximation declared",
                text=(
                    "A risk's share of the variance reaching the budget through delay is "
                    "apportioned, not exact. Delay is a maximum over network paths, so no "
                    "exact additive split among risks exists. The burn-rate term's own "
                    "share is exact; only its division between risks is approximate."
                ),
            )
        )

    blocks.append(
        Paragraph(
            text="Methodology anchors: AACE International RP 57R-09 for integrated "
            "cost/schedule risk analysis, the DCMA 14-point assessment for schedule "
            "quality, and the Hulett risk-driver method for schedule risk."
        )
    )
    return Section(id="method", title="Method and assumptions", blocks=tuple(blocks))


# ----------------------------------------------------------------------------- register


def _build_register(data: ReportData) -> Section:
    risks = data.risks
    scored = [r for r in risks if r.score is not None]
    quantified = [r for r in risks if r.quantified]

    blocks: list[AnyBlock] = [
        KeyValues(
            items=(
                _kv("Risks in register", len(risks), fmt="int"),
                _kv("Qualitatively scored", len(scored), fmt="int"),
                _kv(
                    "Carrying a quantitative estimate",
                    len(quantified),
                    note="A qualitative score is not an estimate. Only quantified risks "
                    "can reach a contingency figure.",
                    fmt="int",
                ),
            )
        )
    ]

    band_rows = []
    for name, count in _counts(r.band for r in risks):
        color = next((r.band_color for r in risks if r.band == name), None)
        band_rows.append(
            [
                text_cell(name, color=color),
                value_cell(count),
                value_cell(count / len(risks) if risks else 0.0),
            ]
        )
    blocks.append(
        _table(
            "By risk band",
            [
                Column(label="Band", width=20),
                Column(label="Risks", align="right", format="int", width=10),
                Column(label="Share", align="right", format="pct", width=10),
            ],
            band_rows,
        )
    )

    blocks.append(
        _table(
            "By RBS category",
            [
                Column(label="Category", width=32),
                Column(label="Risks", align="right", format="int", width=10),
            ],
            [[text_cell(name), value_cell(count)] for name, count in _counts(
                r.category for r in risks
            )],
        )
    )

    blocks.append(
        _table(
            "By status",
            [
                Column(label="Status", width=20),
                Column(label="Risks", align="right", format="int", width=10),
            ],
            [[text_cell(name), value_cell(count)] for name, count in _counts(
                r.status for r in risks
            )],
        )
    )

    top = sorted(scored, key=lambda r: (-(r.score or 0), r.code))[:15]
    blocks.append(
        _table(
            "Highest-scoring risks",
            [
                Column(label="Risk", width=16),
                Column(label="Title", width=44),
                Column(label="Category", width=22),
                Column(label="Owner", width=18),
                Column(label="P", align="right", format="int", width=6),
                Column(label="I", align="right", format="int", width=6),
                Column(label="Score", align="right", format="int", width=8),
                Column(label="Band", width=16),
                Column(label="Quantified", width=12),
            ],
            [
                [
                    text_cell(r.code),
                    text_cell(r.title),
                    text_cell(r.category),
                    text_cell(r.owner),
                    value_cell(r.probability),
                    value_cell(r.impact),
                    value_cell(r.score, emphasis=True),
                    text_cell(r.band, color=r.band_color),
                    text_cell("Yes" if r.quantified else "No"),
                ]
                for r in top
            ],
            note="Ranked on the qualitative score. Which risks move the *money* is a "
            "different question, answered by the sensitivity section.",
            empty_text="No risk has been scored on both probability and impact.",
        )
    )
    return Section(id="register", title="Risk register", blocks=tuple(blocks))


# ------------------------------------------------------------------------------- matrix


def _build_matrix(data: ReportData) -> Section:
    matrix = data.matrix
    assert matrix is not None  # guarded by unavailable()

    note = None
    if matrix.unplaced:
        note = (
            f"{matrix.placed} of {matrix.placed + matrix.unplaced} risks are placed. "
            f"{matrix.unplaced} are not scored on this view"
        )
        if matrix.off_scale:
            note += f", of which {matrix.off_scale} sit outside the active scale"
        note += "."

    return Section(
        id="matrix",
        title="Risk matrix",
        blocks=(
            MatrixBlock(
                caption=f"{matrix.lens_label} · {matrix.basis_label}",
                probability_levels=matrix.probability_levels,
                impact_levels=matrix.impact_levels,
                cells=matrix.cells,
                bands=matrix.bands,
                note=note,
            ),
        ),
    )


# --------------------------------------------------------------------------------- cost


def _build_cost(data: ReportData) -> Section:
    run = data.run
    assert run is not None and run.result is not None
    result = run.result
    view = result.contingency
    currency = data.currency

    p80 = _percentile(view.contingency, HEADLINE_P)
    base = view.base_cost
    blocks: list[AnyBlock] = [
        KeyValues(
            caption="Headline",
            items=(
                _kv("Base cost", base, fmt="currency", currency=currency),
                _kv("Mean total cost", view.mean_total_cost, fmt="currency",
                    currency=currency),
                _kv(
                    f"Contingency at P{HEADLINE_P:.0f}",
                    p80,
                    note="Total cost at this percentile, less the base.",
                    fmt="currency",
                    currency=currency,
                ),
                _kv(
                    "Contingency as share of base",
                    None if (p80 is None or base <= 0) else p80 / base,
                    fmt="pct",
                ),
                _kv(
                    f"Total cost at P{HEADLINE_P:.0f}",
                    None if p80 is None else base + p80,
                    fmt="currency",
                    currency=currency,
                ),
            ),
        )
    ]

    rows = []
    for point in view.contingency:
        total = _percentile(result.total_cost.percentiles, point.p)
        risk_only = _percentile(result.risk_cost.percentiles, point.p)
        rows.append(
            [
                text_cell(f"P{point.p:.0f}"),
                value_cell(total),
                value_cell(point.value, emphasis=abs(point.p - HEADLINE_P) < 1e-9),
                value_cell(risk_only),
            ]
        )
    blocks.append(
        _table(
            "Cost distribution",
            [
                Column(label="Percentile", width=12),
                Column(label="Total cost", align="right", format="currency", width=18),
                Column(label="Contingency", align="right", format="currency", width=18),
                Column(label="Risk cost only", align="right", format="currency", width=18),
            ],
            rows,
            note="Risk cost excludes the base and the burn-rate term. Total cost is the "
            "integrated figure the contingency is read off.",
        )
    )

    if view.additive_error_at_p80 is not None:
        integrated = view.integrated_p80_total
        additive = view.additive_p80_total
        error = view.additive_error_at_p80
        share = None if not integrated else error / integrated
        blocks.append(
            Callout(
                tone="method",
                title="What adding the percentiles would have cost",
                text=(
                    f"The integrated P80 total is "
                    f"{format_value(integrated, 'currency', currency)}. Percentiling the "
                    f"parts and adding them gives "
                    f"{format_value(additive, 'currency', currency)} — "
                    f"{format_value(error, 'currency', currency)} higher"
                    + (f" ({format_value(share, 'pct')})" if share is not None else "")
                    + ". The second figure is reported so the gap is visible, and is never "
                    "the number to use."
                ),
            )
        )

    blocks.append(
        KeyValues(
            caption="Where the spread comes from",
            items=(
                _kv(
                    "Owned by the risks' own cost draws",
                    view.cost_variance_share,
                    fmt="pct",
                ),
                _kv(
                    "Reaching the budget through delay",
                    view.schedule_variance_share,
                    note="The burn-rate term's exact share of total-cost variance. The "
                    "two shares sum to one.",
                    fmt="pct",
                ),
            ),
        )
    )

    if result.warnings:
        blocks.append(
            _table(
                "Engine warnings",
                [Column(label="Warning", width=110)],
                [[text_cell(w)] for w in result.warnings],
            )
        )
    return Section(id="cost", title="Cost contingency", blocks=tuple(blocks))


# ----------------------------------------------------------------------------- schedule


def _build_schedule(data: ReportData) -> Section:
    run = data.run
    assert run is not None and run.result is not None
    result = run.result
    delay = result.delay_days
    assert delay is not None
    finish = result.finish_day
    burn = result.schedule_driven_cost
    det = result.deterministic
    currency = data.currency

    blocks: list[AnyBlock] = [
        KeyValues(
            caption="Deterministic baseline",
            items=(
                _kv("Activities", det.activities, fmt="int"),
                _kv("Relationships", det.relationships, fmt="int"),
                _kv(
                    "Risk activities inserted",
                    det.inserted_activities,
                    note="Discrete risk events modelled as their own activities rather "
                    "than as duration factors.",
                    fmt="int",
                ),
                _kv("Deterministically critical activities", det.critical_activities,
                    fmt="int"),
                _kv(
                    "Baseline finish (engine forward pass)",
                    det.baseline_finish_day,
                    fmt="days",
                ),
            ),
        ),
        KeyValues(
            caption="Headline",
            items=(
                _kv("Mean delay", delay.mean, fmt="days"),
                _kv(f"Delay at P{HEADLINE_P:.0f}",
                    _percentile(delay.percentiles, HEADLINE_P), fmt="days"),
                _kv("Burn rate", run.burn_rate_per_day, fmt="currency", currency=currency),
                _kv(
                    f"Burn-rate cost at P{HEADLINE_P:.0f}",
                    None if burn is None else _percentile(burn.percentiles, HEADLINE_P),
                    note="Read off the burn-rate term's own distribution. It is not the "
                    "P80 delay multiplied by the burn rate, and the two differ.",
                    fmt="currency",
                    currency=currency,
                ),
            ),
        ),
    ]

    rows = []
    for point in delay.percentiles:
        rows.append(
            [
                text_cell(f"P{point.p:.0f}"),
                value_cell(point.value, emphasis=abs(point.p - HEADLINE_P) < 1e-9),
                value_cell(None if finish is None else _percentile(finish.percentiles,
                                                                  point.p)),
                value_cell(None if burn is None else _percentile(burn.percentiles, point.p)),
            ]
        )
    blocks.append(
        _table(
            "Schedule distribution",
            [
                Column(label="Percentile", width=12),
                Column(label="Delay", align="right", format="days", width=14),
                Column(label="Finish day", align="right", format="days", width=14),
                Column(label="Burn-rate cost", align="right", format="currency", width=18),
            ],
            rows,
            note="Delay is measured against the engine's own deterministic finish and is "
            "unclamped: negative percentiles are iterations that finished early.",
        )
    )
    blocks.append(
        KeyValues(
            caption="Spread",
            items=(
                _kv("Standard deviation of delay", delay.sd, fmt="days"),
                _kv("Shortest iteration", delay.minimum, fmt="days"),
                _kv("Longest iteration", delay.maximum, fmt="days"),
            ),
        )
    )
    return Section(id="schedule", title="Schedule outcome", blocks=tuple(blocks))


# -------------------------------------------------------------------------------- joint


def _build_joint(data: ReportData) -> Section:
    run = data.run
    assert run is not None and run.result is not None and run.result.joint is not None
    joint = run.result.joint
    currency = data.currency

    blocks: list[AnyBlock] = [
        Callout(
            tone="method",
            title="Meeting both targets is harder than meeting either",
            text=(
                f"Quoting the P{joint.marginal_pair_target:.0f} cost and the "
                f"P{joint.marginal_pair_target:.0f} date side by side implies a plan that "
                f"is {format_value(joint.joint_at_marginal_pair, 'pct')} likely to meet "
                "both. The joint frontier below is the set of pairs that actually carry "
                "the stated confidence."
            ),
        ),
        KeyValues(
            items=(
                _kv(
                    f"Marginal P{joint.marginal_pair_target:.0f} cost",
                    joint.marginal_cost,
                    fmt="currency",
                    currency=currency,
                ),
                _kv(
                    f"Marginal P{joint.marginal_pair_target:.0f} delay",
                    joint.marginal_delay_days,
                    fmt="days",
                ),
                _kv("Confidence in the pair", joint.joint_at_marginal_pair, fmt="pct"),
                _kv(
                    "Cost / delay rank correlation",
                    joint.cost_delay_correlation,
                    note="Part of this dependence is mechanical where a burn rate prices "
                    "delay into cost." if joint.burn_rate_coupled else None,
                    fmt="ratio",
                ),
            )
        ),
    ]

    rows = []
    for frontier in joint.frontiers:
        point = frontier.balanced
        if point is None:
            continue
        rows.append(
            [
                text_cell(f"Joint P{frontier.target:.0f}"),
                value_cell(point.total_cost),
                value_cell(point.delay_days),
                text_cell(f"P{point.cost_p:.0f}"),
                text_cell(f"P{point.delay_p:.0f}"),
            ]
        )
    blocks.append(
        _table(
            "Balanced points on the joint frontier",
            [
                Column(label="Joint confidence", width=18),
                Column(label="Total cost", align="right", format="currency", width=18),
                Column(label="Delay", align="right", format="days", width=14),
                Column(label="Cost is at", align="right", width=12),
                Column(label="Delay is at", align="right", width=12),
            ],
            rows,
            note="The balanced point is where cost and date carry equal marginal "
            "stringency. The last two columns are what that joint confidence costs on "
            "each axis on its own.",
            empty_text="Too few iterations to place a joint quantile.",
        )
    )
    return Section(id="joint", title="Joint cost-schedule confidence", blocks=tuple(blocks))


# ------------------------------------------------------------------------------ drivers


def _build_drivers(data: ReportData) -> Section:
    run = data.run
    assert run is not None and run.result is not None
    result = run.result
    currency = data.currency
    ranked = sorted(
        result.risk_sensitivity,
        key=lambda r: -abs(r.combined_variance_share),
    )[:15]

    rows = []
    for rank, item in enumerate(ranked, start=1):
        rows.append(
            [
                value_cell(rank),
                text_cell(item.code),
                text_cell(item.title),
                value_cell(item.combined_variance_share, emphasis=True),
                value_cell(item.cost_variance_share),
                value_cell(item.schedule_variance_share),
                value_cell(item.mean_contribution),
                value_cell(item.p80_contribution),
                value_cell(item.realised_frequency),
            ]
        )

    blocks: list[AnyBlock] = [
        _table(
            "Risks ranked by share of total-cost variance",
            [
                Column(label="#", align="right", format="int", width=5),
                Column(label="Risk", width=16),
                Column(label="Title", width=40),
                Column(label="Combined share", align="right", format="pct", width=14),
                Column(label="Cost share", align="right", format="pct", width=12),
                Column(label="Via delay", align="right", format="pct", width=12),
                Column(label="Mean contribution", align="right", format="currency",
                       width=18),
                Column(label="P80 contribution", align="right", format="currency",
                       width=18),
                Column(label="Occurred in", align="right", format="pct", width=12),
            ],
            rows,
            note="Ranked on variance share, not on rank correlation: the shares decompose "
            "the whole, so a bar can be read as 'this risk owns eleven percent of the "
            "spread'. Correlation bars add to nothing and rank a frequent trivial risk "
            "above a rare severe one.",
            empty_text="No risk carried enough variance to rank.",
        ),
        KeyValues(
            items=(
                _kv(
                    "Variance owned by the schedule",
                    result.schedule_variance_share,
                    note="The burn-rate term's own share, reported beside the risks "
                    "rather than folded into them: it is not a risk, it is what the "
                    "network did to the budget.",
                    fmt="pct",
                ),
                _kv(
                    "Risks ranked",
                    len(result.risk_sensitivity),
                    fmt="int",
                ),
            )
        ),
    ]
    if any(item.schedule_variance_share is not None for item in ranked):
        blocks.append(
            Callout(
                tone="method",
                title="The delay column is apportioned",
                text=(
                    "Delay is a maximum over network paths, so a risk's share of the "
                    "variance arriving through the burn-rate term has no exact additive "
                    "split. The term's total share is exact; its division between risks "
                    "is an approximation and should be read as a ranking, not a figure."
                ),
            )
        )
    _ = currency
    return Section(id="drivers", title="What drives the answer", blocks=tuple(blocks))


# -------------------------------------------------------------------------- criticality


def _build_criticality(data: ReportData) -> Section:
    run = data.run
    assert run is not None and run.result is not None
    ranked = sorted(
        run.result.activity_criticality,
        key=lambda a: (-a.criticality_index, -a.schedule_sensitivity_index),
    )[:20]

    rows = []
    for item in ranked:
        rows.append(
            [
                text_cell(item.code or item.activity_id),
                text_cell(item.name),
                value_cell(item.criticality_index, emphasis=True),
                value_cell(item.mean_total_float_days),
                value_cell(item.duration_sd_days),
                value_cell(item.duration_sensitivity),
                value_cell(item.cruciality),
                value_cell(item.schedule_sensitivity_index),
                text_cell("Risk event" if item.is_inserted else ""),
            ]
        )

    return Section(
        id="criticality",
        title="Critical activities",
        blocks=(
            _table(
                "Activities by criticality index",
                [
                    Column(label="Activity", width=18),
                    Column(label="Name", width=40),
                    Column(label="Criticality", align="right", format="pct", width=12),
                    Column(label="Mean float", align="right", format="days", width=12),
                    Column(label="Duration SD", align="right", format="days", width=12),
                    Column(label="Duration sens.", align="right", format="ratio", width=12),
                    Column(label="Cruciality", align="right", format="ratio", width=12),
                    Column(label="SSI", align="right", format="ratio", width=10),
                    Column(label="", width=12),
                ],
                rows,
                note="Criticality is the fraction of iterations the activity sat on the "
                "critical path. Cruciality is criticality times duration sensitivity; the "
                "schedule sensitivity index puts scale back in and is the figure a "
                "reviewer coming from Primavera Risk Analysis will ask for. The two "
                "disagree exactly where scale matters, and neither substitutes for the "
                "other. A blank duration sensitivity means the duration never varied.",
                empty_text="No schedule was simulated, so no activity criticality exists.",
            ),
        ),
    )


# --------------------------------------------------------------------------- mitigation


def _build_mitigation(data: ReportData) -> Section:
    blocks: list[AnyBlock] = []
    currency = data.currency
    plan = data.plan
    roi = data.roi

    if plan is not None:
        blocks.append(
            KeyValues(
                caption="Mitigation package",
                items=(
                    _kv("Plan", plan.name),
                    _kv("Status", plan.status.replace("_", " ").title()),
                    _kv("Actions", plan.action_count, fmt="int"),
                    _kv("Priced actions", plan.costed_count, fmt="int"),
                    _kv(
                        "Unpriced actions",
                        plan.unpriced_count,
                        note="Actions with neither a budget nor a duration. Treating "
                        "these as zero is the cost-side twin of dropping a risk from a "
                        "run." if plan.unpriced_count else None,
                        fmt="int",
                    ),
                    _kv("Package budget", plan.total_budget, fmt="currency",
                        currency=currency),
                    _kv("Package duration", plan.total_sched_days, fmt="days"),
                    _kv("Residual register written", plan.materialized_at),
                    _kv("Risks retired by the plan", plan.materialized_retired_count,
                        fmt="int"),
                ),
            )
        )

    if roi is not None and roi.issues:
        blocks.append(
            Callout(
                tone="warning",
                title="This comparison is no longer valid",
                text=" ".join(roi.issues),
            )
        )
    elif roi is not None:
        cont = roi.contingency
        blocks.append(
            Callout(
                tone="method",
                title="Effectiveness is measured by re-simulation",
                text=(
                    "The reduction below is the difference between two full runs — the "
                    "register before the package and the residual register after it — at "
                    f"P{roi.percentile:.0f}. It is not a residual score, and the package's "
                    "own cost sits beside the contingency rather than inside it."
                ),
            )
        )
        blocks.append(
            KeyValues(
                caption=f"At P{roi.percentile:.0f}",
                items=(
                    _kv(
                        "Contingency before",
                        None if cont is None else cont.at_percentile.before,
                        fmt="currency",
                        currency=currency,
                    ),
                    _kv(
                        "Contingency after",
                        None if cont is None else cont.at_percentile.after,
                        fmt="currency",
                        currency=currency,
                    ),
                    _kv(
                        "Reduction",
                        None if cont is None else cont.at_percentile.reduction,
                        note="Not distinguishable from Monte Carlo noise at this "
                        "iteration count."
                        if cont is not None and cont.within_noise
                        else None,
                        fmt="currency",
                        currency=currency,
                    ),
                    _kv("Package budget", roi.plan_budget, fmt="currency",
                        currency=currency),
                    _kv(
                        "Net at this percentile",
                        roi.net_at_percentile,
                        note="Reduction less the package budget.",
                        fmt="currency",
                        currency=currency,
                    ),
                    _kv("Benefit / cost", roi.benefit_cost_ratio, fmt="ratio"),
                    _kv("Risks retired", roi.retired_count, fmt="int"),
                ),
            )
        )
        if roi.delay_days is not None:
            blocks.append(
                KeyValues(
                    caption="Schedule effect",
                    items=(
                        _kv("Delay before", roi.delay_days.at_percentile.before,
                            fmt="days"),
                        _kv("Delay after", roi.delay_days.at_percentile.after, fmt="days"),
                        _kv("Reduction", roi.delay_days.at_percentile.reduction,
                            fmt="days"),
                    ),
                )
            )
        blocks.append(
            _table(
                "Where the reduction came from",
                [
                    Column(label="Risk", width=16),
                    Column(label="Title", width=40),
                    Column(label="Movement", width=14),
                    Column(label="Before", align="right", format="currency", width=16),
                    Column(label="After", align="right", format="currency", width=16),
                    Column(label="Reduction", align="right", format="currency", width=16),
                ],
                [
                    [
                        text_cell(m.code),
                        text_cell(m.title),
                        text_cell(m.movement.title()),
                        value_cell(m.contribution_before),
                        value_cell(m.contribution_after),
                        value_cell(m.contribution_reduction),
                    ]
                    for m in roi.risk_movers
                ],
                empty_text="No risk changed its contribution measurably.",
            )
        )
        if roi.basis:
            blocks.append(
                _table(
                    "Basis of this comparison",
                    [Column(label="Statement", width=110)],
                    [[text_cell(line)] for line in roi.basis],
                )
            )
        if roi.warnings:
            blocks.append(
                _table(
                    "Warnings",
                    [Column(label="Warning", width=110)],
                    [[text_cell(line)] for line in roi.warnings],
                )
            )

    return Section(id="mitigation", title="Mitigation and its effect", blocks=tuple(blocks))


# ------------------------------------------------------------------------------ actions


def _build_actions(data: ReportData) -> Section:
    rows = []
    for action in data.actions:
        rows.append(
            [
                text_cell(action.risk_code),
                text_cell(action.action),
                text_cell(action.owner),
                text_cell(None if action.due_date is None else action.due_date.isoformat()),
                value_cell(action.budget),
                value_cell(action.sched_days),
                value_cell(
                    None if action.completion_pct is None else action.completion_pct / 100
                ),
                text_cell(action.status.replace("_", " ").title()),
            ]
        )
    return Section(
        id="actions",
        title="Mitigation actions",
        blocks=(
            _table(
                None,
                [
                    Column(label="Risk", width=16),
                    Column(label="Action", width=50),
                    Column(label="Owner", width=18),
                    Column(label="Due", width=12),
                    Column(label="Budget", align="right", format="currency", width=16),
                    Column(label="Days", align="right", format="days", width=10),
                    Column(label="Complete", align="right", format="pct", width=10),
                    Column(label="Status", width=14),
                ],
                rows,
                empty_text="No mitigation action is recorded against this scope.",
            ),
        ),
    )


# ----------------------------------------------------------------------------- registry


def _needs_run(data: ReportData) -> str | None:
    if data.run is None:
        return "No simulation run was selected."
    if data.run.result is None:
        return "The selected run has no readable result."
    return None


def _needs_schedule(data: ReportData) -> str | None:
    reason = _needs_run(data)
    if reason:
        return reason
    assert data.run is not None and data.run.result is not None
    if data.run.result.delay_days is None:
        return "This run simulated cost only — no schedule was attached."
    return None


def _needs_risks(data: ReportData) -> str | None:
    return None if data.risks else "No risk is recorded against this scope."


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        id="cover",
        title="Cover",
        summary="Who this is for, what it covers, and what it was computed from.",
        build=_build_cover,
        unavailable=lambda _data: None,
    ),
    SectionSpec(
        id="basis",
        title="Basis of the analysis",
        summary="Seed, iterations, engine version, gate status, and every risk excluded.",
        build=_build_basis,
        unavailable=lambda _data: None,
    ),
    SectionSpec(
        id="method",
        title="Method and assumptions",
        summary="How the numbers were produced, and which of them are approximations.",
        build=_build_method,
        unavailable=lambda _data: None,
    ),
    SectionSpec(
        id="register",
        title="Risk register",
        summary="Register size, distribution by band and category, highest-scoring risks.",
        build=_build_register,
        unavailable=_needs_risks,
    ),
    SectionSpec(
        id="matrix",
        title="Risk matrix",
        summary="The qualitative matrix as placed against the active scoring config.",
        build=_build_matrix,
        unavailable=lambda data: "No matrix configuration is active."
        if data.matrix is None
        else _needs_risks(data),
    ),
    SectionSpec(
        id="cost",
        title="Cost contingency",
        summary="Base, percentiles, contingency, and the additive-percentile gap.",
        build=_build_cost,
        unavailable=_needs_run,
    ),
    SectionSpec(
        id="schedule",
        title="Schedule outcome",
        summary="Delay and finish distributions, and the burn-rate cost they carry.",
        build=_build_schedule,
        unavailable=_needs_schedule,
    ),
    SectionSpec(
        id="joint",
        title="Joint cost-schedule confidence",
        summary="What quoting a P80 cost and a P80 date together is actually worth.",
        build=_build_joint,
        unavailable=lambda data: _needs_run(data)
        or (
            None
            if data.run is not None
            and data.run.result is not None
            and data.run.result.joint is not None
            else "This run produced no joint frontier."
        ),
    ),
    SectionSpec(
        id="drivers",
        title="What drives the answer",
        summary="Risks ranked by their share of total-cost variance.",
        build=_build_drivers,
        unavailable=lambda data: _needs_run(data)
        or (
            None
            if data.run is not None
            and data.run.result is not None
            and data.run.result.risk_sensitivity
            else "No risk sensitivity was computed for this run."
        ),
    ),
    SectionSpec(
        id="criticality",
        title="Critical activities",
        summary="Criticality index, cruciality and schedule sensitivity per activity.",
        build=_build_criticality,
        unavailable=lambda data: _needs_schedule(data)
        or (
            None
            if data.run is not None
            and data.run.result is not None
            and data.run.result.activity_criticality
            else "No activity criticality was computed for this run."
        ),
    ),
    SectionSpec(
        id="mitigation",
        title="Mitigation and its effect",
        summary="The package, its cost, and the contingency it removed by re-simulation.",
        build=_build_mitigation,
        unavailable=lambda data: None
        if (data.plan is not None or data.roi is not None)
        else "No mitigation plan or ROI comparison was selected.",
    ),
    SectionSpec(
        id="actions",
        title="Mitigation actions",
        summary="The action register: owner, due date, budget and completion.",
        build=_build_actions,
        unavailable=lambda data: None
        if data.actions
        else "No mitigation action is recorded against this scope.",
    ),
)


def section_by_id(section_id: str) -> SectionSpec | None:
    for spec in SECTIONS:
        if spec.id == section_id:
            return spec
    return None


def available_ids(data: ReportData) -> tuple[str, ...]:
    return tuple(spec.id for spec in SECTIONS if spec.unavailable(data) is None)


def build_sections(
    data: ReportData, requested: Sequence[str] | None = None
) -> tuple[Section, ...]:
    """Build the requested sections, in registry order, skipping the unavailable.

    ``requested`` is a filter, never an ordering: a report whose sections come out in the
    order somebody happened to tick the boxes is not a document.
    """
    wanted = None if requested is None else {s.strip() for s in requested if s.strip()}
    out: list[Section] = []
    for spec in SECTIONS:
        if wanted is not None and spec.id not in wanted:
            continue
        if spec.unavailable(data) is not None:
            continue
        out.append(spec.build(data))
    return tuple(out)
