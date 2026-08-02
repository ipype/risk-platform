/**
 * One risk's declared residual under one plan.
 *
 * The wording on this form is doing real work. Nothing here is a claim that a treatment
 * works — it is a statement of what to simulate, and the difference between the two runs
 * is what says whether it worked. A form that said "effectiveness: 60%" would invite the
 * number to be read as a result, which is the mistake the whole module is arranged to
 * avoid.
 *
 * Factors are capped at 1. A treatment that makes a risk worse is a secondary risk and
 * belongs in the register as its own line; the database enforces the same bound, so a
 * value above one is refused twice rather than silently clamped.
 */

import { useEffect, useState } from "react";
import type { TreatmentKind, TreatmentMode, TreatmentWrite } from "../../mitigation-types";
import { DEFAULT_TREATMENT } from "../../mitigation-types";

interface Props {
  riskCode: string;
  title: string;
  value: TreatmentWrite | null;
  busy: boolean;
  onSave: (payload: TreatmentWrite) => void;
  onClear: () => void;
  onClose: () => void;
}

const TREATMENTS: { key: TreatmentKind; label: string; hint: string }[] = [
  {
    key: "reduce",
    label: "Reduce",
    hint: "The risk stays in the register at a smaller size or a lower likelihood.",
  },
  {
    key: "retire",
    label: "Retire",
    hint: "Removed from the post-mitigation register entirely. Nothing is simulated for it.",
  },
  {
    key: "accept",
    label: "Accept",
    hint: "Carried through unchanged, as a decision rather than an omission.",
  },
];

function num(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function factorProblem(value: number): string | null {
  if (!Number.isFinite(value)) return "must be a number";
  if (value <= 0) return "must be above zero — use Retire to eliminate a risk";
  if (value > 1) return "cannot be above 1 — a treatment that makes a risk worse is a new risk";
  return null;
}

export function TreatmentEditor({
  riskCode,
  title,
  value,
  busy,
  onSave,
  onClear,
  onClose,
}: Props) {
  const [draft, setDraft] = useState<TreatmentWrite>(value ?? DEFAULT_TREATMENT);

  useEffect(() => {
    setDraft(value ?? DEFAULT_TREATMENT);
  }, [value, riskCode]);

  function set<K extends keyof TreatmentWrite>(key: K, next: TreatmentWrite[K]) {
    setDraft((d) => ({ ...d, [key]: next }));
  }

  const factorIssues = [
    ["Probability", factorProblem(draft.p_factor)],
    ["Cost", factorProblem(draft.cost_factor)],
    ["Schedule", factorProblem(draft.sched_factor)],
  ].filter(([, problem]) => problem !== null) as [string, string][];

  const blocked = draft.treatment === "reduce" && draft.mode === "factor" && factorIssues.length > 0;

  return (
    <div className="mit-editor">
      <header className="mit-editor-head">
        <div>
          <span className="mit-code">{riskCode}</span>
          <h3 className="mit-editor-title">{title}</h3>
        </div>
        <button type="button" className="link" onClick={onClose}>
          Close
        </button>
      </header>

      <fieldset className="mit-fieldset">
        <legend>Treatment</legend>
        {TREATMENTS.map((t) => (
          <label className="mit-radio" key={t.key}>
            <input
              type="radio"
              name={`treatment-${riskCode}`}
              checked={draft.treatment === t.key}
              onChange={() => set("treatment", t.key)}
            />
            <span>
              <strong>{t.label}</strong>
              <em>{t.hint}</em>
            </span>
          </label>
        ))}
      </fieldset>

      {draft.treatment === "reduce" && (
        <>
          <fieldset className="mit-fieldset">
            <legend>How the residual is expressed</legend>
            {(
              [
                ["factor", "Scale what was elicited"],
                ["absolute", "State the residual numbers outright"],
              ] as [TreatmentMode, string][]
            ).map(([mode, label]) => (
              <label className="mit-radio inline" key={mode}>
                <input
                  type="radio"
                  name={`mode-${riskCode}`}
                  checked={draft.mode === mode}
                  onChange={() => set("mode", mode)}
                />
                <span>{label}</span>
              </label>
            ))}
          </fieldset>

          {draft.mode === "factor" ? (
            <div className="mit-grid">
              {(
                [
                  ["p_factor", "Probability ×"],
                  ["cost_factor", "Cost ×"],
                  ["sched_factor", "Schedule ×"],
                ] as [keyof TreatmentWrite, string][]
              ).map(([key, label]) => (
                <label key={String(key)}>
                  {label}
                  <input
                    type="number"
                    step="0.05"
                    min="0.01"
                    max="1"
                    value={String(draft[key] ?? 1)}
                    onChange={(e) => set(key, (num(e.target.value) ?? 1) as never)}
                  />
                </label>
              ))}
              <p className="mit-note">
                1.0 leaves a dimension exactly alone, which is how “this shortens the delay
                but does not touch the cost” gets said.
              </p>
            </div>
          ) : (
            <div className="mit-grid">
              <label>
                Residual probability
                <input
                  type="number"
                  step="0.01"
                  min="0.01"
                  max="1"
                  value={draft.residual_p ?? ""}
                  onChange={(e) => set("residual_p", num(e.target.value))}
                />
              </label>
              {(
                [
                  ["residual_cost_min", "Cost min"],
                  ["residual_cost_ml", "Cost most likely"],
                  ["residual_cost_max", "Cost max"],
                  ["residual_sched_min", "Days min"],
                  ["residual_sched_ml", "Days most likely"],
                  ["residual_sched_max", "Days max"],
                ] as [keyof TreatmentWrite, string][]
              ).map(([key, label]) => (
                <label key={String(key)}>
                  {label}
                  <input
                    type="number"
                    value={(draft[key] as number | null) ?? ""}
                    onChange={(e) => set(key, num(e.target.value) as never)}
                  />
                </label>
              ))}
              <p className="mit-note">
                Anything left blank keeps the elicited number. Shape and day basis always
                carry over — to change those, edit the residual estimate in Quantify after
                materialising.
              </p>
            </div>
          )}
        </>
      )}

      <label className="mit-rationale">
        Rationale
        <textarea
          rows={3}
          placeholder="Why this package leaves that behind…"
          value={draft.rationale ?? ""}
          onChange={(e) => set("rationale", e.target.value || null)}
        />
      </label>

      {factorIssues.length > 0 && (
        <ul className="mit-issues">
          {factorIssues.map(([label, problem]) => (
            <li key={label}>
              {label} factor {problem}.
            </li>
          ))}
        </ul>
      )}

      <div className="mit-editor-foot">
        <button
          type="button"
          className="btn primary"
          disabled={busy || blocked}
          onClick={() => onSave(draft)}
        >
          Save treatment
        </button>
        {value !== null && (
          <button type="button" className="link danger" disabled={busy} onClick={onClear}>
            Remove treatment
          </button>
        )}
        <span className="mit-note inline">
          Declared, not measured. What it buys is the difference between two runs.
        </span>
      </div>
    </div>
  );
}
