# REFERENCE.md — the why

Open before editing a subsystem documented here, or when unsure why the code is the way it
is. Invariants, gotchas, dated decisions. Append, do not rewrite history.

Schedule ingestion, the DCMA gate, the Gantt and risk-to-activity mapping split out to
`claude/ref/schedule.md` on 2026-07-30. What stays here is cross-cutting: it applies
whatever subsystem you are in.

The Monte Carlo engine split out to `claude/ref/simulation.md` on 2026-07-31, at creation
rather than after the fact.

## Invariants

### Percentile arithmetic

Percentiles are not additive. Integrating cost contingency with schedule-driven cost must
happen inside each iteration:

```
for i in iterations:
    cost_i  = sample_cost_risks()
    delay_i = simulate_schedule()          # CPM over sampled durations
    total_i = cost_i + delay_i * burn_rate
percentiles(total)                          # once, at the end
```

`P80(cost) + P80(delay) * burn_rate` overstates contingency because it assumes perfect rank
correlation between the two tails. This is the most common error in QSRA output and the most
likely thing to be challenged in review.

### Correlation

Risks are not independent. Weather, labour productivity, and commodity escalation move
together. Iman-Conover rank correlation is applied to the sampled matrix before it reaches
the CPM pass. Independent sampling systematically understates P80/P90.

### Background uncertainty

Activity durations carry inherent variability separate from discrete risk events. Modelling
only discrete risks produces an unrealistically tight base distribution.

### Units

Durations in working days, always paired with the calendar ID used to compute them.
Calendar-agnostic day counts are a silent corruption source across `.xer` imports.

## Gotchas

- **`make fmt` is not safe to run casually.** There is no ruff config in the repo, so
  `ruff format .` uses ruff's default 88 rather than the 100 this file's conventions once
  claimed, and the tree is clean at neither width — 25 files would reformat. Running it
  over pre-existing files pulls hundreds of lines of unrelated reflow into your diff. When
  editing an existing file, match its surrounding hand-wrapped style; new files can be
  format-clean at 88. See `BACKLOG.md` → Surfaced 2026-07-30.
- Verify against the repo's *pinned* dependency versions (`requirements.txt` /
  `requirements-dev.txt`), not whatever a bare `pip install <pkg>` resolves to. An
  unpinned FastAPI silently guards a `-> None` + `status_code=204` edge case that the
  pinned `fastapi==0.115.6` does not — a route crashed on container boot despite passing
  67/67 tests, because the tests ran against a newer, unpinned FastAPI. See the
  2026-07-29 decision below for the exact mechanism.
- **In-memory SQLite is a single connection, and a held transaction deadlocks the suite.**
  SQLAlchemy backs `sqlite+aiosqlite://` with a `StaticPool` — one DBAPI connection for the
  whole engine. A test that reads through a session fixture and then calls the ASGI client
  hangs forever rather than failing: the fixture's session still holds an open transaction,
  the request waits for the only connection, and there is no traceback to read. Note that
  `expire_on_commit` makes this easy to hit by accident, because touching any attribute
  after a commit opens a *new* transaction. Scope every direct database read to its own
  `async with session_factory() as db:` block so the connection is always released
  (found 2026-07-30, cost about twenty minutes).
- **A test harness that creates a subset of tables still needs the whole metadata.**
  `create_all(tables=[...])` cannot emit a foreign key unless the *target* `Table` object
  is registered, even when the target table is deliberately not created. Import
  `app.db.base` in `conftest.py` for its side effect. Without it, whether the harness works
  depends on whether some earlier test module happened to import the missing model first —
  which is how `tests/conftest.py` passed a full-suite run and failed when its own file was
  run alone (found 2026-07-30).
- **`ondelete="CASCADE"` is a Postgres promise, not a portable one.** SQLite ignores foreign
  keys entirely unless `PRAGMA foreign_keys=ON`, so a delete that leans on the database to
  clean up children behaves differently under test than in production. Delete children
  explicitly in dependency order where the result matters — it also lets the code report
  rows it actually removed rather than a number it assumed.

## Decisions

### 2026-07-31 — the percentile and correlation invariants now have code behind them

`app/sim/` lands with 134 tests, of which `tests/sim/test_invariants.py` is the statistical
regression suite the standing rule requires. The invariants above stop being prose:

- **Percentile arithmetic.** `ContingencyView` carries the additive answer next to the
  integrated one and warns when the gap passes 1% of the contingency. On the reference
  fixture the additive method overstates by about 3%.
- **Correlation.** Iman-Conover with a Spearman-to-Pearson conversion and eigenvalue-clip
  repair. `test_independent_sampling_understates_the_tail` holds the mean fixed and shows
  the P90 move, which is the whole argument in one assertion.
- **Background uncertainty.** A schedule where no activity carries a duration distribution
  now produces a warning on the result rather than a quietly tight answer.
- **Units.** `ScheduleInput.calendar_id` is required. Inside the engine a day is a float,
  so this is the last boundary at which the invariant can be enforced.

Design detail, gotchas and the NumPy-only decision are in `claude/ref/simulation.md`.


