/**
 * Client for the quantitative elicitation endpoints.
 *
 * Separate from `api.ts`, which is already ~600 lines and shared by every view. Splitting
 * per feature matches the repo's rule of splitting rather than consolidating, and keeps a
 * change here from producing a diff across the whole API surface.
 *
 * `BASE` is re-derived rather than imported because `api.ts` keeps it module-private.
 * Worth hoisting both into a `config.ts` next time `api.ts` is open for another reason —
 * two reads of the same env var is one too many.
 */

import { getActor } from "../api";
import type {
  QuantCoverage,
  QuantDriver,
  QuantEstimate,
  QuantEstimateResponse,
  QuantEstimateWrite,
  QuantPreview,
  QuantScenario,
  QuantVocabulary,
} from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

function writeHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", "X-Actor": getActor() };
}

/**
 * A 422 from these endpoints is a list of failing rules, not a string. Flattening it into
 * `Error.message` would lose the field each one belongs to, and the form needs that to put
 * the message next to the input that caused it.
 */
export class QuantValidationError extends Error {
  constructor(
    message: string,
    readonly issues: { field: string; message: string }[]
  ) {
    super(message);
    this.name = "QuantValidationError";
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 422) {
    const body = await res.json().catch(() => null);
    if (body?.error === "quant_estimate_invalid") {
      throw new QuantValidationError(body.detail ?? "Estimate is not simulable", body.issues ?? []);
    }
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep the status text */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Shapes and their guidance. Fetched once per session and cached by the caller. */
export function getQuantVocabulary(): Promise<QuantVocabulary> {
  return fetch(`${BASE}/quant/distributions`).then((r) => handle<QuantVocabulary>(r));
}

/**
 * Validate and derive without saving. Called while the analyst types, which is the point:
 * an SME who watches the curve move revises their numbers, and one who never sees it does
 * not. Errors come back in the body rather than as a thrown 422 so a half-typed form does
 * not read as a failure.
 */
export function previewEstimate(payload: QuantEstimateWrite): Promise<QuantPreview> {
  return fetch(`${BASE}/quant/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => handle<QuantPreview>(r));
}

export function getEstimates(riskId: number): Promise<QuantEstimate[]> {
  return fetch(`${BASE}/risks/${riskId}/quant`).then((r) => handle<QuantEstimate[]>(r));
}

export function saveEstimate(
  riskId: number,
  scenario: QuantScenario,
  payload: QuantEstimateWrite
): Promise<QuantEstimateResponse> {
  return fetch(`${BASE}/risks/${riskId}/quant/${scenario}`, {
    method: "PUT",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<QuantEstimateResponse>(r));
}

export function deleteEstimate(riskId: number, scenario: QuantScenario): Promise<void> {
  return fetch(`${BASE}/risks/${riskId}/quant/${scenario}`, {
    method: "DELETE",
    headers: { "X-Actor": getActor() },
  }).then((r) => handle<void>(r));
}

/** Freeze or release an estimate a simulation run depends on. */
export function setEstimateLock(
  riskId: number,
  scenario: QuantScenario,
  locked: boolean
): Promise<QuantEstimate> {
  return fetch(`${BASE}/risks/${riskId}/quant/${scenario}/lock`, {
    method: "PATCH",
    headers: writeHeaders(),
    body: JSON.stringify({ locked }),
  }).then((r) => handle<QuantEstimate>(r));
}

export function getTriage(): Promise<{ risk_ids: number[] }> {
  return fetch(`${BASE}/quant/triage`).then((r) => handle<{ risk_ids: number[] }>(r));
}

export function setTriage(riskIds: number[], quantify: boolean): Promise<{ updated: number }> {
  return fetch(`${BASE}/quant/triage`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify({ risk_ids: riskIds, quantify }),
  }).then((r) => handle<{ updated: number }>(r));
}

export function getQuantCoverage(scenario: QuantScenario): Promise<QuantCoverage> {
  return fetch(`${BASE}/quant/coverage?scenario=${scenario}`).then((r) =>
    handle<QuantCoverage>(r)
  );
}

export function getDrivers(): Promise<QuantDriver[]> {
  return fetch(`${BASE}/drivers`).then((r) => handle<QuantDriver[]>(r));
}

export function getRiskDrivers(riskId: number): Promise<QuantDriver[]> {
  return fetch(`${BASE}/risks/${riskId}/drivers`).then((r) => handle<QuantDriver[]>(r));
}

export function setRiskDrivers(riskId: number, driverIds: number[]): Promise<QuantDriver[]> {
  return fetch(`${BASE}/risks/${riskId}/drivers`, {
    method: "PUT",
    headers: writeHeaders(),
    body: JSON.stringify({ driver_ids: driverIds }),
  }).then((r) => handle<QuantDriver[]>(r));
}
