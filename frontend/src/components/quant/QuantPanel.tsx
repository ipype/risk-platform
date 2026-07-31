import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DimensionEditor from "./DimensionEditor";
import {
  QuantValidationError,
  deleteEstimate,
  getEstimates,
  previewEstimate,
  saveEstimate,
  setEstimateLock,
} from "../../quant/api";
import {
  draftFromEstimate,
  draftToPayload,
  emptyDraft,
  issuesFor,
  readyToPreview,
} from "../../quant/draft";
import type { DraftDimension, DraftEstimate } from "../../quant/draft";
import type {
  BoundInterpretation,
  Confidence,
  QuantEstimate,
  QuantIssue,
  QuantScenario,
  QuantSummary,
  QuantVocabulary,
} from "../../quant/types";

/**
 * The quantitative estimate for one risk.
 *
 * Both scenarios live behind tabs rather than side by side. Post-mitigation is a separate
 * elicitation, not an edit of the pre-mitigation numbers, and putting them in one view
 * invites nudging one column until the delta looks like the answer somebody wanted.
 *
 * Nothing here derives numbers from the matrix scores on the register. Turning an ordinal
 * impact band into a currency range would invent precision nobody supplied and leave no
 * record of who supplied it; the matrix decides *which* risks reach this form, and that is
 * the whole of its job here.
 */

const INTERPRETATION_LABELS: Record<BoundInterpretation, string> = {
  absolute: "Absolute extremes",
  p10_p90: "P10 and P90",
  p5_p95: "P5 and P95",
};

const CONFIDENCE_LABELS: Record<Confidence, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
};

const SCENARIO_LABELS: Record<QuantScenario, string> = {
  pre_mitigation: "Pre-mitigation",
  post_mitigation: "Post-mitigation",
};

interface Props {
  riskId: number;
  riskCode: string;
  riskTitle: string;
  vocabulary: QuantVocabulary;
  onSaved?: () => void;
}

