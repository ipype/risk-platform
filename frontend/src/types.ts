export interface Subcategory {
  code: string;
  name: string;
  prefix: string;
}
export interface Category {
  code: string;
  name: string;
  subcategories: Subcategory[];
}
export interface Risk {
  id: number;
  risk_code: string;
  title: string;
  description: string | null;
  causes: string | null;
  consequences: string | null;
  status: string;
  probability: number | null;
  impact: number | null;
  impact_scores: Record<string, number> | null;
  risk_level: string | null;
  target_probability: number | null;
  target_impact: number | null;
  target_impact_scores: Record<string, number> | null;
  target_risk_level: string | null;
  mitigation_actions: string | null;
  owner: string | null;
  last_review_date: string | null;
  comments: string | null;
  custom_fields: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}
export interface RiskCreate {
  subcategory_prefix: string;
  title: string;
  description?: string | null;
  causes?: string | null;
  consequences?: string | null;
  status?: string;
  probability?: number | null;
  impact?: number | null;
  impact_scores?: Record<string, number> | null;
  target_probability?: number | null;
  target_impact?: number | null;
  target_impact_scores?: Record<string, number> | null;
  mitigation_actions?: string | null;
  owner?: string | null;
  last_review_date?: string | null;
  comments?: string | null;
  custom_fields?: Record<string, unknown> | null;
}
export type RiskUpdate = Partial<Omit<RiskCreate, "subcategory_prefix">>;

export interface LevelDef {
  level: number;
  label: string;
}
export interface AreaDef {
  code: string;
  name: string;
  descriptors: Record<string, string>;
}
export interface BandDef {
  name: string;
  min_score: number;
  max_score: number;
  color: string;
}
export interface MatrixConfig {
  name: string;
  probability_levels: LevelDef[];
  impact_levels: LevelDef[];
  impact_areas: AreaDef[];
  bands: BandDef[];
}

export interface ChangeItem {
  field: string;
  old: unknown;
  new: unknown;
}
export interface HistoryEntry {
  id: number;
  risk_id: number;
  risk_code: string;
  action: string;
  actor: string;
  changes: ChangeItem[] | null;
  created_at: string;
}

export interface MitigationAction {
  id: number;
  risk_id: number;
  /** The package this action belongs to, if any. Actions may sit outside every plan. */
  plan_id: number | null;
  action: string;
  owner: string | null;
  due_date: string | null;
  budget: number | null;
  /** Programme the action itself consumes, not the delay it removes. */
  sched_days: number | null;
  completion_pct: number | null;
  effectiveness: string | null;
  status: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
}
export interface MitigationInput {
  action?: string;
  owner?: string | null;
  due_date?: string | null;
  budget?: number | null;
  sched_days?: number | null;
  completion_pct?: number | null;
  effectiveness?: string | null;
  status?: string;
  plan_id?: number | null;
}

export interface FieldDef {
  key: string;
  label: string;
  type: string;
  options: string[];
}
export interface CustomFieldConfig {
  fields: FieldDef[];
}

/* ------------------------------------------------------------------------- *
 * schedule
 * ------------------------------------------------------------------------- */

export interface ScheduleVersionSummary {
  id: number;
  file_id: number;
  source_project_id: string;
  project_name: string;
  source_format: string;
  parser_version: string;
  data_date: string | null;
  must_finish_by: string | null;
  activity_count: number;
  relationship_count: number;
  warnings: string[];
  is_current: boolean;
  created_by: string;
  created_at: string;
}

export interface ScheduleActivity {
  id: number;
  source_id: string;
  code: string;
  name: string;
  type: string;
  status: string;
  wbs_source_id: string | null;
  /** Days are meaningless without the calendar they were measured against. */
  duration_calendar_id: string;
  original_duration_days: number | null;
  remaining_duration_days: number | null;
  total_float_days: number | null;
  free_float_days: number | null;
  early_start: string | null;
  early_finish: string | null;
  baseline_finish: string | null;
  actual_start: string | null;
  actual_finish: string | null;
  constraint_type: string;
  is_critical: boolean;
  has_resource_assignment: boolean;
  budgeted_cost: number | null;
}

export interface ScheduleFormat {
  suffixes: string[];
  name: string;
  available: boolean;
  /** Why an unavailable format cannot be read here — shown instead of a silent refusal. */
  reason: string;
}

export type DcmaCheckStatus = "pass" | "fail" | "not_assessed";

export interface DcmaCheck {
  number: number;
  name: string;
  status: DcmaCheckStatus;
  metric: number | null;
  metric_label: string;
  threshold_label: string;
  offender_count: number;
  population: number;
  offenders: string[];
  truncated: boolean;
  note: string;
  /** Checks 1, 7 and 9 by default: failure blocks simulation rather than warning. */
  blocking: boolean;
}

