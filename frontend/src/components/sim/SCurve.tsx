import type { SeriesSummary } from "../../simulation-types";
import { fmtCompact, fmtUnits } from "./format";

/**
 * The cumulative distribution of one simulated quantity.
 *
 * Hand-rolled SVG for the same reason the Gantt is: `package.json` carries two runtime
 * dependencies and a charting library would be the third for one curve. Nothing here
 * needs a library — it is a polyline through a hundred points the server already sorted.
 *
 * The S-curve sits next to the percentile table rather than replacing it because the two
 * answer different questions. The table gives a number to put in a budget; the curve
 * shows how steeply the answer changes either side of it, which is what says whether the
 * number deserves the confidence it is about to be given. A flat curve at P80 means the
 * next ten percent of confidence is nearly free; a steep one means it is not.
 */

const W = 720;
const H = 300;
const PAD = { top: 16, right: 20, bottom: 40, left: 72 };

const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

interface Props {
  series: SeriesSummary;
  /** Percentiles to call out on the curve. */
  markers?: number[];
  title?: string;
}

export default function SCurve({ series, markers = [50, 80], title }: Props) {
  const points = series.s_curve;
  if (points.length < 2) {
    return <p className="sim-chart-empty">Not enough points to draw a curve.</p>;
  }

  const lo = points[0].x;
  const hi = points[points.length - 1].x;
  // A degenerate series — every iteration identical — would divide by zero and render a
  // vertical line off the plot. One unit of width keeps it drawable and visibly flat.
  const span = hi > lo ? hi - lo : 1;

  const x = (v: number) => PAD.left + ((v - lo) / span) * PLOT_W;
  const y = (p: number) => PAD.top + (1 - p) * PLOT_H;

  const path = points.map((pt, i) => `${i === 0 ? "M" : "L"}${x(pt.x).toFixed(2)},${y(pt.p).toFixed(2)}`).join(" ");
  const area = `${path} L${x(hi).toFixed(2)},${y(0).toFixed(2)} L${x(lo).toFixed(2)},${y(0).toFixed(2)} Z`;

  const byPercentile = new Map(series.percentiles.map((p) => [p.p, p.value]));
  const called = markers
    .map((p) => ({ p, value: byPercentile.get(p) }))
    .filter((m): m is { p: number; value: number } => m.value != null);

  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => lo + f * span);
  const yTicks = [0, 0.2, 0.4, 0.6, 0.8, 1];

  const label = title ?? series.label;

  return (
    <figure className="sim-chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="sim-svg"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`${label}: cumulative probability curve from ${fmtUnits(lo, series.units)} to ${fmtUnits(hi, series.units)}`}
      >
        <title>{label}</title>

        {yTicks.map((p) => (
          <g key={`y${p}`}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={y(p)}
              y2={y(p)}
              className="sim-grid"
            />
            <text x={PAD.left - 10} y={y(p) + 4} className="sim-axis-text" textAnchor="end">
              {Math.round(p * 100)}%
            </text>
          </g>
        ))}

        {xTicks.map((v, i) => (
          <text
            key={`x${i}`}
            x={x(v)}
            y={H - PAD.bottom + 20}
            className="sim-axis-text"
            textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
          >
            {fmtCompact(v)}
          </text>
        ))}

        <path d={area} className="sim-curve-fill" />
        <path d={path} className="sim-curve-line" />

        {called.map((m) => (
          <g key={m.p}>
            <line
              x1={x(m.value)}
              x2={x(m.value)}
              y1={y(m.p / 100)}
              y2={y(0)}
              className="sim-marker-line"
            />
            <line
              x1={PAD.left}
              x2={x(m.value)}
              y1={y(m.p / 100)}
              y2={y(m.p / 100)}
              className="sim-marker-line"
            />
            <circle cx={x(m.value)} cy={y(m.p / 100)} r={4} className="sim-marker-dot" />
            <text
              x={x(m.value)}
              y={y(m.p / 100) - 10}
              className="sim-marker-text"
              textAnchor={m.value > lo + span * 0.75 ? "end" : "start"}
            >
              P{m.p} {fmtUnits(m.value, series.units)}
            </text>
          </g>
        ))}

        <line
          x1={PAD.left}
          x2={PAD.left}
          y1={PAD.top}
          y2={H - PAD.bottom}
          className="sim-axis"
        />
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={H - PAD.bottom}
          y2={H - PAD.bottom}
          className="sim-axis"
        />
      </svg>
      <figcaption className="sim-chart-caption">
        {label} — {series.iterations.toLocaleString()} iterations, mean{" "}
        {fmtUnits(series.mean, series.units)}, sd {fmtUnits(series.sd, series.units)}
      </figcaption>
    </figure>
  );
}