export default function QuantPanel({
  riskId,
  riskCode,
  riskTitle,
  vocabulary,
  onSaved,
}: Props) {
  const [scenario, setScenario] = useState<QuantScenario>("pre_mitigation");
  const [stored, setStored] = useState<QuantEstimate[]>([]);
  const [draft, setDraft] = useState<DraftEstimate>(emptyDraft);
  const [summary, setSummary] = useState<Partial<QuantSummary>>({});
  const [issues, setIssues] = useState<QuantIssue[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  const current = stored.find((e) => e.scenario === scenario) ?? null;
  const locked = current?.locked ?? false;

  const load = useCallback(async () => {
    setLoading(true);
    setBanner(null);
    try {
      const rows = await getEstimates(riskId);
      setStored(rows);
      const match = rows.find((e) => e.scenario === scenario);
      setDraft(match ? draftFromEstimate(match) : emptyDraft());
      setIssues([]);
      setSummary({});
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Could not load the estimate");
    } finally {
      setLoading(false);
    }
  }, [riskId, scenario]);

  useEffect(() => {
    void load();
  }, [load]);

  // Debounced preview. The request is cheap and stateless, but firing on every keystroke
  // would still race itself: a slow response for "12" can land after the one for "1200"
  // and redraw the older curve. The generation counter drops anything stale.
  const generation = useRef(0);
  useEffect(() => {
    if (!readyToPreview(draft)) {
      setSummary({});
      return;
    }
    const mine = ++generation.current;
    const timer = window.setTimeout(() => {
      previewEstimate(draftToPayload(draft))
        .then((res) => {
          if (mine !== generation.current) return;
          setSummary(res.summary);
          setIssues([...res.errors, ...res.warnings]);
        })
        .catch(() => {
          /* preview is advisory; a failed one must not block the form */
        });
    }, 350);
    return () => window.clearTimeout(timer);
  }, [draft]);

  const errors = useMemo(() => issues.filter((i) => i.severity === "error"), [issues]);
  const warnings = useMemo(() => issues.filter((i) => i.severity === "warning"), [issues]);
  const topLevel = (field: string) =>
    issues.find((i) => i.field === field)?.message ?? null;

  const setDimension = (key: "cost" | "sched") => (next: DraftDimension) =>
    setDraft((d) => ({ ...d, [key]: next }));

  async function save() {
    setSaving(true);
    setBanner(null);
    try {
      const res = await saveEstimate(riskId, scenario, draftToPayload(draft));
      setStored((rows) => [
        ...rows.filter((r) => r.scenario !== scenario),
        res.estimate,
      ]);
      setDraft(draftFromEstimate(res.estimate));
      setSummary(res.summary);
      setIssues(res.warnings);
      setSavedAt(new Date().toLocaleTimeString());
      onSaved?.();
    } catch (err) {
      if (err instanceof QuantValidationError) {
        setIssues(
          err.issues.map((i) => ({ severity: "error" as const, field: i.field, message: i.message }))
        );
        setBanner("Some values cannot be simulated as given. See the messages below.");
      } else {
        setBanner(err instanceof Error ? err.message : "Save failed");
      }
    } finally {
      setSaving(false);
    }
  }

  async function toggleLock() {
    try {
      const updated = await setEstimateLock(riskId, scenario, !locked);
      setStored((rows) => [...rows.filter((r) => r.scenario !== scenario), updated]);
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Could not change the lock");
    }
  }

  async function remove() {
    try {
      await deleteEstimate(riskId, scenario);
      await load();
      onSaved?.();
    } catch (err) {
      setBanner(err instanceof Error ? err.message : "Could not remove the estimate");
    }
  }

  if (loading) return <div className="qnt-panel qnt-empty">Loading…</div>;

  return (
    <div className="qnt-panel">
      <header className="qnt-panel-head">
        <div>
          <span className="qnt-code">{riskCode}</span>
          <h2 className="qnt-panel-title">{riskTitle}</h2>
        </div>
        {current && (
          <span className="qnt-muted">
            Last set by {current.estimated_by}
            {savedAt ? ` · saved ${savedAt}` : ""}
          </span>
        )}
      </header>

      <div className="qnt-tabs" role="tablist">
        {vocabulary.scenarios.map((s) => (
          <button
            key={s}
            role="tab"
            aria-selected={scenario === s}
            className={scenario === s ? "qnt-tab qnt-tab-active" : "qnt-tab"}
            onClick={() => setScenario(s)}
          >
            {SCENARIO_LABELS[s] ?? s}
            {stored.some((e) => e.scenario === s) && <span className="qnt-dot" aria-hidden />}
          </button>
        ))}
      </div>

      {banner && <p className="qnt-banner">{banner}</p>}
      {locked && (
        <p className="qnt-locked">
          Frozen against a simulation run. Unlock to edit — the run stops being reproducible
          the moment its inputs move.
        </p>
      )}

      <section className="qnt-occurrence">
        <label className="qnt-field">
          <span className="qnt-label">Probability of occurrence</span>
          <input
            className={topLevel("p_occurrence") ? "qnt-input qnt-input-bad" : "qnt-input"}
            inputMode="decimal"
            value={draft.pOccurrence}
            disabled={locked || draft.isVariability}
            onChange={(e) => setDraft((d) => ({ ...d, pOccurrence: e.target.value }))}
          />
        </label>

        <label className="qnt-check">
          <input
            type="checkbox"
            checked={draft.isVariability}
            disabled={locked}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                isVariability: e.target.checked,
                pOccurrence: e.target.checked ? "1" : d.pOccurrence,
              }))
            }
          />
          <span>
            Estimate variability
            <span
              className="qnt-hint"
              title="Inherent range on a base estimate rather than a discrete event. Always present, so probability is fixed at 1. AACE 57R-09 keeps the two apart; without the distinction a register quietly turns into an estimate."
            >
              ?
            </span>
          </span>
        </label>

        <label className="qnt-field">
          <span className="qnt-label">
            Bounds are
            <span
              className="qnt-hint"
              title="What the minimum and maximum actually mean. SMEs asked for extremes usually give something nearer a P10 and P90; recording that lets trigen recover the real bounds instead of truncating the tail."
            >
              ?
            </span>
          </span>
          <select
            className="qnt-select"
            value={draft.boundInterpretation}
            disabled={locked}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                boundInterpretation: e.target.value as BoundInterpretation,
              }))
            }
          >
            {vocabulary.bound_interpretations.map((b) => (
              <option key={b} value={b}>
                {INTERPRETATION_LABELS[b] ?? b}
              </option>
            ))}
          </select>
        </label>

        <label className="qnt-field">
          <span className="qnt-label">Confidence</span>
          <select
            className="qnt-select"
            value={draft.confidence}
            disabled={locked}
            onChange={(e) =>
              setDraft((d) => ({ ...d, confidence: e.target.value as Confidence }))
            }
          >
            {vocabulary.confidences.map((c) => (
              <option key={c} value={c}>
                {CONFIDENCE_LABELS[c] ?? c}
              </option>
            ))}
          </select>
        </label>
      </section>

      {topLevel("p_occurrence") && <p className="qnt-error">{topLevel("p_occurrence")}</p>}

      <div className="qnt-dimensions">
        <DimensionEditor
          title="Cost impact"
          unit={draft.costBasis === "pct_of_base" ? "%" : "currency"}
          value={draft.cost}
          options={vocabulary.distributions}
          summary={summary.cost ?? null}
          issues={issuesFor(issues, "cost") as QuantIssue[]}
          disabled={locked}
          onChange={setDimension("cost")}
        >
          <label className="qnt-field qnt-field-inline">
            <span className="qnt-label">Basis</span>
            <select
              className="qnt-select qnt-select-sm"
              value={draft.costBasis}
              disabled={locked}
              onChange={(e) => setDraft((d) => ({ ...d, costBasis: e.target.value }))}
            >
              {vocabulary.cost_bases.map((b) => (
                <option key={b} value={b}>
                  {b === "pct_of_base" ? "Percent of base" : "Absolute"}
                </option>
              ))}
            </select>
          </label>
        </DimensionEditor>

        <DimensionEditor
          title="Schedule impact"
          unit="days"
          value={draft.sched}
          options={vocabulary.distributions}
          summary={summary.sched ?? null}
          issues={issuesFor(issues, "sched") as QuantIssue[]}
          disabled={locked}
          onChange={setDimension("sched")}
        >
          <label className="qnt-field qnt-field-inline">
            <span className="qnt-label">
              Days are
              <span
                className="qnt-hint"
                title="Working and calendar days differ by roughly 40 percent, and the error is invisible in the output. Never inferred."
              >
                ?
              </span>
            </span>
            <select
              className="qnt-select qnt-select-sm"
              value={draft.schedDayBasis}
              disabled={locked}
              onChange={(e) => setDraft((d) => ({ ...d, schedDayBasis: e.target.value }))}
            >
              {vocabulary.day_bases.map((b) => (
                <option key={b} value={b}>
                  {b === "working" ? "Working" : "Calendar"}
                </option>
              ))}
            </select>
          </label>
        </DimensionEditor>
      </div>

      <label className="qnt-field">
        <span className="qnt-label">Notes</span>
        <textarea
          className="qnt-textarea"
          rows={2}
          value={draft.notes}
          disabled={locked}
          onChange={(e) => setDraft((d) => ({ ...d, notes: e.target.value }))}
        />
      </label>

      {warnings.length > 0 && (
        <ul className="qnt-warnings">
          {warnings.map((w, i) => (
            <li key={`${w.field}-${i}`}>
              <strong>{w.field}</strong> {w.message}
            </li>
          ))}
        </ul>
      )}

      <footer className="qnt-actions">
        <button
          type="button"
          className="qnt-primary"
          disabled={saving || locked || errors.length > 0}
          onClick={() => void save()}
        >
          {saving ? "Saving…" : current ? "Update estimate" : "Save estimate"}
        </button>
        {current && (
          <>
            <button type="button" className="qnt-secondary" onClick={() => void toggleLock()}>
              {locked ? "Unlock" : "Lock for a run"}
            </button>
            <button
              type="button"
              className="qnt-secondary"
              disabled={locked}
              onClick={() => void remove()}
            >
              Remove
            </button>
          </>
        )}
        {errors.length > 0 && (
          <span className="qnt-muted">
            {errors.length} issue{errors.length === 1 ? "" : "s"} to resolve
          </span>
        )}
      </footer>
    </div>
  );
}
