/**
 * The schedule as drawn, with risk landings overlaid.
 *
 * Why this view exists at all, given the register already lists everything: two questions
 * a table cannot answer. Whether the parse is right — a schedule whose bars sit in the
 * wrong year, or whose critical path is a scatter rather than a chain, has a parse or a
 * source problem no field-by-field read will surface. And where the risk cover actually
 * is: coverage percentages hide the case where the register is fully mapped and the
 * driving path has nothing pointing at it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ActivityDetail from "../components/gantt/ActivityDetail";
import GanttChart from "../components/gantt/GanttChart";
import { getActivityLandings, getGantt, getScheduleVersions } from "../api";
import { NO_WBS_KEY } from "../types";
import type {
  ActivityLandings,
  GanttPayload,
  GanttWbsRow,
  ScheduleVersionSummary,
} from "../types";
import type { Zoom } from "../components/gantt/gantt-util";
import { ancestorsOf, fmtDate } from "../components/gantt/gantt-util";
import "../gantt.css";

/** Rows below this expand on load; above it, only the top level does. */
const EXPAND_ALL_UNDER = 400;

const ZOOMS: { key: Zoom; label: string; title: string }[] = [
  { key: "fit", label: "Fit", title: "Fit the whole schedule in view" },
  { key: "month", label: "Month", title: "Month scale" },
  { key: "week", label: "Week", title: "Week scale" },
  { key: "day", label: "Day", title: "Day scale" },
];

