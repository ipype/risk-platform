import { useMemo, useState } from "react";
import type { JointConfidence, JointGrid } from "../../simulation-types";
import {
  dateToDay,
  dayToDate,
  fmtDate,
  fmtDays,
  fmtMoney,
  fmtPercent,
  toIsoDay,
} from "./format";

/**
 * What *your* pair is worth.
 *
 * The frontier answers "which pairs are P80 together". It does not answer the question
 * anybody actually walks in with, which is that the board has already fixed a date and a
 * budget and wants to know what they bought. That target almost never sits on a frontier,
 * and reading the nearest curve by eye is how a 62% commitment gets reported as 80%.
 *
 * So: two inputs and one number, counted rather than fitted. `joint.grid` carries
 * `P(delay <= D and cost <= C)` over every iteration on a mesh at the marginal quantiles
 * of each axis, so a target landing on a node is exact and one landing between nodes is
 * *bracketed* — the joint CDF cannot dip between two nodes, so the surrounding cells are
 * a bound rather than an error bar. The bracket is printed whenever it is wide enough to
 * matter, because a number quoted to the point is a number that will be held to it.
 *
 * A run made before the mesh existed falls back to the thinned scatter, which is a
 * genuine estimate with genuine sampling error, and says so in those words. Twelve
 * hundred pairs put about two and a half points of noise on the answer — the same size as
 * the effect being measured — and a reader who is not told that will read it as exact.
 *
 * The marginals are read off the same mesh rather than off the two S-curves. Two
 * constructions would disagree in the third decimal and produce the one thing this panel
 * must never show: a pair more likely than one of its own halves.
 */

interface Reading {
  /** Best estimate, 0..1. */
  value: number;
  /** Bounds that contain the truth. Equal to `value` when the target lands on nodes. */
  lower: number;
  upper: number;
  exact: boolean;
}

const EXACT = (v: number): Reading => ({ value: v, lower: v, upper: v, exact: true });

/** Index of the last node at or below `v`, or -1 when `v` is below every node. */
function lastAtOrBelow(nodes: number[], v: number): number {
  let lo = 0;
  let hi = nodes.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (nodes[mid] <= v) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found;
}

/**
 * `P(delay <= d and cost <= c)` off the mesh.
 *
 * Pass `Infinity` for either argument to read that axis out entirely, which is how the
 * marginals come from the same construction as the pair.
 */
function readGrid(grid: JointGrid, d: number, c: number): Reading {
  const { delay_days: dn, total_cost: cn, counts, iterations: n } = grid;
  if (n <= 0 || dn.length === 0 || cn.length === 0) return EXACT(0);

  // Below the smallest node on either axis is below the smallest value in the sample, so
  // nothing is admitted. Exactly zero, not an interpolation towards it.
  if (d < dn[0] || c < cn[0]) return EXACT(0);

  const iLo = lastAtOrBelow(dn, d);
  const jLo = lastAtOrBelow(cn, c);
  const iHi = Math.min(iLo + 1, dn.length - 1);
  const jHi = Math.min(jLo + 1, cn.length - 1);

  const at = (i: number, j: number) => counts[i][j] / n;
  const lower = at(iLo, jLo);
  const upper = at(iHi, jHi);
  if (iHi === iLo && jHi === jLo) return EXACT(lower);

  const wd = iHi > iLo && dn[iHi] > dn[iLo] ? (d - dn[iLo]) / (dn[iHi] - dn[iLo]) : 0;
  const wc = jHi > jLo && cn[jHi] > cn[jLo] ? (c - cn[jLo]) / (cn[jHi] - cn[jLo]) : 0;
  const value =
    (1 - wd) * (1 - wc) * at(iLo, jLo) +
    wd * (1 - wc) * at(iHi, jLo) +
    (1 - wd) * wc * at(iLo, jHi) +
    wd * wc * at(iHi, jHi);

  return { value, lower, upper, exact: false };
}

/** The same question of the thinned cloud, for a run with no mesh. */
function readScatter(
  scatter: [number, number][],
  d: number,
  c: number
): Reading & { sample: number } {
  const m = scatter.length;
  if (m === 0) return { ...EXACT(0), sample: 0 };
  let hits = 0;
  for (const [delay, cost] of scatter) {
    if (delay <= d && cost <= c) hits += 1;
  }
  const p = hits / m;
  // Wald interval. Wilson would be better at the ends, but the ends are exactly where
  // this panel is telling the reader to stop trusting the number anyway.
  const half = 1.96 * Math.sqrt(Math.max(p * (1 - p), 0) / m);
  return {
    value: p,
    lower: Math.max(0, p - half),
    upper: Math.min(1, p + half),
    exact: false,
    sample: m,
  };
}

/**
 * The smallest budget that reaches `target` jointly while holding the date.
 *
 * Read off the mesh row at or below the target date rather than interpolated across two —
 * a budget recommendation that is one node optimistic is worse than one that is one node
 * conservative. `null` when the date alone is less likely than the target, in which case
 * no budget on any axis fixes it and saying so is the answer.
 */
function costForJointTarget(grid: JointGrid, d: number, target: number): number | null {
  const { delay_days: dn, total_cost: cn, counts, iterations: n } = grid;
  if (n <= 0 || d < dn[0]) return null;
  const i = Math.max(0, lastAtOrBelow(dn, d));
  for (let j = 0; j < cn.length; j += 1) {
    if (counts[i][j] / n >= target) return cn[j];
  }
  return null;
}