### 2026-07-24 — doc architecture established

Hub-and-satellite adopted. `CLAUDE.md` is a map read every session; `SYSTEM.md` and
`ACTIVE.md` join it at bootstrap; everything else is trigger-read. Rationale: bootstrap cost
is paid every chat, so it must stay small, and a map means an unread file is never a lost
file. Split, never consolidate.

### 2026-07-29 — verify against pinned dependencies, not resolved-latest

`DELETE /mappings/{id}` crashed the API container on boot: an `async def ... -> None`
return annotation combined with `status_code=204` and no explicit `response_model=None`
resolves to a truthy `NoneType` response model under `fastapi==0.115.6` (the repo's actual
pin), and FastAPI's `assert is_body_allowed_for_status_code(...)` fires *at import time* —
before uvicorn can bind a port. The bug passed 67/67 tests in an earlier verification pass
because that pass ran against an unpinned, newer FastAPI version that silently guards this
exact case. Fix: `response_model=None` explicit in the decorator. Going forward,
verification for this repo must run against `requirements.txt` +
`requirements-dev.txt` pinned exactly — `pip install <pkg>` with no version pin is not a
substitute and can hide version-dependent bugs that only appear in the pinned production
environment.

### 2026-07-30 — verify against a local clone of the real tree

The repo is public and `github.com` / `codeload.github.com` are reachable from the sandbox,
so `git clone --depth 1` gets `main` and verification can run against actual code: the full
pytest suite, `ruff`, `tsc --noEmit`, `vite build`, and a real `.xer` driven end-to-end
through the upload path. Prefer this to a hand-built harness. A harness with stubbed sibling
views is what left the 2026-07-29 mapping frontend delta unconfirmed across two sessions —
it was in fact fine, and one clone would have said so. Writes are still Sam's `git push`;
cloning is read-only and unrelated to the MCP write block.

**Extended 2026-07-30 (second session):** finish by unpacking the delivered zip over a
*fresh* clone and re-running the suite there. Verifying in the working tree proves the code
is right; verifying in a fresh clone proves the zip is, which is the artefact Sam actually
applies. Catches a file staged from the wrong path or omitted from the archive — neither of
which the working tree can tell you about.

### 2026-07-30 — pure frontend logic is verified but not committed

Twice now — the Gantt's row flattening and scale arithmetic in 2.4, the arrow geometry in
this session — the most test-worthy code in a delivery has been validated by a throwaway
`esbuild` + `node` script and shipped with no committed test. The script is real
verification against the real module, not a mock, and the arrow work ran 33 assertions
including a property over all eight routing combinations. But it lives in `/tmp` and dies
with the session, so the third change to that code has nothing to run.

This is a stack decision, not a delivery decision, which is why it keeps getting deferred:
adding Vitest means adding a dev dependency and a `make test` target to a repo that has
deliberately kept `frontend/package.json` at two runtime dependencies. Recorded here so the
cost is visible rather than rediscovered. See `BACKLOG.md` → Surfaced.

### 2026-08-01 — the engine's schedule axis is elapsed days

Assembly used to refuse a schedule whose activities sat on more than one calendar. That
refusal was correct — a CPM adds durations along a path, and adding six-day-week days to
five-day-week days produces a finish date wrong by the ratio between them with nothing
visible to show for it — but it also blocked essentially every real P6 export, because
multi-calendar is the norm: standard week, six-day construction, seven-day continuous, plus
a separate calendar holding the milestones.

Durations, relationship lags and working-day risk impacts are now converted to **elapsed
days** before reaching the engine, and `ScheduleInput.calendar_id` carries the basis label
`"elapsed"` rather than a calendar id. Elapsed days are the only unit several calendars
agree on.

**`app/sim/` did not change.** The engine already treated a day as a bare float, so "run
the CPM in elapsed days" is achieved entirely in the adapter. This was the whole reason the
change was affordable: sim purity held, and all 134 engine tests stayed green through a
change to what every duration in the system means.

Consequences worth knowing before editing anything nearby:

- **Reported delay is in elapsed days.** It is a number people quote, so the UI says so in
  bold above the S-curve rather than leaving it to be discovered in the code.
- **The calendar-day refusal is gone.** A `sched_day_basis="calendar"` estimate is already
  on the engine's axis; converting it would be the error, not the fix.
- **`min_start_day` is finally set**, from start-on-or-after constraints, in elapsed days
  from the data date. It had been permanently `None`.
- **The conversion is a measured density, not a date walk** — real working days over real
  elapsed days across the project's own window, holidays included. Exact for a weekly
  pattern with scattered holidays; least accurate when a long shutdown sits inside the
  window and only some activities cross it. `CalendarDensity.measured` distinguishes a
  measurement from a weekly-pattern fallback, and the run's notes say which happened. An
  approximation nobody can see is what this codebase refuses; one on the face of the result
  is a modelling choice a reviewer can weigh.

### 2026-08-01 — a directory that looks like a clone may not be one

