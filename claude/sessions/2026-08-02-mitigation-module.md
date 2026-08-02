# 2026-08-02 — mitigation module (4.4)

## Delivered

`p4-mitigation-module.zip`, 20 files, folder-swap. Not yet applied/committed by Sam —
same status as prior deliveries in this doc close.

- **Backend**: `app/models/mitigation.py` (extended — `MitigationPlan`,
  `MitigationPlanRisk` added; `MitigationAction` gained `plan_id`, `sched_days`),
  `app/services/mitigation_plan.py` (new — pure residual projection + materialisation),
  `app/api/routes/mitigation_plans.py` (new router, mounted in `main.py`), migration
  `0015_mitigation_plans.py`.
- **Frontend**: `MitigateView.tsx`, `TreatmentEditor.tsx`, `ResidualTable.tsx`,
  `PlanCostPanel.tsx`, `mitigation-api.ts`, `mitigation-types.ts`, `mitigation.css`, a
  "Mitigate" nav entry in `App.tsx`, `MitigationActions.tsx` gained a days-consumed field.
- **Tests**: `test_mitigation_residual.py` (17, pure), `test_mitigation_plans_api.py` (22,
  end-to-end), `test_mitigation_migration.py` (7, executed against a hand-built pre-0015
  SQLite database + Postgres offline render).

## Design

No new estimate-shaped table. `RiskQuantEstimate.scenario`, its unique constraint, and
`sim_assembly.assemble(scenario=...)` existed since 0011/0013 and were unused — 4.4 fills
them. A `MitigationPlanRisk` declares a treatment (`reduce`/`retire`/`accept`, factor or
absolute); materialising projects it into `post_mitigation` estimates. A materialised plan
is then directly simulable with zero new engine code — proven by
`TestAssemblesAsPostMitigation` in the API test file, which is the reason 4.5
(re-simulation ROI) should be cheap.

Three rules carry correctness: an untreated risk is materialised **unchanged** (the
residual register is the whole register, not the treated subset — dropping the rest
understates residual contingency invisibly); nothing in the module claims a benefit, only
what to simulate; plan cost (deterministic, additive) never enters a contingency figure
(percentile, not additive — invariant 1 extended to a new place it could be violated).

Factors bounded `(0, 1]` at the database level, not just in the API — a factor above 1 is
a secondary risk pretending to be a mitigation and belongs in the register as its own
line. Locked residuals (frozen by a run, invariant 6) are never overwritten by
materialise; a residual that changed since the plan last wrote it requires
`confirm_replace_edited` and the 409 refusal does not half-write.

## Verified

Fresh clone, zip unpacked over it: 695 passed / 3 skipped (up from a 649/3 baseline —
`ACTIVE.md` was stale, see below), `ruff` clean on every new file, `tsc --noEmit` clean,
`vite build` clean. OpenAPI ↔ TypeScript diffed field-by-field across all 11 new/changed
types (`Plan`, `PlanDetail`, `PlanCost`, `Treatment`, `TreatmentWrite`, `ResidualLine`,
`ResidualPreview`, `MaterializeResult`, `ScopeAction`, `MitigationVocabulary`,
`MitigationAction`) — no drift.

## Stale docs found

`ACTIVE.md` at session start listed three pending-apply items and a 637/3 baseline that
did not match `main` at `aee2e81` — 4.1–4.3 (JCL, SSI, delay tornado) and 4.7/4.8 (scope
hierarchy schema + UI) had already shipped, per `REFERENCE.md`'s own 2026-08-01 entries.
Real baseline on this session's clone was 649 passed / 3 skipped. This is the same
"treat any pending-apply line older than the current session as unconfirmed" pattern
`BACKLOG.md` → Watch items already names — recorded here as a second occurrence, not a
new lesson.

## New gotcha

`sa.text("now()")` in a migration's `server_default` is not portable: SQLite has no
`now()` function, so a migration using it cannot be executed against SQLite under test —
only rendered offline. `sa.func.now()` compiles to `CURRENT_TIMESTAMP` on SQLite and
`now()` on Postgres and is what 0014 already used; 0007 predates the convention and has
never been executed under test as a result. 0015 follows 0014's form.

## Surfaced, not built

- `sim_assembly.assemble()` still reads `risk_quant_estimate` unfiltered by scope (4.8's
  read-filtering pass covered `list_risks`, `list_versions`, `list_runs`, `options`,
  matrix exports, register export, quant triage/coverage — not simulation assembly). A
  post-mitigation run for one project would currently pull every project's estimates.
  Pre-existing gap, not introduced this session, but it lands squarely on 4.4/4.5: a
  mitigation plan's whole point is a per-scope residual register. Moved to `BACKLOG.md` as
  a Blocked-adjacent watch item rather than fixed inline, to keep this delivery to one
  theme.
- `TreatmentEditor`'s factor-bound validation and `ResidualTable`'s delta arithmetic are
  pure, untested frontend logic — the same no-test-runner gap `BACKLOG.md` already tracks,
  now with two more instances.
