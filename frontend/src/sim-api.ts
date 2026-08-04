/**
 * Simulation API client.
 *
 * Its own module, and its own `handle`, because `api.ts` does not export one and adding
 * an export there would mean rewriting six hundred lines to add two. The duplication is
 * eight lines and it buys a file that can be read on its own.
 *
 * The one real difference from `api.ts`'s helper is that failures here are structured.
 * A blocked gate and an unassemblable run are both things the screen has to *do*
 * something about — offer an override with a reason, list the issues next to the fields
 * that caused them — and a flattened `"409: ..."` string would have to be re-parsed to
 * get back what the server already said.
 */

import { getActor } from "./api";
import { API_BASE as BASE } from "./config";
import { scopedQuery } from "./scope-state";
import type {
  RunDetail,
  RunPreview,
  RunRequest,
  RunSummary,
  SimulationOptions,
} from "./simulation-types";

/** A domain failure the UI can branch on, rather than a string it has to parse. */
export class SimApiError extends Error {
  readonly status: number;
  /** `schedule_gate_blocked`, `simulation_not_assemblable`, or whatever the API sent. */
  readonly code: string;
  /** Assembly problems, one per thing an analyst can go and fix. */
  readonly issues: string[];
  /** DCMA checks standing between this schedule and a run. */
  readonly blockingFailures: string[];

  constructor(
    status: number,
    code: string,
    detail: string,
    issues: string[] = [],
    blockingFailures: string[] = []
  ) {
    super(detail);
    this.name = "SimApiError";
    this.status = status;
    this.code = code;
    this.issues = issues;
    this.blockingFailures = blockingFailures;
  }
}

function detailText(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    // FastAPI's own 422 shape: a list of per-field errors.
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
        .join("; ");
    }
    if (detail != null) return JSON.stringify(detail);
  }
  return "";
}

async function handle<T>(res: Response): Promise<T> {
  if (res.ok) {
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  let body: Record<string, unknown> = {};
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    // A gateway timeout or a proxy error page: there is nothing structured to recover.
  }
  throw new SimApiError(
    res.status,
    typeof body.error === "string" ? body.error : "http_error",
    detailText(body) || res.statusText || `Request failed (${res.status})`,
    Array.isArray(body.issues) ? (body.issues as string[]) : [],
    Array.isArray(body.blocking_failures) ? (body.blocking_failures as string[]) : []
  );
}

function writeHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", "X-Actor": getActor() };
}

/** Everything the run form needs to render, in one request. */
export function getSimulationOptions(): Promise<SimulationOptions> {
  return fetch(`${BASE}/simulations/options${scopedQuery()}`).then((r) =>
    handle<SimulationOptions>(r)
  );
}

/**
 * What a run would contain, without spending the CPU to find out.
 *
 * Every refusal lives in assembly, so this is where the gate, the calendar checks and the
 * per-risk exclusions surface. Called on every configuration change.
 */
export function previewRun(payload: RunRequest): Promise<RunPreview> {
  return fetch(`${BASE}/simulations/preview${scopedQuery()}`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<RunPreview>(r));
}

export function startRun(payload: RunRequest): Promise<RunDetail> {
  return fetch(`${BASE}/simulations${scopedQuery()}`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<RunDetail>(r));
}

export function getRuns(limit = 50): Promise<RunSummary[]> {
  return fetch(`${BASE}/simulations${scopedQuery({ limit })}`).then((r) =>
    handle<RunSummary[]>(r)
  );
}

export function getRun(id: number): Promise<RunDetail> {
  return fetch(`${BASE}/simulations/${id}`).then((r) => handle<RunDetail>(r));
}

/**
 * Withdraw a run still sitting in ``queued`` — most often one a dead or missing worker
 * was never going to claim. Not a delete: the row stays, now recording who withdrew it
 * and when. Rejected with a structured 409 once the run has left ``queued``.
 */
export function cancelRun(id: number): Promise<RunDetail> {
  return fetch(`${BASE}/simulations/${id}/cancel`, {
    method: "POST",
    headers: writeHeaders(),
  }).then((r) => handle<RunDetail>(r));
}
