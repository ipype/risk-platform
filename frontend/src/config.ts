/**
 * Deployment-time configuration, read once.
 *
 * Three modules used to re-derive `VITE_API_URL` privately — `api.ts`, `sim-api.ts` and
 * `quant/api.ts` — and the comment at the top of the third asked for exactly this the
 * next time `api.ts` was open for another reason. It is open for scope threading, so the
 * duplication goes now: one read of the env var, one place to change when a deployment
 * puts the API somewhere other than localhost.
 */

export const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
