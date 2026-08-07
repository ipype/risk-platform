/**
 * Structured report API client.
 *
 * Its own `handle` and its own query builder, for one concrete reason each. `api.ts` does
 * not export a `handle`, as `sim-api.ts` and `roi-api.ts` already note. And `scopedQuery`
 * builds from a flat record, which cannot express `?section=cover&section=cost` — the
 * section filter is repeatable, so it needs `append` rather than `set`.
 *
 * **Scope is not sent when a run is named.** The server would ignore it and record a note
 * saying so, which is right for an API someone drives by hand and wrong for a screen where
 * the scope selector and the run list are already showing the same project. Sending it
 * would put a caveat on every report this UI produces.
 */

import { API_BASE as BASE } from "./config";
import { getScopeId } from "./scope-state";
import type { ReportQuery, ReportSectionsResponse } from "./report-types";

export class ReportApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ReportApiError";
    this.status = status;
  }

  /** Every requested section had nothing to report. */
  get isEmptySelection(): boolean {
    return this.status === 422;
  }
}

async function detailOf(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = (body as { detail?: unknown }).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  } catch {
    return res.statusText;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) throw new ReportApiError(res.status, await detailOf(res));
  return (await res.json()) as T;
}

function queryString(query: ReportQuery): string {
  const qs = new URLSearchParams();
  qs.set("title", query.title);
  if (query.prepared_by) qs.set("prepared_by", query.prepared_by);
  if (query.currency) qs.set("currency", query.currency);
  if (query.lens) qs.set("lens", query.lens);
  if (query.basis) qs.set("basis", query.basis);
  if (query.run_id !== null) qs.set("run_id", String(query.run_id));
  if (query.roi_id !== null) qs.set("roi_id", String(query.roi_id));
  if (query.plan_id !== null) qs.set("plan_id", String(query.plan_id));

  const scopeId = getScopeId();
  if (query.run_id === null && scopeId !== null) qs.set("scope_id", String(scopeId));

  for (const id of query.sections ?? []) qs.append("section", id);
  return `?${qs.toString()}`;
}

/** What this report could contain right now, and the reason for anything it could not. */
export function getReportSections(query: ReportQuery): Promise<ReportSectionsResponse> {
  return fetch(`${BASE}/reports/sections${queryString(query)}`).then((r) =>
    handle<ReportSectionsResponse>(r)
  );
}

/** The URL an iframe can point at for the live preview. */
export function reportPreviewUrl(query: ReportQuery): string {
  return `${BASE}/reports/report.html${queryString(query)}`;
}

/**
 * Stream a rendering to a Blob and trigger a download, so an HTTP error surfaces as a
 * thrown Error rather than the user landing on a broken tab — the same reason `api.ts`
 * does it this way rather than pointing an anchor at the URL.
 */
export async function downloadReport(
  query: ReportQuery,
  format: "html" | "xlsx" | "json"
): Promise<void> {
  const qs = queryString(query) + (format === "html" ? "&download=true" : "");
  const res = await fetch(`${BASE}/reports/report.${format}${qs}`);
  if (!res.ok) throw new ReportApiError(res.status, await detailOf(res));

  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const filename =
    match?.[1] ?? `risk_report_${new Date().toISOString().slice(0, 10)}.${format}`;

  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
}
