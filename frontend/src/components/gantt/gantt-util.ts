/**
 * Timeline arithmetic, row flattening and formatters for the Gantt.
 *
 * Dates arrive as naive ISO stamps — `2026-06-02T08:00:00`, no offset — because P6 and
 * MS Project dates are wall-clock times with no timezone behind them. `new Date(s)` on a
 * stamp without an offset parses as local time, which is the reading that matches what
 * the planner typed. Nothing here converts to UTC: doing so would shift every bar by the
 * viewer's offset and silently move a midnight milestone onto the previous day.
 */

import { NO_WBS_KEY } from "../../types";
import type { GanttBar, GanttPayload, GanttWbsRow } from "../../types";

export const DAY_MS = 86_400_000;
export const ROW_H = 26;
export const HEADER_H = 46;

export type Zoom = "day" | "week" | "month" | "fit";

const PX_PER_DAY: Record<Exclude<Zoom, "fit">, number> = {
  day: 22,
  week: 6,
  month: 1.7,
};

export function parseDate(iso: string | null): number | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime();
  return Number.isFinite(ms) ? ms : null;
}

export interface Tick {
  ms: number;
  label: string;
}

export interface Scale {
  t0: number;
  t1: number;
  pxPerDay: number;
  width: number;
  x(ms: number): number;
  /** Coarse tier: months, or years once a month is too narrow to label. */
  top: Tick[];
  /** Fine tier: weeks, or months when zoomed out. */
  bottom: Tick[];
  /** Where to draw a gridline. Same boundaries as the bottom tier. */
  gridlines: number[];
  bottomUnit: "week" | "month";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function monthStart(ms: number): number {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth(), 1).getTime();
}

function nextMonth(ms: number): number {
  const d = new Date(ms);
  return new Date(d.getFullYear(), d.getMonth() + 1, 1).getTime();
}

function mondayOnOrAfter(ms: number): number {
  const d = new Date(ms);
  d.setHours(0, 0, 0, 0);
  const shift = (8 - (d.getDay() || 7)) % 7;
  return d.getTime() + shift * DAY_MS;
}

/**
 * Build the horizontal scale.
 *
 * `extra` carries the dates that must stay reachable even when they sit outside the
 * activity span — the data date, the must-finish constraint, today. A must-finish line
 * that falls off the right edge is exactly the one worth seeing.
 */
export function buildScale(
  startIso: string | null,
  finishIso: string | null,
  extra: (string | null)[],
  zoom: Zoom,
  viewportWidth: number
): Scale | null {
  const candidates = [startIso, finishIso, ...extra]
    .map(parseDate)
    .filter((ms): ms is number => ms !== null);
  if (candidates.length === 0) return null;

  let t0 = monthStart(Math.min(...candidates));
  let t1 = nextMonth(Math.max(...candidates));
  // A single-day schedule would otherwise divide by zero on fit.
  if (t1 - t0 < 30 * DAY_MS) t1 = t0 + 30 * DAY_MS;

  const totalDays = (t1 - t0) / DAY_MS;
  const pxPerDay =
    zoom === "fit"
      ? Math.max(0.25, Math.min(22, (Math.max(320, viewportWidth) - 24) / totalDays))
      : PX_PER_DAY[zoom];

  const width = Math.max(240, totalDays * pxPerDay);
  const x = (ms: number) => ((ms - t0) / DAY_MS) * pxPerDay;

  const months: Tick[] = [];
  for (let cursor = t0; cursor < t1; cursor = nextMonth(cursor)) {
    const d = new Date(cursor);
    months.push({ ms: cursor, label: `${MONTHS[d.getMonth()]} ${String(d.getFullYear()).slice(2)}` });
  }

  const years: Tick[] = [];
  for (let cursor = t0; cursor < t1; cursor = nextMonth(cursor)) {
    const d = new Date(cursor);
    if (d.getMonth() === 0 || cursor === t0) {
      years.push({ ms: cursor, label: String(d.getFullYear()) });
    }
  }

  const weeks: Tick[] = [];
  for (let cursor = mondayOnOrAfter(t0); cursor < t1; cursor += 7 * DAY_MS) {
    weeks.push({ ms: cursor, label: String(new Date(cursor).getDate()) });
  }

  // Week labels need roughly 22px to stay legible; below that, months are the fine tier
  // and years the coarse one.
  const weeklyFits = pxPerDay * 7 >= 22;
  return {
    t0,
    t1,
    pxPerDay,
    width,
    x,
    top: weeklyFits ? months : years,
    bottom: weeklyFits ? weeks : months,
    gridlines: (weeklyFits ? weeks : months).map((t) => t.ms),
    bottomUnit: weeklyFits ? "week" : "month",
  };
}

