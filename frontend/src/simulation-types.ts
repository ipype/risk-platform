/**
 * Simulation domain types.
 *
 * A separate module rather than another block in `types.ts`, which is already 19 KB:
 * splitting on the subsystem seam keeps both files openable, and nothing in here is
 * referenced by the register, the matrix or the Gantt.
 *
 * The result shapes mirror `app/sim/results.py` and `app/sim/engine.py` field for field.
 * The API serves `SimulationResult` whole rather than re-declaring it, so the engine stays
 * the single owner of that shape and these interfaces follow it.
 */

export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
export type Sampling = "lhs" | "mc";

/* ------------------------------------------------------------------------- *
 * options and configuration
 * ------------------------------------------------------------------------- */

export interface GateView {
  assessed: boolean;
  passed?: boolean | null;
  failed_count?: number | null;
  run_at?: string | null;
  blocking_failures: string[];
}

export interface VersionOption {
  id: number;
  project_name: string;
  source_project_id: string;
  is_current: boolean;
  activity_count: number;
  relationship_count: number;
  created_at: string;
  data_date?: string | null;
  gate: GateView;
  /** Accepted mappings only. A green gate with zero of these says nothing about delay. */
  accepted_mappings: number;
}

export interface ScenarioOption {
  value: string;
  label: string;
  estimate_count: number;
}

export interface SimulationOptions {
  scenarios: ScenarioOption[];
  schedule_versions: VersionOption[];
  defaults: Record<string, unknown>;
}

export interface RunRequest {
  name?: string;
  scenario?: string;
  schedule_version_id?: number | null;
  iterations?: number;
  seed?: number;
  sampling?: Sampling;
  base_cost?: number;
  burn_rate_per_day?: number;
  allow_negative_delay_credit?: boolean;
  correlate_occurrence?: boolean;
  intra_risk_cost_sched_correlation?: number;
  gate_override?: boolean;
  gate_override_reason?: string | null;
}

export interface ExcludedRisk {
  risk_id: number;
  risk_code: string;
  title: string;
  reason: string;
}

export interface RunPreview {
  risk_count: number;
  mapped_risk_count: number;
  activity_count: number;
  excluded: ExcludedRisk[];
  notes: string[];
  gate: GateView;
  inputs_sha256: string;
}

/* ------------------------------------------------------------------------- *
 * results
 * ------------------------------------------------------------------------- */

export interface PercentilePoint {
  p: number;
  value: number;
}

export interface CurvePoint {
  x: number;
  /** Cumulative probability, 0..1 — not a percentage. */
  p: number;
}

export interface Histogram {
  edges: number[];
  counts: number[];
}

export interface SeriesSummary {
  label: string;
  units: string;
  iterations: number;
  mean: number;
  sd: number;
  minimum: number;
  maximum: number;
  percentiles: PercentilePoint[];
  s_curve: CurvePoint[];
  histogram: Histogram;
}

export interface RunManifest {
  engine_version: string;
  seed: number;
  iterations: number;
  sampling: string;
  centered_lhs: boolean;
  chunk_size: number;
  inputs_sha256: string;
  calendar_id?: string | null;
}

export interface DeterministicView {
  base_cost: number;
  activities: number;
  relationships: number;
  inserted_activities: number;
  baseline_finish_day?: number | null;
  critical_activities: number;
}

export interface ContingencyView {
  base_cost: number;
  mean_total_cost: number;
  contingency: PercentilePoint[];
  /** The wrong arithmetic, carried so the gap can be read instead of argued about. */
  additive_error_at_p80?: number | null;
  additive_p80_total?: number | null;
  integrated_p80_total?: number | null;
  cost_variance_share: number;
  schedule_variance_share: number;
}

export interface RiskSensitivity {
  risk_id: number;
  code: string;
  title: string;
  cost_variance_share: number;
  /** Null when the risk drives no activity — not zero, which would read as measured. */
  schedule_variance_share?: number | null;
  combined_variance_share: number;
  spearman_total_cost: number;
  spearman_delay?: number | null;
  mean_contribution: number;
  p80_contribution: number;
  realised_frequency: number;
}

export interface ActivityCriticality {
  activity_id: string;
  code: string;
  name: string;
  criticality_index: number;
  mean_total_float_days: number;
  duration_sensitivity?: number | null;
  cruciality: number;
  duration_sd_days: number;
  /**
   * Criticality index times the ratio of this activity's duration spread to the
   * project's. The Primavera Risk Analysis metric; scale-aware where cruciality is
   * correlation-based, and they part company wherever a shared risk driver correlates
   * durations.
   */
  schedule_sensitivity_index: number;
  is_inserted: boolean;
}

/* ------------------------------------------------------------------------- *
 * joint cost and schedule
 * ------------------------------------------------------------------------- */

export interface JointPoint {
  delay_days: number;
  finish_day: number;
  total_cost: number;
  /** Marginal percentile of this delay, 0-100. */
  delay_p: number;
  /** Marginal percentile of this cost, 0-100. */
  cost_p: number;
}

export interface JointFrontier {
  target: number;
  points: JointPoint[];
  /** Where cost and date carry equal marginal stringency. */
  balanced?: JointPoint | null;
}

export interface JointConfidence {
  iterations: number;
  frontiers: JointFrontier[];
  marginal_pair_target: number;
  marginal_cost: number;
  marginal_delay_days: number;
  marginal_finish_day: number;
  /** What quoting the two marginals side by side is actually worth, 0..1. */
  joint_at_marginal_pair: number;
  cost_delay_correlation: number;
  burn_rate_coupled: boolean;
  /** `[delay_days, total_cost]` per retained iteration, thinned by a fixed stride. */
  scatter: [number, number][];
  scatter_stride: number;
}

export interface CorrelationReport {
  variables: number;
  repair_max_delta: number;
  repaired: boolean;
  min_eigenvalue: number;
  max_pair_error: number;
  mean_pair_error: number;
  notes: string[];
}

export interface SimulationResult {
  manifest: RunManifest;
  deterministic: DeterministicView;
  contingency: ContingencyView;
  risk_cost: SeriesSummary;
  total_cost: SeriesSummary;
  delay_days?: SeriesSummary | null;
  finish_day?: SeriesSummary | null;
  schedule_driven_cost?: SeriesSummary | null;
  risk_sensitivity: RiskSensitivity[];
  schedule_variance_share: number;
  activity_criticality: ActivityCriticality[];
  /** Null on a cost-only run, and on a run too short to place a joint quantile in. */
  joint?: JointConfidence | null;
  correlation: CorrelationReport;
  warnings: string[];
}

/* ------------------------------------------------------------------------- *
 * runs
 * ------------------------------------------------------------------------- */

export interface RunSummary {
  id: number;
  name: string;
  status: RunStatus;
  scenario: string;
  schedule_version_id: number | null;
  iterations: number;
  seed: number;
  sampling: string;
  base_cost: number;
  burn_rate_per_day: number;
  risk_count: number;
  mapped_risk_count: number;
  activity_count: number;
  engine_version: string | null;
  inputs_sha256: string | null;
  gate_passed: boolean | null;
  gate_override: boolean;
  created_by: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error: string | null;
  cancelled_by: string | null;
  cancelled_at: string | null;
}

export interface RunDetail extends RunSummary {
  gate_override_reason: string | null;
  excluded: ExcludedRisk[];
  assembly_notes: string[];
  result: SimulationResult | null;
}