export interface DcmaReportBody {
  project_id: string;
  project_name: string;
  checks: DcmaCheck[];
  thresholds: Record<string, unknown>;
}

export interface DcmaRun {
  run_id: number;
  version_id: number;
  gate_passed: boolean;
  blocking_failures: number[];
  thresholds: Record<string, unknown>;
  run_by: string;
  created_at: string;
  report: DcmaReportBody;
}

export interface GateSummary {
  run_id: number;
  gate_passed: boolean;
  passed: number;
  failed: number;
  not_assessed: number;
  blocking_failures: number[];
}

export interface ScheduleUploadResult {
  version: ScheduleVersionSummary;
  gate: GateSummary;
  /** False when these exact bytes were already stored: the same export mailed round twice. */
  file_created: boolean;
}

export interface AmbiguousProject {
  id: string;
  name: string;
  activity_count: number;
}

/** A 409 from upload: the export holds several projects and one must be chosen. */
export interface AmbiguousProjectChoice {
  error: "ambiguous_project";
  detail: string;
  file_id: number;
  projects: AmbiguousProject[];
}

export interface ActivityPage {
  total: number;
  limit: number;
  offset: number;
  items: ScheduleActivity[];
}

/* ------------------------------------------------------------------------- *
 * risk-to-activity mapping
 * ------------------------------------------------------------------------- */

/**
 * The three ways a risk reaches the network.
 *
 * `duration_driver` stretches an existing activity; every activity a risk drives takes
 * the same sampled factor, which is what makes them correlated. `inserted_activity` adds
 * work that is not in the schedule. `scoped_driver` is a driver aimed at a filter rather
 * than a list, re-resolved on every read.
 */
export type MappingType = "duration_driver" | "inserted_activity" | "scoped_driver";
export type MappingStatus = "proposed" | "accepted" | "rejected" | "superseded";
export type MappingOrigin = "suggested" | "manual" | "carried_forward";
export type SignalName = "lexical" | "taxonomy" | "wbs_affinity" | "precedent";
export type Confidence = "strong" | "moderate" | "weak";
export type MaterialityBand = "high" | "medium" | "low" | "unknown";

/** Reported alongside relevance, never blended into it. */
export interface Materiality {
  band: MaterialityBand;
  why: string;
  total_float_days: number | null;
  is_critical: boolean;
  remaining_duration_days: number | null;
}

export interface MappingCandidate {
  activity_source_id: string;
  activity_code: string;
  activity_name: string;
  activity_type: string;
  activity_status: string;
  wbs_source_id: string | null;
  wbs_path: string;
  remaining_duration_days: number | null;
  total_float_days: number | null;
  is_critical: boolean;
  score: number;
  confidence: Confidence;
  /** `null` means the signal abstained — no evidence, not evidence against. */
  signals: Record<SignalName, number | null>;
  matched_terms: string[];
  recommended_type: MappingType;
  materiality: Materiality;
  warnings: string[];
}

export interface ScopeSuggestion {
  field: string;
  op: string;
  value: string;
  label: string;
  covered: number;
  total_in_scope: number;
}

export interface SuggestionResponse {
  risk_id: number;
  risk_code: string;
  version_id: number;
  suggester_version: string;
  activities_considered: number;
  precedent_available: boolean;
  candidates: MappingCandidate[];
  scope_suggestion: ScopeSuggestion | null;
  already_mapped: string[];
}

export interface MappingScope {
  field: "wbs" | "wbs_path" | "activity_type" | "name" | "code";
  op: "equals" | "starts_with" | "contains";
  value: string;
}

export interface ResolvedActivity {
  activity_source_id: string;
  activity_code: string;
  activity_name: string;
}

export interface Mapping {
  id: number;
  risk_id: number;
  version_id: number;
  mapping_type: MappingType;
  activity_source_id: string | null;
  predecessor_source_id: string | null;
  successor_source_id: string | null;
  scope: MappingScope | null;
  allocation_pct: number | null;
  status: MappingStatus;
  origin: MappingOrigin;
  suggestion_score: number | null;
  suggestion_signals: Record<string, number | null> | null;
  rationale: string | null;
  proposed_by: string;
  decided_by: string | null;
  decided_at: string | null;
  carried_from_id: number | null;
  created_at: string;
  updated_at: string;

  /* Context attached by the API when `include_context` is on. Recomputed against the
   * version on every read rather than stored, so it can never disagree with the data. */
  activity_code?: string;
  activity_name?: string;
  wbs_path?: string;
  predecessor_name?: string | null;
  successor_name?: string | null;
  existing_link?: boolean;
  resolved_count?: number;
  resolved_sample?: ResolvedActivity[];
  materiality?: Materiality | null;
  warnings?: string[];
}

