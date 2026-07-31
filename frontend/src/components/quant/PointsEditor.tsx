import type { DistName } from "../../quant/types";

/**
 * Point editor for the two shapes that are not a three-point.
 *
 * One table for both, because the columns are the same and only the reading of `p` differs:
 * cumulative probability climbing to 1 for a curve, an outcome's own probability summing to
 * 1 for a discrete set. The running total under the discrete table is there because "these
 * must sum to one" is the rule people break, and finding out at save time means re-deriving
 * which row was wrong.
 */

interface Props {
  dist: DistName;
  points: { x: string; p: string }[];
  disabled?: boolean;
  unit: string;
  onChange: (next: { x: string; p: string }[]) => void;
}

export default function PointsEditor({ dist, points, disabled, unit, onChange }: Props) {
  const cumulative = dist === "cumulative";

  const update = (i: number, key: "x" | "p", v: string) => {
    const next = points.map((p, idx) => (idx === i ? { ...p, [key]: v } : p));
    onChange(next);
  };

  const add = () => onChange([...points, { x: "", p: "" }]);
  const remove = (i: number) => onChange(points.filter((_, idx) => idx !== i));

  const total = points.reduce((sum, p) => {
    const v = Number(p.p);
    return Number.isFinite(v) ? sum + v : sum;
  }, 0);
  const totalOff = !cumulative && points.length > 0 && Math.abs(total - 1) > 1e-6;

  return (
    <div className="qnt-points">
      <table className="qnt-points-table">
        <thead>
          <tr>
            <th scope="col">Value ({unit})</th>
            <th scope="col">{cumulative ? "Cumulative probability" : "Probability"}</th>
            <th scope="col">
              <span className="qnt-sr">Remove</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {points.map((p, i) => (
            <tr key={i}>
              <td>
                <input
                  className="qnt-input"
                  inputMode="decimal"
                  value={p.x}
                  disabled={disabled}
                  aria-label={`Value for point ${i + 1}`}
                  onChange={(e) => update(i, "x", e.target.value)}
                />
              </td>
              <td>
                <input
                  className="qnt-input"
                  inputMode="decimal"
                  value={p.p}
                  disabled={disabled}
                  aria-label={`Probability for point ${i + 1}`}
                  onChange={(e) => update(i, "p", e.target.value)}
                />
              </td>
              <td>
                <button
                  type="button"
                  className="qnt-mini"
                  disabled={disabled || points.length <= 2}
                  onClick={() => remove(i)}
                  aria-label={`Remove point ${i + 1}`}
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="qnt-points-foot">
        <button type="button" className="qnt-mini" disabled={disabled} onClick={add}>
          Add point
        </button>
        {cumulative ? (
          <span className="qnt-muted">Must run from 0 to 1.</span>
        ) : (
          <span className={totalOff ? "qnt-warn-inline" : "qnt-muted"}>
            Total {total.toFixed(3)}
            {totalOff ? " — must be 1" : ""}
          </span>
        )}
      </div>
    </div>
  );
}