interface Props {
  joint: JointConfidence;
  /** `YYYY-MM-DD` day zero of the network, when the schedule version carries one. */
  dayZero?: string | null;
}

export default function JointTargets({ joint, dayZero = null }: Props) {
  // Taken from the joint view's own two figures rather than from the deterministic
  // block, so the date axis here and the `finish_day` on every frontier point are
  // guaranteed to share an origin.
  const baselineFinish = joint.marginal_finish_day - joint.marginal_delay_days;
  const onDates = dayZero != null;

  const [cost, setCost] = useState(() => String(Math.round(joint.marginal_cost)));
  const [when, setWhen] = useState(() =>
    onDates
      ? toIsoDay(dayToDate(dayZero, joint.marginal_finish_day))
      : String(Math.round(joint.marginal_delay_days))
  );

  const targetCost = Number(cost);
  const targetDelay = useMemo(() => {
    if (!onDates) return Number(when);
    const day = dateToDay(dayZero, when);
    return day == null ? NaN : day - baselineFinish;
  }, [onDates, when, dayZero, baselineFinish]);

  const ready = Number.isFinite(targetCost) && Number.isFinite(targetDelay) && cost !== "" && when !== "";
  const grid = joint.grid ?? null;

  const reading = useMemo(() => {
    if (!ready) return null;
    if (grid) {
      return {
        both: readGrid(grid, targetDelay, targetCost),
        costOnly: readGrid(grid, Infinity, targetCost),
        dateOnly: readGrid(grid, targetDelay, Infinity),
        source: "grid" as const,
        sample: grid.iterations,
      };
    }
    return {
      both: readScatter(joint.scatter, targetDelay, targetCost),
      costOnly: readScatter(joint.scatter, Infinity, targetCost),
      dateOnly: readScatter(joint.scatter, targetDelay, Infinity),
      source: "scatter" as const,
      sample: joint.scatter.length,
    };
  }, [ready, grid, joint.scatter, targetDelay, targetCost]);

  const neededCost =
    grid && ready ? costForJointTarget(grid, targetDelay, joint.marginal_pair_target / 100) : null;
  const bracketed =
    reading != null && !reading.both.exact && reading.both.upper - reading.both.lower > 0.005;

  return (
    <div className="sim-targets">
      <h3>Price your own pair</h3>
      <div className="sim-targets-fields">
        <label className="sim-field">
          <span>Target total cost</span>
          <input
            type="number"
            min={0}
            step={1000}
            value={cost}
            onChange={(e) => setCost(e.target.value)}
          />
        </label>
        <label className="sim-field">
          <span>{onDates ? "Target finish date" : "Target slip, elapsed days"}</span>
          <input
            type={onDates ? "date" : "number"}
            step={onDates ? undefined : 1}
            value={when}
            onChange={(e) => setWhen(e.target.value)}
          />
        </label>
      </div>

      {!ready || reading == null ? (
        <p className="sim-note">
          Enter a budget and a {onDates ? "date" : "slip"} to see what the pair is worth.
        </p>
      ) : (
        <>
          <div className="sim-targets-figure">
            <span className="sim-headline-label">Probability of meeting both</span>
            <span className="sim-headline-value">{fmtPercent(reading.both.value)}</span>
            <span className="sim-headline-sub">
              {fmtMoney(targetCost)} by{" "}
              {onDates
                ? fmtDate(dayToDate(dayZero, baselineFinish + targetDelay))
                : fmtDays(targetDelay)}
            </span>
          </div>

          <p className="sim-targets-marginals">
            Cost alone <strong>{fmtPercent(reading.costOnly.value)}</strong> ·{" "}
            {onDates ? "Date" : "Slip"} alone{" "}
            <strong>{fmtPercent(reading.dateOnly.value)}</strong> · the two are not the
            same iteration, which is the whole gap between those and the figure above.
          </p>

          {reading.source === "grid" ? (
            <p className="sim-chart-note">
              Counted over all {reading.sample.toLocaleString()} iterations.
              {bracketed
                ? ` Your pair falls between mesh nodes, so the exact answer is somewhere between ${fmtPercent(reading.both.lower)} and ${fmtPercent(reading.both.upper)}; the figure above interpolates within that bound.`
                : " Your pair lands on the mesh, so the figure is a count and not an estimate."}
            </p>
          ) : (
            <p className="sim-chart-note">
              This run predates the joint mesh (engine 1.3.0), so the figure is counted off
              the thinned cloud of {reading.sample.toLocaleString()} retained iterations
              rather than off all {joint.iterations.toLocaleString()}. That is a sample, not
              a count: read it as {fmtPercent(reading.both.lower)} to{" "}
              {fmtPercent(reading.both.upper)}. Re-run — same seed and inputs reproduce the
              same numbers — for an exact reading.
            </p>
          )}

          {grid && (
            <p className="sim-note">
              {neededCost == null ? (
                <>
                  Holding this {onDates ? "date" : "slip"} is itself only{" "}
                  {fmtPercent(reading.dateOnly.value)} likely, so no budget reaches P
                  {joint.marginal_pair_target.toFixed(0)} on both while it stands. The date
                  is the binding constraint, not the money.
                </>
              ) : (
                <>
                  To reach P{joint.marginal_pair_target.toFixed(0)} on both while holding
                  this {onDates ? "date" : "slip"}, the budget would need to be about{" "}
                  <strong>{fmtMoney(neededCost)}</strong>.
                </>
              )}
            </p>
          )}
        </>
      )}
    </div>
  );
}
