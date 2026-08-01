import type { RiskSensitivity } from "../../simulation-types";
import { fmtMoney, fmtPercent } from "./format";

/**
 * Which risks own the answer.
 *
 * Ranked and drawn on **variance share**, not on rank correlation. The shares decompose
 * the total exactly — every risk's cost share plus the burn-rate term's share sums to one
 * — so a bar can be read as "this risk owns eleven percent of the spread". Correlation
 * coefficients cannot be read that way: the bars add to nothing, and a rare severe risk
 * sorts below a frequent trivial one because correlation is blind to scale.
 *
 * Each bar is split. The dark segment is the risk's own cost draw; the lighter one is
 * what reaches the budget through delay and the burn rate. The split matters because a
 * risk with no direct cost at all can own half the contingency on a schedule-driven
 * project, and ranking on the cost share alone would sort it to the bottom of its own
 * tornado. The schedule half is apportioned rather than exact — delay is a maximum over
 * network paths and has no exact additive split — which is why it is labelled and why the
 * caption says so rather than leaving it implied.
 */

const ROW_H = 30;
const W = 720;
const LABEL_W = 128;
const VALUE_W = 96;
const PLOT_W = W - LABEL_W - VALUE_W - 16;

interface Props {
  rows: RiskSensitivity[];
  limit?: number;
  onSelect?: (riskId: number) => void;
  /** `variance` decomposes total cost; `delay` ranks what moves the finish date. */
  metric?: "variance" | "delay";
}

export default function Tornado({ rows, limit = 12, onSelect, metric = "variance" }: Props) {
  const delayMode = metric === "delay";
  const ranked = delayMode
    ? rows
        .filter((r) => r.spearman_delay != null)
        .slice()
        .sort((a, b) => Math.abs(b.spearman_delay ?? 0) - Math.abs(a.spearman_delay ?? 0))
    : rows;
  const shown = ranked.slice(0, limit);
  if (shown.length === 0) {
    return (
      <p className="sim-chart-empty">
        {delayMode
          ? "No risk is mapped to an activity, so nothing in the register moves the date."
          : "No risk carried enough variance to rank."}
      </p>
    );
  }

  const magnitude = (r: RiskSensitivity) =>
    delayMode ? Math.abs(r.spearman_delay ?? 0) : Math.abs(r.combined_variance_share);
  const peak = Math.max(...shown.map(magnitude), 1e-9);
  const height = shown.length * ROW_H + 28;
  const scale = (share: number) => (Math.abs(share) / peak) * PLOT_W;

  return (
    <figure className="sim-chart">
      <svg
        viewBox={`0 0 ${W} ${height}`}
        className="sim-svg"
        preserveAspectRatio="xMidYMin meet"
        role="img"
        aria-label={
          delayMode
            ? `Tornado: ${shown.length} risks ranked by rank correlation with project delay`
            : `Tornado: ${shown.length} risks ranked by share of total cost variance`
        }
      >
        <title>{delayMode ? "Risk drivers of the finish date" : "Risk drivers by variance share"}</title>
        {shown.map((row, i) => {
          const y = i * ROW_H + 6;
          const rho = row.spearman_delay ?? 0;
          const costShare = delayMode ? Math.abs(rho) : Math.abs(row.cost_variance_share);
          const schedShare = delayMode ? 0 : Math.abs(row.schedule_variance_share ?? 0);
          const costW = scale(costShare);
          const schedW = scale(schedShare);
          const negative = delayMode ? rho < 0 : row.combined_variance_share < 0;

          return (
            <g
              key={row.risk_id}
              className={onSelect ? "sim-tornado-row clickable" : "sim-tornado-row"}
              onClick={onSelect ? () => onSelect(row.risk_id) : undefined}
            >
              <title>
                {`${row.code} — ${row.title}\n`}
                {delayMode
                  ? `rank correlation with project delay ${rho.toFixed(2)}`
                  : `cost share ${fmtPercent(row.cost_variance_share)}, ` +
                    (row.schedule_variance_share == null
                      ? "drives no activity"
                      : `schedule share ${fmtPercent(row.schedule_variance_share)} (apportioned)`)}
                {`\noccurred in ${fmtPercent(row.realised_frequency)} of iterations`}
                {`\nmean contribution ${fmtMoney(row.mean_contribution)}`}
              </title>
              <text x={0} y={y + ROW_H / 2} className="sim-tornado-label">
                {row.code || `#${row.risk_id}`}
              </text>
              <rect
                x={LABEL_W}
                y={y + 5}
                width={Math.max(costW, 1)}
                height={ROW_H - 14}
                className={
                  (delayMode ? "sim-bar-sched" : "sim-bar-cost") + (negative ? " negative" : "")
                }
              />
              {schedW > 0 && (
                <rect
                  x={LABEL_W + costW}
                  y={y + 5}
                  width={schedW}
                  height={ROW_H - 14}
                  className="sim-bar-sched"
                />
              )}
              <text
                x={W - VALUE_W + 8}
                y={y + ROW_H / 2}
                className="sim-tornado-value"
              >
                {delayMode ? rho.toFixed(2) : fmtPercent(row.combined_variance_share)}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="sim-chart-caption">
        {delayMode ? (
          <span className="sim-chart-note">
            Rank correlation between each risk's own sampled delay and the project delay.
            Ranking only — these bars do not add to anything, because delay is a maximum over
            network paths and has no exact split among the risks that drive it. A risk absent
            here drives no activity; one absent from the cost tornado carries no direct cost.
          </span>
        ) : (
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
