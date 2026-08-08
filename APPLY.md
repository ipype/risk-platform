# APPLY — simulation tab: schedule dates, deletable percentiles, target-pair pricing

Folder-swap. Unpack over the repo root, paths intact. Fourteen files: twelve replacements,
two new. No migration, no deletion.

```
unzip -o sim-schedule-dates-and-targets.zip -d /path/to/Risk-Platform
cd /path/to/Risk-Platform
git status --porcelain --untracked-files=all
```

**Note: `APPLY.md` is currently tracked on `main`** — the previous delivery's copy was
committed at `b049164` rather than deleted. This zip overwrites it. Delete it before
committing and the stale file leaves `main` with this commit.

## Commit message

```
sim: read the schedule as a date, delete added percentiles, price a target pair

Three things the simulation tab could not do.

1. Every schedule figure came back as a slip in elapsed days. A run now also renders
   its finish series as a duration or as a calendar date, at any marked percentile.
   Day zero comes from the schedule version, exposed as RunDetail.schedule_start_date
   and resolved through the new sim_calendars.version_window(), which is now the single
   owner of that anchor — the same function the calendar conversion counts from, so the
   dates on the screen and the arithmetic behind them cannot drift onto two origins.

2. An added percentile marker vanished from the chip row when unmarked, which made
   "hide this line" and "I mistyped 87" the same irreversible gesture. Added markers now
   persist as chips and carry an explicit delete; presets are furniture and stay.

3. The joint view could only price the pairs its frontiers happened to pass through,
   which is never the pair a board has already fixed. JointConfidence now carries a grid:
   P(delay <= D and cost <= C) counted over every iteration on a mesh at the marginal
   quantiles of each axis. A target on a node is a count; one between nodes is bracketed,
   because the joint CDF cannot dip between two nodes. The panel prints the bracket
   whenever it is wider than half a point rather than quoting to a precision the mesh
   does not have, and says what budget the target date would actually need.

Engine 1.2.0 -> 1.3.0. No number moves; the request fingerprint is untouched, so every
run recorded before this still verifies against its stored hash. A run made before the
bump has no mesh and falls back to the thinned scatter, which is a genuine sample with
genuine error and now says so in those words.
```

## What changed

| File | |
| --- | --- |
| `backend/app/sim/joint.py` | `JointGrid` model and `_grid()`; populated in `joint_confidence` |
| `backend/app/sim/engine.py` | `ENGINE_VERSION` 1.2.0 → 1.3.0, with the reason next to the others |
| `backend/app/sim/__init__.py` | re-export `JointGrid` |
| `backend/app/services/sim_calendars.py` | `version_window()` extracted; `load_calendar_set` now calls it |
| `backend/app/api/routes/simulations.py` | `RunDetail.schedule_start_date`, `day_zero()`, three call sites |
| `backend/tests/sim/test_joint.py` | `TestGrid` — 7 tests, all against brute-force counts |
| `backend/tests/test_simulations_api.py` | 4 tests for the calendar anchor |
| `frontend/src/simulation-types.ts` | `JointGrid`, `JointConfidence.grid`, `RunDetail.schedule_start_date` |
| `frontend/src/components/sim/format.ts` | `parseDay`, `dayToDate`, `dateToDay`, `toIsoDay`, `fmtDate`, `fmtCompactDate` |
| `frontend/src/components/sim/DistributionChart.tsx` | date axis, `dayZero`/`dateOffsetDays`/`defaultAsDate`, deletable markers |
| `frontend/src/components/sim/ScheduleDistribution.tsx` | **new** — slip vs finish switch |
| `frontend/src/components/sim/JointTargets.tsx` | **new** — target-pair pricing |
| `frontend/src/views/SimulationView.tsx` | wires both; the schedule prose moved into `ScheduleDistribution` |
| `frontend/src/simulation.css` | `.sim-chip-group`, `.sim-chip-slot`, `.sim-chip-x`, `.sim-targets*` |

## Decisions — flag anything you want reverted

**Mesh size is 51 × 51, and it is a judgement call.** A node every two marginal
percentiles bounds a mid-cell target to about four points of probability worst case and
well under one in practice, for 17 KB on a 102 KB `result_json` — smaller than the
scatter it sits beside. Doubling the mesh quarters the bound and quadruples the transport.
One constant, `_GRID_NODES` in `joint.py`.

**`schedule_start_date` is resolved at read time, not stored on the run.** No migration.
The schedule version is append-only so the answer cannot move, and a column would be a
second copy of a fact that already has an owner. The cost is one small query per
`GET /simulations/{id}`. If you would rather it were frozen onto the row at creation — so
deleting a schedule version leaves the dates readable rather than nulling them — that is a
migration and a different call, worth making deliberately rather than by default.

**The engine version bumped even though no number moved.** Same reasoning as 1.1.0 and
1.2.0: the bump is what lets the UI tell "this run has no mesh" from "this run measured
nothing". Revertible by pinning it back, at the cost of the fallback message going wrong
on old runs.

**Dates render in UTC, from a bare `YYYY-MM-DD`.** Nothing here is a moment in time.
Parsing the anchor through the local zone puts every reader west of Greenwich a day early
on every date the screen prints.

**The date reading is offered on the finish series only.** A slip of forty days is not a
date, and rendering it as one would be inventing an origin for it.

**Elapsed, not working days, and the caption says so.** A P80 finish landing on a Sunday
is shown on the Sunday rather than moved to the Monday: the forward pass has no working
calendar to move it onto, and quietly nudging the date would make the screen disagree with
the number behind it.

## Verification

Run against a **fresh clone** with this zip unpacked over it, on the repo's pinned deps
(`pip install -r requirements.txt -r requirements-dev.txt`, `npm ci`).

- `python -m pytest -q` → **928 passed, 3 skipped**. Baseline on `main` @ `b049164` is
  917 passed, 3 skipped — 11 new, nothing changed.
- `ruff check .` → 3 errors, the same 3 that are already on `main`, none new
- `ruff format --check .` → 98 would reformat, identical to `main`
- `npx tsc --noEmit --strict` → clean
- `npx vite build` → clean

The grid maths was checked on both sides of the wire. Python `_grid` is asserted against
brute-force counts at nodes, for monotonicity on both axes, for the corners, for the
marginals in the last row and column, and on a degenerate axis where every iteration
finishes on the same day. The TypeScript reader was then run over a real 8000-iteration
grid against fifteen brute-forced targets: **zero bracket misses**, **zero cases where the
pair came out more likely than one of its own marginals**, worst interpolation error 1.3
points, worst bracket 3.4 points, corners exact, monotone under a date sweep, and the
"no budget reaches this" branch fires where it should.

## Still open

No marker is drawn on the scatter at the reader's own target. The panel and the picture
sit beside each other without the picture knowing about the panel; a crosshair and a
shaded box on `JointScatter` would close that, and it is a small change.

The frontend still has no test runner. The joint reading in `JointTargets.tsx` is the most
maths-heavy thing yet put in a component, and it was verified by porting it verbatim into
Node and running it against brute-forced truths — which proves the algorithm and proves
nothing about the component that ships. That gap is now load-bearing rather than
theoretical.
