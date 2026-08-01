# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] Apply and commit `p4-monte-carlo-ui.zip` (30 files) — still pending from before this
      session, unchanged. Same three steps as before: migration (`make migrate`), image
      rebuild (`docker compose up -d --build api worker`), worker log check.
- [ ] Apply and commit `p4-jcl-sensitivity.zip` (12 files, folder-swap). No migration, no
      image rebuild — `result_json` already stores the result whole. Ships 4.1 (joint
      cost-schedule confidence / JCL), 4.2 (schedule sensitivity index), 4.3 (delay-ranked
      tornado). `ENGINE_VERSION` → `1.1.0`.
- [ ] Apply and commit `p4-scope-hierarchy.zip` (25 files, folder-swap). **Independent of
      the zip above — no file overlap, either order is fine — but needs a migration**:
      `0014_scope_hierarchy` creates `scope_node` and backfills `risk` /
      `schedule_file` / `simulation_run` into one default project. `make migrate`. No image
      rebuild. Ships 4.7's backend (schema, containment rules, CRUD API). **4.8 — the scope
      tree sidebar and scoped routing — is not started; reads are still unfiltered.**
- [ ] Apply and commit the doc close from this session (this file, `BACKLOG.md`,
      `REFERENCE.md`, and the new `sessions/` entry). Safe to combine with either delivery
      above in one commit.
- [ ] **Next build target: 4.8.** Scope tree sidebar + scoped routing across register,
      mapping, sim, and reports, using the schema `p4-scope-hierarchy.zip` lands. Until
      this ships, every read in the platform is unfiltered by scope even though writes are
      scoped — a real gap, not just an unfinished feature.

## Notes

- **P4 is complete through 4.3.** 4.1 (JCL), 4.2 (SSI), 4.3 (delay tornado) verified from a
  pristine clone with both pending zips applied together: 637 passed / 3 skipped, `tsc` and
  `vite build` clean. 4.4 (mitigation module), 4.5 (re-simulation ROI), 4.6 (first
  structured report) not started.
- **4.7 backend is done; 4.7's UI and 4.8 are not.** Scope hierarchy schema, backfill
  migration, and CRUD API exist and are tested end-to-end (migration executed against a
  real pre-0014 SQLite database, not just diffed). No frontend scope picker anywhere yet —
  every write defaults silently to the one auto-created project, which is correct behaviour
  for a single-project install and invisible scope-mixing risk the moment a second project
  exists before 4.8 ships.
- **The engine's schedule axis is elapsed days, not working days.** Read `REFERENCE.md`
  2026-08-01 before touching anything that reads or reports a duration, and
  `ref/simulation.md` before editing the engine.
- Verification for this repo runs against a **local clone** of the real tree, finishing
  with the delivered zip(s) unpacked over a *fresh* clone. Check `git status` on that clone
  before trusting it.
- `alembic upgrade head` against SQLite has never worked (Postgres-only `CREATE EXTENSION`
  in migration 0001) — see `REFERENCE.md` 2026-08-01 for what "SQLite end-to-end run" means
  in practice for this repo.
- Sam holds the current copy of `Risk_Platform_Build_Schedule.xlsx` locally — not tracked
  in the repo.
