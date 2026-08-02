import type {
  ActivityPage,
  AmbiguousProjectChoice,
  BulkAcceptItem,
  BulkAcceptResult,
  CarryForwardResult,
  Category,
  CoverageReport,
  CustomFieldConfig,
  DcmaRun,
  HistoryEntry,
  Mapping,
  MappingCreate,
  MappingHistoryEntry,
  MappingUpdate,
  MatrixConfig,
  MitigationAction,
  MitigationInput,
  Risk,
  RiskCreate,
  RiskUpdate,
  ScheduleDeleteImpact,
  ScheduleDeleteResult,
  ActivityLandings,
  GanttPayload,
  RelationshipPage,
  ScheduleFormat,
  ScheduleUploadResult,
  ScheduleVersionSummary,
  SuggestionResponse,
  ValidateResult,
} from "./types";

import { API_BASE as BASE } from "./config";
import { scopedQuery } from "./scope-state";

/** Which set of scores a matrix is drawn from: pre-mitigation or post-mitigation. */
export type MatrixBasis = "current" | "target";

const ACTOR_KEY = "risk-actor";

export function getActor(): string {
  try {
    return localStorage.getItem(ACTOR_KEY) || "Unknown";
  } catch {
    return "Unknown";
  }
}
export function setActor(name: string) {
  try {
    localStorage.setItem(ACTOR_KEY, name);
  } catch {
    // ignore
  }
}
function writeHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", "X-Actor": getActor() };
}

async function detailOf(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    return res.statusText;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw new Error(`${res.status}: ${await detailOf(res)}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function getCategories(): Promise<Category[]> {
  return fetch(`${BASE}/rbs/categories`).then((r) => handle<Category[]>(r));
}

export function getRisks(
  params: { category?: string; status?: string; limit?: number } = {}
): Promise<Risk[]> {
  return fetch(
    `${BASE}/risks${scopedQuery({
      category: params.category,
      status: params.status,
      limit: params.limit,
    })}`
  ).then((r) => handle<Risk[]>(r));
}

/**
 * The scope is a query parameter rather than part of the body because the server treats
 * it as routing, not content: it resolves the owning project and refuses anything that is
 * not one. Sending it even when it will be refused is deliberate — silently defaulting a
 * risk typed under a program into the default project is the scope-mixing this exists to
 * stop.
 */
export function createRisk(payload: RiskCreate): Promise<Risk> {
  return fetch(`${BASE}/risks${scopedQuery()}`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<Risk>(r));
}

export function updateRisk(id: number, payload: RiskUpdate): Promise<Risk> {
  return fetch(`${BASE}/risks/${id}`, {
    method: "PATCH",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<Risk>(r));
}

export function deleteRisk(id: number): Promise<void> {
  return fetch(`${BASE}/risks/${id}`, {
    method: "DELETE",
    headers: { "X-Actor": getActor() },
  }).then((r) => handle<void>(r));
}

export function getRiskHistory(id: number): Promise<HistoryEntry[]> {
  return fetch(`${BASE}/risks/${id}/history`).then((r) => handle<HistoryEntry[]>(r));
}

/**
 * Platform-wide, deliberately unscoped.
 *
 * `risk_history` carries no scope of its own and outlives the risk it describes, so the
 * only way to narrow this would be a join back to `risk` — which would erase every
 * deleted risk from the audit trail the moment a scope was selected. Scoping the feed
 * properly means denormalising `scope_id` onto the history table in a migration, which is
 * a decision, not a join.
 */
export function getActivity(limit = 100): Promise<HistoryEntry[]> {
  return fetch(`${BASE}/history?limit=${limit}`).then((r) => handle<HistoryEntry[]>(r));
}

export function getActions(riskId: number): Promise<MitigationAction[]> {
  return fetch(`${BASE}/risks/${riskId}/actions`).then((r) =>
    handle<MitigationAction[]>(r)
  );
}

export function createAction(
  riskId: number,
  payload: MitigationInput
): Promise<MitigationAction> {
  return fetch(`${BASE}/risks/${riskId}/actions`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<MitigationAction>(r));
}

export function updateAction(
  riskId: number,
  actionId: number,
  payload: MitigationInput
): Promise<MitigationAction> {
  return fetch(`${BASE}/risks/${riskId}/actions/${actionId}`, {
    method: "PATCH",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<MitigationAction>(r));
}

export function deleteAction(riskId: number, actionId: number): Promise<void> {
  return fetch(`${BASE}/risks/${riskId}/actions/${actionId}`, {
    method: "DELETE",
    headers: { "X-Actor": getActor() },
  }).then((r) => handle<void>(r));
}

export function getMatrixConfig(): Promise<MatrixConfig> {
  return fetch(`${BASE}/matrix-config`).then((r) => handle<MatrixConfig>(r));
}

export function saveMatrixConfig(cfg: MatrixConfig): Promise<MatrixConfig> {
  return fetch(`${BASE}/matrix-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  }).then((r) => handle<MatrixConfig>(r));
}

