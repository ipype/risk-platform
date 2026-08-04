import type { CurveRow } from "../../roi-types";
import { fmtCompact, fmtUnits } from "../sim/format";

/**
 * Two cumulative distributions on one axis.
 *
 * The single most useful picture in a before/after review, and the reason it is worth
 * hand-rolling: two curves side by side in separate charts are read as two answers, and
 * two curves on one axis are read as one movement. The gap between them at a given
 * confidence level *is* the reduction, so the number in the headline card is visible as a
 * distance rather than taken on trust.
 *
 * The horizontal bar at the chosen percentile is drawn deliberately: a reader's eye goes
 * to the widest part of the gap, which is usually in the tail and is usually not the
 * percentile anybody is quoting.
 */

const W = 720;
const H = 320;
const PAD = { top: 16, right: 20, bottom: 44, left: 76 };

const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

interface Props {
  curve: CurveRow[];
  percentile: number;
  units: string;
}

export function CurveOverlay({ curve, percentile, units }: Props) {
  if (curve.length < 2) {
    return <p className="sim-chart-empty">Not enough points to draw the two curves.</p>;
  }

  const values = curve.flatMap((row) => [row.before, row.after]);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  // A pair of degenerate runs would divide by zero and render two vertical lines off the
  // plot. One unit of width keeps it drawable and visibly flat.
  const span = hi > lo ? hi - lo : 1;

  const x = (v: number) => PAD.left + ((v - lo) / span) * PLOT_W;
  const y = (p: number) => PAD.top + (1 - p / 100) * PLOT_H;

  const line = (pick: (row: CurveRow) => number) =>
    curve
      .map((row, i) => `${i === 0 ? "M" : "L"}${x(pick(row)).toFixed(2)},${y(row.p).toFixed(2)}`)
      .join(" ");

  const marked = curve.reduce((best, row) =>
    Math.abs(row.p - percentile) < Math.abs(best.p - percentile) ? row : best
  );

  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + f * span);

  return (
    <figure className="roi-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cost distribution before and after mitigation">
        {[0, 25, 50, 75, 100].map((p) => (
          <g key={p}>
            <line className="roi-grid" x1={PAD.left} x2={W - PAD.right} y1={y(p)} y2={y(p)} />
            <text className="roi-axis" x={PAD.left - 10} y={y(p) + 4} textAnchor="end">
              {p}%
            </text>
          </g>
        ))}

        {ticks.map((v) => (
          <text key={v} className="roi-axis" x={x(v)} y={H - PAD.bottom + 20} textAnchor="middle">
            {fmtCompact(v)}
          </text>
        ))}

        {/* The gap at the quoted percentile, drawn before the curves so they sit on top. */}
        <line
          className="roi-gap"
          x1={x(marked.after)}
          x2={x(marked.before)}
          y1={y(marked.p)}
          y2={y(marked.p)}
        />

        <path className="roi-curve before" d={line((r) => r.before)} fill="none" />
        <path className="roi-curve after" d={line((r) => r.after)} fill="none" />

        <circle className="roi-dot before" cx={x(marked.before)} cy={y(marked.p)} r={4} />
        <circle className="roi-dot after" cx={x(marked.after)} cy={y(marked.p)} r={4} />

        <text className="roi-axis" x={W / 2} y={H - 6} textAnchor="middle">
          Total cost
        </text>
      </svg>

      <figcaption className="roi-legend">
        <span className="roi-key before">Baseline</span>
        <span className="roi-key after">After mitigation</span>
        <span className="roi-key gap">
          Gap at P{percentile}: {fmtUnits(marked.before - marked.after, units)}
        </span>
      </figcaption>
    </figure>
  );
}
