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
  anyAssessed,
  draftFromEstimate,
  draftToPayload,
  emptyDraft,
  issuesFor,
  readyToPreview,
  usesBounds,
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
import { getActions } from "../../api";
import type { MitigationAction } from "../../types";

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
 *
 * "Bounds are" sits inside each dimension rather than above both. The two impacts are
 * routinely elicited differently — a delay capped by a contract milestone is an absolute
 * bound, while the cost it drags along is whatever the SME would defend, which is nearer a
 * P10/P90 — and under one shared control that pair is not merely awkward but rejected,
 * because triangular refuses a percentile reading and trigen refuses an absolute one. The
 * estimate-level value survives underneath as the session default and is what an untouched
 * row still simulates under.
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

const BOUNDS_HINT =
  "What the minimum and maximum actually mean for this dimension. SMEs asked for extremes " +
  "usually give something nearer a P10 and P90; recording that lets trigen recover the real " +
  "bounds instead of truncating the tail. The two dimensions can differ.";

const num = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 0 });

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

  const assessed = anyAssessed(draft);

  const setDimension = (key: "cost" | "sched") => (next: DraftDimension) =>
    setDraft((d) => ({ ...d, [key]: next }));

  const setBounds = (key: "cost" | "sched") => (value: BoundInterpretation) =>
    setDraft((d) => ({ ...d, [key]: { ...d[key], boundInterpretation: value } }));

  /** The bounds control, rendered inside whichever dimension it belongs to. */
  function boundsField(key: "cost" | "sched") {
    if (!usesBounds(draft[key].dist)) return null;
    const message = topLevel(`${key}.bound_interpretation`);
    return (
      <>
        <label className="qnt-field qnt-field-inline">
          <span className="qnt-label">
            Bounds are
            <span className="qnt-hint" title={BOUNDS_HINT}>
              ?
            </span>
          </span>
          <select
            className="qnt-select qnt-select-sm"
            value={draft[key].boundInterpretation}
            disabled={locked}
            onChange={(e) => setBounds(key)(e.target.value as BoundInterpretation)}
          >
            {vocabulary.bound_interpretations.map((b) => (
              <option key={b} value={b}>
                {INTERPRETATION_LABELS[b] ?? b}
              </option>
            ))}
          </select>
        </label>
        {message && <p className="qnt-warn">{message}</p>}
      </>
    );
  }

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

      {scenario === "post_mitigation" && <PlannedActions riskId={riskId} />}

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

          {draft.costBasis === "pct_of_base" && (
            <>
              <label className="qnt-field qnt-field-inline">
                <span className="qnt-label">
                  Base amount
                  <span
                    className="qnt-hint"
                    title="The figure these percentages are a percentage of — the package, subcontract or estimate line the risk actually scales with. Leave it blank and the run's own base cost is used instead, which is right for a risk that scales with the whole project and wrong, by the ratio between them, for one that does not."
                  >
                    ?
                  </span>
                </span>
                <input
                  className={
                    topLevel("cost_base_value") ? "qnt-input qnt-input-sm qnt-input-bad" : "qnt-input qnt-input-sm"
                  }
                  inputMode="decimal"
                  placeholder="run base cost"
                  value={draft.costBaseValue}
                  disabled={locked}
                  onChange={(e) => setDraft((d) => ({ ...d, costBaseValue: e.target.value }))}
                />
              </label>
              {topLevel("cost_base_value") && (
                <p
                  className={
                    errors.some((i) => i.field === "cost_base_value") ? "qnt-error" : "qnt-warn"
                  }
                >
                  {topLevel("cost_base_value")}
                </p>
              )}
            </>
          )}

          {boundsField("cost")}
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

          {boundsField("sched")}
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
          disabled={saving || locked || !assessed || errors.length > 0}
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
        {!assessed && (
          <span className="qnt-muted">
            Choose a shape for the cost impact, the schedule impact, or both. A risk with
            neither cannot reach the contingency.
          </span>
        )}
        {assessed && errors.length > 0 && (
          <span className="qnt-muted">
            {errors.length} issue{errors.length === 1 ? "" : "s"} to resolve
          </span>
        )}
      </footer>
    </div>
  );
}