export function getCustomFields(): Promise<CustomFieldConfig> {
  return fetch(`${BASE}/custom-fields`).then((r) => handle<CustomFieldConfig>(r));
}

export function saveCustomFields(cfg: CustomFieldConfig): Promise<CustomFieldConfig> {
  return fetch(`${BASE}/custom-fields`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  }).then((r) => handle<CustomFieldConfig>(r));
}

/**
 * Stream a file response to a Blob and trigger a browser download, so HTTP errors
 * surface as thrown Errors instead of the user landing on a broken tab.
 */
async function download(url: string, fallbackName: string): Promise<void> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`${res.status}: ${await detailOf(res)}`);
  }

  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const filename = match?.[1] ?? fallbackName;

  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
}

const today = () => new Date().toISOString().slice(0, 10);

export function exportRegister(): Promise<void> {
  return download(
    `${BASE}/export/register.xlsx${scopedQuery()}`,
    `risk_register_${today()}.xlsx`
  );
}

export interface MatrixQuery {
  /** Impact area code, or the overall sentinel understood by the API. */
  lens?: string;
  basis?: MatrixBasis;
  category?: string;
  status?: string;
  owner?: string;
  showCodes?: boolean;
  title?: string;
}

/**
 * Export the matrix exactly as it is currently on screen. Every filter is sent to the
 * server, which rebuilds the same grid from the same placement rules.
 */
export function exportMatrix(
  query: MatrixQuery = {},
  format: "xlsx" | "svg" = "xlsx"
): Promise<void> {
  const q = scopedQuery({
    lens: query.lens,
    basis: query.basis,
    category: query.category,
    status: query.status,
    owner: query.owner,
    show_codes: query.showCodes,
    title: format === "svg" ? query.title : undefined,
  });
  return download(
    `${BASE}/export/matrix.${format}${q}`,
    `risk_matrix_${today()}.${format}`
  );
}

/* ------------------------------------------------------------------------- *
 * schedule
 * ------------------------------------------------------------------------- */

export function getScheduleVersions(
  currentOnly = false
): Promise<ScheduleVersionSummary[]> {
  const q = scopedQuery({ current_only: currentOnly ? true : undefined });
  return fetch(`${BASE}/schedules${q}`).then((r) => handle<ScheduleVersionSummary[]>(r));
}

/** Which formats exist and which this deployment can actually read. */
export function getScheduleFormats(): Promise<ScheduleFormat[]> {
  return fetch(`${BASE}/schedules/formats`).then((r) => handle<ScheduleFormat[]>(r));
}

/**
 * An upload either parses or needs a project chosen, and both are ordinary outcomes.
 * Modelling the second as a thrown error would force the caller to parse an exception
 * to recover the file id it needs to finish the job.
 */
export type UploadOutcome =
  | { kind: "parsed"; result: ScheduleUploadResult }
  | { kind: "ambiguous"; choice: AmbiguousProjectChoice };

export async function uploadSchedule(file: File, projectId?: string): Promise<UploadOutcome> {
  const body = new FormData();
  body.append("file", file);
  body.append("actor", getActor());
  if (projectId) body.append("project_id", projectId);

  // No Content-Type header: the browser must set the multipart boundary itself, and
  // setting it by hand produces a body the server cannot split.
  const res = await fetch(`${BASE}/schedules/upload${scopedQuery()}`, {
    method: "POST",
    headers: { "X-Actor": getActor() },
    body,
  });

  if (res.status === 409) {
    const payload = (await res.json()) as AmbiguousProjectChoice;
    if (payload?.error === "ambiguous_project") return { kind: "ambiguous", choice: payload };
    throw new Error(`409: ${payload?.detail ?? "conflict"}`);
  }
  return { kind: "parsed", result: await handle<ScheduleUploadResult>(res) };
}

