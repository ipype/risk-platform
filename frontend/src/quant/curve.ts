/**
 * Shape of the density behind an estimate, for the preview sketch.
 *
 * The server owns the numbers — mean, standard deviation, the recovered bounds. This only
 * works out what the curve looks like, at whatever resolution the sparkline needs. Drawing
 * it client-side keeps the preview responsive while typing without asking the API for a
 * few hundred sampled points on every keystroke.
 *
 * Densities are returned unnormalised and scaled to their own maximum. The preview says
 * "this is the shape of your judgement", not "this is a calibrated probability density",
 * and pretending otherwise would invite reading values off an axis that isn't there.
 */

import type { DimensionSummary, QuantPoint } from "./types";

export interface CurveShape {
  kind: "curve";
  /** Points in data space, `y` scaled to a 0..1 peak. */
  points: [number, number][];
  lo: number;
  hi: number;
}

export interface BarShape {
  kind: "bars";
  bars: { x: number; p: number }[];
  lo: number;
  hi: number;
}

export type PreviewShape = CurveShape | BarShape;

const RESOLUTION = 96;

function scaleToPeak(points: [number, number][]): [number, number][] {
  const peak = points.reduce((m, [, y]) => Math.max(m, y), 0);
  if (peak <= 0) return points.map(([x]) => [x, 0]);
  return points.map(([x, y]) => [x, y / peak]);
}

/**
 * Beta density up to its normalising constant.
 *
 * Evaluated in log space. A PERT with a mode near one end produces alpha or beta around 5,
 * and `t**(a-1) * (1-t)**(b-1)` on a range in the millions underflows to zero long before
 * the curve is drawn — the sparkline goes flat and the shape silently disappears.
 */
function betaDensity(t: number, alpha: number, beta: number): number {
  if (t <= 0) return alpha > 1 ? 0 : Number.POSITIVE_INFINITY;
  if (t >= 1) return beta > 1 ? 0 : Number.POSITIVE_INFINITY;
  return Math.exp((alpha - 1) * Math.log(t) + (beta - 1) * Math.log(1 - t));
}

function triangularDensity(x: number, lo: number, ml: number, hi: number): number {
  if (x < lo || x > hi) return 0;
  if (hi === lo) return 1;
  if (x <= ml) return ml > lo ? (x - lo) / (ml - lo) : 1;
  return hi > ml ? (hi - x) / (hi - ml) : 1;
}

/**
 * Step density implied by a piecewise-linear CDF: each segment carries uniform density
 * equal to its probability over its width. Deliberately drawn as steps rather than
 * smoothed — the blockiness is how much the analyst actually specified.
 */
function cumulativeSteps(points: QuantPoint[]): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const width = points[i + 1].x - points[i].x;
    const mass = points[i + 1].p - points[i].p;
    const density = width > 0 ? mass / width : 0;
    out.push([points[i].x, density]);
    out.push([points[i + 1].x, density]);
  }
  return out;
}

export function previewShape(
  summary: DimensionSummary,
  points: QuantPoint[] | null
): PreviewShape | null {
  const { kind, lo, hi } = summary;

  if (kind === "discrete") {
    if (!points || !points.length) return null;
    const xs = points.map((p) => p.x);
    return {
      kind: "bars",
      bars: points.map((p) => ({ x: p.x, p: p.p })),
      lo: Math.min(...xs),
      hi: Math.max(...xs),
    };
  }

  if (kind === "cumulative") {
    if (!points || points.length < 2) return null;
    return { kind: "curve", points: scaleToPeak(cumulativeSteps(points)), lo, hi };
  }

  if (hi <= lo) return null;

  const raw: [number, number][] = [];
  for (let i = 0; i <= RESOLUTION; i += 1) {
    const x = lo + ((hi - lo) * i) / RESOLUTION;
    let y = 0;
    if (kind === "uniform") {
      y = 1;
    } else if (kind === "pert") {
      const alpha = summary.alpha ?? 3;
      const beta = summary.beta ?? 3;
      y = betaDensity((x - lo) / (hi - lo), alpha, beta);
    } else {
      y = triangularDensity(x, lo, summary.ml ?? (lo + hi) / 2, hi);
    }
    raw.push([x, Number.isFinite(y) ? y : 0]);
  }

  return { kind: "curve", points: scaleToPeak(raw), lo, hi };
}

/** Compact axis labels. Money in a risk register runs to eight figures and the raw number
 *  will not fit under a 240px sparkline. */
export function formatValue(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(2)}bn`;
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}m`;
  if (abs >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  if (abs >= 1) return v.toFixed(abs >= 100 ? 0 : 1);
  return v.toFixed(3);
}
