import type { Category, Risk, RiskCreate, RiskUpdate } from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail =
        typeof body.detail === "string"
          ? body.detail
          : JSON.stringify(body.detail);
    } catch {
      // no JSON body
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function getCategories(): Promise<Category[]> {
  return fetch(`${BASE}/rbs/categories`).then((r) => handle<Category[]>(r));
}

export function getRisks(
  params: { category?: string; status?: string } = {}
): Promise<Risk[]> {
  const qs = new URLSearchParams();
  if (params.category) qs.set("category", params.category);
  if (params.status) qs.set("status", params.status);
  const q = qs.toString();
  return fetch(`${BASE}/risks${q ? `?${q}` : ""}`).then((r) => handle<Risk[]>(r));
}

export function createRisk(payload: RiskCreate): Promise<Risk> {
  return fetch(`${BASE}/risks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => handle<Risk>(r));
}

export function updateRisk(id: number, payload: RiskUpdate): Promise<Risk> {
  return fetch(`${BASE}/risks/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => handle<Risk>(r));
}

export function deleteRisk(id: number): Promise<void> {
  return fetch(`${BASE}/risks/${id}`, { method: "DELETE" }).then((r) =>
    handle<void>(r)
  );
}