export default function GanttView() {
  const [versions, setVersions] = useState<ScheduleVersionSummary[] | null>(null);
  const [versionId, setVersionId] = useState<number | null>(null);

  const [payload, setPayload] = useState<GanttPayload | null>(null);
  const [landings, setLandings] = useState<ActivityLandings | null>(null);
  const [landingsError, setLandingsError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [criticalOnly, setCriticalOnly] = useState(false);
  const [branch, setBranch] = useState<string | null>(null);
  /** The unfiltered tree, kept so filtering to a branch cannot strand the picker. */
  const [fullTree, setFullTree] = useState<GanttWbsRow[]>([]);

  const [zoom, setZoom] = useState<Zoom>("fit");
  const [showBaseline, setShowBaseline] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [jumpTo, setJumpTo] = useState<string | null>(null);
  const collapseInitialized = useRef<number | null>(null);

  useEffect(() => {
    getScheduleVersions()
      .then((rows) => {
        setVersions(rows);
        const current = rows.find((r) => r.is_current) ?? rows[0];
        if (current) setVersionId(current.id);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    if (versionId === null) return;
    let live = true;
    setLoading(true);
    setError(null);

    // Settled, not all: the chart is worth having without the risk badges, and one
    // rejected read must not blank the screen and blame the wrong thing.
    Promise.allSettled([
      getGantt(versionId, { q: q || null, criticalOnly, wbs: branch }),
      getActivityLandings(versionId),
    ])
      .then(([chart, overlay]) => {
        if (!live) return;
        if (chart.status === "fulfilled") {
          setPayload(chart.value);
          if (!branch) setFullTree(chart.value.wbs.filter((n) => n.source_id !== NO_WBS_KEY));
        } else {
          setPayload(null);
          setError(`Could not load the schedule: ${chart.reason}`);
        }
        if (overlay.status === "fulfilled") {
          setLandings(overlay.value);
          setLandingsError(null);
        } else {
          setLandings(null);
          setLandingsError(String(overlay.reason));
        }
      })
      .finally(() => live && setLoading(false));

    return () => {
      live = false;
    };
  }, [versionId, q, criticalOnly, branch]);

  // Collapse state belongs to the version, not to the filters — re-deriving it on every
  // keystroke would throw away whatever the analyst had opened.
  useEffect(() => {
    if (!payload || collapseInitialized.current === payload.version.id) return;
    collapseInitialized.current = payload.version.id;
    const rowCount = payload.wbs.length + payload.activities.length;
    setCollapsed(
      rowCount <= EXPAND_ALL_UNDER
        ? new Set()
        : new Set(payload.wbs.filter((n) => n.depth >= 1).map((n) => n.source_id))
    );
    setSelected(null);
  }, [payload]);

  const barsById = useMemo(
    () => new Map((payload?.activities ?? []).map((b) => [b.source_id, b])),
    [payload]
  );
  const wbsById = useMemo(
    () => new Map((payload?.wbs ?? []).map((n) => [n.source_id, n])),
    [payload]
  );

  const selectedBar = selected ? barsById.get(selected) ?? null : null;

  // A filter is a request to see what matched. Honouring collapse on top of it would show
  // branch headers with counts and no bars under them.
  const filtering = Boolean(q) || criticalOnly;
  const effectiveCollapsed = filtering ? new Set<string>() : collapsed;

  const onToggle = useCallback((sourceId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(sourceId)) next.delete(sourceId);
      else next.add(sourceId);
      return next;
    });
  }, []);

  const onJump = useCallback(
    (sourceId: string) => {
      if (!payload) return;
      const target = barsById.get(sourceId);
      if (!target) return;
      const chain = ancestorsOf(payload, target.wbs_source_id);
      setCollapsed((prev) => {
        const next = new Set(prev);
        chain.forEach((id) => next.delete(id));
        return next;
      });
      setSelected(sourceId);
      setJumpTo(sourceId);
    },
    [payload, barsById]
  );

  function clearFilters() {
    setSearch("");
    setQ("");
    setCriticalOnly(false);
    setBranch(null);
  }

  if (error && !payload) return <div className="error">{error}</div>;
  if (versions === null) return <div className="muted">Loading…</div>;

  if (versions.length === 0) {
    return (
      <div className="gt">
        <header className="topbar">
          <h1>Gantt</h1>
        </header>
        <div className="empty">
          No schedule imported yet. Import a <code>.xer</code> on the Schedule tab and it will
          appear here.
        </div>
      </div>
    );
  }

  const gate = payload?.gate;
  const landingMap = landings?.landings ?? {};

  return (
    <div className="gt">
      <header className="topbar">
        <h1>Gantt</h1>
        {payload && (
          <span className="muted gt-subtitle">
            {payload.version.project_name} · {payload.version.source_format}
            {!payload.version.is_current && " · superseded version"}
          </span>
        )}
      </header>

      <div className="toolbar gt-toolbar">
        <label className="gt-control">
          Version
          <select
            value={versionId ?? ""}
            onChange={(e) => {
              setVersionId(Number(e.target.value));
              setSelected(null);
            }}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                #{v.id} {v.project_name} — {fmtDate(v.created_at)}
                {v.is_current ? " (current)" : ""}
              </option>
            ))}
          </select>
        </label>

        <label className="gt-control">
          Branch
          <select value={branch ?? ""} onChange={(e) => setBranch(e.target.value || null)}>
            <option value="">Whole schedule</option>
            {fullTree.map((node) => (
              <option key={node.source_id} value={node.source_id}>
                {"\u00a0".repeat(node.depth * 2)}
                {node.code} {node.name}
              </option>
            ))}
          </select>
        </label>

        <label className="gt-control">
          Find
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="code or name"
          />
        </label>

        <label className="gt-check">
          <input
            type="checkbox"
            checked={criticalOnly}
            onChange={(e) => setCriticalOnly(e.target.checked)}
          />
          Critical only
        </label>

        <label className="gt-check">
          <input
            type="checkbox"
            checked={showBaseline}
            onChange={(e) => setShowBaseline(e.target.checked)}
          />
          Baseline
        </label>

        <div className="gt-zoom" role="group" aria-label="Timeline scale">
          {ZOOMS.map((z) => (
            <button
              key={z.key}
              type="button"
              className={`gt-zoom-btn${zoom === z.key ? " is-active" : ""}`}
              onClick={() => setZoom(z.key)}
              title={z.title}
              aria-pressed={zoom === z.key}
            >
              {z.label}
            </button>
          ))}
        </div>

        <div className="nav-spacer" />

        <button
          type="button"
          className="btn"
          onClick={() =>
            setCollapsed(
              collapsed.size > 0
                ? new Set()
                : new Set((payload?.wbs ?? []).filter((n) => n.depth >= 1).map((n) => n.source_id))
            )
          }
          disabled={!payload || filtering}
          title={filtering ? "Collapse is ignored while a filter is active" : undefined}
        >
          {collapsed.size > 0 ? "Expand all" : "Collapse all"}
        </button>
      </div>

      {gate && !gate.gate_passed && (
        <div className="gt-banner gt-banner--warn">
          <strong>DCMA gate failed</strong> — blocking check
          {gate.blocking_failures.length === 1 ? " " : "s "}
          {gate.blocking_failures.join(", ") || "none recorded"}. This schedule renders exactly
          as well as one that passed; it still cannot enter simulation. The full report is on
          the Schedule tab.
        </div>
      )}

      {payload && payload.version.warnings.length > 0 && (
        <div className="gt-banner">
          <strong>Parse warnings</strong> — {payload.version.warnings.slice(0, 3).join(" · ")}
          {payload.version.warnings.length > 3 &&
            ` (and ${payload.version.warnings.length - 3} more)`}
        </div>
      )}

      {payload?.truncated && (
        <div className="gt-banner">
          Showing the first {payload.returned.toLocaleString()} of{" "}
          {payload.total.toLocaleString()} activities. Branch totals above are for the whole
          schedule. Pick a branch to see the rest.
        </div>
      )}

      {landingsError && (
        <div className="gt-banner">
          <strong>Risk overlay unavailable</strong> — {landingsError}. The chart is complete;
          only the risk badges are missing.
        </div>
      )}

      {payload && (
        <div className="gt-stats">
          <span>
            <strong>{payload.counts.activities.toLocaleString()}</strong> activities
          </span>
          <span>
            <strong>{payload.counts.critical.toLocaleString()}</strong> critical
          </span>
          <span>
            <strong>{payload.counts.complete.toLocaleString()}</strong> complete ·{" "}
            {payload.counts.in_progress.toLocaleString()} in progress
          </span>
          {payload.counts.undated > 0 && (
            <span title="No usable dates in the export — these cannot be scheduled or simulated">
              <strong>{payload.counts.undated.toLocaleString()}</strong> undated
            </span>
          )}
          {landings && (
            <>
              <span>
                <strong>{landings.risks_landed}</strong> risks landed
              </span>
              <span>
                <strong>{landings.activities_touched}</strong> activities covered
              </span>
              {landings.scoped_drivers > 0 && (
                <span>
                  <strong>{landings.scoped_drivers}</strong> scoped drivers resolved
                </span>
              )}
            </>
          )}
          <span className="gt-legend">
            <i className="gt-key gt-key--critical" /> critical
            <i className="gt-key gt-key--done" /> complete
            <i className="gt-key gt-key--wip" /> in progress
            <i className="gt-key gt-key--risk" /> risks mapped
          </span>
        </div>
      )}

      {loading && !payload && <div className="muted">Loading schedule…</div>}

      {payload && payload.total === 0 ? (
        <div className="empty">
          {filtering || branch ? (
            <>
              No activity matches this filter.{" "}
              <button type="button" className="link" onClick={clearFilters}>
                Clear filters
              </button>
            </>
          ) : (
            "This version parsed with no activities."
          )}
        </div>
      ) : (
        payload && (
          <div className={`gt-workspace${selectedBar ? " has-detail" : ""}`}>
            <GanttChart
              payload={payload}
              landings={landingMap}
              collapsed={effectiveCollapsed}
              onToggle={onToggle}
              selected={selected}
              onSelect={setSelected}
              zoom={zoom}
              showBaseline={showBaseline}
              jumpTo={jumpTo}
              onJumped={() => setJumpTo(null)}
            />
            {selectedBar && versionId !== null && (
              <ActivityDetail
                versionId={versionId}
                bar={selectedBar}
                wbsPath={wbsById.get(selectedBar.wbs_source_id ?? "")?.path ?? ""}
                landing={landingMap[selectedBar.source_id]}
                landingsUnavailable={Boolean(landingsError)}
                barsById={barsById}
                onJump={onJump}
                onClose={() => setSelected(null)}
              />
            )}
          </div>
        )
      )}
    </div>
  );
}