/* ------------------------------------------------------------------------- *
 * rows
 * ------------------------------------------------------------------------- */

export interface WbsRowItem {
  kind: "wbs";
  key: string;
  node: GanttWbsRow;
  hasChildren: boolean;
  collapsed: boolean;
}

export interface BarRowItem {
  kind: "bar";
  key: string;
  bar: GanttBar;
  depth: number;
}

export type Row = WbsRowItem | BarRowItem;

/** `wbs_source_id -> activities`, falling back to the no-WBS bucket the server emits. */
export function groupByWbs(payload: GanttPayload): Map<string, GanttBar[]> {
  const known = new Set(payload.wbs.map((n) => n.source_id));
  const out = new Map<string, GanttBar[]>();
  for (const bar of payload.activities) {
    const key =
      bar.wbs_source_id && known.has(bar.wbs_source_id) ? bar.wbs_source_id : NO_WBS_KEY;
    const list = out.get(key);
    if (list) list.push(bar);
    else out.set(key, [bar]);
  }
  return out;
}

/**
 * Flatten the tree into what is on screen, honouring collapse.
 *
 * `payload.wbs` is already depth-first, so a node's descendants are the contiguous run
 * of rows with a greater depth — collapsing is a skip, not a tree walk.
 */
export function buildRows(payload: GanttPayload, collapsed: Set<string>): Row[] {
  const byWbs = groupByWbs(payload);
  const nodes = payload.wbs;
  const rows: Row[] = [];
  let skipBelowDepth: number | null = null;

  for (let i = 0; i < nodes.length; i += 1) {
    const node = nodes[i];
    if (skipBelowDepth !== null) {
      if (node.depth > skipBelowDepth) continue;
      skipBelowDepth = null;
    }

    const own = byWbs.get(node.source_id) ?? [];
    const hasChildNode = i + 1 < nodes.length && nodes[i + 1].depth > node.depth;
    const isCollapsed = collapsed.has(node.source_id);

    rows.push({
      kind: "wbs",
      key: `w:${node.source_id}`,
      node,
      hasChildren: hasChildNode || own.length > 0,
      collapsed: isCollapsed,
    });

    if (isCollapsed) {
      skipBelowDepth = node.depth;
      continue;
    }
    for (const bar of own) {
      rows.push({ kind: "bar", key: `a:${bar.source_id}`, bar, depth: node.depth + 1 });
    }
  }

  return rows;
}

/** The chain of WBS ids from a node up to the root, the node itself included. */
export function ancestorsOf(payload: GanttPayload, wbsSourceId: string | null): string[] {
  if (!wbsSourceId) return [NO_WBS_KEY];
  const byId = new Map(payload.wbs.map((n) => [n.source_id, n]));
  const chain: string[] = [];
  const seen = new Set<string>();
  let cursor: string | null = wbsSourceId;
  while (cursor && byId.has(cursor) && !seen.has(cursor)) {
    seen.add(cursor);
    chain.push(cursor);
    cursor = byId.get(cursor)?.parent_source_id ?? null;
  }
  return chain.length > 0 ? chain : [NO_WBS_KEY];
}

/* ------------------------------------------------------------------------- *
 * formatting
 * ------------------------------------------------------------------------- */

export function fmtDate(iso: string | null): string {
  const ms = parseDate(iso);
  if (ms === null) return "—";
  const d = new Date(ms);
  return `${String(d.getDate()).padStart(2, "0")} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}

export function fmtDays(value: number | null, calendarId?: string): string {
  if (value === null || value === undefined) return "—";
  const rounded = Math.round(value * 100) / 100;
  const days = `${rounded}d`;
  return calendarId ? `${days} on ${calendarId}` : days;
}

/** Minor currency units, as stored. No symbol: the source format does not carry one. */
export function fmtMoney(minorUnits: number | null): string {
  if (minorUnits === null || minorUnits === undefined) return "—";
  return (minorUnits / 100).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function fmtPct(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

export const BASIS_NOTE: Record<string, string> = {
  actual: "Actual start and finish — the work is done.",
  in_progress: "Actual start, forecast finish from the last CPM run.",
  planned: "Early dates from the last CPM run. Not started.",
  undated: "No usable dates in the export. Nothing to draw.",
};
