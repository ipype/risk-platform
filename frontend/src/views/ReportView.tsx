/**
 * Assemble a report and look at it before sending it to anybody.
 *
 * Three decisions shape this screen.
 *
 * The **picker is server-driven**. Sections come from `/reports/sections`, which answers
 * for the selected run rather than in general, so "Schedule outcome — this run simulated
 * cost only" appears against the checkbox instead of the section silently not being in
 * the output. A hardcoded list here would drift from the registry the first time one was
 * added.
 *
 * An unavailable section is **shown and disabled**, not hidden. "Where is the criticality
 * section?" is a question worth answering on the page.
 *
 * The **preview is the artifact**, not a mock of it. The iframe renders the same HTML the
 * download produces, so what is on screen is what lands in the client's inbox. It is
 * sandboxed without script permission because the document carries none and there is no
 * reason to grant what it does not use.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getActor } from "../api";
import { getPlans } from "../mitigation-api";
import { listComparisons } from "../roi-api";
import {
  ReportApiError,
  downloadReport,
  getReportSections,
  reportPreviewUrl,
} from "../report-api";
import { DEFAULT_REPORT } from "../report-types";
import type { ReportQuery, ReportSectionOption } from "../report-types";
import { getRuns } from "../sim-api";
import type { RunSummary } from "../simulation-types";
import type { Plan } from "../mitigation-types";
import type { RoiSummary } from "../roi-types";
import "../report.css";

/** A run with no result cannot carry a quantitative section, so it is not offered. */
function reportable(run: RunSummary): boolean {
  return run.status === "succeeded";
}

function runLabel(run: RunSummary): string {
  const bits = [`#${run.id}`, run.name || "Unnamed run"];
  if (run.scenario === "post_mitigation") bits.push("post-mitigation");
  if (run.gate_override) bits.push("gate overridden");
  return bits.join(" · ");
}

