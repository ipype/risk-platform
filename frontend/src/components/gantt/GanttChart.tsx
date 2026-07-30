/**
 * The chart. One scroll container, sticky header and sticky label column, so the two
 * panes cannot drift apart — scroll-sync between separate scrollers is the classic Gantt
 * bug and there is no reason to own it.
 *
 * Rows are fixed height and windowed: only what fits on screen is in the DOM, with
 * spacers standing in for the rest. A 12,000-activity schedule is ordinary on a capital
 * project and 12,000 rows of DOM is not.
 *
 * One tab stop per row, roving. Arrow keys move the selection; 5,000 focusable rows would
 * make Tab useless for reaching anything after the chart.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ActivityLanding, GanttBar, GanttPayload } from "../../types";
import type { Row, Zoom } from "./gantt-util";
import { HEADER_H, ROW_H, buildRows, buildScale, fmtDate, parseDate } from "./gantt-util";

const OVERSCAN = 8;
const MIN_BAR_PX = 3;

interface Props {
  payload: GanttPayload;
  landings: Record<string, ActivityLanding>;
  collapsed: Set<string>;
  onToggle: (wbsSourceId: string) => void;
  selected: string | null;
  onSelect: (sourceId: string) => void;
  zoom: Zoom;
  showBaseline: boolean;
  /** Set by the view to pull a specific activity into sight; cleared via `onJumped`. */
  jumpTo: string | null;
  onJumped: () => void;
}

function barClass(bar: GanttBar, isSelected: boolean): string {
  const parts = ["gt-bar"];
  if (bar.is_critical) parts.push("gt-bar--critical");
  if (bar.basis === "actual") parts.push("gt-bar--done");
  if (bar.basis === "in_progress") parts.push("gt-bar--wip");
  if (bar.is_summary_row) parts.push("gt-bar--loe");
  if (bar.has_hard_constraint) parts.push("gt-bar--constrained");
  if (isSelected) parts.push("is-selected");
  return parts.join(" ");
}

function riskLabel(landing: ActivityLanding | undefined): string {
  if (!landing) return "no risks mapped";
  const bits: string[] = [];
  if (landing.accepted) bits.push(`${landing.accepted} accepted`);
  if (landing.proposed) bits.push(`${landing.proposed} proposed`);
  return bits.length ? `risks: ${bits.join(", ")}` : "no risks mapped";
}

function barAria(bar: GanttBar, landing: ActivityLanding | undefined): string {
  const dates =
    bar.basis === "undated"
      ? "no dates"
      : `${fmtDate(bar.start)} to ${fmtDate(bar.finish)}`;
  const float =
    bar.total_float_days === null ? "float unknown" : `${bar.total_float_days} days float`;
  return [
    bar.code,
    bar.name,
    dates,
    bar.is_critical ? "critical" : float,
    riskLabel(landing),
  ].join(", ");
}