export interface MappingCreate {
  risk_id: number;
  version_id: number;
  mapping_type: MappingType;
  activity_source_id?: string | null;
  predecessor_source_id?: string | null;
  successor_source_id?: string | null;
  scope?: MappingScope | null;
  allocation_pct?: number | null;
  rationale?: string | null;
  origin?: "suggested" | "manual";
  suggestion_score?: number | null;
  suggestion_signals?: Record<string, number | null> | null;
  accept?: boolean;
}

export interface MappingUpdate {
  status?: MappingStatus;
  allocation_pct?: number | null;
  rationale?: string | null;
  scope?: MappingScope | null;
}

export interface BulkAcceptItem {
  activity_source_id: string;
  mapping_type?: MappingType;
  suggestion_score?: number | null;
  suggestion_signals?: Record<string, number | null> | null;
}

export interface BulkAcceptResult {
  created: Mapping[];
  created_count: number;
  refused: { activity_source_id: string; reason: string }[];
}

export interface ValidateResult {
  ok: boolean;
  warnings: string[];
  context: Record<string, unknown>;
}

export interface UnmappedRisk {
  risk_id: number;
  risk_code: string;
  title: string;
  schedule_impact: number | null;
}

export interface UncoveredActivity {
  activity_source_id: string;
  activity_code: string;
  activity_name: string;
  total_float_days: number | null;
  remaining_duration_days: number | null;
}

export interface CoverageReport {
  version_id: number;
  schedule_impact_area: string | null;
  risks_in_scope: number;
  risks_with_accepted_mapping: number;
  risks_with_proposed_only: number;
  risks_unmapped: number;
  coverage_pct: number;
  unmapped: UnmappedRisk[];
  activities_total: number;
  activities_covered: number;
  critical_activities: number;
  /** The half that gets forgotten: driving-path work with nothing pointing at it. */
  critical_activities_uncovered: number;
  critical_uncovered: UncoveredActivity[];
  mappings_total: number;
  mappings_accepted: number;
  mappings_proposed: number;
}

export interface CarryForwardResult {
  from_version_id: number;
  to_version_id: number;
  carried: number;
  skipped_existing: number;
  dropped: { mapping_id: number; risk_id: number; reason: string; activity_code?: string }[];
  dropped_count: number;
}

export interface MappingHistoryEntry {
  id: number;
  mapping_id: number;
  risk_id: number;
  version_id: number;
  action: string;
  actor: string;
  changes: ChangeItem[] | null;
  created_at: string;
}

/* ------------------------------------------------------------------------- *
 * schedule Gantt
 * ------------------------------------------------------------------------- */

/**
 * Which rule chose a bar's dates. Sent per row rather than inferred from the colour,
 * because `planned` on a schedule six months into execution is a finding, not a detail.
 */
export type DateBasis = "actual" | "in_progress" | "planned" | "undated";

export interface GanttBar {
  source_id: string;
  code: string;
  name: string;
  type: string;
  status: string;
  wbs_source_id: string | null;

  start: string | null;
  finish: string | null;
  basis: DateBasis;

  baseline_start: string | null;
  baseline_finish: string | null;
  /** Calendar days, positive for late. Not a working-day duration — see the API docstring. */
  baseline_slip_calendar_days: number | null;

  original_duration_days: number | null;
  remaining_duration_days: number | null;
  total_float_days: number | null;
  /** The calendar every `*_days` value above was measured against. */
  duration_calendar_id: string;

  /** Remaining against original. Not a physical or cost percent complete. */
  duration_pct_complete: number | null;

  is_critical: boolean;
  is_milestone: boolean;
  /** Level-of-effort and WBS-summary rows: they take their dates from what they span. */
  is_summary_row: boolean;
  has_hard_constraint: boolean;
  constraint_type: string;
  budgeted_cost: number | null;
}

export interface GanttWbsRow {
  source_id: string;
  code: string;
  name: string;
  parent_source_id: string | null;
  depth: number;
  path: string;
  /** Rolled up over the subtree and computed before truncation. */
  start: string | null;
  finish: string | null;
  activity_count: number;
  critical_count: number;
}

export interface GanttWindow {
  start: string | null;
  finish: string | null;
  data_date: string | null;
  must_finish_by: string | null;
  baseline_finish: string | null;
}

/** Carried with the chart so a gate-blocked schedule cannot look like a green light. */
export interface GanttGate {
  run_id: number;
  gate_passed: boolean;
  blocking_failures: number[];
  run_at: string;
}