`git clone` into the sandbox reported success and produced a tree that was in fact the
previous session's working directory, carrying two thousand lines of uncommitted work. A
fresh clone cannot have modified or untracked files, and its files all carry the clone's
timestamp; this one was dirty and its mtimes were staggered across the previous evening in
authoring order. **Run `git status` and check one mtime before trusting a clone**, and when
MCP and the local tree disagree about whether a file exists, MCP is describing `main` and
the local tree is describing something else — reconcile it rather than assuming the docs
lagged. Two signals were explained away before this was caught: a test count that did not
match `ACTIVE.md`, and MCP listings missing files the tree had.

### 2026-08-01 — diff the TypeScript against the OpenAPI schema, not just `tsc`

`tsc --noEmit` proves the frontend is internally consistent and says nothing about whether
it agrees with the API. Where a UI and its routes are written in one sitting and never run
together, that is exactly the seam that breaks. Dump `app.openapi()` and the engine's
Pydantic `model_fields`, and diff them field-by-field against the interfaces — thirteen
types took one script and found the contract sound, which is knowledge `tsc` could not have
supplied. Worth repeating on any delivery that adds both a route and its client.

### 2026-08-01 — `git status --porcelain` hides untracked directories

Packaging a delivery by looping over `git status --porcelain` silently omits whole
directories: an untracked directory is reported as a single entry, so `app/tasks/` and
`frontend/src/components/sim/` were dropped from the zip and only `cp` complaining about
`-r` revealed it. Use `--untracked-files=all`. This is precisely the class of error the
fresh-clone verification step exists to catch, and it would have caught it — the reason to
write it down is that the packaging loop looked obviously correct.

### 2026-08-01 — portfolio/program/project hierarchy: scope, rollup methods, and where the schema lands

Sam wants a portfolio of programs and projects, with a collapsible tree that scopes the
register and every downstream page, and QRA results rollable up to program and portfolio
level. Design settled this session:

- **Scope is a context, not a set of separate pages.** The selected tree node — portfolio,
  program, or project — is applied to the existing register, mapping, simulation and report
  pages. No parallel "program register" component; the same component filters by scope.
- **Hierarchy is a strict tree.** One parent per node, no project shared across programs.
  This resolves the tenancy Blocked item in `BACKLOG.md` — it was the same decision wearing
  a different name.
- **Program register holds three classes of risk**: rolled-up (read-only at program level,
  owned and edited only at the project that created it — editing at program level would
  fork the audit trail), escalated (still project-owned, flagged upward when exposure
  crosses a program threshold), and program-native (interfaces, shared procurement, a
  shared site's weather — these map to activities across *multiple* projects using the
  existing shared-draw Hulett semantic: one delay draw per risk, applied everywhere it's
  mapped, same as a single risk driving several activities on one calendar).
- **Two rollup methods, chosen per run, not one over the other**:
  - *Method A — integrated master schedule.* Upload an IMS through the existing `.xer`
    pipeline; the DCMA gate applies unchanged (invariant 3). Best fidelity, requires an
    actual master schedule to exist and pass the gate.
  - *Method B — per-iteration aggregation of child runs.* For programs and portfolios where
    no master schedule exists (the common case above project level), select one completed
    run per child project, re-correlate their already-persisted `RunArrays` iteration
    columns with Iman-Conover, then combine cost and schedule *inside each iteration* before
    percentiling once. This is the percentile-arithmetic invariant extended upward:
    **portfolio P80 is not the sum of project P80s.** A rollup run pins the child run IDs
    and seeds it aggregated (extends invariant 6, reproducibility, to composite runs) — if a
    child project re-simulates afterward, the rollup shows stale rather than silently
    drifting.
- **Quantified impacts surface in the register, but never as a raw per-risk percentile.**
  From a risk's last accepted run: mean cost/schedule impact (means are legitimately
  additive, unlike percentiles), contribution-to-contingency from the tornado decomposition,
  a rank badge from the same decomposition, and a staleness flag when the risk's estimates
  were edited after the run they're quoting. Showing a P80 per risk would recreate the
  additive-percentile mistake one level down — reviewers would sum them.
- **Schema and scope routing come before P5, not after P4's analytics work.** Originally
  scoped as P8 (after P7). Sam correctly pushed it earlier: the AI agent's corpus,
  suggestion, and workshop tables (P5) all need to know which node they belong to, and
  retrofitting a scope foreign key through those tables later is the exact expensive-
  retrofit scenario the tenancy Blocked item warned about. Landed as 4.7 (schema + backfill
  migration) and 4.8 (scope tree sidebar + scoped routing) — after the simulation engine
  that already exists, before anything in P5. The backfill touches existing tables
  (`register`, `schedules`, `simulation_run`, ...), not just new ones — see `BACKLOG.md` →
  Watch items for the verification approach this implies.
- Two smaller ideas surfaced but deliberately not scheduled yet: a portfolio heat view
  (small-multiples risk matrix) and cross-project dedup on rollup (cheap name/RBS-code
  clustering, a preview of P5's dedup work and often the actual discovery mechanism for a
  program-native risk). See `BACKLOG.md` → Surfaced 2026-08-01.
