# 2026-08-01 — Scope UI (4.8) and scoped reads (4.7 close)

## Delivered

`p4-scope-ui.zip`, 19 files, folder-swap. No migration, no image rebuild.

**Frontend (13 files, new unless noted):**
- `config.ts` — hoists `VITE_API_URL` out of three private copies (`api.ts`, `sim-api.ts`,
  `quant/api.ts`), closing the gap `quant/api.ts`'s own comment named.
- `scope-state.ts` — selected scope held outside React, same shape as `getActor`/`setActor`;
  `scopedQuery()` builds the query string every read/write call site now sends.
- `scope-types.ts` — pure functions over the flat node list: tree fold, breadcrumb path,
  subtree, containment rules, parent-move legality, selection fallback. Orphaned or
  cyclic input degrades (promoted to root / dropped) rather than throwing or hanging.
- `scope-api.ts` — `/scopes` client, structured `ScopeApiError` carrying delete-refusal
  reasons.
- `ScopeContext.tsx` — loads the tree once, resolves selection, exposes `useScope()`.
- `components/scope/{ScopeTree,ScopeEditPanel,ScopeBar}.tsx` — sidebar (single tab stop,
  full roving-tabindex arrow-key nav), create/rename/move/default/delete panel, breadcrumb
  bar with the rollup-is-read-only notice.
- `scope.css` — sidebar, tree rows, breadcrumb, drawer behaviour under 900px.
- `App.tsx` (edited) — wraps in `ScopeProvider`, adds the sidebar shell, keys the view host
  on `scope.scopeId` so switching project remounts every view instead of editing ten of
  them individually.
- `api.ts`, `sim-api.ts`, `quant/api.ts` (edited) — every list/create/export call now sends
  `scope_id`. Two calls deliberately left unscoped, each with a comment: the activity feed
  (`risk_history` has no `scope_id` and must outlive deleted risks — invariant 5) and
  drivers (a shared vocabulary, not project-owned).

**Backend (5 routes edited, 1 new test file):**
- `resolve_read_scope` (already existed in `services/scope.py`, called by nothing) is now
  called from `list_risks`, `list_versions` (schedules), `list_runs` + `options`
  (simulations), both matrix exports, the register export, `quant/triage`,
  `quant/coverage`.
- Only `Risk`, `ScheduleFile`, `SimulationRun` carry `scope_id`; schedule versions filter
  through `ScheduleFile.file_id` since the scope belongs to the stored bytes, not the parse.
- `tests/test_scoped_reads.py` — 12 new tests: project reads only its own rows, program and
  portfolio roll up correctly, no-scope reads unfiltered, unknown scope is a named 404, scope
  composes with existing filters, runs and schedule versions follow the same rules. Reverted
  against unfiltered code to confirm 4 of them fail without the join/filter — not vacuous.

## Verified

Fresh clone, real toolchain. `npm ci`, `tsc --noEmit` clean, `vite build` clean (325 kB).
Backend: 649 passed / 3 skipped (baseline 637 + 12 new). `ruff check` clean on every touched
file. Pure scope functions also exercised by a throwaway node harness (tree fold, cycle
handling, breadcrumb, subtree, placement, selection fallback) — same gap as prior sessions,
no runner exists to keep this as a real suite.

## Stale-doc correction

`ACTIVE.md` going into this session listed `p4-monte-carlo-ui.zip`, `p4-jcl-sensitivity.zip`
and `p4-scope-hierarchy.zip` as still pending Sam's local apply. They were not: reading
`backend/app/api/routes/scopes.py`, `services/scope.py` and the passing scope migration /
scopes-API tests directly off `main` this session showed all three already committed before
this chat opened. `ACTIVE.md` had not been updated after that apply. Routed below.

## P4 status after this session

4.1–4.3 (JCL, SSI, delay tornado): shipped, per prior verification.
4.7 (hierarchy schema + CRUD API + UI) and 4.8 (scope tree sidebar + scoped routing,
now including scoped reads): **shipped and verified this session.**
4.4 (mitigation module), 4.5 (re-simulation ROI), 4.6 (first structured report): not
started — still under "subsystems not yet designed in depth."

## Surfaced, not resolved

- Activity feed has no scope filter and needs a migration decision (denormalise
  `scope_id` onto `risk_history`, or accept it stays platform-wide) before it can be
  scoped without breaking invariant 5. Moved to BACKLOG.
- Frontend test runner gap is now three deliveries deep with only throwaway harnesses.
