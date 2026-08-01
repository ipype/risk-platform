# 2026-08-01 — JCL / SSI / delay tornado (P4 4.1–4.3), scope hierarchy backend (4.7)

Two independent deliveries, no file overlap, verified together and separately from
pristine clones.

## Shipped

**P4 4.1–4.3 — `p4-jcl-sensitivity.zip` (12 files, folder-swap, no migration)**

- `app/sim/joint.py` — joint cost-schedule confidence. Frontiers as exact level sets of the
  empirical joint CDF (order-statistic walk along the delay axis), the balanced point
  (equal marginal stringency on both axes), and the marginal-pair trap: quoting a P80 cost
  beside a P80 date typically claims ~65–75% joint confidence, not 80% — the
  two-dimensional sibling of the additive-percentile invariant. No copula, no fitted
  bivariate; the sample is the joint distribution.
- Schedule sensitivity index on `ActivityCriticality` (`schedule_sensitivity_index`,
  `duration_sd_days`) — the Primavera Risk Analysis metric, CI × duration-sd / project-sd.
  Agrees with cruciality exactly under independent durations, diverges exactly under a
  shared risk driver. Truncation now retains the union of the top-N by both rankings.
- Frontend: `JointScatter.tsx` (new — cloud, frontier, shaded "meets both" box, target
  chips), `Tornado` gained `metric="delay"` (rank correlation with project delay), 
  `CriticalityTable` rewritten sortable with the SSI column.
- No new `RunConfig` field — frontier targets read the run's own `percentiles` grid, so the
  request fingerprint and every stored run's hash are untouched. `ENGINE_VERSION` →
  `1.1.0` (bump only distinguishes old rows from ones with nothing joint to report; no
  number moved).

**4.7 backend — `p4-scope-hierarchy.zip` (25 files, folder-swap, needs `make migrate`)**

- `app/models/scope.py` — one `scope_node` table for portfolio/program/project.
- `app/services/scope.py` — containment order and cycle guard enforced in code, not schema.
- Migration `0014` — creates `scope_node`, backfills `risk` / `schedule_file` /
  `simulation_run` into one auto-created default project (named after the loaded schedule
  where exactly one exists), then tightens the new FK to NOT NULL. Pure SQL over
  subqueries, so it renders offline for Postgres.
- `app/api/routes/scopes.py` — list/create/patch/delete/set-default/subtree, with named
  refusals (`ScopeNotFound` 404, `ScopeInvalid` 422, `ScopeDeleteBlocked` 409 — three new
  handlers registered in `app/api/errors.py`).
- Register uniqueness (`risk_code`, `(subcategory_id, seq)`) moved from global to
  per-scope; schedule-file dedup moved from global to per-scope.
- Write paths scoped: `create_risk`, `store_file`, `create_run`, via `resolve_write_scope`.
- **Not shipped: 4.8.** No scope tree UI, no scoped routing. Every read stays unfiltered.

## Verified

Combined, from a fresh clone with both zips applied (37 files, zero collisions):
- Backend: 637 passed / 3 skipped. `ruff check` clean except 3 pre-existing offenses,
  confirmed present in the untouched pristine clone too.
- `app.main` imports clean.
- Frontend: `tsc --noEmit` clean, `vite build` clean.
- Migration `0014` executed (not just diffed) against a real pre-0014 SQLite database
  built from hand-written DDL — backfill correctness, NOT NULL tightening, per-scope
  uniqueness, single-default constraint, and an offline Postgres SQL render, all in
  `test_scope_migration.py` (11 tests).
- `test_scopes_api.py` (22 tests) exercises the router end-to-end: containment order,
  move validation, delete refusal with named reasons, default-flag handoff, subtree.

## Decisions

- Frontier targets derive from the run's existing percentile grid rather than a new field
  — protects the fingerprint/hash invariant. See `REFERENCE.md` 2026-08-01.
- Scope containment (portfolio < program < project) is enforced in `services/scope.py`,
  not the schema. Consequence: the cycle guard (`assert_move_is_acyclic`) is unreachable
  through the API by construction — any cycle-forming move already violates containment
  order first. It's real defense against a hand-edited row, tested directly at the service
  layer instead of through routes that can't trigger it.
- `alembic upgrade head` against SQLite has never worked (Postgres-only `CREATE EXTENSION`
  in migration 0001, present since day one). "SQLite end-to-end run" in the standing
  verification method means executing one migration's `upgrade()` in isolation against a
  hand-built prior schema — documented in `REFERENCE.md` so this isn't rediscovered.
- `from __future__ import annotations` + `-> None` + `status_code=204` breaks FastAPI route
  registration outright (not just under an unpinned dependency, the earlier-known
  mechanism). `scopes.py` omits the future import; commented so it isn't added back.

## Stale claims corrected

- `ACTIVE.md`'s 4.7 watch item ("backfill migration touches existing tables, needs the
  offline check and a round-trip test") is now satisfied — removed from `BACKLOG.md`.

## Surfaced, not scheduled

See `BACKLOG.md` → Surfaced 2026-08-01 (JCL/SSI and scope hierarchy session): unscoped
reads until 4.8, `JointScatter.tsx` joins the untested-frontend-arithmetic list,
`result_json` payload growth, scope delete cascade untested under Postgres, `ScopeUpdate`
missing the create-path's code-uniqueness guard.

## Next

4.8 — scope tree sidebar and scoped routing across register, mapping, sim, and reports.
Should ship soon: every write is scoped now but every read is not, which is a real
correctness gap the moment a second project scope exists, not just an incomplete feature.
