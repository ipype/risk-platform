/**
 * Mitigation ROI API client.
 *
 * Its own `handle` for the same reason `sim-api.ts` and `mitigation-api.ts` have theirs:
 * `api.ts` does not export one.
 *
 * Three statuses carry meaning here and the screen branches on all of them. 409 is a
 * package that has not been materialised, or a pair that already exists — both are things
 * the analyst can act on rather than errors. 422 is an incomparable pair, and its detail
 * names every field that moved, so it is shown verbatim rather than flattened into
 * "request failed".
 */

import { getActor } from "./api";
import { API_BASE as BASE } from "./config";
import { scopedQuery } from "./scope-state";
import type { PairRequest, RoiDetail, RoiSummary } from "./roi-types";

export class RoiApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "RoiApiError";
    this.status = status;
  }

  /** The package has no residual register yet, or this pair already exists. */
  get isConflict(): boolean {
    return this.status === 409;
  }

  /** The two runs are not comparable, and `message` says which fields differ. */
  get isNotComparable(): boolean {
    return this.status === 422;
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
  throw new RoiApiError(res.status, detail);
}

function writeHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", "X-Actor": getActor() };
}

export function listComparisons(planId?: number | null): Promise<RoiSummary[]> {
  return fetch(`${BASE}/roi${scopedQuery({ plan_id: planId ?? null })}`).then((r) =>
    handle<RoiSummary[]>(r)
  );
}

/**
 * Read one comparison, optionally at a different percentile.
 *
 * The percentile must be one the runs computed; the grid is fixed at run time and the
 * server refuses to interpolate. A percentile it does not have comes back as a null
 * reduction with a warning rather than a plausible number.
 */
export function getComparison(id: number, percentile?: number | null): Promise<RoiDetail> {
  const qs = percentile == null ? "" : `?percentile=${percentile}`;
  return fetch(`${BASE}/roi/${id}${qs}`).then((r) => handle<RoiDetail>(r));
}

/** Start a matched pair: one set of settings, one seed, two runs. */
export function launchPair(planId: number, payload: PairRequest): Promise<RoiDetail> {
  return fetch(`${BASE}/roi/plans/${planId}/runs`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<RoiDetail>(r));
}

/** Pair two runs that already exist. Refused unless they differ only in scenario. */
export function pairExisting(payload: {
  plan_id: number;
  before_run_id: number;
  after_run_id: number;
  name?: string;
  note?: string | null;
  percentile?: number;
}): Promise<RoiDetail> {
  return fetch(`${BASE}/roi`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<RoiDetail>(r));
}
