import type { RiskSensitivity } from "../../simulation-types";
import { fmtMoney, fmtPercent } from "./format";

/**
 * Which risks own the answer — and which answer.
 *
 * Three readings, because a register has three different top risks and quoting one of
 * them as "the" top risk is how a mitigation budget gets spent on the wrong thing.
 *
 * **Cost** is the exact variance decomposition of total cost against each risk's own cost
 * draw. `sum_i cov(x_i, total) / var(total) == 1` for any sum, correlated or not, so a bar
 * reads as "this risk owns eleven percent of the spread in the budget". It says nothing
 * about the date, and a risk that reaches the budget only through delay is absent from it.
 *
 * **Schedule** is the same estimator pointed at project delay instead. These bars do *not*
 * add to one and are not renormalised to: delay is a maximum over network paths, not a sum
 * of the risks driving it, so the shortfall is the schedule's own duration uncertainty.
 * That shortfall is printed under the chart, because a register credited with a date it
 * did not drive is the finding, not a rounding error.
 *
 * **Both** is what the contingency actually rests on: each risk's cost share plus the
 * portion of the burn-rate term apportioned to it. The split segments say which half is
 * which. The schedule half is apportioned by covariance weight rather than measured — the
 * subtotal is exact, the attribution is not — which is why it is labelled and why the
 * caption says so rather than leaving it implied.
 *
 * Ranked and drawn on shares, never on rank correlation. Correlation coefficients cannot
 * be read as a breakdown: the bars add to nothing, and a rare severe risk sorts below a
 * frequent trivial one because correlation is blind to scale. Spearman is carried in the
 * tooltip, where it answers the other question people ask — how *reliably* this risk moves
 * with the outcome, independent of size.
 */

const ROW_H = 30;
const W = 720;
const LABEL_W = 128;
const VALUE_W = 96;
const PLOT_W = W - LABEL_W - VALUE_W - 16;

export type TornadoMetric = "cost" | "schedule" | "combined";

interface Props {
  rows: RiskSensitivity[];
  limit?: number;
  onSelect?: (riskId: number) => void;
  metric?: TornadoMetric;
}

