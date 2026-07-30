import { useEffect, useMemo, useRef, useState } from "react";
import type { Risk, UncoveredActivity } from "../../types";

interface Props {
  /** `coverage.critical_uncovered` — capped at 200 by the API. */
  activities: UncoveredActivity[];
  /** `coverage.critical_activities_uncovered` — the true count, which may exceed the list. */
  totalUncovered: number;
  criticalTotal: number;
  activitiesCovered: number;
  activitiesTotal: number;
  risks: Risk[];
  /** Impact area code the coverage report treats as "schedule", or null if none is configured. */
  scheduleArea: string | null;
  busy: boolean;
  /** Rendered inside the dialog: the backdrop hides the view's own error banner. */
  error: string | null;
  /** Resolves false when the write failed, so the picker stays open over a retryable error. */
  onAttach: (activitySourceId: string, riskId: number) => Promise<boolean>;
  onClose: () => void;
}

function scheduleImpactOf(risk: Risk, area: string | null): number | null {
  if (!area) return null;
  const raw = risk.impact_scores?.[area];
  return typeof raw === "number" ? raw : null;
}

/**
 * Days remaining is the exposure; float is how much of it the project absorbs. Sorting on
 * duration first puts the real holes at the top — a zero-float 60-day activity with nothing
 * pointing at it matters far more than a zero-float 2-day handover, and a count alone
 * ("12 uncovered") ranks them identically.
 */
function byExposure(a: UncoveredActivity, b: UncoveredActivity): number {
  const ra = a.remaining_duration_days ?? 0;
  const rb = b.remaining_duration_days ?? 0;
  if (rb !== ra) return rb - ra;
  const fa = a.total_float_days ?? Number.MAX_SAFE_INTEGER;
  const fb = b.total_float_days ?? Number.MAX_SAFE_INTEGER;
  if (fa !== fb) return fa - fb;
  return a.activity_code.localeCompare(b.activity_code);
}

export default function ExposurePanel({
  activities,
  totalUncovered,
  criticalTotal,
  activitiesCovered,
  activitiesTotal,
  risks,
  scheduleArea,
  busy,
  error,
  onAttach,
  onClose,
}: Props) {
  const [openFor, setOpenFor] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const closeRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    if (openFor) searchRef.current?.focus();
  }, [openFor]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      e.preventDefault();
      // Escape backs out one level at a time, so a mis-opened picker does not cost the
      // whole panel and the scroll position with it.
      if (openFor) setOpenFor(null);
      else onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openFor, onClose]);

  const sorted = useMemo(() => [...activities].sort(byExposure), [activities]);

  /**
   * Ranked by schedule impact when the matrix has a schedule area, because attaching a
   * risk that carries no schedule consequence to a driving activity is a modelling error
   * that the simulation will faithfully reproduce.
   */
  const candidates = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matched = q
      ? risks.filter(
          (r) =>
            r.risk_code.toLowerCase().includes(q) || (r.title ?? "").toLowerCase().includes(q)
        )
      : risks;
    return [...matched]
      .sort((a, b) => {
        const sa = scheduleImpactOf(a, scheduleArea) ?? -1;
        const sb = scheduleImpactOf(b, scheduleArea) ?? -1;
        if (sb !== sa) return sb - sa;
        return a.risk_code.localeCompare(b.risk_code);
      })
      .slice(0, 12);
  }, [risks, query, scheduleArea]);

  const truncated = totalUncovered > sorted.length;

  return (
    <div className="map-exposure-backdrop" onMouseDown={onClose}>
      <div
        className="map-exposure"
        role="dialog"
        aria-modal="true"
        aria-labelledby="map-exposure-title"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="map-exposure-head">
          <div>
            <h2 id="map-exposure-title">Critical work with no accepted mapping</h2>
            <p className="map-exp-note">
              {totalUncovered} of {criticalTotal} incomplete critical activities have nothing
              pointing at them. Across the whole schedule, {activitiesCovered} of{" "}
              {activitiesTotal} activities are covered. Ordered by remaining duration, so the
              longest unprotected work is first.
            </p>
          </div>
          <button
            type="button"
            className="map-exposure-close"
            onClick={onClose}
            ref={closeRef}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="map-exposure-body">
          <div role="status" aria-live="polite">
            {error && <p className="map-exp-error">{error}</p>}
          </div>
          {sorted.length === 0 ? (
            <p className="map-exp-empty">
              Every incomplete critical activity has an accepted mapping. That is the
              condition worth re-checking after each schedule re-import, not once.
            </p>
          ) : (
            <ul className="map-exp-list">
              {sorted.map((a) => {
                const picking = openFor === a.activity_source_id;
                return (
                  <li key={a.activity_source_id} className="map-exp-row">
                    <div className="map-exp-main">
                      <div className="map-exp-id">
                        <b title={a.activity_code}>{a.activity_code || a.activity_source_id}</b>
                        <span title={a.activity_name}>{a.activity_name || "(unnamed)"}</span>
                      </div>
                      <div className="map-exp-meta">
                        <span>
                          {a.remaining_duration_days ?? "—"}
                          <small>d remaining</small>
                        </span>
                        <span>
                          {a.total_float_days ?? "—"}
                          <small>d float</small>
                        </span>
                      </div>
                      <button
                        type="button"
                        className="map-exp-attach"
                        aria-expanded={picking}
                        disabled={busy}
                        onClick={() => {
                          setQuery("");
                          setOpenFor(picking ? null : a.activity_source_id);
                        }}
                      >
                        {picking ? "Cancel" : "Attach risk"}
                      </button>
                    </div>

                    {picking && (
                      <div className="map-picker">
                        <input
                          ref={searchRef}
                          className="map-picker-input"
                          value={query}
                          placeholder="Search risks by code or title"
                          onChange={(e) => setQuery(e.target.value)}
                          aria-label={`Search risks to attach to ${a.activity_code}`}
                        />
                        {candidates.length === 0 ? (
                          <p className="map-exp-empty">No risk matches that.</p>
                        ) : (
                          <ul className="map-picker-list">
                            {candidates.map((r) => {
                              const impact = scheduleImpactOf(r, scheduleArea);
                              return (
                                <li key={r.id}>
                                  <button
                                    type="button"
                                    className="map-picker-item"
                                    disabled={busy}
                                    onClick={() => {
                                      void onAttach(a.activity_source_id, r.id).then(
                                        (ok) => {
                                          if (!ok) return;
                                          setOpenFor(null);
                                          setQuery("");
                                        }
                                      );
                                    }}
                                  >
                                    <b>{r.risk_code}</b>
                                    <span title={r.title}>{r.title}</span>
                                    {scheduleArea &&
                                      (impact && impact > 0 ? (
                                        <em className="map-picker-score">
                                          schedule impact {impact}
                                        </em>
                                      ) : (
                                        <em className="map-picker-score is-muted">
                                          no schedule impact
                                        </em>
                                      ))}
                                  </button>
                                </li>
                              );
                            })}
                          </ul>
                        )}
                        <p className="map-exp-note">
                          Lands as a duration driver, accepted, attributed to you. Change the
                          type or reverse it from the mappings pane.
                        </p>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {truncated && (
            <p className="map-exp-note">
              Showing the first {sorted.length}. The API caps this list at 200; clear some to
              see the rest.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