export default function ReportView() {
  const [query, setQuery] = useState<ReportQuery>(() => ({
    ...DEFAULT_REPORT,
    prepared_by: getActor() === "Unknown" ? "" : getActor(),
  }));
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [comparisons, setComparisons] = useState<RoiSummary[]>([]);
  const [sections, setSections] = useState<ReportSectionOption[]>([]);
  const [notes, setNotes] = useState<string[]>([]);

  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewRef = useRef<HTMLIFrameElement | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [runList, planList, roiList] = await Promise.all([
          getRuns(50),
          getPlans().catch(() => [] as Plan[]),
          listComparisons().catch(() => [] as RoiSummary[]),
        ]);
        const usable = runList.filter(reportable);
        setRuns(usable);
        setPlans(planList);
        setComparisons(roiList);
        setQuery((current) =>
          current.run_id === null && usable.length > 0
            ? { ...current, run_id: usable[0].id }
            : current
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load the report options");
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // The manifest depends only on what is being reported on, never on the wording or the
  // section selection — refetching it on every keystroke in the title field would be a
  // request per character for an answer that cannot have changed.
  const manifestKey = `${query.run_id}|${query.roi_id}|${query.plan_id}`;

  const loadSections = useCallback(async () => {
    try {
      const body = await getReportSections({ ...query, sections: null });
      setSections(body.sections);
      setNotes(body.notes);
      setError(null);
    } catch (e) {
      setSections([]);
      setError(e instanceof Error ? e.message : "Could not read the section list");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [manifestKey]);

  useEffect(() => {
    if (loading) return;
    void loadSections();
  }, [loading, loadSections]);

  const availableIds = useMemo(
    () => sections.filter((s) => s.available).map((s) => s.id),
    [sections]
  );

  /** `null` means "everything available", which is not the same as ticking them all. */
  const chosen = query.sections;
  const isChosen = useCallback(
    (id: string) => (chosen === null ? availableIds.includes(id) : chosen.includes(id)),
    [chosen, availableIds]
  );

  function toggle(id: string) {
    setQuery((current) => {
      const base = current.sections ?? availableIds;
      const next = base.includes(id)
        ? base.filter((s) => s !== id)
        : [...availableIds.filter((s) => base.includes(s) || s === id)];
      return { ...current, sections: next };
    });
    setPreviewUrl(null);
  }

  function set<K extends keyof ReportQuery>(key: K, value: ReportQuery[K]) {
    setQuery((current) => ({ ...current, [key]: value }));
    setPreviewUrl(null);
  }

  const selectedCount = chosen === null ? availableIds.length : chosen.length;

  function preview() {
    setError(null);
    if (selectedCount === 0) {
      setError("Choose at least one section before previewing.");
      return;
    }
    setPreviewUrl(reportPreviewUrl(query));
  }

  async function save(format: "html" | "xlsx" | "json") {
    setBusy(true);
    setError(null);
    try {
      await downloadReport(query, format);
    } catch (e) {
      setError(
        e instanceof ReportApiError && e.isEmptySelection
          ? "Nothing in the current selection has anything to report."
          : e instanceof Error
            ? e.message
            : "The report could not be produced"
      );
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="report-boot">Loading report options…</div>;

  return (
    <div className="report">
      <div className="report-setup">
        <section className="report-card">
          <h2>What this report covers</h2>

          <label className="field">
            <span>Title</span>
            <input
              value={query.title}
              maxLength={160}
              onChange={(e) => set("title", e.target.value)}
            />
          </label>

          <div className="field-row">
            <label className="field">
              <span>Prepared by</span>
              <input
                value={query.prepared_by}
                maxLength={120}
                placeholder="your name"
                onChange={(e) => set("prepared_by", e.target.value)}
              />
            </label>
            <label className="field narrow">
              <span>Currency</span>
              <input
                value={query.currency}
                maxLength={4}
                placeholder="none"
                onChange={(e) => set("currency", e.target.value)}
              />
            </label>
          </div>

          <label className="field">
            <span>Simulation run</span>
            <select
              value={query.run_id ?? ""}
              onChange={(e) => set("run_id", e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">None — register and matrix only</option>
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {runLabel(run)}
                </option>
              ))}
            </select>
          </label>
          {runs.length === 0 ? (
            <p className="hint">
              No completed run yet. The register, matrix and action sections are still
              available.
            </p>
          ) : (
            <p className="hint">
              Naming a run fixes the scope to that run's project, so the register and the
              contingency describe the same set of risks.
            </p>
          )}

          <div className="field-row">
            <label className="field">
              <span>Mitigation plan</span>
              <select
                value={query.plan_id ?? ""}
                onChange={(e) =>
                  set("plan_id", e.target.value ? Number(e.target.value) : null)
                }
              >
                <option value="">None</option>
                {plans.map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>ROI comparison</span>
              <select
                value={query.roi_id ?? ""}
                onChange={(e) =>
                  set("roi_id", e.target.value ? Number(e.target.value) : null)
                }
              >
                <option value="">None</option>
                {comparisons.map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name || `Comparison ${row.id}`}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </section>

        <section className="report-card">
          <div className="card-head">
            <h2>Sections</h2>
            <span className="count">
              {selectedCount} of {availableIds.length}
            </span>
          </div>

          <div className="section-actions">
            <button className="link" onClick={() => set("sections", null)}>
              Select all available
            </button>
            <button className="link" onClick={() => set("sections", [])}>
              Clear
            </button>
          </div>

          <ul className="section-list">
            {sections.map((section) => (
              <li key={section.id} className={section.available ? "" : "off"}>
                <label>
                  <input
                    type="checkbox"
                    disabled={!section.available}
                    checked={section.available && isChosen(section.id)}
                    onChange={() => toggle(section.id)}
                  />
                  <span className="title">{section.title}</span>
                </label>
                <p className="summary">
                  {section.available ? section.summary : section.reason}
                </p>
              </li>
            ))}
          </ul>

          {notes.length > 0 ? (
            <ul className="report-notes">
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
        </section>

        <section className="report-card">
          <h2>Produce</h2>
          {error !== null ? <div className="error">{error}</div> : null}
          <div className="produce">
            <button className="btn primary" onClick={preview} disabled={busy}>
              Preview
            </button>
            <button className="btn" onClick={() => void save("html")} disabled={busy}>
              Download HTML
            </button>
            <button className="btn" onClick={() => void save("xlsx")} disabled={busy}>
              Download workbook
            </button>
            <button className="btn" onClick={() => void save("json")} disabled={busy}>
              Download JSON
            </button>
          </div>
          <p className="hint">
            The HTML file is self-contained and prints to PDF from a browser. The workbook
            keeps every figure as a number, one sheet per section.
          </p>
        </section>
      </div>

      <div className="report-preview">
        {previewUrl === null ? (
          <div className="preview-empty">
            <p>The preview is the file itself, not a mock of it.</p>
            <p className="hint">Choose the sections, then press Preview.</p>
          </div>
        ) : (
          <iframe
            ref={previewRef}
            title="Report preview"
            src={previewUrl}
            sandbox="allow-same-origin"
          />
        )}
      </div>
    </div>
  );
}