export interface GanttVersion {
  id: number;
  project_name: string;
  source_project_id: string;
  source_format: string;
  parser_version: string;
  is_current: boolean;
  activity_count: number;
  relationship_count: number;
  warnings: string[];
}

/** Totals over the filtered set, before truncation — never derivable from `activities`. */
export interface GanttCounts {
  activities: number;
  critical: number;
  milestones: number;
  /** No usable date. A parse-quality finding: it cannot be scheduled or simulated. */
  undated: number;
  complete: number;
  in_progress: number;
}

/**
 * One dependency, thinned to what an arrow needs.
 *
 * Only links whose *both* endpoints are in `activities` are sent. A link with one end
 * filtered out, truncated away, or pointing into another project has nowhere to
 * terminate, and an arrow into empty space reads as a schedule error rather than a
 * display limit — `GanttLinkCounts.dangling` says how many were left out.
 */
export interface GanttLink {
  source_id: string;
  predecessor_source_id: string;
  successor_source_id: string;
  /** `FS` / `SS` / `FF` / `SF`. Decides which edge of each bar the arrow joins. */
  type: string;
  lag_days: number | null;
  /** Both ends critical. Not a claim the link is driving — that needs a forward pass. */
  is_critical: boolean;
}

export interface GanttLinkCounts {
  /** Relationships stored against this version. */
  total: number;
  /** Both endpoints present in the returned bars, so drawable. */
  drawable: number;
  /** At least one endpoint outside the returned bars. */
  dangling: number;
  truncated: boolean;
}

export interface GanttPayload {
  version: GanttVersion;
  window: GanttWindow;
  counts: GanttCounts;
  gate: GanttGate | null;
  wbs: GanttWbsRow[];
  /** Display order: WBS depth-first, then start date within a node. Kept as sent. */
  activities: GanttBar[];
  /** Dependencies between the activities above, in stored order. */
  links: GanttLink[];
  link_counts: GanttLinkCounts;
  returned: number;
  /** Matching the filter before the limit was applied. */
  total: number;
  truncated: boolean;
  limit: number;
  filters: { wbs: string | null; critical_only: boolean; q: string | null };
}

/** Bucket for activities whose WBS reference is missing or points outside the export. */
export const NO_WBS_KEY = "__no_wbs__";

export type LandingVia = "direct" | "scope" | "insert_predecessor" | "insert_successor";

export interface LandedRisk {
  mapping_id: number;
  risk_id: number;
  risk_code: string | null;
  title: string | null;
  mapping_type: MappingType;
  status: MappingStatus;
  via: LandingVia;
}

/** Counts stay exact even when `risks` is capped; `risks_truncated` says by how much. */
export interface ActivityLanding {
  accepted: number;
  proposed: number;
  risks: LandedRisk[];
  risks_truncated: number;
}

export interface ActivityLandings {
  version_id: number;
  landings: Record<string, ActivityLanding>;
  activities_touched: number;
  risks_landed: number;
  mappings_live: number;
  scoped_drivers: number;
  max_risks_per_activity: number;
}

export interface ScheduleRelationship {
  id: number;
  source_id: string;
  predecessor_source_id: string;
  successor_source_id: string;
  type: string;
  lag_days: number | null;
  lag_calendar_id: string | null;
}

/**
 * What deleting a schedule version would cost, counted before anything is removed.
 *
 * Fetched for the confirmation rather than inferred from a 409: a dialog that cannot name
 * what it is about to destroy asks the analyst to accept a risk nobody has quantified.
 */
export interface ScheduleDeleteImpact {
  version_id: number;
  project_name: string;
  source_project_id: string;
  is_current: boolean;
  activities: number;
  relationships: number;
  wbs_nodes: number;
  calendars: number;
  dcma_runs: number;
  mappings_total: number;
  mappings_accepted: number;
  mappings_proposed: number;
  /** Which version takes over as current. Null when this is the project's only one. */
  promotes_version_id: number | null;
  file_id: number;
  filename: string;
  file_size_bytes: number;
  file_versions_remaining: number;
  file_removable: boolean;
  /** Accepted mappings would be lost, so the delete needs `force`. */
  needs_force: boolean;
}

export interface ScheduleDeleteResult {
  version_id: number;
  project_name: string;
  /** Rows removed, per table. Counted from the delete, not inferred. */
  deleted: Record<string, number>;
  /** `mapping_history` rows written on the way out. History outlives the mapping. */
  mapping_history_kept: number;
  promoted_version_id: number | null;
  file_deleted: boolean;
  file_retained: string | null;
}

export interface RelationshipPage {
  total: number;
  limit: number;
  offset: number;
  items: ScheduleRelationship[];
}
