import { useState } from "react";
import type { ActivityCriticality } from "../../simulation-types";
import { fmtDays, fmtPercent } from "./format";

/**
 * Which activities decide the finish date.
 *
 * Sorted on **cruciality**, not on the criticality index, and the difference is the whole
 * point of the table. An activity can sit on the critical path in every single iteration
 * and still be worth nothing to manage, because its duration never moves — a fixed
 * curing period, a contractual notice window. Cruciality is the criticality index times
 * the absolute duration sensitivity, so it separates "always critical" from "decides the
 * date".
 *
 * A null sensitivity is printed as a dash rather than as zero. Zero is a measured result
 * meaning the duration moved and the finish did not care; null means the duration never
 * moved at all and there was nothing to measure. Until activity-duration uncertainty is
 * elicited, every real activity here will read null — which is honest, and is the reason
 * the empty state says so instead of showing a table of zeroes.
 */

const PAGE = 15;

interface Props {
  rows: ActivityCriticality[];
}

export default function CriticalityTable({ rows }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (rows.length === 0) {
    return <p className="sim-chart-empty">No network was simulated, so nothing was on a path.</p>;
  }

  const shown = expanded ? rows : rows.slice(0, PAGE);
  const anySensitivity = rows.some((r) => r.duration_sensitivity != null);

  return (
    <div className="sim-table-wrap">
      <table className="sim-table">
        <thead>
          <tr>
            <th scope="col">Activity</th>
            <th scope="col">Name</th>
            <th scope="col" className="num">
              Criticality
            </th>
            <th scope="col" className="num">
              Mean float
            </th>
            <th scope="col" className="num">
              Sensitivity
            </th>
            <th scope="col" className="num">
              Cruciality
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.map((row) => (
            <tr key={row.activity_id} className={row.is_inserted ? "sim-inserted" : undefined}>
              <td>
                {row.code || row.activity_id}
                {row.is_inserted && <span className="sim-tag">risk</span>}
              </td>
              <td className="sim-name">{row.name || "—"}</td>
              <td className="num">{fmtPercent(row.criticality_index)}</td>
              <td className="num">{fmtDays(row.mean_total_float_days)}</td>
              <td className="num">
                {row.duration_sensitivity == null
                  ? "—"
                  : row.duration_sensitivity.toFixed(2)}
              </td>
              <td className="num">{row.cruciality.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {!anySensitivity && (
        <p className="sim-note">
          Every duration sensitivity is blank because no activity carries elicited duration
          uncertainty yet — only discrete risk events are moving this network. The
          criticality index is still real; the ranking between equally critical activities
          is not.
        </p>
      )}

      {rows.length > PAGE && (
        <button className="link" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show top 15" : `Show all ${rows.length}`}
        </button>
      )}
    </div>
  );
}
