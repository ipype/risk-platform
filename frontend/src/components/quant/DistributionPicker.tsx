import { useState } from "react";
import type { DistName, DistributionGuidance } from "../../quant/types";

/**
 * Shape selector, with the guidance the server supplies.
 *
 * The advice is fetched rather than written here so the picker, the validator, and the
 * docs cannot drift apart — adding a shape on the backend makes it appear here, with its
 * guidance, and nothing needs editing twice.
 *
 * "Use when" is always visible because that is the line that changes the decision. The
 * caution is behind a toggle: it is the most important text for someone who has already
 * chosen wrongly and the least useful for someone who has not chosen yet.
 */

interface Props {
  value: DistName;
  options: DistributionGuidance[];
  disabled?: boolean;
  onChange: (next: DistName) => void;
}

export default function DistributionPicker({ value, options, disabled, onChange }: Props) {
  const [showAll, setShowAll] = useState(false);
  const guidance = options.find((o) => o.value === value);

  return (
    <div className="qnt-picker">
      <label className="qnt-field">
        <span className="qnt-label">Distribution</span>
        <select
          className="qnt-select"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value as DistName)}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>

      {guidance && (
        <div className="qnt-guidance">
          <p className="qnt-guidance-summary">{guidance.summary}</p>
          <p className="qnt-guidance-line">
            <strong>Use when</strong> {guidance.use_when}
          </p>
          {showAll && (
            <>
              <p className="qnt-guidance-line">
                <strong>Avoid when</strong> {guidance.avoid_when}
              </p>
              <p className="qnt-guidance-line qnt-guidance-caution">
                <strong>Watch out</strong> {guidance.caution}
              </p>
            </>
          )}
          <button type="button" className="qnt-mini" onClick={() => setShowAll((v) => !v)}>
            {showAll ? "Less" : "When not to, and what it costs"}
          </button>
        </div>
      )}
    </div>
  );
}
