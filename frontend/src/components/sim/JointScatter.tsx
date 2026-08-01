import { useMemo, useState } from "react";
import type { JointConfidence, JointPoint } from "../../simulation-types";
import { fmtCompact, fmtDays, fmtMoney, fmtPercent } from "./format";

/**
 * Cost and date read together instead of side by side.
 *
 * The shaded box is the claim a sanction paper makes when it puts a P80 budget next to a
 * P80 date: everything inside it meets both. The percentage on it is how much of the
 * cloud is actually in there, and it is never the 80% the two marginals advertise —
 * around 65-70% on a register whose cost and schedule tails are not the same iteration.
 * That is the same error as adding percentiles, one dimension out, and it is drawn rather
 * than argued because the picture settles it in a way a sentence does not.
 *
 * The curve is the honest version. For a target confidence there is no single answer,
 * there is a trade-off: accept more delay and the cost you must carry falls. The marked
 * point on it is where both axes are held to the same marginal stringency, which is what
 * people mean when they ask for "the P80 package" and do not want to choose a trade-off.
 *
 * With a burn rate in play the cloud has a straight lower-left edge of that slope, since
 * part of the cost is the delay repriced. That is real dependence, not an artefact — but
 * it is mechanical rather than elicited, and the caption says so where it applies.
 */

