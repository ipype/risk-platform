/**
 * Mitigation plans, declared residuals, and what a package costs.
 *
 * Mirrors `app/api/routes/mitigation_plans.py`. Two shapes here are worth reading twice
 * before changing either.
 *
 * `ResidualLine` covers *every* risk in scope with a baseline, not only the treated ones.
 * A line with `treatment: "untreated"` carries its baseline through at full size, which is
 * what will be simulated. Filtering those out of the table would make a residual register
 * look smaller than the one the engine reads.
 *
 * `PlanCost` is deterministic money and days. It sits beside a contingency figure and
 * never inside one — percentiles are not additive and a package's price is.
 */

export type PlanStatus = "draft" | "proposed" | "approved" | "rejected" | "superseded";
export type TreatmentKind = "reduce" | "retire" | "accept";
export type TreatmentMode = "factor" | "absolute";

export interface PlanCost {
  action_count: number;
  costed_count: number;
  /** Actions with neither a budget nor a duration. Not zero — unknown. */
  unpriced_count: number;
  cancelled_count: number;
  total_budget: number;
  total_sched_days: number;
  by_status: Record<string, number>;
}

export interface Plan {
  id: number;
  scope_id: number;
  name: string;
  description: string | null;
  status: string;
  materialized_at: string | null;
  materialized_by: string | null;
  materialized_fingerprint: string | null;
  materialized_risk_count: number | null;
  materialized_retired_count: number | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface PlanDetail extends Plan {
  cost: PlanCost;
  treated_count: number;
}

export interface TreatmentWrite {
  treatment: TreatmentKind;
  mode: TreatmentMode;
  p_factor: number;
  cost_factor: number;
  sched_factor: number;
  residual_p: number | null;
  residual_cost_min: number | null;
  residual_cost_ml: number | null;
  residual_cost_max: number | null;
  residual_sched_min: number | null;
  residual_sched_ml: number | null;
  residual_sched_max: number | null;
  rationale: string | null;
}

export interface Treatment extends TreatmentWrite {
  id: number;
  plan_id: number;
  risk_id: number;
}

export interface ResidualLine {
  risk_id: number;
  risk_code: string;
  title: string;
  /** `reduce` / `retire` / `accept` / `untreated`. */
  treatment: string;
  retired: boolean;
  base_p: number;
  residual_p: number | null;
  base_cost_ev: number | null;
  residual_cost_ev: number | null;
  base_sched_ev: number | null;
  residual_sched_ev: number | null;
  issues: string[];
  /** A run froze this residual. Materialising will step over it. */
  locked: boolean;
  /** The residual on file changed after this plan last wrote it. */
  edited_since: boolean;
}

export interface ResidualPreview {
  plan_id: number;
  fingerprint: string;
  matches_materialized: boolean;
  lines: ResidualLine[];
  treated: number;
  untreated: number;
  retired: number;
  locked: string[];
  edited_since: string[];
  /** A sum of means. A sanity check, never a contingency. */
  base_cost_ev_total: number;
  residual_cost_ev_total: number;
}

export interface MaterializeResult {
  written: number;
  unchanged: number;
  retired: number;
  skipped_locked: string[];
  replaced_edited: string[];
  orphans: string[];
  issues: string[];
  fingerprint: string;
}

export interface ScopeAction {
  id: number;
  risk_id: number;
  risk_code: string;
  plan_id: number | null;
  action: string;
  owner: string | null;
  budget: number | null;
  sched_days: number | null;
  status: string;
}

export interface MitigationVocabulary {
  plan_statuses: string[];
  treatments: string[];
  modes: string[];
}

export const DEFAULT_TREATMENT: TreatmentWrite = {
  treatment: "reduce",
  mode: "factor",
  p_factor: 1,
  cost_factor: 1,
  sched_factor: 1,
  residual_p: null,
  residual_cost_min: null,
  residual_cost_ml: null,
  residual_cost_max: null,
  residual_sched_min: null,
  residual_sched_ml: null,
  residual_sched_max: null,
  rationale: null,
};