/** Finish an ambiguous upload by id, rather than re-sending tens of megabytes. */
export function parseStoredFile(
  fileId: number,
  projectId: string
): Promise<ScheduleUploadResult> {
  const qs = new URLSearchParams({ project_id: projectId, actor: getActor() });
  return fetch(`${BASE}/schedules/files/${fileId}/parse?${qs}`, {
    method: "POST",
    headers: { "X-Actor": getActor() },
  }).then((r) => handle<ScheduleUploadResult>(r));
}

/** The most recent gate run for a version, with the full 14-check report. */
export function getDcma(versionId: number): Promise<DcmaRun> {
  return fetch(`${BASE}/schedules/${versionId}/dcma`).then((r) => handle<DcmaRun>(r));
}

export function getScheduleActivities(
  versionId: number,
  params: { q?: string; status?: string; type?: string; limit?: number } = {}
): Promise<ActivityPage> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.status) qs.set("status", params.status);
  if (params.type) qs.set("type", params.type);
  qs.set("limit", String(params.limit ?? 50));
  return fetch(`${BASE}/schedules/${versionId}/activities?${qs}`).then((r) =>
    handle<ActivityPage>(r)
  );
}

/**
 * Ceiling on bars in one Gantt response. Mirrors `MAX_GANTT_ROWS` in
 * `schedule_gantt.py`, which rejects anything higher with a 422 — asking for more than
 * the server allows is how the mapping tab spent a release insisting no schedule had
 * been imported.
 */
export const GANTT_ROW_LIMIT = 5000;

export interface GanttQuery {
  /** Restrict to a WBS node and everything under it. */
  wbs?: string | null;
  criticalOnly?: boolean;
  q?: string | null;
  limit?: number;
}

/** The whole chart in one request: ordering only makes sense once, server-side. */
export function getGantt(versionId: number, query: GanttQuery = {}): Promise<GanttPayload> {
  const qs = new URLSearchParams();
  if (query.wbs) qs.set("wbs", query.wbs);
  if (query.criticalOnly) qs.set("critical_only", "true");
  if (query.q) qs.set("q", query.q);
  qs.set("limit", String(Math.min(query.limit ?? 2000, GANTT_ROW_LIMIT)));
  return fetch(`${BASE}/schedules/${versionId}/gantt?${qs}`).then((r) =>
    handle<GanttPayload>(r)
  );
}

/**
 * Where risks land on the network, keyed by activity. Separate from the chart on
 * purpose: a `scoped_driver` only resolves against the mapping tables, and a failure
 * here should cost the badges rather than the whole Gantt.
 */
export function getActivityLandings(versionId: number): Promise<ActivityLandings> {
  return fetch(`${BASE}/mappings/activity-landings?version_id=${versionId}`).then((r) =>
    handle<ActivityLandings>(r)
  );
}

/**
 * What a delete would remove, counted server-side without removing any of it.
 *
 * Always fetched before the confirmation opens. The numbers are the whole point of the
 * dialog; without them it is a yes/no on an unknown quantity.
 */
export function getScheduleDeleteImpact(
  versionId: number
): Promise<ScheduleDeleteImpact> {
  return fetch(`${BASE}/schedules/${versionId}/delete-impact`).then((r) =>
    handle<ScheduleDeleteImpact>(r)
  );
}

export interface DeleteScheduleOptions {
  /** Confirm that accepted risk-to-activity mappings may go too. */
  force?: boolean;
  /** Also remove the stored source bytes, if no other version was parsed from them. */
  deleteFile?: boolean;
}

/**
 * Delete a parsed version and everything derived from it.
 *
 * A 409 here is not a failure to report and forget: it means accepted mappings would be
 * lost and the caller has to say so explicitly. `ScheduleView` reopens the confirmation
 * with the acknowledgement rather than swallowing it into the error banner.
 */
export function deleteScheduleVersion(
  versionId: number,
  opts: DeleteScheduleOptions = {}
): Promise<ScheduleDeleteResult> {
  const qs = new URLSearchParams();
  if (opts.force) qs.set("force", "true");
  if (opts.deleteFile) qs.set("delete_file", "true");
  const q = qs.toString();
  return fetch(`${BASE}/schedules/${versionId}${q ? `?${q}` : ""}`, {
    method: "DELETE",
    headers: { "X-Actor": getActor() },
  }).then((r) => handle<ScheduleDeleteResult>(r));
}