const W = 720;
const H = 380;
const PAD = { top: 18, right: 26, bottom: 46, left: 84 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

interface Props {
  joint: JointConfidence;
  /** Frontier drawn on load. Falls back to the first available target. */
  defaultTarget?: number;
}

export default function JointScatter({ joint, defaultTarget = 80 }: Props) {
  const targets = joint.frontiers.map((f) => f.target);
  const [target, setTarget] = useState(() =>
    targets.includes(defaultTarget) ? defaultTarget : (targets[targets.length - 1] ?? 0)
  );

  const frontier = joint.frontiers.find((f) => f.target === target) ?? null;

  const bounds = useMemo(() => {
    const xs = joint.scatter.map((p) => p[0]);
    const ys = joint.scatter.map((p) => p[1]);
    if (xs.length === 0) return null;
    let x0 = Math.min(...xs);
    let x1 = Math.max(...xs);
    let y0 = Math.min(...ys);
    let y1 = Math.max(...ys);
    // The marginal lines and the frontier can sit outside the thinned cloud by a stride's
    // worth. Widening to include them beats drawing a reference line off the canvas.
    x1 = Math.max(x1, joint.marginal_delay_days);
    y1 = Math.max(y1, joint.marginal_cost);
    const xPad = (x1 - x0 || 1) * 0.04;
    const yPad = (y1 - y0 || 1) * 0.04;
    return { x0: x0 - xPad, x1: x1 + xPad, y0: y0 - yPad, y1: y1 + yPad };
  }, [joint]);

  if (!bounds) {
    return <p className="sim-chart-empty">No joint sample was retained for this run.</p>;
  }

  const spanX = bounds.x1 - bounds.x0 || 1;
  const spanY = bounds.y1 - bounds.y0 || 1;
  const x = (v: number) => PAD.left + ((v - bounds.x0) / spanX) * PLOT_W;
  const y = (v: number) => PAD.top + (1 - (v - bounds.y0) / spanY) * PLOT_H;

  // One path for the whole cloud. Twelve hundred `<rect>` nodes render correctly and
  // scroll badly; a single path is one node and the same picture.
  const cloud = joint.scatter
    .map((p) => `M${x(p[0]).toFixed(1)} ${y(p[1]).toFixed(1)}h2v2h-2z`)
    .join("");

  const curve =
    frontier && frontier.points.length > 1
      ? frontier.points
          .map(
            (p, i) =>
              `${i === 0 ? "M" : "L"}${x(p.delay_days).toFixed(2)},${y(p.total_cost).toFixed(2)}`
          )
          .join(" ")
      : null;

  const mx = x(joint.marginal_delay_days);
  const my = y(joint.marginal_cost);
  const balanced = frontier?.balanced ?? null;

  const xTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => bounds.x0 + f * spanX);
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((f) => bounds.y0 + f * spanY);

  return (
    <figure className="sim-chart">
      <div className="sim-jcl-controls" role="group" aria-label="Joint confidence target">
        <span className="sim-jcl-controls-label">Joint confidence</span>
        {targets.map((t) => (
          <button
            key={t}
            type="button"
            className={t === target ? "sim-chip active" : "sim-chip"}
            aria-pressed={t === target}
            onClick={() => setTarget(t)}
          >
            P{t}
          </button>
        ))}
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="sim-svg"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={
          `Joint cost and delay scatter over ${joint.iterations.toLocaleString()} iterations. ` +
          `The P${joint.marginal_pair_target} cost and P${joint.marginal_pair_target} delay quoted ` +
          `together are met in ${fmtPercent(joint.joint_at_marginal_pair)} of them.`
        }
      >
        <title>Joint cost and schedule confidence</title>

        {yTicks.map((v, i) => (
          <g key={`y${i}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="sim-grid" />
            <text x={PAD.left - 10} y={y(v) + 4} className="sim-axis-text" textAnchor="end">
              {fmtCompact(v)}
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

        {/* Everything under both marginals. The label on it is the whole finding. */}
        <rect
          x={PAD.left}
          y={my}
          width={Math.max(mx - PAD.left, 0)}
          height={Math.max(PAD.top + PLOT_H - my, 0)}
          className="sim-jcl-box"
        />

        <path d={cloud} className="sim-jcl-cloud" />

        <line x1={mx} x2={mx} y1={PAD.top} y2={PAD.top + PLOT_H} className="sim-marker-line" />
        <line x1={PAD.left} x2={W - PAD.right} y1={my} y2={my} className="sim-marker-line" />

        {curve && <path d={curve} className="sim-jcl-frontier" />}

        {balanced && (
          <g>
            <circle
              cx={x(balanced.delay_days)}
              cy={y(balanced.total_cost)}
              r={5}
              className="sim-jcl-balanced"
            />
            <text
              x={x(balanced.delay_days) + 10}
              y={y(balanced.total_cost) - 8}
              className="sim-marker-text"
              textAnchor={balanced.delay_days > bounds.x0 + spanX * 0.7 ? "end" : "start"}
            >
              P{balanced.cost_p.toFixed(0)} both
            </text>
          </g>
        )}

        <text
          x={PAD.left + 8}
          y={PAD.top + PLOT_H - 10}
          className="sim-jcl-box-text"
        >
          {fmtPercent(joint.joint_at_marginal_pair)} of iterations meet both
        </text>

        <line x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={H - PAD.bottom} className="sim-axis" />
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={H - PAD.bottom}
          y2={H - PAD.bottom}
          className="sim-axis"
        />
        <text x={W - PAD.right} y={H - 8} className="sim-axis-text" textAnchor="end">
          delay, elapsed days →
        </text>
      </svg>

      <figcaption className="sim-chart-caption">
        <span className="sim-chart-note">
          {joint.scatter_stride === 1
            ? `Each mark is one of the run's ${joint.iterations.toLocaleString()} iterations.`
            : `Each mark is one iteration, thinned to every ${joint.scatter_stride.toLocaleString()}${ordinal(joint.scatter_stride)} of ${joint.iterations.toLocaleString()}.`}{" "}
          The curve is every (cost, date) pair this run is P{target} confident of meeting
          together; accepting more delay lowers the cost that must be carried.
          {joint.burn_rate_coupled
            ? " The straight lower-left edge of the cloud is the burn rate: part of the cost is the delay repriced, so that much of the dependence is mechanical rather than elicited."
            : ""}
        </span>
      </figcaption>
    </figure>
  );
}

function ordinal(n: number): string {
  if (n % 100 >= 11 && n % 100 <= 13) return "th";
  return ["th", "st", "nd", "rd"][n % 10] ?? "th";
}

/** The headline pair, stated in words next to the picture that proves it. */
export function JointVerdict({ joint }: { joint: JointConfidence }) {
  const target = joint.marginal_pair_target;
  const achieved = joint.joint_at_marginal_pair;
  const frontier = joint.frontiers.find((f) => f.target === target);
  const balanced: JointPoint | null = frontier?.balanced ?? null;
  const short = achieved < target / 100 - 0.02;

  return (
    <div className={short ? "sim-reconcile" : "sim-jcl-verdict"}>
      <p>
        A budget of <strong>{fmtMoney(joint.marginal_cost)}</strong> with a delay allowance of{" "}
        <strong>{fmtDays(joint.marginal_delay_days)}</strong> is P{target} on each measure
        separately, and <strong>{fmtPercent(achieved)}</strong> on both together. The two tails
        are not the same iteration, so quoting them side by side describes a commitment nobody
        simulated.
        {balanced && (
          <>
            {" "}
            Holding both to P{balanced.cost_p.toFixed(0)} —{" "}
            <strong>{fmtMoney(balanced.total_cost)}</strong> and{" "}
            <strong>{fmtDays(balanced.delay_days)}</strong> — is the pair that really is P
            {target}.
          </>
        )}
      </p>
      <p className="sim-chart-note">
        Cost and delay rank-correlate at {joint.cost_delay_correlation.toFixed(2)} in this run.
        The closer that runs to one, the smaller the gap above; at zero the joint confidence
        would be the product of the two marginals.
      </p>
    </div>
  );
}
