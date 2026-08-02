# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] Apply and commit `p4-mitigation-module.zip` (20 files, folder-swap). Ships 4.4
      (mitigation module: actions, plans, cost, declared residual → materialised into
      `post_mitigation` estimates). **Needs a migration**: `0015_mitigation_plans` adds
      `mitigation_plan`, `mitigation_plan_risk`, and two columns (`plan_id`,
      `sched_days`) on `mitigation_action`. `make migrate`, then image rebuild
      (`docker compose up -d --build api worker`) — new router mounted in `main.py`.
- [ ] **Next build target: 4.5 — re-simulation ROI (before/after).** 4.4 leaves this
      cheap on purpose: a materialised plan is already directly simulable under
      `scenario="post_mitigation"` with no new engine path (proven by
      `TestAssemblesAsPostMitigation`). 4.5 is running both scenarios and computing the
      delta — contingency before minus contingency after, next to the plan's
      deterministic cost, never summed into it. **Before starting**, fix the scope gap
      below — a post-mitigation run for one project currently reads every project's
      estimates, which would make an ROI delta wrong from day one.
- [ ] **Known gap to fix first (or explicitly defer) in 4.5**: `sim_assembly.assemble()`
      is not scope-filtered. 4.8 scoped every list/read endpoint but not simulation
      assembly itself — a run for project A currently pulls every project's
      `risk_quant_estimate` rows. Pre-existing since before 4.4; 4.4 just made it acute,
      since a plan's residual register is inherently per-scope. See `BACKLOG.md` →
      Blocked.

## Notes

- **P4 is complete through 4.4.** 4.1 (JCL), 4.2 (SSI), 4.3 (delay tornado), 4.7 (scope
  schema + backfill), 4.8 (scope tree sidebar + scoped reads) all verified and shipped
  before this session — `ACTIVE.md` had drifted and understated this; see
  `claude/sessions/2026-08-02-mitigation-module.md`. Baseline test count as of this
  session's clone: **695 passed / 3 skipped**, folder-swap-verified. 4.5 (re-simulation
  ROI), 4.6 (first structured report) not started.
- Verification for this repo runs against a **local clone** of the real tree, finishing
  with the delivered zip(s) unpacked over a *fresh* clone. Check `git status` on that clone
  before trusting it — a directory that looks like a clone may not be one
  (`REFERENCE.md` 2026-08-01).
- `alembic upgrade head` against SQLite has never worked (Postgres-only `CREATE EXTENSION`
  in migration 0001). "SQLite end-to-end run" means executing one migration's `upgrade()`
  against a hand-built pre-migration database with real rows, not the literal CLI —
  `test_mitigation_migration.py` is 0015's instance of the pattern `test_scope_migration.py`
  established for 0014.
- Sam holds the current copy of `Risk_Platform_Build_Schedule.xlsx` locally — not tracked
  in the repo.
- **Do not trust a "pending Sam's local apply" line in this file once it predates the
  current session** without checking it against `main` first. This is the second time it
  has drifted (first: `REFERENCE.md`/`BACKLOG.md` Watch items, 2026-08-01; second: this
  session, three items and a test count both stale).
