/**
 * Types for the quantitative elicitation API.
 *
 * Kept out of `types.ts` on purpose. That file is already long and every type in it is
 * shared across several views; these are used by one feature and would only make the
 * shared surface harder to read. Same reasoning as the per-feature CSS.
 */

export type QuantScenario = "pre_mitigation" | "post_mitigation";

export type DistName =
  | "pert"
  | "triangular"
  | "trigen"
  | "uniform"
  | "cumulative"
  | "discrete"
  | "none";

/** What a shape needs the analyst to enter. Drives which controls the form renders. */
export type DistInputs = "three_point" | "bounds_only" | "points" | "none";

export type BoundInterpretation = "absolute" | "p10_p90" | "p5_p95";
export type RationaleKey = "min" | "ml" | "max";
export type QuantSource = "sme" | "historical" | "analyst" | "agent_proposal";
export type Confidence = "low" | "medium" | "high";

/** When to reach for a shape, and what it costs you to get it wrong. Served, not hardcoded. */
export interface DistributionGuidance {
  value: DistName;
  label: string;
  inputs: DistInputs;
  summary: string;
  use_when: string;
  avoid_when: string;
  caution: string;
}

export interface QuantVocabulary {
  distributions: DistributionGuidance[];
  bound_interpretations: BoundInterpretation[];
  scenarios: QuantScenario[];
  sources: QuantSource[];
  confidences: Confidence[];
  day_bases: string[];
  cost_bases: string[];
  rationale_keys: RationaleKey[];
}

export interface QuantPoint {
  x: number;
  p: number;
}

/** Why one of the three numbers is what it is. `source` is what keeps an agent's
 *  wording from silently becoming the analyst's own judgement. */
export interface RationaleEntry {
  text?: string | null;
  source: QuantSource;
  author?: string | null;
  at?: string | null;
}

export interface QuantDimension {
  dist: DistName;
  min?: number | null;
  ml?: number | null;
  max?: number | null;
  pert_lambda: number;
  points?: QuantPoint[] | null;
  rationale?: Partial<Record<RationaleKey, RationaleEntry>> | null;
}

export interface QuantEstimate {
  id: number;
  risk_id: number;
  scenario: QuantScenario;
  p_occurrence: number;
  is_variability: boolean;
  bound_interpretation: BoundInterpretation;
  cost: QuantDimension;
  sched: QuantDimension;
  cost_basis: string;
  sched_day_basis: string;
  source: QuantSource;
  confidence: Confidence;
  estimated_by: string;
  estimated_at: string;
  notes: string | null;
  locked: boolean;
  created_at: string;
  updated_at: string;
}

export interface QuantDimensionWrite {
  dist: DistName;
  min?: number | null;
  ml?: number | null;
  max?: number | null;
  pert_lambda?: number;
  points?: QuantPoint[] | null;
  rationale?: Partial<Record<RationaleKey, RationaleEntry>> | null;
}

export interface QuantEstimateWrite {
  p_occurrence: number;
  is_variability: boolean;
  bound_interpretation: BoundInterpretation;
  cost: QuantDimensionWrite;
  sched: QuantDimensionWrite;
  cost_basis: string;
  sched_day_basis: string;
  source: QuantSource;
  confidence: Confidence;
  notes?: string | null;
}

export interface QuantIssue {
  severity: "error" | "warning";
  /** Dotted path, e.g. `cost.ml` or `p_occurrence`. Keys the message to its input. */
  field: string;
  message: string;
}

/**
 * What the numbers look like once sampled.
 *
 * `lo` and `hi` are the *true* bounds after interpretation. `elicited_lo` / `elicited_hi`
 * are what was typed. When `widened` is true the two differ, and showing both is the only
 * way an SME sees that trigen moved their bounds outward rather than ignoring them.
 */
export interface DimensionSummary {
  kind: DistName;
  lo: number;
  ml: number | null;
  hi: number;
  mean: number;
  sd: number;
  alpha: number | null;
  beta: number | null;
  expected_value: number;
  elicited_lo: number | null;
  elicited_hi: number | null;
  widened: boolean;
}

export interface QuantSummary {
  cost: DimensionSummary | null;
  sched: DimensionSummary | null;
}

export interface QuantPreview {
  ok: boolean;
  errors: QuantIssue[];
  warnings: QuantIssue[];
  summary: Partial<QuantSummary>;
}

export interface QuantEstimateResponse {
  estimate: QuantEstimate;
  warnings: QuantIssue[];
  summary: Partial<QuantSummary>;
}

export interface QuantCoverage {
  flagged_for_quantification: number;
  estimated: number;
  missing: number[];
}

export interface QuantDriver {
  id: number;
  name: string;
  description: string | null;
  correlation_default: number;
}
