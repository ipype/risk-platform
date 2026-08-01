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

## Notes

- **P4 is complete.** Persistence, the Celery worker, the API surface and the whole UI have
  shipped and are verified from a pristine clone: 581 passed / 3 skipped, `tsc` and
  `vite build` clean. Nothing in P4 is outstanding.
- **The engine's schedule axis is now elapsed days, not working days.** This changes the
  meaning of a number people quote. Read `REFERENCE.md` 2026-08-01 before touching anything
  that reads or reports a duration, and `ref/simulation.md` before editing the engine.
- Verification for this repo runs against a **local clone** of the real tree, finishing with
  the delivered zip unpacked over a *fresh* clone. Check `git status` on that clone before
  trusting it — see `REFERENCE.md` 2026-08-01.
- Three of five original architecture questions remain open — see `BACKLOG.md` → Blocked.
  Gantt component decided 2026-07-30 (in-house); charts decided 2026-08-01 (hand-rolled SVG,
  no Recharts); `.mpp` scope stays parked, `.xer`-only.
