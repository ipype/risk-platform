# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] Apply and commit `p4-scope-ui.zip` (19 files, folder-swap). No migration, no image
      rebuild. Ships 4.7's UI and 4.8 in full: scope tree sidebar, breadcrumb bar,
      create/rename/move/default/delete panel, and scoped reads across `risks`,
      `schedules`, `simulations`, `export`, `quant` — `list_risks`, `list_versions`,
      `list_runs`, `options`, both matrix exports, the register export, `quant/triage`,
      `quant/coverage`. 12 new backend tests (`test_scoped_reads.py`), reverted against
      unfiltered code to confirm they fail without the filter. 649 passed / 3 skipped,
      `tsc` and `vite build` clean.
- [ ] Apply and commit the doc close from this session (this file, `BACKLOG.md`,
      `REFERENCE.md`, and the new `sessions/` entry). Safe to combine with the delivery
      above in one commit.
- [ ] **Next build target: 4.4, the mitigation module** (actions, cost, residual scoring).
      First of the three P4 items still under "not yet designed in depth" — see
      `BACKLOG.md`. No plan file exists for it yet.

## Notes

- **P4 is complete through 4.3, 4.7, and 4.8.** Verified from a pristine clone this
  session, both backend and frontend. 4.4 (mitigation module), 4.5 (re-simulation ROI),
  4.6 (first structured report) are the only P4 items left, and none are started.
- **Reads are now scoped.** Only `Risk`, `ScheduleFile`, `SimulationRun` carry `scope_id`;
  everything else (mappings, quant estimates) inherits scoping through those. Two reads
  stay deliberately unscoped — the activity feed and the driver vocabulary — each with an
  inline comment explaining why. See `REFERENCE.md` 2026-08-01.
- **Stale-doc correction, now fixed:** this file previously listed
  `p4-monte-carlo-ui.zip`, `p4-jcl-sensitivity.zip`, and `p4-scope-hierarchy.zip` as
  pending Sam's local apply. They were already committed to `main` before this session
  opened — confirmed by reading `scopes.py` and `services/scope.py` directly off `main`.
  Watch for this class of drift: this file only reflects reality if it's updated the
  session an apply actually happens, not assumed from an earlier session's TODO.
- **The engine's schedule axis is elapsed days, not working days.** Read `REFERENCE.md`
  2026-08-01 before touching anything that reads or reports a duration, and
  `ref/simulation.md` before editing the engine.
- Verification for this repo runs against a **local clone** of the real tree, finishing
  with the delivered zip(s) unpacked over a *fresh* clone. Check `git status` on that
  clone before trusting it.
- `alembic upgrade head` against SQLite has never worked (Postgres-only `CREATE EXTENSION`
  in migration 0001) — see `REFERENCE.md` 2026-08-01 for what "SQLite end-to-end run"
  means in practice for this repo.
- Sam holds the current copy of `Risk_Platform_Build_Schedule.xlsx` locally — not tracked
  in the repo.
