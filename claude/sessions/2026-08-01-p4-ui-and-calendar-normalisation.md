# 2026-08-01 — P4 finished, and the calendar axis moved

Base commit: `2ddd523` ("Monte Carlo engine backend"). No commits made this session; the
delivery is `p4-monte-carlo-ui.zip`, 30 files, folder-swap, applied by Sam.

## What happened

**The sandbox was not a clone.** `git clone` into `/home/claude/repo` reported success but
produced a directory that already held the previous session's uncommitted P4 work — seven
modified files and fourteen untracked ones. Caught by `git status` returning dirty on a
supposedly fresh clone, and confirmed by mtimes staggered across the previous evening in
authoring order. A genuine clone to a clean path came back at `2ddd523` with none of it.
GitHub MCP had been right the whole time: `api/routes/simulations.py` and `sim-api.ts` are
not in `main`.

Two things were explained away before this was noticed: a baseline of 545 tests against
the 283 in `ACTIVE.md` (read as "docs lag"), and MCP listings that disagreed with the local
tree (not reconciled at all). Both were the same signal.

**The previous session died on one missing file.** `SimulationView.tsx` imports
`../simulation.css`, which was never written, so `vite build` could not resolve it and the
frontend could not build at all. Everything else was complete. Wrote the stylesheet (~600
lines, 63 classes), wired the nav entry in `App.tsx`, documented the new settings in
`.env.example`.

**Then the calendar work**, after Sam hit `Simulation cannot be assembled: activities are
measured against 2 different calendars (CAL-6D, CAL-STD)` on a real schedule. Options 2
(real calendar arithmetic) and 3 (run the CPM in elapsed days) were chosen together; they
compose, because 3 cannot be done without 2.

## Shipped

- `app/schedule/calendars.py` — `add_working_days` (the inverse of the existing
  `working_days_between`), `density`, `describe`. Pure.
- `app/services/sim_calendars.py` — `ScheduleCalendar` rows to `WorkCalendar`, measured
  over the version's own window.
- `sim_assembly.py` — durations and lags converted to elapsed days; the two-calendar
  refusal became a note; working-day risk impacts rescaled; `min_start_day` now set from
  start-on-or-after constraints.
- `tests/test_sim_calendars.py` — 15 tests against closed forms, not against the
  implementation's own output.
- P4 itself: `SimulationRun` + `0013`, Celery app and task, `sim_assembly` / `sim_execute`
  / `sim_dispatch`, `/simulations` routes, and the whole UI.

## Found

- **Live bug**: `rebuild()` unpacked `build_schedule_input` as a 2-tuple after it became a
  3-tuple, breaking every replay path. Four failing tests caught it; reading did not.
- **The zip nearly shipped incomplete.** `git status --porcelain` reports untracked
  *directories* as one entry, so `app/tasks/` and `components/sim/` were silently skipped
  by the packaging loop until `cp` complained. `--untracked-files=all` is required.
- Two tests asserted refusals this change removes, and were rewritten rather than deleted.

## Verified

581 passed, 3 skipped. `tsc --noEmit` and `vite build` clean. Zip unpacked over a pristine
clone and re-run there. TypeScript interfaces diffed field-by-field against the live
OpenAPI schema and the engine's Pydantic models — thirteen types, zero drift.
