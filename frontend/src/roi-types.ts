/**
 * Types for mitigation ROI.
 *
 * One naming rule runs through the whole file and it is worth stating once: every figure
 * is a **reduction**, meaning baseline minus treated. A positive number is the package
 * taking something away. Nothing here is a `delta` whose sign a reader has to work out
 * from context, because the one thing a before/after screen must never be is ambiguous
 * about which direction is good.
 */

import type { RunSummary } from "./simulation-types";

export interface Reduction {
  before: number | null;
  after: number | null;
  /** `before - after`. Positive means the package reduced it. */
  reduction: number | null;
  reduction_pct: number | null;
}

export interface SeriesReduction {
  label: string;
  units: string;
  mean: Reduction;
  at_percentile: Reduction;
  /** Estimated sampling error on the difference, reported as an upper bound. */
  standard_error: number | null;
  /** The reduction does not clear that bar, so it is not distinguishable from noise. */
  within_noise: boolean;
}

export interface CurveRow {
  /** 0–100. */
  p: number;
  before: number;
  after: number;
  reduction: number;
}

export type Movement = "retired" | "reduced" | "unchanged" | "increased" | "entered";

export interface RiskMover {
  risk_id: number;
  code: string;
  title: string;
  movement: Movement;
  share_before: number | null;
  share_after: number | null;
  contribution_before: number | null;
  contribution_after: number | null;
  contribution_reduction: number | null;
  rank_before: number | null;
  rank_after: number | null;
}

export interface CriticalityMover {
  activity_id: string;
  code: string;
  name: string;
  index_before: number | null;
  index_after: number | null;
  /** `after - before`: positive means this path became *more* critical. */
  index_change: number | null;
}

export interface Comparison {
  percentile: number;
  contingency: SeriesReduction | null;
  total_cost: SeriesReduction | null;
  risk_cost: SeriesReduction | null;
  delay_days: SeriesReduction | null;
  finish_day: SeriesReduction | null;
  schedule_driven_cost: SeriesReduction | null;
  curve: CurveRow[];
  plan_budget: number;
  plan_sched_days: number;
  plan_unpriced_count: number;
  benefit_cost_ratio: number | null;
  net_at_percentile: number | null;
  risk_movers: RiskMover[];
  criticality_movers: CriticalityMover[];
  risk_count_before: number;
  risk_count_after: number;
  retired_count: number;
  /** Approximations and conventions, meant to be shown rather than stored. */
  basis: string[];
  warnings: string[];
}

export type RoiStatus = "pending" | "ready" | "failed";

export interface RoiSummary {
  id: number;
  plan_id: number;
  plan_name: string;
  scope_id: number;
  name: string;
  note: string | null;
  percentile: number;
  seed_shared: boolean;
  before_run_id: number;
  after_run_id: number;
  status: RoiStatus;
  plan_budget: number;
  plan_sched_days: number;
  plan_unpriced_count: number;
  /** The package was re-materialised after this pair was run. */
  stale: boolean;
  /** An action was re-costed after this pair was made. */
  cost_moved: boolean;
  created_by: string;
  created_at: string;
}

export interface RoiDetail extends RoiSummary {
  before: RunSummary | null;
  after: RunSummary | null;
  /** Live re-check of comparability. Non-empty means something moved after pairing. */
  issues: string[];
  current_plan_budget: number;
  current_plan_sched_days: number;
  comparison: Comparison | null;
}

export interface PairRequest {
  name?: string;
  note?: string | null;
  percentile?: number;
  schedule_version_id?: number | null;
  iterations?: number;
  seed?: number;
  sampling?: "lhs" | "mc";
  base_cost?: number;
  burn_rate_per_day?: number;
  allow_negative_delay_credit?: boolean;
  correlate_occurrence?: boolean;
  intra_risk_cost_sched_correlation?: number;
  gate_override?: boolean;
  gate_override_reason?: string | null;
}

export const DEFAULT_PAIR: Required<
  Pick<PairRequest, "iterations" | "seed" | "sampling" | "base_cost" | "burn_rate_per_day" | "percentile">
> = {
  iterations: 10000,
  seed: 12345,
  sampling: "lhs",
  base_cost: 0,
  burn_rate_per_day: 0,
  percentile: 80,
};
