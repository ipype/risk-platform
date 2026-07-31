/**
 * The form's own model of an estimate, and the conversions either side of it.
 *
 * Numbers are held as strings while editing. A controlled `<input type="number">` bound to
 * a number cannot hold "1.", "-", or "" without either fighting the keystroke or coercing
 * a half-typed value into something the preview will happily draw. Parsing happens once,
 * on the way out.
 *
 * Pure functions, no React, so the round trip can be tested without mounting anything.
 */

import type {
  BoundInterpretation,
  Confidence,
  DistName,
  QuantDimension,
  QuantEstimate,
  QuantEstimateWrite,
  QuantPoint,
  QuantSource,
  RationaleEntry,
  RationaleKey,
} from "./types";

export const RATIONALE_KEYS: RationaleKey[] = ["min", "ml", "max"];

export const RATIONALE_LABELS: Record<RationaleKey, string> = {
  min: "Minimum",
  ml: "Most likely",
  max: "Maximum",
};

export interface DraftRationale {
  text: string;
  source: QuantSource;
  author: string | null;
  at: string | null;
}

export interface DraftDimension {
  dist: DistName;
  min: string;
  ml: string;
  max: string;
  pertLambda: string;
  points: { x: string; p: string }[];
  rationale: Record<RationaleKey, DraftRationale>;
}

export interface DraftEstimate {
  pOccurrence: string;
  isVariability: boolean;
  boundInterpretation: BoundInterpretation;
  cost: DraftDimension;
  sched: DraftDimension;
  costBasis: string;
  schedDayBasis: string;
  source: QuantSource;
  confidence: Confidence;
  notes: string;
}

export type DimensionKey = "cost" | "sched";

function emptyRationale(): Record<RationaleKey, DraftRationale> {
  return {
    min: { text: "", source: "sme", author: null, at: null },
    ml: { text: "", source: "sme", author: null, at: null },
    max: { text: "", source: "sme", author: null, at: null },
  };
}

export function emptyDimension(dist: DistName = "none"): DraftDimension {
  return {
    dist,
    min: "",
    ml: "",
    max: "",
    pertLambda: "4",
    points: [
      { x: "", p: "0" },
      { x: "", p: "1" },
    ],
    rationale: emptyRationale(),
  };
}

export function emptyDraft(): DraftEstimate {
  return {
    pOccurrence: "0.3",
    isVariability: false,
    boundInterpretation: "absolute",
    cost: emptyDimension("pert"),
    sched: emptyDimension("none"),
    costBasis: "absolute",
    schedDayBasis: "working",
    source: "sme",
    confidence: "medium",
    notes: "",
  };
}

const str = (v: number | null | undefined): string =>
  v === null || v === undefined ? "" : String(v);

function dimensionFrom(d: QuantDimension): DraftDimension {
  const rationale = emptyRationale();
  for (const key of RATIONALE_KEYS) {
    const entry = d.rationale?.[key];
    if (entry) {
      rationale[key] = {
        text: entry.text ?? "",
        source: entry.source ?? "sme",
        author: entry.author ?? null,
        at: entry.at ?? null,
      };
    }
  }
  return {
    dist: d.dist,
    min: str(d.min),
    ml: str(d.ml),
    max: str(d.max),
    pertLambda: str(d.pert_lambda) || "4",
    points:
      d.points && d.points.length
        ? d.points.map((p) => ({ x: String(p.x), p: String(p.p) }))
        : emptyDimension().points,
    rationale,
  };
}

export function draftFromEstimate(e: QuantEstimate): DraftEstimate {
  return {
    pOccurrence: str(e.p_occurrence),
    isVariability: e.is_variability,
    boundInterpretation: e.bound_interpretation,
    cost: dimensionFrom(e.cost),
    sched: dimensionFrom(e.sched),
    costBasis: e.cost_basis,
    schedDayBasis: e.sched_day_basis,
    source: e.source,
    confidence: e.confidence,
    notes: e.notes ?? "",
  };
}

/** `null` rather than `NaN` for anything unparseable, so a blank field reads as absent. */
function num(s: string): number | null {
  const t = s.trim();
  if (t === "") return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

function rationaleOut(
  r: Record<RationaleKey, DraftRationale>,
  keys: RationaleKey[]
): Partial<Record<RationaleKey, RationaleEntry>> | null {
  const out: Partial<Record<RationaleKey, RationaleEntry>> = {};
  for (const key of keys) {
    const entry = r[key];
    if (entry.text.trim() === "") continue;
    out[key] = {
      text: entry.text.trim(),
      source: entry.source,
      author: entry.author,
      at: entry.at,
    };
  }
  return Object.keys(out).length ? out : null;
}

/** Which of the three slots a shape actually has, so rationale is not saved against a
 *  number the shape does not carry. */
export function slotsFor(dist: DistName): RationaleKey[] {
  if (dist === "pert" || dist === "triangular" || dist === "trigen") return RATIONALE_KEYS;
  if (dist === "uniform") return ["min", "max"];
  return [];
}

function dimensionOut(d: DraftDimension) {
  const slots = slotsFor(d.dist);
  const usesPoints = d.dist === "cumulative" || d.dist === "discrete";

  const points: QuantPoint[] | null = usesPoints
    ? d.points
        .map((p) => ({ x: num(p.x), p: num(p.p) }))
        .filter((p): p is QuantPoint => p.x !== null && p.p !== null)
    : null;

  return {
    dist: d.dist,
    min: slots.includes("min") ? num(d.min) : null,
    ml: slots.includes("ml") ? num(d.ml) : null,
    max: slots.includes("max") ? num(d.max) : null,
    pert_lambda: num(d.pertLambda) ?? 4,
    points: points && points.length ? points : null,
    rationale: rationaleOut(d.rationale, slots),
  };
}

export function draftToPayload(d: DraftEstimate): QuantEstimateWrite {
  return {
    p_occurrence: num(d.pOccurrence) ?? 0,
    is_variability: d.isVariability,
    bound_interpretation: d.boundInterpretation,
    cost: dimensionOut(d.cost),
    sched: dimensionOut(d.sched),
    cost_basis: d.costBasis,
    sched_day_basis: d.schedDayBasis,
    source: d.source,
    confidence: d.confidence,
    notes: d.notes.trim() || null,
  };
}

/**
 * Whether the draft is complete enough to be worth previewing.
 *
 * Without this the form fires a request on the first digit of the minimum and shows the
 * SME a wall of "must satisfy min <= most likely <= max" before they have typed the other
 * two. Errors should arrive when the analyst has finished a thought, not mid-word.
 */
export function readyToPreview(d: DraftEstimate): boolean {
  const dimReady = (dim: DraftDimension): boolean => {
    if (dim.dist === "none") return true;
    if (dim.dist === "cumulative" || dim.dist === "discrete") {
      return dim.points.filter((p) => num(p.x) !== null && num(p.p) !== null).length >= 2;
    }
    return slotsFor(dim.dist).every((slot) => {
      const raw = slot === "min" ? dim.min : slot === "ml" ? dim.ml : dim.max;
      return num(raw) !== null;
    });
  };

  if (num(d.pOccurrence) === null) return false;
  if (d.cost.dist === "none" && d.sched.dist === "none") return false;
  return dimReady(d.cost) && dimReady(d.sched);
}

/** Issues for one dimension, keyed by the dotted paths the API returns. */
export function issuesFor(
  issues: { field: string; message: string }[],
  prefix: string
): { field: string; message: string }[] {
  return issues.filter((i) => i.field === prefix || i.field.startsWith(`${prefix}.`));
}