export default function Tornado({ rows, limit = 12, onSelect, metric = "combined" }: Props) {
  // A run from before engine 1.2.0 carries no delay shares at all. That is a different
  // thing from a register where nothing drives the network, and the fallback says which.
  const hasDelayShares =
    metric === "schedule" && rows.some((r) => r.delay_variance_share != null);
  const legacySchedule = metric === "schedule" && !hasDelayShares;

  const magnitude = (r: RiskSensitivity): number => {
    if (metric === "cost") return Math.abs(r.cost_variance_share);
    if (metric === "combined") return Math.abs(r.combined_variance_share);
    return legacySchedule
      ? Math.abs(r.spearman_delay ?? 0)
      : Math.abs(r.delay_variance_share ?? 0);
  };

  const eligible = rows.filter((r) => {
    if (metric === "schedule") {
      return legacySchedule ? r.spearman_delay != null : r.delay_variance_share != null;
    }
    return true;
  });

  const ranked =
    metric === "combined"
      ? eligible
      : eligible.slice().sort((a, b) => magnitude(b) - magnitude(a));
  const shown = ranked.slice(0, limit);

  if (shown.length === 0) {
    return (
      <p className="sim-chart-empty">
        {metric === "schedule"
          ? "No risk is mapped to an activity, so nothing in the register moves the date."
          : "No risk carried enough variance to rank."}
      </p>
    );
  }

  const peak = Math.max(...shown.map(magnitude), 1e-9);
  const height = shown.length * ROW_H + 28;
  const scale = (share: number) => (Math.abs(share) / peak) * PLOT_W;

  // Everything that drives the network, not just the twelve on screen: the remainder is a
  // statement about the whole register and truncating it would understate the schedule's
  // own share of its own spread.
  const explained = eligible.reduce((sum, r) => sum + (r.delay_variance_share ?? 0), 0);

  const heading =
    metric === "cost"
      ? "Risk drivers of the budget"
      : metric === "schedule"
        ? "Risk drivers of the finish date"
        : "Risk drivers of the contingency";

  return (
    <figure className="sim-chart">
      <svg
        viewBox={`0 0 ${W} ${height}`}
        className="sim-svg"
        preserveAspectRatio="xMidYMin meet"
        role="img"
        aria-label={
          metric === "cost"
            ? `Tornado: ${shown.length} risks ranked by share of total cost variance from their own cost draw`
            : metric === "schedule"
              ? `Tornado: ${shown.length} risks ranked by share of project delay variance`
              : `Tornado: ${shown.length} risks ranked by combined share of total cost variance`
        }
      >
        <title>{heading}</title>
        {shown.map((row, i) => {
          const y = i * ROW_H + 6;
          const rho = row.spearman_delay ?? 0;

          // Only the combined view splits a bar. The other two measure one thing each, and
          // a split there would imply a decomposition that was never computed.
          const primary =
            metric === "cost"
              ? Math.abs(row.cost_variance_share)
              : metric === "schedule"
                ? magnitude(row)
                : Math.abs(row.cost_variance_share);
          const secondary =
            metric === "combined" ? Math.abs(row.schedule_variance_share ?? 0) : 0;
          const primaryW = scale(primary);
          const secondaryW = scale(secondary);

          const signed =
            metric === "cost"
              ? row.cost_variance_share
              : metric === "schedule"
                ? legacySchedule
                  ? rho
                  : (row.delay_variance_share ?? 0)
                : row.combined_variance_share;
          const negative = signed < 0;
          const scheduleTone = metric === "schedule";

          // One string, not a child array. An SVG `<title>` holding several nodes renders
          // its own markup as text in every browser, which is how this tooltip spent a
          // release showing angle brackets to anyone who hovered a bar.
          const tip = [
            `${row.code || `#${row.risk_id}`} — ${row.title}`,
            metric === "schedule"
              ? legacySchedule
                ? `rank correlation with project delay ${rho.toFixed(2)}`
                : `share of delay spread ${fmtPercent(row.delay_variance_share)}, rank correlation ${rho.toFixed(2)}`
              : `cost share ${fmtPercent(row.cost_variance_share)}`,
            ...(metric === "combined"
              ? [
                  row.schedule_variance_share == null
                    ? "drives no activity"
                    : `schedule share ${fmtPercent(row.schedule_variance_share)} (apportioned)`,
                ]
              : []),
            `occurred in ${fmtPercent(row.realised_frequency)} of iterations`,
            `mean cost contribution ${fmtMoney(row.mean_contribution)}`,
          ].join("\n");

          return (
            <g
              key={row.risk_id}
              className={onSelect ? "sim-tornado-row clickable" : "sim-tornado-row"}
              onClick={onSelect ? () => onSelect(row.risk_id) : undefined}
            >
              <title>{tip}</title>
              <text x={0} y={y + ROW_H / 2} className="sim-tornado-label">
                {row.code || `#${row.risk_id}`}
              </text>
              <rect
                x={LABEL_W}
                y={y + 5}
                width={Math.max(primaryW, 1)}
                height={ROW_H - 14}
                className={
                  (scheduleTone ? "sim-bar-sched" : "sim-bar-cost") +
                  (negative ? " negative" : "")
                }
              />
              {secondaryW > 0 && (
                <rect
                  x={LABEL_W + primaryW}
                  y={y + 5}
                  width={secondaryW}
                  height={ROW_H - 14}
                  className="sim-bar-sched"
                />
              )}
              <text x={W - VALUE_W + 8} y={y + ROW_H / 2} className="sim-tornado-value">
                {legacySchedule ? rho.toFixed(2) : fmtPercent(signed)}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="sim-chart-caption">
        {metric === "cost" && (
          <span className="sim-chart-note">
            Exact share of the variance of total cost owned by each risk's own cost draw.
            These decompose the whole: a risk absent here carries no direct cost, and one
            that reaches the budget only through delay will not appear no matter how much
            of the contingency it owns. Use <em>Both</em> for that.
          </span>
        )}
        {metric === "schedule" && !legacySchedule && (
          <span className="sim-chart-note">
            Share of the variance of <strong>project delay</strong> — the date, not the
            budget. These deliberately do not sum to one: delay is a maximum over network
            paths rather than a sum of the risks driving it. The register explains{" "}
            <strong>{fmtPercent(explained)}</strong> of the spread in the finish date; the
            rest is the schedule's own duration uncertainty and which path happened to be
            critical.{" "}
            {explained < 0.4
              ? "Most of the date risk is therefore in the schedule itself, not in the register — mitigating these risks will not move the finish much."
              : ""}
          </span>
        )}
        {metric === "schedule" && legacySchedule && (
          <span className="sim-chart-note">
            This run predates the delay variance share, so these bars are rank correlations
            between each risk's own sampled delay and the project delay. Ranking only — they
            do not add to anything. Re-run it, same seed and inputs, to read contributions.
          </span>
        )}
        {metric === "combined" && (
          <>
            <span className="sim-key">
              <span className="sim-swatch cost" /> cost draw
            </span>
            <span className="sim-key">
              <span className="sim-swatch sched" /> through delay (apportioned)
            </span>
            <span className="sim-chart-note">
              Shares decompose the variance of total cost. The schedule half is divided among
              driving risks by covariance weight: the subtotal is exact, the attribution is
              not.
            </span>
          </>
        )}
      </figcaption>
    </figure>
  );
}