/**
 * What is actually being done about the risk, shown while its residual is being elicited.
 *
 * Read-only on purpose. Editing lives on the register, and duplicating it here would give
 * two screens that can disagree about what the plan is. What this needs to do is stop an
 * SME inventing a residual out of optimism: a post-mitigation range is only defensible
 * next to the actions that justify it, and the commonest failure in this step is a number
 * that assumes work nobody has funded or scheduled.
 *
 * The totals are deterministic money and days and stay outside every percentile on this
 * screen (invariant 1). A plan's price is additive and a contingency is not, so the two
 * are quoted side by side and never summed.
 */
function PlannedActions({ riskId }: { riskId: number }) {
  const [actions, setActions] = useState<MitigationAction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setActions(null);
    setError(null);
    getActions(riskId)
      .then((rows) => {
        if (live) setActions(rows);
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : "Could not load the actions");
      });
    return () => {
      live = false;
    };
  }, [riskId]);

  if (error) return <p className="qnt-banner">{error}</p>;
  if (actions === null) return <p className="qnt-muted qnt-plan-loading">Loading actions…</p>;

  // Cancelled work is shown but never counted: it is context for why a residual moved, not
  // spend anyone is committing to.
  const live = actions.filter((a) => (a.status ?? "") !== "Cancelled");
  const budget = live.reduce((sum, a) => sum + (a.budget ?? 0), 0);
  const days = live.reduce((sum, a) => sum + (a.sched_days ?? 0), 0);
  const unpriced = live.filter((a) => a.budget === null && a.sched_days === null).length;

  return (
    <section className="qnt-plan">
      <header className="qnt-plan-head">
        <h3 className="qnt-plan-title">Mitigation actions ({actions.length})</h3>
        <span className="qnt-muted">
          The residual below should be what is left <em>after</em> these, and only these.
        </span>
      </header>

      {actions.length === 0 ? (
        <p className="qnt-plan-empty">
          Nothing recorded against this risk. A post-mitigation estimate with no actions
          behind it is a hope rather than a plan — add them on the register first, or say in
          the notes what the reduction rests on.
        </p>
      ) : (
        <>
          <table className="qnt-plan-table">
            <thead>
              <tr>
                <th>Action</th>
                <th>Owner</th>
                <th>Status</th>
                <th className="qnt-num">Cost</th>
                <th className="qnt-num">Days</th>
              </tr>
            </thead>
            <tbody>
              {actions.map((a) => {
                const cancelled = (a.status ?? "") === "Cancelled";
                return (
                  <tr key={a.id} className={cancelled ? "qnt-plan-cancelled" : undefined}>
                    <td>{a.action?.trim() || <span className="qnt-muted">Untitled action</span>}</td>
                    <td>{a.owner || <span className="qnt-muted">—</span>}</td>
                    <td>{a.status || "—"}</td>
                    <td className="qnt-num">{num(a.budget)}</td>
                    <td className="qnt-num">{num(a.sched_days)}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={3}>Committed total ({live.length} live)</td>
                <td className="qnt-num">{num(budget)}</td>
                <td className="qnt-num">{num(days)}</td>
              </tr>
            </tfoot>
          </table>

          <p className="qnt-plan-note">
            Deterministic plan cost. It sits beside the contingency and never inside it —
            percentiles are not additive and a package&rsquo;s price is.
            {unpriced > 0 && (
              <>
                {" "}
                {unpriced} action{unpriced === 1 ? " carries" : "s carry"} neither a cost nor a
                duration, so the total is a floor, not a price.
              </>
            )}
          </p>
        </>
      )}
    </section>
  );
}
