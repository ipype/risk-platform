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

/**
 * The symbol put in front of every simulated magnitude.
 *
 * The platform stores elicited costs as plain numbers and has no per-project currency
 * field, so for a long time nothing printed a symbol at all rather than invent one. That
 * was the right call about correctness and the wrong call about reading: a column of bare
 * six-figure numbers next to a column of days is ambiguous on the page in a way it never
 * is in someone's head, and every reviewer supplied the missing `$` mentally anyway.
 *
 * So it is printed — but from here, once, not hard-coded at thirty call sites. A
 * deployment in another currency sets `VITE_CURRENCY` and every figure in the app follows.
 * The day a project carries its own currency, this constant becomes that field's default
 * and nothing else has to move.
 */
export const CURRENCY = import.meta.env.VITE_CURRENCY ?? "$";
