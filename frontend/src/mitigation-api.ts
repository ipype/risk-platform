/**
 * Mitigation API client.
 *
 * Its own `handle`, for the same reason `sim-api.ts` has one: `api.ts` does not export
 * its helper and adding an export there would mean editing six hundred lines to gain two.
 *
 * The status code matters here. Materialising refuses with 409 when it would overwrite a
 * residual that has changed since the plan last wrote it, and the screen has to be able to
 * tell that apart from a server error so it can offer the confirmation rather than a
 * shrug.
 */

import { getActor } from "./api";
import { API_BASE as BASE } from "./config";
import { scopedQuery } from "./scope-state";
import type {
  MaterializeResult,
  MitigationVocabulary,
  Plan,
  PlanDetail,
  ResidualPreview,
  ScopeAction,
  Treatment,
  TreatmentWrite,
} from "./mitigation-types";

export class MitigationApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "MitigationApiError";
    this.status = status;
  }

  /** The materialise guard, as opposed to anything else that can go wrong. */
  get needsConfirmation(): boolean {
    return this.status === 409;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }
  let detail = res.statusText || `Request failed (${res.status})`;
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (Array.isArray(body.detail)) {
      detail = body.detail
        .map((d) =>
          d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)
        )
        .join("; ");
    }
  } catch {
    // A proxy error page has nothing structured to recover.
  }
  throw new MitigationApiError(res.status, detail);
}

function writeHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", "X-Actor": getActor() };
}

export function getVocabulary(): Promise<MitigationVocabulary> {
  return fetch(`${BASE}/mitigation/vocabulary`).then((r) => handle<MitigationVocabulary>(r));
}

export function getPlans(): Promise<Plan[]> {
  return fetch(`${BASE}/mitigation/plans${scopedQuery()}`).then((r) => handle<Plan[]>(r));
}

export function getPlan(id: number): Promise<PlanDetail> {
  return fetch(`${BASE}/mitigation/plans/${id}`).then((r) => handle<PlanDetail>(r));
}

export function createPlan(payload: {
  name: string;
  description?: string | null;
}): Promise<PlanDetail> {
  return fetch(`${BASE}/mitigation/plans${scopedQuery()}`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<PlanDetail>(r));
}

export function updatePlan(
  id: number,
  payload: { name?: string; description?: string | null; status?: string }
): Promise<PlanDetail> {
  return fetch(`${BASE}/mitigation/plans/${id}`, {
    method: "PATCH",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<PlanDetail>(r));
}

export function deletePlan(id: number): Promise<void> {
  return fetch(`${BASE}/mitigation/plans/${id}`, {
    method: "DELETE",
    headers: writeHeaders(),
  }).then((r) => handle<void>(r));
}

export function getTreatments(planId: number): Promise<Treatment[]> {
  return fetch(`${BASE}/mitigation/plans/${planId}/risks`).then((r) => handle<Treatment[]>(r));
}

export function setTreatment(
  planId: number,
  riskId: number,
  payload: TreatmentWrite
): Promise<Treatment> {
  return fetch(`${BASE}/mitigation/plans/${planId}/risks/${riskId}`, {
    method: "PUT",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<Treatment>(r));
}

export function clearTreatment(planId: number, riskId: number): Promise<void> {
  return fetch(`${BASE}/mitigation/plans/${planId}/risks/${riskId}`, {
    method: "DELETE",
    headers: writeHeaders(),
  }).then((r) => handle<void>(r));
}

export function getResidual(planId: number): Promise<ResidualPreview> {
  return fetch(`${BASE}/mitigation/plans/${planId}/residual`).then((r) =>
    handle<ResidualPreview>(r)
  );
}

export function materialize(
  planId: number,
  confirmReplaceEdited = false
): Promise<MaterializeResult> {
  return fetch(`${BASE}/mitigation/plans/${planId}/materialize`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ confirm_replace_edited: confirmReplaceEdited }),
  }).then((r) => handle<MaterializeResult>(r));
}

export function getScopeActions(params: {
  planId?: number;
  unassigned?: boolean;
} = {}): Promise<ScopeAction[]> {
  const query = scopedQuery({
    plan_id: params.planId ?? null,
    unassigned: params.unassigned ? true : null,
  });
  return fetch(`${BASE}/mitigation/actions${query}`).then((r) => handle<ScopeAction[]>(r));
}
