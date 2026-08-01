# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] Apply and commit `p4-monte-carlo-ui.zip` (30 files, folder-swap). Three things this
      delivery needs that a plain apply does not do:
      1. **A migration.** `0013_simulation_runs` creates `simulation_run`. `make migrate`.
      2. **An image rebuild.** `requirements.txt` gained `celery==5.5.3` and the `worker`
         service is new: `docker compose up -d --build api worker`. Without the rebuild the
         API still starts — `sim_dispatch` holds the celery import inside the function — but
         the worker crash-loops and every run sits at `queued` forever.
      3. **A worker log check.** `docker compose logs worker --tail=20`. A dead API and a
         queued-forever run look identical from the UI.
- [ ] Apply and commit the doc close from this session (this file, `BACKLOG.md`,
      `REFERENCE.md`, `ref/simulation.md`, `ref/schedule.md`, `CLAUDE.md`, and the new
      `sessions/` entry). Safe to combine with the delivery above in one commit.
- [ ] **Next build target: 4.7 then 4.8.** Hierarchy schema + backfill migration
      (portfolio → program → project, strict tree, one parent per node — no project shared
      across programs), then the scope tree sidebar with scoped routing across register,
      mapping, sim, and reports. Both pulled forward from P8 to land before any P5 table
      exists, so the AI agent's corpus/suggestion/workshop tables get a scope foreign key at
      creation instead of a retrofit. Design detail in `REFERENCE.md` 2026-08-01.

## Notes

- **P4 is complete for what it covered when it shipped** — persistence, the Celery worker,
  the API surface and the whole simulation UI, verified from a pristine clone: 581 passed /
  3 skipped, `tsc` and `vite build` clean. P4 has since grown two more tasks (4.7, 4.8, see
  above) that were not part of that delivery and are not yet started.
- **The engine's schedule axis is now elapsed days, not working days.** This changes the
  meaning of a number people quote. Read `REFERENCE.md` 2026-08-01 before touching anything
  that reads or reports a duration, and `ref/simulation.md` before editing the engine.
- Verification for this repo runs against a **local clone** of the real tree, finishing with
  the delivered zip unpacked over a *fresh* clone. Check `git status` on that clone before
  trusting it — see `REFERENCE.md` 2026-08-01.
- Architecture questions: **tenancy model is now resolved** — strict portfolio → program →
  project tree, `REFERENCE.md` 2026-08-01. Still open: embedding provider, deployment
  target — see `BACKLOG.md` → Blocked. Gantt component decided 2026-07-30 (in-house);
  charts decided 2026-08-01 (hand-rolled SVG, no Recharts); `.mpp` scope stays parked,
  `.xer`-only.
- Sam holds the current copy of `Risk_Platform_Build_Schedule.xlsx` locally — it is not
  tracked in the repo. Total effort now reads 551h (was 465h) after the 2026-08-01
  hierarchy/scoping resequence; see `claude/sessions/2026-08-01-hierarchy-scoping-and-schedule-resequence.md`.
