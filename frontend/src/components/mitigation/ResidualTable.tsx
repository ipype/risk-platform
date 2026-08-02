/**
 * The residual register, before and after, one row per risk.
 *
 * Every risk in scope with a baseline is here, including the ones nobody has treated.
 * That is not a completeness flourish: an untreated risk is materialised at full size and
 * simulated at full size, and a table that hid it would show a residual register smaller
 * than the one the engine reads.
 *
 * The two impact columns are expected values — probability times mean — and the header
 * says so. They are here because a residual table with no numbers cannot be reviewed, and
 * because means, unlike percentiles, are legitimately additive. They are not contingency
 * and nothing on this screen sums them into one.
 */

import type { ResidualLine } from "../../mitigation-types";
import { fmtDays, fmtMoney, fmtPercent } from "../sim/format";

interface Props {
  lines: ResidualLine[];
  selectedRiskId: number | null;
  onSelect: (riskId: number) => void;
}

const LABELS: Record<string, string> = {
  reduce: "Reduce",
  retire: "Retire",
  accept: "Accept",
  untreated: "Untreated",
};

function delta(before: number | null, after: number | null): string {
  if (before == null || after == null || before === 0) return "";
  const change = (after - before) / before;
  if (Math.abs(change) < 0.0005) return "";
  return `${change > 0 ? "+" : ""}${(change * 100).toFixed(0)}%`;
}

export function ResidualTable({ lines, selectedRiskId, onSelect }: Props) {
  if (lines.length === 0) {
    return (
      <p className="mit-empty">
        No pre-mitigation estimates in this scope yet. A residual register is built from
        the elicited baseline, so quantify the register first.
      </p>
    );
  }

  return (
    <div className="mit-tablewrap">
      <table className="mit-table">
        <thead>
          <tr>
            <th scope="col">Risk</th>
            <th scope="col">Treatment</th>
            <th scope="col" className="num">
              Probability
            </th>
            <th scope="col" className="num">
              Expected cost
            </th>
            <th scope="col" className="num">
              Expected delay
            </th>
            <th scope="col">Flags</th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => {
            const untreated = line.treatment === "untreated";
            return (
              <tr
                key={line.risk_id}
                className={[
                  selectedRiskId === line.risk_id ? "selected" : "",
                  line.retired ? "retired" : "",
                  untreated ? "untreated" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onClick={() => onSelect(line.risk_id)}
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(line.risk_id);
                  }
                }}
              >
                <th scope="row">
                  <span className="mit-code">{line.risk_code}</span>
                  <span className="mit-rowtitle">{line.title}</span>
                </th>
                <td>
                  <span className={`mit-pill ${line.treatment}`}>
                    {LABELS[line.treatment] ?? line.treatment}
                  </span>
                </td>
                <td className="num">
                  <span className="mit-before">{fmtPercent(line.base_p)}</span>
                  <span className="mit-arrow" aria-hidden="true">
                    →
                  </span>
                  <span className="mit-after">
                    {line.retired ? "—" : fmtPercent(line.residual_p)}
                  </span>
                </td>
                <td className="num">
                  <span className="mit-before">{fmtMoney(line.base_cost_ev)}</span>
                  <span className="mit-arrow" aria-hidden="true">
                    →
                  </span>
                  <span className="mit-after">
                    {line.retired ? "—" : fmtMoney(line.residual_cost_ev)}
                  </span>
                  <span className="mit-delta">
                    {line.retired ? "" : delta(line.base_cost_ev, line.residual_cost_ev)}
                  </span>
                </td>
                <td className="num">
                  <span className="mit-before">{fmtDays(line.base_sched_ev)}</span>
                  <span className="mit-arrow" aria-hidden="true">
                    →
                  </span>
                  <span className="mit-after">
                    {line.retired ? "—" : fmtDays(line.residual_sched_ev)}
                  </span>
                </td>
                <td>
                  {line.locked && (
                    <span className="mit-flag locked" title="A run froze this residual">
                      locked
                    </span>
                  )}
                  {line.edited_since && !line.locked && (
                    <span
                      className="mit-flag edited"
                      title="Changed since this plan last wrote it"
                    >
                      changed
                    </span>
                  )}
                  {line.issues.length > 0 && (
                    <span className="mit-flag issue" title={line.issues.join(" ")}>
                      {line.issues.length} note{line.issues.length > 1 ? "s" : ""}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
