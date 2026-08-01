import { useMemo, useState } from "react";
import type { ActivityCriticality } from "../../simulation-types";
import { fmtDays, fmtPercent } from "./format";

/**
 * Which activities decide the finish date.
 *
 * Three columns answer that question and they disagree, which is why all three are here
 * and why the table sorts on any of them.
 *
 * **Criticality index** is how often the activity sat on the critical path. It is exact
 * and it is not, on its own, a management instruction: an activity can be critical in
 * every iteration and be worth nothing to work, because its duration never moves.
 *
 * **Cruciality** is that index times the absolute correlation between the activity's
 * duration and the finish. It separates "always critical" from "decides the date".
 *
 * **SSI** — the schedule sensitivity index — is the index times the ratio of the
 * activity's duration spread to the project's. Correlation is blind to scale; the spread
 * ratio is not. On a network of independent durations the two agree exactly. They part
 * company wherever a shared risk driver correlates durations: the correlation sees the
 * whole of the shared cause's effect on the finish and credits it to each driven
 * activity, while the spread ratio counts only what that activity itself contributes.
 * Neither is wrong. Ranking on one and deleting the other loses a real reading, which is
 * also why the engine retains the top of both when it truncates.
 *
 * A null sensitivity is a dash rather than a zero. Zero is a measured result meaning the
 * duration moved and the finish did not care; null means the duration never moved at all
 * and there was nothing to measure. The SSI in that row is a true zero — the spread
 * genuinely is zero — and the two columns disagreeing that way is correct, not a bug.
 */

const PAGE = 15;

type SortKey =
  | "criticality_index"
  | "cruciality"
  | "schedule_sensitivity_index"
  | "mean_total_float_days";

const COLUMNS: { key: SortKey; label: string; hint: string }[] = [
  {
    key: "criticality_index",
    label: "Criticality",
    hint: "Share of iterations on the critical path",
  },
  {
    key: "mean_total_float_days",
    label: "Mean float",
    hint: "Average total float across iterations",
  },
  {
    key: "cruciality",
    label: "Cruciality",
    hint: "Criticality index times absolute duration/finish correlation",
  },
  {
    key: "schedule_sensitivity_index",
    label: "SSI",
    hint: "Criticality index times duration spread over project spread",
  },
];

interface Props {
  rows: ActivityCriticality[];
}

export default function CriticalityTable({ rows }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [sort, setSort] = useState<SortKey>("cruciality");

  const sorted = useMemo(() => {
    // Float is the one column where small is interesting; everything else ranks high-first.
    const dir = sort === "mean_total_float_days" ? 1 : -1;
    return rows
      .slice()
      .sort(
        (a, b) => dir * (a[sort] - b[sort]) || a.activity_id.localeCompare(b.activity_id)
      );
  }, [rows, sort]);

  if (rows.length === 0) {
    return (
      <p className="sim-chart-empty">No network was simulated, so nothing was on a path.</p>
    );
  }

  const shown = expanded ? sorted : sorted.slice(0, PAGE);
  const anySensitivity = rows.some((r) => r.duration_sensitivity != null);
  const disagree =
    rows.length > 1 &&
    leader(rows, "cruciality") !== leader(rows, "schedule_sensitivity_index");

  return (
    <div className="sim-table-wrap">
      <table className="sim-table">
        <thead>
          <tr>
            <th scope="col">Activity</th>
            <th scope="col">Name</th>
            <th scope="col" className="num">
              Sensitivity
            </th>
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                scope="col"
                className="num"
                aria-sort={sort === c.key ? "descending" : "none"}
              >
                <button
                  type="button"
                  className={sort === c.key ? "sim-sort active" : "sim-sort"}
                  title={c.hint}
                  onClick={() => setSort(c.key)}
                >
                  {c.label}
                </button>
              </th>
            ))}
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
              <td className="num">
                {row.duration_sensitivity == null ? "—" : row.duration_sensitivity.toFixed(2)}
              </td>
              <td className="num">
                <span className="sim-ci">
                  <span
                    className="sim-ci-bar"
                    style={{ width: `${Math.round(row.criticality_index * 100)}%` }}
                  />
                  <span className="sim-ci-text">{fmtPercent(row.criticality_index)}</span>
                </span>
              </td>
              <td className="num">{fmtDays(row.mean_total_float_days)}</td>
              <td className="num">{row.cruciality.toFixed(3)}</td>
              <td className="num" title={`duration sd ${fmtDays(row.duration_sd_days)}`}>
                {row.schedule_sensitivity_index.toFixed(3)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {!anySensitivity && (
        <p className="sim-note">
          Every duration sensitivity is blank because no activity carries elicited duration
          uncertainty yet — only discrete risk events are moving this network. The criticality
          index is still real; the ranking between equally critical activities is not.
        </p>
      )}

      {anySensitivity && disagree && (
        <p className="sim-note">
          Cruciality and SSI put different activities at the top. That is the shared-driver
          case: correlation credits each driven activity with the whole of the risk's effect
          on the finish, while the spread ratio counts only that activity's own share of it.
          Work the SSI list for duration estimates and the cruciality list for the drivers
          behind them.
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

function leader(rows: ActivityCriticality[], key: SortKey): string {
  let best = rows[0];
  for (const r of rows) if (r[key] > best[key]) best = r;
  return best.activity_id;
}
