/**
 * Client for `/scopes`.
 *
 * Its own module for the same reason `sim-api.ts` is: a feature-sized surface with its
 * own failure shape, kept out of the six hundred lines every view already imports.
 *
 * The hierarchy routes never carry `scope_id`. They *are* the scope, and filtering the
 * tree by the node currently selected inside it would hide the rest of the tree from the
 * control used to leave it.
 */

import { getActor } from "./api";
import { API_BASE } from "./config";
import type { ScopeCreate, ScopeNode, ScopeUpdate } from "./scope-types";

/**
 * A refusal from the hierarchy, kept structured.
 *
 * `ScopeDeleteBlocked` sends the list of things standing in the way — child scopes, risks,
 * schedule files, runs — and the panel prints them as a list of next steps. Flattening
 * that into one string would turn four actionable items into one sentence to re-read.
 */
export class ScopeApiError extends Error {
  readonly status: number;
  /** `scope_invalid`, `scope_delete_blocked`, `scope_not_found`, or `http_error`. */
  readonly code: string;
  /** Why a delete was refused, one entry per blocker. Empty for every other failure. */
  readonly reasons: string[];

  constructor(status: number, code: string, detail: string, reasons: string[] = []) {
    super(detail);
    this.name = "ScopeApiError";
    this.status = status;
    this.code = code;
    this.reasons = reasons;
  }
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
    // A proxy error page. There is nothing structured to recover.
  }

  const detail = body.detail;
  const message =
    typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail
            .map((d) =>
              d && typeof d === "object" && "msg" in d
                ? String((d as { msg: unknown }).msg)
                : String(d)
            )
            .join("; ")
        : res.statusText || `Request failed (${res.status})`;

  throw new ScopeApiError(
    res.status,
    typeof body.error === "string" ? body.error : "http_error",
    message,
    Array.isArray(body.reasons) ? (body.reasons as string[]) : []
  );
}

function writeHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", "X-Actor": getActor() };
}

/**
 * Every node, flat. A fresh install has none, and the server creates the default project
 * on this call rather than making the first write invent a hierarchy.
 */
export function listScopes(): Promise<ScopeNode[]> {
  return fetch(`${API_BASE}/scopes`).then((r) => handle<ScopeNode[]>(r));
}

export function createScope(payload: ScopeCreate): Promise<ScopeNode> {
  return fetch(`${API_BASE}/scopes`, {
    method: "POST",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<ScopeNode>(r));
}

/** Only the keys present are sent: `parent_id: null` moves to the root, absent leaves it. */
export function updateScope(id: number, payload: ScopeUpdate): Promise<ScopeNode> {
  return fetch(`${API_BASE}/scopes/${id}`, {
    method: "PATCH",
    headers: writeHeaders(),
    body: JSON.stringify(payload),
  }).then((r) => handle<ScopeNode>(r));
}

/** Never cascades. Refused while anything still points at the node, with the reasons. */
export function deleteScope(id: number): Promise<void> {
  return fetch(`${API_BASE}/scopes/${id}`, {
    method: "DELETE",
    headers: { "X-Actor": getActor() },
  }).then((r) => handle<void>(r));
}

/** Move the flag for where unscoped work lands. Projects only. */
export function setDefaultScope(id: number): Promise<ScopeNode> {
  return fetch(`${API_BASE}/scopes/${id}/default`, {
    method: "POST",
    headers: writeHeaders(),
  }).then((r) => handle<ScopeNode>(r));
}
