/**
 * The selected scope, held outside React so the API layer can read it.
 *
 * Deliberately the same shape as `getActor` / `setActor` in `api.ts`: a value every
 * request carries, owned by one module, persisted to `localStorage`, never threaded
 * through props. `ScopeContext` is the only writer; `api.ts`, `sim-api.ts` and
 * `quant/api.ts` are the only readers.
 *
 * No subscription mechanism. React already re-renders from the context that writes here,
 * and a second source of truth for the same number is how the sidebar and the register
 * end up disagreeing about which project is open.
 */

const SCOPE_KEY = "risk-scope";

function read(): number | null {
  try {
    const raw = localStorage.getItem(SCOPE_KEY);
    if (!raw) return null;
    const id = Number(raw);
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

let current: number | null = read();

export function getScopeId(): number | null {
  return current;
}

export function setScopeId(id: number | null): void {
  current = id;
  try {
    if (id === null) localStorage.removeItem(SCOPE_KEY);
    else localStorage.setItem(SCOPE_KEY, String(id));
  } catch {
    // Private browsing, or storage disabled. The selection still holds for this session.
  }
}

/**
 * Stamp the active scope onto a query string.
 *
 * On a read the server answers for the node and everything under it, so a portfolio
 * returns its programs' projects. On a write the server resolves the owning project and
 * refuses anything that is not one. Omitted — which is what happens before the tree has
 * loaded — means unfiltered read and default project on write, the behaviour every call
 * site had before scoping existed.
 */
export function applyScope(qs: URLSearchParams): URLSearchParams {
  const id = getScopeId();
  if (id !== null) qs.set("scope_id", String(id));
  return qs;
}

/**
 * Build a scoped query string, `?a=b&scope_id=3` or `""`.
 *
 * `null` and `undefined` values are dropped; `false` is kept, because a filter that is
 * explicitly off is not the same request as one that was never sent.
 */
export function scopedQuery(
  params: Record<string, string | number | boolean | null | undefined> = {}
): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    qs.set(key, String(value));
  }
  const query = applyScope(qs).toString();
  return query ? `?${query}` : "";
}
