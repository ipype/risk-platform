import type {
  Category,
  HistoryEntry,
  MatrixConfig,
  CustomFieldConfig,
  MitigationAction,
  MitigationInput,
  Risk,
  RiskCreate,
  RiskUpdate,
} from "./types";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
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

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail =
        typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
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
