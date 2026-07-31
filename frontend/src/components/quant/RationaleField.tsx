import { useState } from "react";
import type { QuantSource } from "../../quant/types";
import type { DraftRationale } from "../../quant/draft";

/**
 * The justification behind one of the three numbers.
 *
 * Collapsed by default. Three shapes across two dimensions is nine numbers on screen, and
 * nine permanently-open textareas turns a form an SME can fill in during a workshop into
 * one they abandon. The dot on the toggle is what makes an unwritten rationale visible
 * without opening it.
 *
 * `source` is the part that matters later. When an agent starts drafting these, its wording
 * stays marked as the agent's until somebody adopts it — an unattributed suggestion that
 * reads as the analyst's own judgement is how a review signs off on reasoning nobody did.
 */

const SOURCE_LABELS: Record<QuantSource, string> = {
  sme: "SME judgement",
  historical: "Historical data",
  analyst: "Analyst",
  agent_proposal: "AI proposal",
};

const OWNABLE: QuantSource[] = ["sme", "historical", "analyst"];

interface Props {
  label: string;
  value: DraftRationale;
  disabled?: boolean;
  onChange: (next: DraftRationale) => void;
}

export default function RationaleField({ label, value, disabled, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const filled = value.text.trim().length > 0;
  const proposed = value.source === "agent_proposal";

  return (
    <div className="qnt-rationale">
      <button
        type="button"
        className={open ? "qnt-why qnt-why-open" : "qnt-why"}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title={filled ? "Rationale recorded" : "No rationale yet"}
      >
        Why?
        {filled && <span className={proposed ? "qnt-dot qnt-dot-ai" : "qnt-dot"} aria-hidden />}
      </button>

      {open && (
        <div className="qnt-rationale-body">
          <label className="qnt-sr" htmlFor={`rat-${label}`}>
            Rationale for {label}
          </label>
          <textarea
            id={`rat-${label}`}
            className="qnt-textarea"
            rows={3}
            value={value.text}
            disabled={disabled}
            placeholder={`Why is the ${label.toLowerCase()} this number?`}
            onChange={(e) => onChange({ ...value, text: e.target.value })}
          />

          <div className="qnt-rationale-foot">
            {proposed ? (
              <>
                <span className="qnt-badge qnt-badge-ai">AI proposal</span>
                <button
                  type="button"
                  className="qnt-mini"
                  disabled={disabled}
                  onClick={() => onChange({ ...value, source: "analyst" })}
                >
                  Adopt as mine
                </button>
              </>
            ) : (
              <select
                className="qnt-select qnt-select-sm"
                value={value.source}
                disabled={disabled}
                aria-label={`Source of the ${label.toLowerCase()} rationale`}
                onChange={(e) => onChange({ ...value, source: e.target.value as QuantSource })}
              >
                {OWNABLE.map((s) => (
                  <option key={s} value={s}>
                    {SOURCE_LABELS[s]}
                  </option>
                ))}
              </select>
            )}
            {value.author && <span className="qnt-muted">{value.author}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