/** The links on either side of one activity — why its bar sits where it does. */
export function getRelationshipsTouching(
  versionId: number,
  activitySourceId: string
): Promise<RelationshipPage> {
  const qs = new URLSearchParams({ touching: activitySourceId, limit: "200" });
  return fetch(`${BASE}/schedules/${versionId}/relationships?${qs}`).then((r) =>
    handle<RelationshipPage>(r)
  );
}

/* ------------------------------------------------------------------------- *
 * risk-to-activity mapping
 * ------------------------------------------------------------------------- */

export function getSuggestions(
  versionId: number,
  riskId: number,
  opts: { limit?: number; minScore?: number } = {}
): Promise<SuggestionResponse> {
  const qs = new URLSearchParams({
    version_id: String(versionId),
    risk_id: String(riskId),
  });
  if (opts.limit) qs.set("limit", String(opts.limit));
  if (opts.minScore !== undefined) qs.set("min_score", String(opts.minScore));
  return fetch(`${BASE}/mappings/suggestions?${qs}`).then((r) =>
    handle<SuggestionResponse>(r)
  );
}

export function getMappings(
  versionId: number,
  params: { riskId?: number; status?: string; mappingType?: string } = {}
): Promise<{ items: Mapping[]; count: number }> {
  const qs = new URLSearchParams({ version_id: String(versionId) });
  if (params.riskId !== undefined) qs.set("risk_id", String(params.riskId));
  if (params.status) qs.set("status", params.status);
  if (params.mappingType) qs.set("mapping_type", params.mappingType);
  return fetch(`${BASE}/mappings?${qs}`).then((r) =>
    handle<{ items: Mapping[]; count: number }>(r)
  );
}

export function createMapping(payload: MappingCreate): Promise<Mapping> {
  return fetch(`${BASE}/mappings`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<Mapping>(r));
}

/** Dry run: same checks as create, nothing written. Lets the UI warn before saving. */
export function validateMapping(payload: MappingCreate): Promise<ValidateResult> {
  return fetch(`${BASE}/mappings/validate`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<ValidateResult>(r));
}

export function updateMapping(id: number, payload: MappingUpdate): Promise<Mapping> {
  return fetch(`${BASE}/mappings/${id}`, {
    method: "PATCH",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<Mapping>(r));
}

export function deleteMapping(id: number): Promise<void> {
  return fetch(`${BASE}/mappings/${id}`, {
    method: "DELETE",
    headers: { "X-Actor": getActor() },
  }).then((r) => handle<void>(r));
}

export function bulkAcceptMappings(
  versionId: number,
  riskId: number,
  items: BulkAcceptItem[],
  accept = true
): Promise<BulkAcceptResult> {
  return fetch(`${BASE}/mappings/bulk-accept`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ version_id: versionId, risk_id: riskId, items, accept }),
  }).then((r) => handle<BulkAcceptResult>(r));
}

/**
 * Record a dismissed suggestion. No mapping is created, but the precedent signal learns
 * from it — a ranker that only ever sees its own accepted output confirms itself forever.
 */
export function rejectSuggestion(
  versionId: number,
  riskId: number,
  activitySourceId: string,
  score?: number | null
): Promise<{ recorded: boolean }> {
  return fetch(`${BASE}/mappings/reject-suggestion`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({
      version_id: versionId,
      risk_id: riskId,
      activity_source_id: activitySourceId,
      score: score ?? null,
    }),
  }).then((r) => handle<{ recorded: boolean }>(r));
}

export function getCoverage(versionId: number): Promise<CoverageReport> {
  return fetch(`${BASE}/mappings/coverage?version_id=${versionId}`).then((r) =>
    handle<CoverageReport>(r)
  );
}

export function getScheduleImpactArea(): Promise<{ schedule_impact_area: string | null }> {
  return fetch(`${BASE}/mappings/schedule-area`).then((r) =>
    handle<{ schedule_impact_area: string | null }>(r)
  );
}

export function carryMappingsForward(
  fromVersionId: number,
  toVersionId: number,
  includeProposed = false
): Promise<CarryForwardResult> {
  return fetch(`${BASE}/mappings/carry-forward`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({
      from_version_id: fromVersionId,
      to_version_id: toVersionId,
      include_proposed: includeProposed,
    }),
  }).then((r) => handle<CarryForwardResult>(r));
}

export function getMappingHistory(id: number): Promise<MappingHistoryEntry[]> {
  return fetch(`${BASE}/mappings/${id}/history`).then((r) =>
    handle<MappingHistoryEntry[]>(r)
  );
}