export default function GanttChart({
  payload,
  landings,
  collapsed,
  onToggle,
  selected,
  onSelect,
  zoom,
  showBaseline,
  jumpTo,
  onJumped,
}: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState({ w: 900, h: 520 });
  const focusWanted = useRef(false);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    const measure = () => setViewport({ w: node.clientWidth, h: node.clientHeight });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const rows = useMemo(() => buildRows(payload, collapsed), [payload, collapsed]);

  // Narrow screens get a narrower label column rather than a chart with no timeline in
  // view. Held in JS because the guide overlay needs the same number.
  const labelW = Math.round(Math.max(150, Math.min(300, viewport.w * 0.42)));

  const scale = useMemo(
    () =>
      buildScale(
        payload.window.start,
        payload.window.finish,
        [
          payload.window.data_date,
          payload.window.must_finish_by,
          payload.window.baseline_finish,
        ],
        zoom,
        viewport.w - labelW
      ),
    [payload.window, zoom, viewport.w, labelW]
  );

  const totalHeight = rows.length * ROW_H;
  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const last = Math.min(
    rows.length,
    Math.ceil((scrollTop + viewport.h) / ROW_H) + OVERSCAN
  );
  const visible = rows.slice(first, last);

  const indexOf = useCallback(
    (sourceId: string | null) =>
      sourceId === null
        ? -1
        : rows.findIndex((r) => r.kind === "bar" && r.bar.source_id === sourceId),
    [rows]
  );

  const scrollToIndex = useCallback(
    (index: number, alsoHorizontal: Row | null) => {
      const node = scrollRef.current;
      if (!node || index < 0) return;
      const target = HEADER_H + index * ROW_H;
      const top = node.scrollTop;
      const bottom = top + node.clientHeight;
      if (target < top + HEADER_H || target + ROW_H > bottom) {
        node.scrollTop = Math.max(0, target - node.clientHeight / 2);
      }
      if (alsoHorizontal && alsoHorizontal.kind === "bar" && scale) {
        const ms = parseDate(alsoHorizontal.bar.start);
        if (ms !== null) {
          node.scrollLeft = Math.max(0, scale.x(ms) - (node.clientWidth - labelW) / 3);
        }
      }
    },
    [scale, labelW]
  );

  // A jump arrives from the detail panel's predecessor and successor lists. The view has
  // already expanded any collapsed ancestors, so by now the row exists.
  useEffect(() => {
    if (!jumpTo) return;
    const index = indexOf(jumpTo);
    if (index >= 0) {
      focusWanted.current = true;
      scrollToIndex(index, rows[index]);
    }
    onJumped();
  }, [jumpTo, indexOf, rows, scrollToIndex, onJumped]);

  // Held rather than cleared on the first attempt: the selected row may not be inside the
  // window yet, because the imperative scroll and the state update land in different
  // commits. Clearing early loses focus and the keyboard user with it.
  useLayoutEffect(() => {
    if (!focusWanted.current) return;
    const node = scrollRef.current?.querySelector<HTMLButtonElement>(".gt-label.is-selected");
    if (!node) return;
    focusWanted.current = false;
    node.focus({ preventScroll: true });
  }, [selected, first, last]);

  function move(delta: number) {
    const bars = rows.filter((r): r is Extract<Row, { kind: "bar" }> => r.kind === "bar");
    if (bars.length === 0) return;
    const current = bars.findIndex((r) => r.bar.source_id === selected);
    const next = current < 0 ? 0 : Math.min(bars.length - 1, Math.max(0, current + delta));
    const bar = bars[next].bar;
    focusWanted.current = true;
    onSelect(bar.source_id);
    scrollToIndex(indexOf(bar.source_id), null);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "PageDown") {
      event.preventDefault();
      move(Math.floor(viewport.h / ROW_H) || 1);
    } else if (event.key === "PageUp") {
      event.preventDefault();
      move(-(Math.floor(viewport.h / ROW_H) || 1));
    }
  }

  if (!scale) {
    return (
      <div className="gt-empty">
        Nothing to draw: no activity in this view carries a usable date. The rows are still
        listed in the register and on the Schedule tab.
      </div>
    );
  }

  const today = Date.now();
  const guides = [
    { key: "data", ms: parseDate(payload.window.data_date), label: "Data date" },
    { key: "today", ms: today >= scale.t0 && today <= scale.t1 ? today : null, label: "Today" },
    { key: "mfb", ms: parseDate(payload.window.must_finish_by), label: "Must finish" },
  ].filter((g): g is { key: string; ms: number; label: string } => g.ms !== null);

  return (
    <div
      className="gt-chart"
      ref={scrollRef}
      onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
      onKeyDown={onKeyDown}
      style={
        {
          ["--gt-label-w" as string]: `${labelW}px`,
          ["--gt-row-h" as string]: `${ROW_H}px`,
        } as React.CSSProperties
      }
    >
      <div
        className="gt-content"
        style={{ width: labelW + scale.width, height: HEADER_H + totalHeight }}
      >
        <div className="gt-head" style={{ height: HEADER_H }}>
          <div className="gt-head-corner">
            <span className="gt-head-count">
              {payload.returned.toLocaleString()} of {payload.total.toLocaleString()}
            </span>
          </div>
          <div className="gt-head-scale" style={{ width: scale.width }}>
            {scale.top.map((tick, i) => {
              const next = scale.top[i + 1]?.ms ?? scale.t1;
              return (
                <div
                  key={tick.ms}
                  className="gt-tick gt-tick--top"
                  style={{ left: scale.x(tick.ms), width: scale.x(next) - scale.x(tick.ms) }}
                >
                  <span>{tick.label}</span>
                </div>
              );
            })}
            {scale.bottom.map((tick, i) => {
              const next = scale.bottom[i + 1]?.ms ?? scale.t1;
              return (
                <div
                  key={tick.ms}
                  className="gt-tick gt-tick--bottom"
                  style={{ left: scale.x(tick.ms), width: scale.x(next) - scale.x(tick.ms) }}
                >
                  <span>{tick.label}</span>
                </div>
              );
            })}
          </div>
        </div>

        <div
          className="gt-guides"
          style={{ left: labelW, top: HEADER_H, height: totalHeight, width: scale.width }}
          aria-hidden="true"
        >
          {scale.gridlines.map((ms) => (
            <div key={ms} className="gt-gridline" style={{ left: scale.x(ms) }} />
          ))}
          {guides.map((guide) => (
            <div
              key={guide.key}
              className={`gt-guide gt-guide--${guide.key}`}
              style={{ left: scale.x(guide.ms) }}
            >
              <span className="gt-guide-label">{guide.label}</span>
            </div>
          ))}
        </div>

        <div className="gt-rows" role="rowgroup">
          <div style={{ height: first * ROW_H }} />
          {visible.map((row) => {
            if (row.kind === "wbs") {
              const { node } = row;
              const start = parseDate(node.start);
              const finish = parseDate(node.finish);
              return (
                <div className="gt-row gt-row--wbs" role="row" key={row.key}>
                  <button
                    type="button"
                    className="gt-label gt-label--wbs"
                    style={{ paddingLeft: 8 + node.depth * 14 }}
                    onClick={() => row.hasChildren && onToggle(node.source_id)}
                    aria-expanded={row.hasChildren ? !row.collapsed : undefined}
                    tabIndex={-1}
                    title={node.path || node.name}
                  >
                    {row.hasChildren ? (
                      <span className="gt-caret">{row.collapsed ? "▸" : "▾"}</span>
                    ) : (
                      <span className="gt-caret gt-caret--empty" />
                    )}
                    <span className="gt-wbs-code">{node.code}</span>
                    <span className="gt-wbs-name">{node.name}</span>
                    <span className="gt-wbs-count">
                      {node.activity_count}
                      {node.critical_count > 0 ? ` · ${node.critical_count} cr` : ""}
                    </span>
                  </button>
                  <div className="gt-track">
                    {start !== null && finish !== null && (
                      <div
                        className="gt-summary"
                        style={{
                          left: scale.x(start),
                          width: Math.max(MIN_BAR_PX, scale.x(finish) - scale.x(start)),
                        }}
                      />
                    )}
                  </div>
                </div>
              );
            }

            const { bar } = row;
            const isSelected = selected === bar.source_id;
            const landing = landings[bar.source_id];
            const start = parseDate(bar.start);
            const finish = parseDate(bar.finish);
            const left = start === null ? 0 : scale.x(start);
            const width =
              start === null || finish === null
                ? 0
                : Math.max(MIN_BAR_PX, scale.x(finish) - scale.x(start));

            return (
              <div
                className={`gt-row${isSelected ? " is-selected" : ""}`}
                role="row"
                key={row.key}
              >
                <button
                  type="button"
                  className={`gt-label${isSelected ? " is-selected" : ""}`}
                  style={{ paddingLeft: 8 + row.depth * 14 }}
                  onClick={() => onSelect(bar.source_id)}
                  tabIndex={isSelected ? 0 : -1}
                  aria-label={barAria(bar, landing)}
                  aria-current={isSelected || undefined}
                  title={`${bar.code} ${bar.name}`}
                >
                  <span className="gt-code">{bar.code}</span>
                  <span className="gt-name">{bar.name}</span>
                  {landing && landing.accepted > 0 && (
                    <span className="gt-risk gt-risk--accepted">{landing.accepted}</span>
                  )}
                  {landing && landing.proposed > 0 && (
                    <span className="gt-risk gt-risk--proposed">{landing.proposed}</span>
                  )}
                </button>
                <div className="gt-track" onClick={() => onSelect(bar.source_id)}>
                  {showBaseline &&
                    bar.baseline_start &&
                    bar.baseline_finish &&
                    (() => {
                      const bs = parseDate(bar.baseline_start);
                      const bf = parseDate(bar.baseline_finish);
                      if (bs === null || bf === null) return null;
                      return (
                        <div
                          className="gt-baseline"
                          style={{
                            left: scale.x(bs),
                            width: Math.max(MIN_BAR_PX, scale.x(bf) - scale.x(bs)),
                          }}
                        />
                      );
                    })()}

                  {bar.basis === "undated" ? (
                    <span className="gt-nodates">no dates</span>
                  ) : bar.is_milestone ? (
                    <div
                      className={`gt-milestone${bar.is_critical ? " is-critical" : ""}${
                        isSelected ? " is-selected" : ""
                      }`}
                      style={{ left: left }}
                    />
                  ) : (
                    <div className={barClass(bar, isSelected)} style={{ left, width }}>
                      {bar.duration_pct_complete !== null &&
                        bar.duration_pct_complete > 0 && (
                          <div
                            className="gt-progress"
                            style={{ width: `${bar.duration_pct_complete * 100}%` }}
                          />
                        )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          <div style={{ height: Math.max(0, (rows.length - last) * ROW_H) }} />
        </div>
      </div>
    </div>
  );
}
