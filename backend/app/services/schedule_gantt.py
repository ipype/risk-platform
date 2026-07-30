"""The render payload for the schedule Gantt.

Built off :func:`app.services.schedule_ingest.hydrate` rather than off the ORM rows
directly. That costs a little more work per request and buys two things worth more than
the saving:

* The chart draws the same network the DCMA gate assessed and the simulation will read.
  A second, independent read path is a second place for the picture on screen to drift
  away from the numbers in the report.
* ``hydrate`` normalizes every datetime to naive on the way out. Schedule columns are
  ``DateTime(timezone=True)``, so rows that went through Postgres come back aware while
  freshly built objects stay naive; a min/max across a mixed set raises ``TypeError``.
  That exact bug took down every upload once already.

What this module deliberately does *not* do: know anything about risks. Risk landings are
the mapping subsystem's answer and arrive from ``GET /mappings/activity-landings``, which
can resolve a ``scoped_driver`` filter. Folding that in here would drag the mapping tables
into every schedule read and put scope semantics in two places.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule import ScheduleVersion
from app.schedule.model import Activity, Schedule, WbsNode
from app.services.schedule_ingest import hydrate, latest_gate

#: Hard ceiling on bars in one response. A real capital-project schedule runs to tens of
#: thousands of activities and nothing useful is read off 30,000 rows at once; the answer
#: is a WBS filter, not a bigger payload. The client sends this same number — keep the two
#: in step (``frontend/src/api.ts`` → ``GANTT_ROW_LIMIT``).
MAX_GANTT_ROWS = 5000
DEFAULT_GANTT_ROWS = 2000

#: Hard ceiling on dependency links in one response. Links are only useful where both
#: ends are drawn, so this sits above the realistic link-to-activity ratio at the row cap
#: rather than being a second filter the analyst has to reason about.
MAX_GANTT_LINKS = 10000

#: Bucket for activities whose WBS reference is missing or dangling. Shown last rather
#: than dropped: a dangling WBS id is a parse problem the analyst needs to see.
NO_WBS_KEY = "__no_wbs__"
NO_WBS_LABEL = "(no WBS)"

#: How a bar's start and finish were chosen. Sent per row so the analyst can see which
#: rule applied instead of inferring it from the colour.
DateBasis = Literal["actual", "in_progress", "planned", "undated"]


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #


class GanttBar(BaseModel):
    source_id: str
    code: str
    name: str
    type: str
    status: str
    wbs_source_id: str | None

    #: Resolved display dates. ``actual_*`` where the work happened, early dates where it
    #: has not, and the two mixed for work in progress — the same ``forecast_start`` /
    #: ``forecast_finish`` rule the rest of the platform uses.
    start: datetime | None
    finish: datetime | None
    basis: DateBasis

    baseline_start: datetime | None
    baseline_finish: datetime | None
    #: Forecast finish minus baseline finish in **calendar** days, positive for late.
    #: Deliberately not a working-day duration: there is no single calendar a slip across
    #: two activities could honestly be measured on, and a calendar-day delta needs no
    #: calendar to be read correctly.
    baseline_slip_calendar_days: float | None

    original_duration_days: float | None
    remaining_duration_days: float | None
    total_float_days: float | None
    #: The calendar every ``*_days`` value above was measured against.
    duration_calendar_id: str

    #: Share of original duration burned, derived from remaining vs original. Not a
    #: physical or cost percent complete — neither .xer nor .mpp carries one the parser
    #: keeps — so the name says what it actually is.
    duration_pct_complete: float | None

    is_critical: bool
    is_milestone: bool
    is_summary_row: bool
    has_hard_constraint: bool
    constraint_type: str
    budgeted_cost: int | None


class GanttLink(BaseModel):
    """One dependency, in the form the chart needs to draw an arrow.

    Only links whose *both* endpoints are in the returned bar list are sent. A link with
    one end filtered out, truncated away, or pointing at another project has nowhere to
    terminate, and an arrow into empty space reads as a schedule error rather than a
    display limit — the count in :class:`GanttLinkCounts` says how many were left out.

    Deliberately thinner than ``GET /schedules/{id}/relationships``: no lag calendar id,
    because the detail panel already fetches the full row for one activity and this list
    is sent for the whole visible schedule.
    """

    source_id: str
    predecessor_source_id: str
    successor_source_id: str
    #: ``FS`` / ``SS`` / ``FF`` / ``SF``. Decides which edge of each bar the arrow joins.
    type: str
    lag_days: float | None
    #: Both endpoints are critical. Not a claim that this link *is* driving — that needs
    #: a forward pass this platform does not run until P3 — but the chain the analyst is
    #: looking for is inside this subset.
    is_critical: bool


class GanttLinkCounts(BaseModel):
    """Totals for the links, so the chart can say what it is not showing."""

    #: Relationships stored against this version.
    total: int
    #: Both endpoints present in the returned bars, so drawable.
    drawable: int
    #: At least one endpoint outside the returned bars — filtered out, truncated away, or
    #: never in this project to begin with.
    dangling: int
    #: True when ``drawable`` exceeded ``MAX_GANTT_LINKS`` and the list was cut.
    truncated: bool


class GanttWbsRow(BaseModel):
    source_id: str
    code: str
    name: str
    parent_source_id: str | None
    depth: int
    path: str
    #: Rolled up from every activity in this node's subtree, and computed *before*
    #: truncation, so the tree keeps telling the truth about the schedule even when the
    #: bar list has been cut short.
    start: datetime | None
    finish: datetime | None
    activity_count: int
    critical_count: int


class GanttCounts(BaseModel):
    """Totals over the filtered set, before truncation.

    Computed here rather than derived on the client, which only ever sees the truncated
    bar list and would quietly report the wrong totals on any large schedule.
    """

    activities: int
    critical: int
    milestones: int
    #: Activities carrying no usable date. A parse-quality finding, not a rounding error:
    #: an activity with no dates cannot be scheduled, mapped to, or simulated.
    undated: int
    complete: int
    in_progress: int


class GanttWindow(BaseModel):
    #: Earliest start and latest finish across the filtered activities. Null on a
    #: schedule with no usable dates at all, which the client renders as an empty state
    #: rather than a one-day timeline.
    start: datetime | None
    finish: datetime | None
    data_date: datetime | None
    must_finish_by: datetime | None
    baseline_finish: datetime | None


class GanttGate(BaseModel):
    """The gate verdict, carried so the chart cannot be mistaken for a green light.

    A schedule that failed DCMA renders exactly as well as one that passed. Invariant 3
    keeps it out of simulation; this keeps it from *looking* fine on the way there.
    """

    run_id: int
    gate_passed: bool
    blocking_failures: list[int]
    run_at: datetime


class GanttVersion(BaseModel):
    id: int
    project_name: str
    source_project_id: str
    source_format: str
    parser_version: str
    is_current: bool
    activity_count: int
    relationship_count: int
    warnings: list[str]


class GanttPayload(BaseModel):
    version: GanttVersion
    window: GanttWindow
    counts: GanttCounts
    gate: GanttGate | None
    wbs: list[GanttWbsRow]
    #: In display order: WBS depth-first, then by start date within a node. The client
    #: keeps this order rather than re-deriving it.
    activities: list[GanttBar]
    #: Dependencies between the activities above, in stored order.
    links: list[GanttLink] = Field(default_factory=list)
    link_counts: GanttLinkCounts = Field(
        default_factory=lambda: GanttLinkCounts(
            total=0, drawable=0, dangling=0, truncated=False
        )
    )
    returned: int
    #: Activities matching the filter before the limit was applied.
    total: int
    truncated: bool
    limit: int
    filters: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# shaping
# --------------------------------------------------------------------------- #


def _resolve_dates(
    activity: Activity,
) -> tuple[datetime | None, datetime | None, DateBasis]:
    """Which pair of dates the bar is drawn between, and by which rule.

    A milestone legitimately carries one date, so a single-ended activity is drawn as a
    point rather than discarded. Nothing is invented: an activity with no dates comes
    back ``undated`` and the client shows that instead of parking a bar at the epoch.
    """
    start = activity.forecast_start
    finish = activity.forecast_finish

    if activity.actual_start and activity.actual_finish:
        basis: DateBasis = "actual"
    elif activity.actual_start:
        basis = "in_progress"
    else:
        basis = "planned"

    if start is None and finish is None:
        return None, None, "undated"
    # One end missing: draw it as a point on the end that exists.
    if start is None:
        start = finish
    if finish is None:
        finish = start
    if finish < start:
        # A finish before its start is a parse or source problem. Clamp rather than
        # render a negative-width bar, and leave the raw values visible in the detail
        # panel via the duration and float fields.
        finish = start
    return start, finish, basis


def _pct_complete(activity: Activity) -> float | None:
    if activity.is_complete:
        return 1.0
    remaining = (
        activity.remaining_duration.days if activity.remaining_duration else None
    )
    original = activity.original_duration.days if activity.original_duration else None
    if activity.actual_start is None:
        return 0.0
    if not original or original <= 0 or remaining is None:
        return None
    return max(0.0, min(1.0, 1.0 - (remaining / original)))


def _slip_days(
    finish: datetime | None, baseline_finish: datetime | None
) -> float | None:
    if finish is None or baseline_finish is None:
        return None
    return round((finish - baseline_finish).total_seconds() / 86400.0, 2)


def _bar(activity: Activity) -> GanttBar:
    start, finish, basis = _resolve_dates(activity)
    return GanttBar(
        source_id=activity.id,
        code=activity.code,
        name=activity.name,
        type=activity.type.value,
        status=activity.status.value,
        wbs_source_id=activity.wbs_id,
        start=start,
        finish=finish,
        basis=basis,
        baseline_start=activity.baseline_start,
        baseline_finish=activity.baseline_finish,
        baseline_slip_calendar_days=_slip_days(finish, activity.baseline_finish),
        original_duration_days=(
            activity.original_duration.days if activity.original_duration else None
        ),
        remaining_duration_days=(
            activity.remaining_duration.days if activity.remaining_duration else None
        ),
        total_float_days=activity.total_float.days if activity.total_float else None,
        duration_calendar_id=(
            activity.original_duration.calendar_id
            if activity.original_duration
            else activity.calendar_id
        ),
        duration_pct_complete=_pct_complete(activity),
        is_critical=activity.is_critical,
        is_milestone=activity.type.is_milestone,
        is_summary_row=not activity.type.is_real_work,
        has_hard_constraint=activity.has_hard_constraint,
        constraint_type=activity.constraint_type.value,
        budgeted_cost=activity.budgeted_cost,
    )


def _wbs_order(nodes: tuple[WbsNode, ...]) -> list[tuple[WbsNode, int]]:
    """Depth-first over the WBS, preserving import order among siblings.

    Import order is what the planner sees in P6, so re-sorting it by code would reorder
    a schedule the analyst already knows how to read. Orphans — nodes whose parent is
    missing from the export — are treated as roots so they stay reachable, and a cycle
    stops the walk instead of hanging it.
    """
    children: dict[str | None, list[WbsNode]] = defaultdict(list)
    known = {n.id for n in nodes}
    for node in nodes:
        parent = node.parent_id if node.parent_id in known else None
        children[parent].append(node)

    ordered: list[tuple[WbsNode, int]] = []
    seen: set[str] = set()

    def walk(parent: str | None, depth: int) -> None:
        for node in children.get(parent, []):
            if node.id in seen:
                continue
            seen.add(node.id)
            ordered.append((node, depth))
            walk(node.id, depth + 1)

    walk(None, 0)
    # Anything left is inside a cycle. Emit it flat rather than losing the activities
    # hanging off it.
    for node in nodes:
        if node.id not in seen:
            seen.add(node.id)
            ordered.append((node, 0))
    return ordered


def _paths(
    ordered: list[tuple[WbsNode, int]], nodes: tuple[WbsNode, ...]
) -> dict[str, str]:
    by_id = {n.id: n for n in nodes}
    cache: dict[str, str] = {}

    def path(node_id: str) -> str:
        if node_id in cache:
            return cache[node_id]
        parts: list[str] = []
        seen: set[str] = set()
        cursor: str | None = node_id
        while cursor and cursor in by_id and cursor not in seen:
            seen.add(cursor)
            node = by_id[cursor]
            if not node.is_project_node:
                parts.append(node.name or node.code or cursor)
            cursor = node.parent_id
        cache[node_id] = " > ".join(reversed(parts))
        return cache[node_id]

    return {node.id: path(node.id) for node, _ in ordered}


def _descendants(nodes: tuple[WbsNode, ...], root: str) -> set[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        if node.parent_id:
            children[node.parent_id].append(node.id)
    out: set[str] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        if current in out:
            continue
        out.add(current)
        stack.extend(children.get(current, []))
    return out


def _sort_key(bar: GanttBar) -> tuple:
    return (bar.start is None, bar.start or datetime.min, bar.code or "", bar.source_id)


def _links(
    schedule: Schedule, drawn: list[GanttBar]
) -> tuple[list[GanttLink], GanttLinkCounts]:
    """Dependencies between the bars that are actually on screen.

    Both endpoints must be in ``drawn``. Three separate things put a relationship outside
    that set and none of them is a data problem the chart should shout about: a WBS or
    critical-path filter, the row limit, and a link crossing into another project (which
    the parser already warns about on import). They are counted together as ``dangling``
    and left undrawn.
    """
    critical = {bar.source_id for bar in drawn if bar.is_critical}
    present = {bar.source_id for bar in drawn}

    links: list[GanttLink] = []
    drawable = 0
    for relationship in schedule.relationships:
        if (
            relationship.predecessor_id not in present
            or relationship.successor_id not in present
        ):
            continue
        drawable += 1
        if len(links) >= MAX_GANTT_LINKS:
            continue
        links.append(
            GanttLink(
                source_id=relationship.id,
                predecessor_source_id=relationship.predecessor_id,
                successor_source_id=relationship.successor_id,
                type=relationship.type.value,
                lag_days=relationship.lag.days if relationship.lag else None,
                is_critical=(
                    relationship.predecessor_id in critical
                    and relationship.successor_id in critical
                ),
            )
        )

    total = len(schedule.relationships)
    return links, GanttLinkCounts(
        total=total,
        drawable=drawable,
        dangling=total - drawable,
        truncated=drawable > len(links),
    )


def build_payload(
    schedule: Schedule,
    version: ScheduleVersion,
    gate: GanttGate | None,
    *,
    wbs: str | None = None,
    critical_only: bool = False,
    q: str | None = None,
    limit: int = DEFAULT_GANTT_ROWS,
) -> GanttPayload:
    """Order, filter and roll up. Pure: no session, no clock, no I/O."""
    limit = max(1, min(limit, MAX_GANTT_ROWS))
    in_subtree = _descendants(schedule.wbs, wbs) if wbs else None

    bars: list[GanttBar] = []
    needle = (q or "").strip().lower()
    for activity in schedule.activities:
        if in_subtree is not None and (activity.wbs_id or "") not in in_subtree:
            continue
        if critical_only and not activity.is_critical:
            continue
        if needle and needle not in f"{activity.code} {activity.name}".lower():
            continue
        bars.append(_bar(activity))

    # Bucket against the nodes that actually exist. An activity pointing at a WBS id the
    # export did not include would otherwise land in a bucket nothing ever reads and
    # vanish from the chart — a silent row count mismatch against the register. The bar
    # keeps its raw ``wbs_source_id`` so the bad reference stays visible.
    known_nodes = {node.id for node in schedule.wbs}
    by_wbs: dict[str, list[GanttBar]] = defaultdict(list)
    for bar in bars:
        key = bar.wbs_source_id if bar.wbs_source_id in known_nodes else NO_WBS_KEY
        by_wbs[key].append(bar)
    for group in by_wbs.values():
        group.sort(key=_sort_key)

    ordered_nodes = [
        (node, depth)
        for node, depth in _wbs_order(schedule.wbs)
        if in_subtree is None or node.id in in_subtree
    ]
    paths = _paths(ordered_nodes, schedule.wbs)

    # Roll up over the subtree, from the filtered set, before truncation. A node's dates
    # have to cover its children or the summary bar is narrower than the work under it.
    subtree_bars: dict[str, list[GanttBar]] = {}
    for node, _ in ordered_nodes:
        keys = _descendants(schedule.wbs, node.id)
        subtree_bars[node.id] = [b for key in keys for b in by_wbs.get(key, [])]

    wbs_rows: list[GanttWbsRow] = []
    for node, depth in ordered_nodes:
        group = subtree_bars.get(node.id, [])
        if not group and (critical_only or needle):
            # A filter emptied this branch; showing the header would imply matches in it.
            continue
        dated = [b for b in group if b.start is not None and b.finish is not None]
        wbs_rows.append(
            GanttWbsRow(
                source_id=node.id,
                code=node.code,
                name=node.name or node.code or node.id,
                parent_source_id=node.parent_id,
                depth=depth,
                path=paths.get(node.id, ""),
                start=min((b.start for b in dated), default=None),
                finish=max((b.finish for b in dated), default=None),
                activity_count=len(group),
                critical_count=sum(1 for b in group if b.is_critical),
            )
        )

    if by_wbs.get(NO_WBS_KEY):
        group = by_wbs[NO_WBS_KEY]
        dated = [b for b in group if b.start is not None and b.finish is not None]
        wbs_rows.append(
            GanttWbsRow(
                source_id=NO_WBS_KEY,
                code="",
                name=NO_WBS_LABEL,
                parent_source_id=None,
                depth=0,
                path=NO_WBS_LABEL,
                start=min((b.start for b in dated), default=None),
                finish=max((b.finish for b in dated), default=None),
                activity_count=len(group),
                critical_count=sum(1 for b in group if b.is_critical),
            )
        )

    display_order = [key for key, _ in ((n.id, d) for n, d in ordered_nodes)]
    display_order.append(NO_WBS_KEY)
    ordered_bars = [bar for key in display_order for bar in by_wbs.get(key, [])]

    total = len(ordered_bars)
    returned_bars = ordered_bars[:limit]
    links, link_counts = _links(schedule, returned_bars)

    dated_all = [
        b for b in ordered_bars if b.start is not None and b.finish is not None
    ]
    window = GanttWindow(
        start=min((b.start for b in dated_all), default=None),
        finish=max((b.finish for b in dated_all), default=None),
        data_date=schedule.data_date,
        must_finish_by=schedule.must_finish_by,
        baseline_finish=schedule.baseline_finish,
    )

    counts = GanttCounts(
        activities=total,
        critical=sum(1 for b in ordered_bars if b.is_critical),
        milestones=sum(1 for b in ordered_bars if b.is_milestone),
        undated=sum(1 for b in ordered_bars if b.basis == "undated"),
        complete=sum(1 for b in ordered_bars if b.basis == "actual"),
        in_progress=sum(1 for b in ordered_bars if b.basis == "in_progress"),
    )

    return GanttPayload(
        version=GanttVersion(
            id=version.id,
            project_name=version.project_name,
            source_project_id=version.source_project_id,
            source_format=version.source_format,
            parser_version=version.parser_version,
            is_current=version.is_current,
            activity_count=version.activity_count,
            relationship_count=version.relationship_count,
            warnings=list(version.warnings or []),
        ),
        window=window,
        counts=counts,
        gate=gate,
        wbs=wbs_rows,
        activities=returned_bars,
        links=links,
        link_counts=link_counts,
        returned=len(returned_bars),
        total=total,
        truncated=total > len(returned_bars),
        limit=limit,
        filters={"wbs": wbs, "critical_only": critical_only, "q": q or None},
    )


async def build_gantt(
    db: AsyncSession,
    version: ScheduleVersion,
    *,
    wbs: str | None = None,
    critical_only: bool = False,
    q: str | None = None,
    limit: int = DEFAULT_GANTT_ROWS,
) -> GanttPayload:
    schedule = await hydrate(db, version)
    run = await latest_gate(db, version.id)
    gate = (
        GanttGate(
            run_id=run.id,
            gate_passed=run.gate_passed,
            blocking_failures=list(run.blocking_failures or []),
            run_at=run.created_at,
        )
        if run is not None
        else None
    )
    return build_payload(
        schedule,
        version,
        gate,
        wbs=wbs,
        critical_only=critical_only,
        q=q,
        limit=limit,
    )
