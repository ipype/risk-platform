import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ExposurePanel from "../components/mapping/ExposurePanel";
import MappingRow from "../components/mapping/MappingRow";
import SuggestionCard from "../components/mapping/SuggestionCard";
import {
  bulkAcceptMappings,
  carryMappingsForward,
  createMapping,
  deleteMapping,
  getCoverage,
  getMappings,
  getRisks,
  getScheduleActivities,
  getScheduleImpactArea,
  getScheduleVersions,
  getSuggestions,
  rejectSuggestion,
  updateMapping,
} from "../api";
import type {
  CoverageReport,
  Mapping,
  MappingCandidate,
  Risk,
  ScheduleActivity,
  ScheduleVersionSummary,
  SuggestionResponse,
} from "../types";
import "../mapping.css";

type QueueFilter = "unmapped" | "proposed" | "all";

/** Whether a keystroke landed in a field the user is typing into. */
function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

export default function MappingView() {
  const [versions, setVersions] = useState<ScheduleVersionSummary[]>([]);
  const [versionId, setVersionId] = useState<number | null>(null);
  const [risks, setRisks] = useState<Risk[]>([]);
  const [scheduleArea, setScheduleArea] = useState<string | null>(null);
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [coverage, setCoverage] = useState<CoverageReport | null>(null);

  const [selectedRiskId, setSelectedRiskId] = useState<number | null>(null);
  const [suggestions, setSuggestions] = useState<SuggestionResponse | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [focusIndex, setFocusIndex] = useState(0);

  const [filter, setFilter] = useState<QueueFilter>("unmapped");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<ScheduleActivity[]>([]);

  const [exposureOpen, setExposureOpen] = useState(false);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const listRef = useRef<HTMLDivElement>(null);
  const exposureTriggerRef = useRef<HTMLButtonElement>(null);

/**
 * `GET /risks` declares `Query(default=100, ge=1, le=500)`. Asking for more is not a
 * generous request that gets clamped — it is a 422 every single time.
 */
const RISK_FETCH_LIMIT = 500;

function messageOf(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason);
}

  const fail = useCallback((e: unknown) => {
    setError(e instanceof Error ? e.message : String(e));
  }, []);

  /* ------------------------------------------------------------------ load */

  /**
   * `allSettled`, not `all`. These three reads are independent, and under `Promise.all` a
   * failure in any one of them rejected the whole batch: a 422 from `/risks` left
   * `versions` empty, and the view then announced "No schedule imported yet" — false, and
   * it points the reader at the wrong subsystem entirely. A view that cannot load its data
   * has to say which part failed.
   */
  useEffect(() => {
    let live = true;
    Promise.allSettled([
      getScheduleVersions(),
      getRisks({ limit: RISK_FETCH_LIMIT }),
      getScheduleImpactArea(),
    ])
      .then(([versionsResult, risksResult, areaResult]) => {
        if (!live) return;
        const problems: string[] = [];

        if (versionsResult.status === "fulfilled") {
          const v = versionsResult.value;
          setVersions(v);
          // Default to the current parse; that is what the analyst almost always wants.
          setVersionId(v.find((x) => x.is_current)?.id ?? v[0]?.id ?? null);
        } else {
          problems.push(`the schedule list (${messageOf(versionsResult.reason)})`);
        }

        if (risksResult.status === "fulfilled") {
          setRisks(risksResult.value);
        } else {
          problems.push(`the risk register (${messageOf(risksResult.reason)})`);
        }

        if (areaResult.status === "fulfilled") {
          setScheduleArea(areaResult.value.schedule_impact_area);
        } else {
          problems.push(`the schedule impact area (${messageOf(areaResult.reason)})`);
        }

        if (problems.length > 0) setError(`Could not load ${problems.join("; ")}.`);
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, []);

  const refreshVersionState = useCallback(
    async (vid: number) => {
      const [m, c] = await Promise.all([getMappings(vid), getCoverage(vid)]);
      setMappings(m.items);
      setCoverage(c);
    },
    []
  );

  useEffect(() => {
    if (versionId == null) return;
    let live = true;
    refreshVersionState(versionId).catch((e) => live && fail(e));
    return () => {
      live = false;
    };
  }, [versionId, refreshVersionState, fail]);

  const loadSuggestions = useCallback(
    async (vid: number, riskId: number) => {
      const s = await getSuggestions(vid, riskId, { limit: 20 });
      setSuggestions(s);
      setFocusIndex(0);
    },
    []
  );

  useEffect(() => {
    if (versionId == null || selectedRiskId == null) {
      setSuggestions(null);
      return;
    }
    let live = true;
    setSuggestions(null);
    setDismissed(new Set());
    loadSuggestions(versionId, selectedRiskId).catch((e) => live && fail(e));
    return () => {
      live = false;
    };
  }, [versionId, selectedRiskId, loadSuggestions, fail]);

  /* --------------------------------------------------- derived: risk queue */

  const mappingsByRisk = useMemo(() => {
    const out = new Map<number, { accepted: number; proposed: number }>();
    for (const m of mappings) {
      const e = out.get(m.risk_id) ?? { accepted: 0, proposed: 0 };
      if (m.status === "accepted") e.accepted += 1;
      else if (m.status === "proposed") e.proposed += 1;
      out.set(m.risk_id, e);
    }
    return out;
  }, [mappings]);

  const scheduleImpact = useCallback(
    (r: Risk): number => (scheduleArea ? (r.impact_scores?.[scheduleArea] ?? 0) : 0),
    [scheduleArea]
  );

  const queue = useMemo(() => {
    // Only risks that can actually move the schedule. Without a configured schedule
    // area, fall back to open risks rather than showing an empty queue.
    const inScope = risks.filter((r) =>
      scheduleArea ? scheduleImpact(r) > 0 : (r.status ?? "").toLowerCase() === "open"
    );
    const filtered = inScope.filter((r) => {
      const c = mappingsByRisk.get(r.id);
      if (filter === "all") return true;
      if (filter === "unmapped") return !c || c.accepted === 0;
      return (c?.proposed ?? 0) > 0;
    });
    return filtered.sort(
      (a, b) =>
        scheduleImpact(b) - scheduleImpact(a) || a.risk_code.localeCompare(b.risk_code)
    );
  }, [risks, filter, mappingsByRisk, scheduleArea, scheduleImpact]);

  const riskMappings = useMemo(
    () => mappings.filter((m) => m.risk_id === selectedRiskId),
    [mappings, selectedRiskId]
  );

  const visibleCandidates = useMemo(
    () => (suggestions?.candidates ?? []).filter((c) => !dismissed.has(c.activity_source_id)),
    [suggestions, dismissed]
  );

  const selectedRisk = risks.find((r) => r.id === selectedRiskId) ?? null;

  /* ------------------------------------------------------------- mutations */

  const after = useCallback(async () => {
    if (versionId == null) return;
    await refreshVersionState(versionId);
    if (selectedRiskId != null) await loadSuggestions(versionId, selectedRiskId);
  }, [versionId, selectedRiskId, refreshVersionState, loadSuggestions]);

  const accept = useCallback(
    async (c: MappingCandidate) => {
      if (versionId == null || selectedRiskId == null) return;
      setBusy(true);
      setError(null);
      try {
        await createMapping({
          risk_id: selectedRiskId,
          version_id: versionId,
          mapping_type: "duration_driver",
          activity_source_id: c.activity_source_id,
          origin: "suggested",
          suggestion_score: c.score,
          suggestion_signals: c.signals,
          accept: true,
        });
        await after();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(false);
      }
    },
    [versionId, selectedRiskId, after, fail]
  );

  const dismiss = useCallback(
    async (c: MappingCandidate) => {
      if (versionId == null || selectedRiskId == null) return;
      // Optimistic: the card goes immediately, the training write follows. A failed
      // write is worth a message but not worth putting the card back and losing the
      // analyst's place in the list.
      setDismissed((prev) => new Set(prev).add(c.activity_source_id));
      try {
        await rejectSuggestion(versionId, selectedRiskId, c.activity_source_id, c.score);
      } catch (e) {
        fail(e);
      }
    },
    [versionId, selectedRiskId, fail]
  );

  const acceptAllStrong = useCallback(async () => {
    if (versionId == null || selectedRiskId == null) return;
    const items = visibleCandidates
      .filter((c) => c.confidence === "strong" && !c.warnings.some((w) => w.startsWith("error:")))
      .map((c) => ({
        activity_source_id: c.activity_source_id,
        suggestion_score: c.score,
        suggestion_signals: c.signals,
      }));
    if (items.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await bulkAcceptMappings(versionId, selectedRiskId, items);
      setNotice(
        `Accepted ${res.created_count}` +
          (res.refused.length ? `, refused ${res.refused.length}` : "")
      );
      await after();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }, [versionId, selectedRiskId, visibleCandidates, after, fail]);

  const acceptScope = useCallback(async () => {
    const scope = suggestions?.scope_suggestion;
    if (!scope || versionId == null || selectedRiskId == null) return;
    setBusy(true);
    setError(null);
    try {
      await createMapping({
        risk_id: selectedRiskId,
        version_id: versionId,
        mapping_type: "scoped_driver",
        scope: { field: "wbs", op: "equals", value: scope.value },
        origin: "suggested",
        rationale: `Scoped to ${scope.label}`,
      });
      await after();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }, [suggestions, versionId, selectedRiskId, after, fail]);

  const addManual = useCallback(
    async (a: ScheduleActivity) => {
      if (versionId == null || selectedRiskId == null) return;
      setBusy(true);
      setError(null);
      try {
        await createMapping({
          risk_id: selectedRiskId,
          version_id: versionId,
          mapping_type: "duration_driver",
          activity_source_id: a.source_id,
          origin: "manual",
        });
        await after();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(false);
      }
    },
    [versionId, selectedRiskId, after, fail]
  );

  /**
   * Activity-first mapping: pick the exposed activity, then the risk that explains it.
   * The workbench only walks risk-to-activity, which cannot answer "what is driving the
   * path and who owns it" — the question a review actually opens with.
   */
  const attachRiskToActivity = useCallback(
    async (activitySourceId: string, riskId: number): Promise<boolean> => {
      if (versionId == null) return false;
      setBusy(true);
      setError(null);
      try {
        await createMapping({
          risk_id: riskId,
          version_id: versionId,
          mapping_type: "duration_driver",
          activity_source_id: activitySourceId,
          origin: "manual",
          accept: true,
        });
        await refreshVersionState(versionId);
        if (selectedRiskId === riskId) await loadSuggestions(versionId, riskId);
        setNotice("Mapping accepted.");
        return true;
      } catch (e) {
        fail(e);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [versionId, selectedRiskId, refreshVersionState, loadSuggestions, fail]
  );

  const patch = useCallback(
    async (id: number, body: Partial<Mapping>) => {
      setBusy(true);
      setError(null);
      try {
        await updateMapping(id, body);
        await after();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(false);
      }
    },
    [after, fail]
  );

  const remove = useCallback(
    async (id: number) => {
      setBusy(true);
      setError(null);
      try {
        await deleteMapping(id);
        await after();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(false);
      }
    },
    [after, fail]
  );

  const carryForward = useCallback(
    async (fromId: number) => {
      if (versionId == null) return;
      setBusy(true);
      setError(null);
      try {
        const res = await carryMappingsForward(fromId, versionId);
        setNotice(
          `Carried ${res.carried} mapping${res.carried === 1 ? "" : "s"} forward as proposals` +
            (res.dropped_count ? `; ${res.dropped_count} dropped (activity gone)` : "")
        );
        await after();
      } catch (e) {
        fail(e);
      } finally {
        setBusy(false);
      }
    },
    [versionId, after, fail]
  );

  /* -------------------------------------------------------- activity search */

  useEffect(() => {
    if (versionId == null || query.trim().length < 2) {
      setFound([]);
      return;
    }
    let live = true;
    const t = setTimeout(() => {
      getScheduleActivities(versionId, { q: query.trim(), limit: 25 })
        .then((page) => live && setFound(page.items))
        .catch((e) => live && fail(e));
    }, 220);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [query, versionId, fail]);

  /* ------------------------------------------------------------- keyboard */

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      // The exposure dialog owns the keyboard while it is open, including Escape.
      if (exposureOpen) return;
      if (visibleCandidates.length === 0) return;
      const current = visibleCandidates[focusIndex];
      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setFocusIndex((i) => Math.min(i + 1, visibleCandidates.length - 1));
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setFocusIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "a" && current && !busy) {
        e.preventDefault();
        if (!current.warnings.some((w) => w.startsWith("error:"))) void accept(current);
      } else if (e.key === "x" && current) {
        e.preventDefault();
        void dismiss(current);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [visibleCandidates, focusIndex, busy, accept, dismiss, exposureOpen]);

  useEffect(() => {
    // Keep the keyboard cursor in view without yanking the page when it is already there.
    const el = listRef.current?.querySelector(".is-focused");
    el?.scrollIntoView({ block: "nearest" });
  }, [focusIndex, visibleCandidates.length]);

  /* ---------------------------------------------------------------- render */

  if (loading) return <div className="map map-empty">Loading…</div>;

  if (versions.length === 0) {
    return (
      <div className="map">
        <div className="map-empty">
          {error ? (
            <>
              <p>
                <strong>Could not load the schedule list.</strong>
              </p>
              <p className="map-error">{error}</p>
              <p>
                This is a load failure, not an empty platform — a schedule may well be
                imported. Check the <b>Schedule</b> tab, and the API log for the failing
                request.
              </p>
            </>
          ) : (
            <>
              <p>
                <strong>No schedule imported yet.</strong>
              </p>
              <p>
                Import a <code>.xer</code> export on the <b>Schedule</b> tab first — mappings are made against one parsed
                version, so there is nothing to map onto until then.
              </p>
            </>
          )}
        </div>
      </div>
    );
  }

  const olderVersions = versions.filter((v) => v.id !== versionId);
  const strongCount = visibleCandidates.filter(
    (c) => c.confidence === "strong" && !c.warnings.some((w) => w.startsWith("error:"))
  ).length;

  return (
    <div className="map">
      <div className="map-toolbar">
        <label>
          Schedule
          <select
            value={versionId ?? ""}
            onChange={(e) => {
              setVersionId(Number(e.target.value));
              setSelectedRiskId(null);
            }}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {v.project_name} · v{v.id}
                {v.is_current ? " (current)" : ""} · {v.activity_count} activities
              </option>
            ))}
          </select>
        </label>

        {olderVersions.length > 0 && (
          <label title="Re-anchor mappings from an earlier parse. Matched on activity ID, and they arrive as proposals for you to re-confirm.">
            Carry forward from
            <select
              defaultValue=""
              disabled={busy}
              onChange={(e) => {
                if (e.target.value) {
                  void carryForward(Number(e.target.value));
                  e.target.value = "";
                }
              }}
            >
              <option value="">choose a version…</option>
              {olderVersions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.id} · {v.project_name}
                </option>
              ))}
            </select>
          </label>
        )}

        <div className="map-spacer" />

        {coverage && (
          <div className="map-coverage">
            <div className="map-meter">
              <div className="map-meter-track">
                <span
                  className="map-meter-accepted"
                  style={{
                    width: `${
                      coverage.risks_in_scope
                        ? (100 * coverage.risks_with_accepted_mapping) / coverage.risks_in_scope
                        : 0
                    }%`,
                  }}
                />
                <span
                  className="map-meter-proposed"
                  style={{
                    width: `${
                      coverage.risks_in_scope
                        ? (100 * coverage.risks_with_proposed_only) / coverage.risks_in_scope
                        : 0
                    }%`,
                  }}
                />
              </div>
              <div className="map-meter-label">
                <span>
                  {coverage.risks_with_accepted_mapping} mapped ·{" "}
                  {coverage.risks_with_proposed_only} proposed · {coverage.risks_unmapped}{" "}
                  to do
                </span>
                <span>{coverage.coverage_pct}%</span>
              </div>
            </div>
            <button
              type="button"
              className={
                coverage.critical_activities_uncovered > 0
                  ? "map-stat map-stat-button is-warn"
                  : "map-stat map-stat-button"
              }
              aria-haspopup="dialog"
              ref={exposureTriggerRef}
              onClick={() => {
                setError(null);
                setExposureOpen(true);
              }}
              title="Critical-path activities with no accepted mapping. A register can read as fully covered while the driving path has nothing pointing at it. Open to see which."
            >
              <b>
                {coverage.critical_activities_uncovered}/{coverage.critical_activities}
              </b>
              <span>critical uncovered</span>
            </button>
          </div>
        )}
      </div>

      {error && (
        <div className="map-error" role="alert">
          {error}{" "}
          <button type="button" className="map-btn" onClick={() => setError(null)}>
            Dismiss
          </button>
        </div>
      )}
      {notice && (
        <div className="map-banner" role="status" aria-live="polite">
          {notice}{" "}
          <button type="button" className="map-btn" onClick={() => setNotice(null)}>
            OK
          </button>
        </div>
      )}

      <div className="map-panes">
        {/* ------------------------------------------------------ risk queue */}
        <section className="map-pane" aria-label="Risks">
          <div className="map-pane-head">
            Risks <span className="map-count">({queue.length})</span>
            <div className="map-spacer" />
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value as QueueFilter)}
              aria-label="Filter risks"
            >
              <option value="unmapped">Unmapped</option>
              <option value="proposed">Has proposals</option>
              <option value="all">All</option>
            </select>
          </div>
          {risks.length >= RISK_FETCH_LIMIT && (
            <p className="map-truncated">
              Showing the first {RISK_FETCH_LIMIT} risks — the register endpoint will not
              return more in one call. Any risk past that cannot be reached from this queue
              yet, so the counts here may sit below the coverage figures above.
            </p>
          )}
          <div className="map-pane-body">
            {queue.length === 0 ? (
              <div className="map-empty">
                {filter === "unmapped" ? "Everything in scope is mapped." : "Nothing here."}
              </div>
            ) : (
              queue.map((r) => {
                const c = mappingsByRisk.get(r.id);
                const state = c?.accepted ? "accepted" : c?.proposed ? "proposed" : "unmapped";
                return (
                  <button
                    type="button"
                    key={r.id}
                    className={
                      r.id === selectedRiskId ? "map-riskrow is-selected" : "map-riskrow"
                    }
                    aria-current={r.id === selectedRiskId}
                    onClick={() => setSelectedRiskId(r.id)}
                  >
                    <span className="map-riskrow-top">
                      <span className="map-riskcode">{r.risk_code}</span>
                      <span className={`map-chip map-chip-${state}`}>
                        {state === "accepted"
                          ? `${c?.accepted} mapped`
                          : state === "proposed"
                            ? `${c?.proposed} proposed`
                            : "unmapped"}
                      </span>
                      {scheduleArea && (
                        <span className="map-hint">S{scheduleImpact(r)}</span>
                      )}
                    </span>
                    <span className="map-risktitle" title={r.title}>
                      {r.title}
                    </span>
                  </button>
                );
              })
            )}
          </div>
        </section>

        {/* ----------------------------------------------------- suggestions */}
        <section className="map-pane" aria-label="Suggested activities">
          <div className="map-pane-head">
            Suggestions
            {suggestions && (
              <span className="map-count">
                ({visibleCandidates.length} of {suggestions.activities_considered} activities)
              </span>
            )}
            <div className="map-spacer" />
            {strongCount > 0 && (
              <button
                type="button"
                className="map-btn"
                disabled={busy}
                onClick={() => void acceptAllStrong()}
              >
                Accept {strongCount} strong
              </button>
            )}
          </div>

          {selectedRisk && (
            <div className="map-search">
              <input
                type="search"
                placeholder="Search activities by ID or name…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search activities"
              />
              <div className="map-hint" style={{ marginTop: 4 }}>
                <span className="map-kbd">j</span> <span className="map-kbd">k</span> move ·{" "}
                <span className="map-kbd">a</span> accept · <span className="map-kbd">x</span>{" "}
                dismiss
              </div>
            </div>
          )}

          <div className="map-pane-body" ref={listRef}>
            {!selectedRiskId ? (
              <div className="map-empty">
                <p>Pick a risk to see where it could land on the schedule.</p>
              </div>
            ) : found.length > 0 ? (
              found.map((a) => (
                <div className="map-card" key={a.source_id}>
                  <div className="map-card-head">
                    <span className="map-riskcode">{a.code}</span>
                    <span className="map-card-name" title={a.name}>
                      {a.name}
                    </span>
                  </div>
                  <div className="map-card-meta">
                    <span className={`map-chip map-chip-${a.is_critical ? "high" : "low"}`}>
                      {a.is_critical ? "critical path" : `${a.total_float_days ?? "?"}d float`}
                    </span>
                    <span className="map-hint">{a.status}</span>
                    <button
                      type="button"
                      className="map-btn"
                      disabled={busy}
                      onClick={() => void addManual(a)}
                    >
                      Map to this
                    </button>
                  </div>
                </div>
              ))
            ) : !suggestions ? (
              <div className="map-empty">Ranking activities…</div>
            ) : (
              <>
                {suggestions.scope_suggestion && (
                  <div className="map-banner">
                    <div>
                      {suggestions.scope_suggestion.covered} of the top candidates sit under{" "}
                      <strong>{suggestions.scope_suggestion.label}</strong>. One scoped driver
                      covers all {suggestions.scope_suggestion.total_in_scope} activities in
                      that branch, and keeps covering them when the branch grows.
                    </div>
                    <div className="map-banner-actions">
                      <button
                        type="button"
                        className="map-btn map-btn-primary"
                        disabled={busy}
                        onClick={() => void acceptScope()}
                      >
                        Add scoped driver
                      </button>
                    </div>
                  </div>
                )}
                {!suggestions.precedent_available && (
                  <div className="map-hint" style={{ padding: "6px 10px" }}>
                    No precedent for this subcategory yet — that signal abstains rather than
                    scoring zero, so these rankings rest on wording and category alone.
                  </div>
                )}
                {visibleCandidates.length === 0 ? (
                  <div className="map-empty">
                    <p>Nothing scored above the threshold.</p>
                    <p className="map-hint">Search above to map an activity by hand.</p>
                  </div>
                ) : (
                  visibleCandidates.map((c, i) => (
                    <SuggestionCard
                      key={c.activity_source_id}
                      candidate={c}
                      focused={i === focusIndex}
                      busy={busy}
                      onAccept={(x) => void accept(x)}
                      onReject={(x) => void dismiss(x)}
                      onFocus={() => setFocusIndex(i)}
                    />
                  ))
                )}
              </>
            )}
          </div>
        </section>

        {/* -------------------------------------------------------- mappings */}
        <section className="map-pane" aria-label="Mappings for this risk">
          <div className="map-pane-head">
            Mappings <span className="map-count">({riskMappings.length})</span>
          </div>
          <div className="map-pane-body">
            {!selectedRiskId ? (
              <div className="map-empty">No risk selected.</div>
            ) : riskMappings.length === 0 ? (
              <div className="map-empty">
                <p>This risk has no landing points yet.</p>
              </div>
            ) : (
              riskMappings.map((m) => (
                <MappingRow
                  key={m.id}
                  mapping={m}
                  busy={busy}
                  onUpdate={(id, body) => void patch(id, body)}
                  onDelete={(id) => void remove(id)}
                />
              ))
            )}
          </div>
        </section>
      </div>

      {exposureOpen && coverage && (
        <ExposurePanel
          activities={coverage.critical_uncovered}
          totalUncovered={coverage.critical_activities_uncovered}
          criticalTotal={coverage.critical_activities}
          activitiesCovered={coverage.activities_covered}
          activitiesTotal={coverage.activities_total}
          risks={risks}
          scheduleArea={scheduleArea}
          busy={busy}
          error={error}
          onAttach={attachRiskToActivity}
          onClose={() => {
            setExposureOpen(false);
            exposureTriggerRef.current?.focus();
          }}
        />
      )}
    </div>
  );
}
