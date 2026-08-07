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
- **`sa.text("now()")` in a migration's `server_default` is not portable.** SQLite has no
  `now()` function, so a migration written this way can only be rendered offline for
  Postgres and never executed against SQLite under test. `sa.func.now()` compiles to
  `CURRENT_TIMESTAMP` on SQLite and `now()` on Postgres, and is what 0014 already used as
  convention. 0007 predates the convention and is, as a result, the one migration in the
  tree that has never actually been executed under test (found 2026-08-02, writing 0015).
- **A `LIKE` scan against a column no index covers is exact but not free.**
  `risk_code`'s history-based high-water mark (2026-08-07) filters `risk_history` with
  `LIKE 'prefix-%'` and no index on that column. Correct at every scale tested; worth an
  index on `risk_history.risk_code` if register size ever makes it show up in a profile.
  Escape the pattern (`_like_pattern` in `services/risk_code.py`) before reusing this
  approach anywhere else — an explicit `ScopeNode.code` is taken verbatim and can contain
  `%` or `_`.

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

**Not available 2026-08-07:** a session can have MCP access without clone access — this one
did. Migration and API changes were instead validated against a hand-built SQLite harness
and reconstructed stub modules for the files not touched (`app/models/mitigation.py`,
`api/errors.py`, `core/errors.py`, `db/session.py`), built from the column set the real
`mitigations.py` already implied rather than from the file itself. That is real verification
of the changed logic, but it is not this standard — no `pytest -q`, `ruff`, or
`vite build` ran against the actual tree, and the delivery says so in its own `APPLY.md`
rather than presenting stub-verified as clone-verified. See `claude/ACTIVE.md`.

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

### 2026-08-01 — P4 4.1–4.3 shipped (joint cost-schedule confidence, SSI); 4.7 backend shipped (scope hierarchy schema)

**4.1–4.3.** `app/sim/joint.py` reads cost and delay together: exact frontiers as level
sets of the empirical joint CDF (order-statistic walk, no copula, no fitted bivariate),
the balanced point, and the marginal-pair trap — quoting a P80 cost beside a P80 date
is typically a ~65–75% joint claim, the two-dimensional sibling of the additive-percentile
invariant. No new `RunConfig` field: frontier targets come from the run's own
`percentiles` grid, so the request fingerprint and every stored run's hash are untouched.
`ENGINE_VERSION` → `1.1.0` (no number moved; the bump only lets a stored run be told apart
from one with nothing joint to report). The schedule sensitivity index
(`schedule_sensitivity_index` — CI × duration-sd / project-finish-sd, the Primavera Risk
Analysis metric) sits beside cruciality on `ActivityCriticality`; the two agree exactly
when durations are independent and diverge exactly under a shared risk driver, because
correlation credits each driven activity with the whole of the shared cause's effect while
the spread ratio counts only that activity's own share. Truncation now retains the union
of the top-N by both. Frontend: `JointScatter.tsx` (cloud + frontier + shaded box, target
chips), `Tornado` gained `metric="delay"` (rank correlation with project delay — ranking
only, does not decompose), `CriticalityTable` is now sortable with the SSI column.

**4.7 (backend only — 4.8's scope tree UI and scoped routing not started).** One
`scope_node` table for portfolio/program/project, containment enforced in
`services/scope.py` (`assert_placement` on every write, `assert_move_is_acyclic` on
every move) rather than in the schema. `risk`, `schedule_file`, `simulation_run` each
gained a NOT NULL `scope_id`. Migration `0014` backfills every existing row into one
project, named after the loaded schedule where exactly one exists. Two register
constraints moved from global to per-scope (`uq_risk_scope_subcategory_seq`,
`uq_risk_scope_code`) — every project's register now starts at 0001, and schedule-file
dedup is per scope so an IMS shared across projects doesn't get silently reassigned.
`resolve_write_scope` get-or-creates the default project, so a fresh install never has to
name a scope before adding a risk. **Reads are still unfiltered** — nothing in 4.7 stops
rows from one project appearing in another's register; that's 4.8.

Two gotchas worth carrying forward:

- **`alembic upgrade head` against SQLite has never worked, from migration 0001** —
  `CREATE EXTENSION IF NOT EXISTS vector` is unconditional and Postgres-only. "SQLite
  end-to-end run" in the standing verification method means executing one migration's
  `upgrade()` against a hand-built pre-migration SQLite schema (the pattern
  `test_schedule_migration.py` established for 0009, extended in `test_scope_migration.py`
  for 0014 with real row insertion and backfill assertions, not just a DDL diff), not the
  literal CLI against a bare SQLite file. The offline Postgres SQL render
  (`MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, ...})`) is
  the other half — between the two, every statement in a migration is either executed or
  rendered, never neither.
- **`from __future__ import annotations` breaks `status_code=204` routes.** Confirmed
  again in `app/api/routes/scopes.py` (first hit was the FastAPI-pin gotcha above, a
  different mechanism — that one only shows up unpinned). Under postponed evaluation
  FastAPI reads a `-> None` return annotation as a response body and refuses to register
  the route at all, so this repo's route modules don't use the future import; a comment in
  `scopes.py` exists so nobody adds it back.

### 2026-08-02 — mitigation module (4.4): residual as a scenario, not a new schema

Design settled and shipped this session:

- **No new estimate-shaped table.** `RiskQuantEstimate.scenario`, its
  `uq_quant_risk_scenario` constraint, and `sim_assembly.assemble(scenario=...)` existed
  since 0011/0013 with only `pre_mitigation` ever written. `mitigation_plan_risk` declares
  a **treatment** — `reduce` (factor or absolute), `retire`, `accept` — and *materialising*
  projects it into `RiskQuantEstimate` rows under `scenario="post_mitigation"`. A
  materialised plan is then directly simulable with zero new engine code, proven by
  `tests/test_mitigation_plans_api.py::TestAssemblesAsPostMitigation`. This is what makes
  4.5 (re-simulation ROI) a comparison of two ordinary runs rather than a second engine.
- **The residual register is the whole register.** A risk the plan says nothing about is
  materialised *unchanged*, not omitted. Materialising only the treated subset would
  understate residual contingency, and would do it invisibly — nothing in a run's output
  would say the register was incomplete. `mitigation_plan.load_lines` is driven by the
  baseline register (every risk with a `pre_mitigation` estimate in scope), not by the
  plan's own entries, which makes the rule structural rather than a check someone has to
  remember to run.
- **Nothing in the module claims a benefit.** `MitigationPlanRisk` is a *declaration* —
  what to simulate — never a measured effectiveness score. The old `MitigationAction`
  already had a free-text `effectiveness` field (Low/Medium/High); that stays as-is for
  qualitative tracking, but the quantitative path is deliberately kept apart from it. What
  a package buys is the delta between two Monte Carlo runs (4.5), because the interaction
  between correlated risks and the critical path cannot be multiplied out of a set of
  per-risk factors.
- **Plan cost is deterministic and additive; it is never added to a contingency figure.**
  `MitigationAction` gained `sched_days` (programme the action itself consumes, separate
  from the delay the risk it treats would have caused) alongside the existing `budget`.
  `PlanCost` sums both, reports an `unpriced_count` for actions with neither, and the
  service and the UI both keep this figure structurally apart from anything a simulation
  produces — invariant 1 (no additive percentiles) extended to a new place a "helpful"
  rollup could have violated it.
- **Factors are bounded `(0, 1]`, enforced by CHECK constraints, not just Pydantic.** A
  factor above 1 — a treatment that makes a risk *worse* — is a secondary risk with its
  own cause and belongs in the register as its own line, not hidden inside a multiplier a
  reviewer would have to notice was above one.
- **Materialise guards, not replaces, prior work.** Locked residuals (frozen by a run —
  invariant 6) are stepped over unconditionally. A residual that changed since the plan
  last wrote it (hand-edited, or written by a different plan) requires
  `confirm_replace_edited=true`; the 409 refusal does not half-write, verified by
  `test_overwriting_a_hand_written_residual_needs_confirmation` asserting the row is
  untouched after the blocked call. Attribution runs on a sha256 fingerprint of exactly
  what a materialisation wrote (`mitigation_plan.fingerprint`), stored on the plan —
  the mechanism a future "is this run still measuring this package" check would use.

Two gaps this surfaced rather than fixed, both in `BACKLOG.md`: `sim_assembly.assemble()`
is not scope-filtered (4.8's read-scoping pass didn't reach simulation assembly, and a
mitigation plan's per-project residual register makes this acute for 4.5); and
`MitigationAction.plan_id` has no cross-scope check at assignment time, only at treatment
time.

New portable-migration gotcha found while writing 0015: `sa.text("now()")` in a
`server_default` cannot run under the SQLite migration tests, because SQLite has no
`now()` function — only `sa.func.now()` compiles correctly on both dialects. 0014 already
used the portable form; 0007 predates the convention.

### 2026-08-06 — structured report (4.6): read once, render many, never disagree

Design settled and shipped this session:

- **The database is read in exactly one place.** `services/report/data.gather()` is the
  only function in the report package that touches a session; it returns one frozen
  `ReportData` snapshot. `sections.py`'s twelve builders are pure functions of that
  snapshot — no DB, no clock, no formatting decision — and `render_html.py` /
  `render_xlsx.py` are pure functions of the block tree those builders return. This is the
  same boundary `app/sim/` keeps, for the same reason: a workbook and a printed page that
  each queried the database separately are two chances to disagree about what the P80 was.
  It also means a pptx or PDF renderer later is a new file against the existing `Document`
  type, not a second copy of the query logic — the block model
  (`Paragraph`/`KeyValues`/`Table`/`Callout`/`MatrixBlock`) was built with that renderer
  deliberately unwritten.
- **Naming a run fixes the scope.** When `run_id` is given, the register, the matrix and
  the mitigation actions are read for *that run's own project*, and any `scope_id` also
  sent is ignored — recorded as a note printed in the basis section rather than silently
  honoured or silently dropped. A report whose contingency came from project A and whose
  register came from the portfolio above it would be internally inconsistent in a way no
  reader could see from the document itself; the combination is refused at the data layer
  so no renderer has to know the rule.
- **A section states why it's missing; it never just disappears.** Every entry in the
  registry carries `unavailable: ReportData -> str | None`. `GET /reports/sections`
  answers with these live, for the *actual* parameters given — "Schedule outcome — this
  run simulated cost only" is a fact about the selected run, not a static capability list,
  and the picker in `ReportView.tsx` shows the disabled reason rather than hiding the
  checkbox.
- **The additive-percentile trap (invariant 1) is now printed, not just prevented.**
  `ContingencyView.additive_error_at_p80` already existed on the engine side (2026-07-31);
  4.6 is the first place a human reads it — the cost section prints the integrated P80
  next to what percentiling-then-adding would have produced, labelled as the wrong
  arithmetic, whenever the engine measured a gap. Same pattern for the schedule-driver
  apportionment (delay has no exact additive split among risks — the burn-rate term's own
  share is exact, its division is not) and for burn-rate cost, which is explicitly not
  printed as P80 delay × rate anywhere in the document.
- **A run that failed, is still queued, or whose stored result won't parse is not an
  error at the API layer.** `/report.json|html|xlsx` return 404 only when a named
  `run_id`/`roi_id`/`plan_id` doesn't exist at all. Anything else — wrong status, an
  unreadable `result_json` — becomes a finding printed in the basis section instead of a
  refusal, on the theory that a report is frequently the tool used to *investigate* why a
  run came out that way.
- **Numbers travel unformatted until the last possible step.** `Cell.value` is a bare
  float/int; `format_value()`/`excel_number_format()` are the single shared source of
  truth both renderers call. The XLSX renderer writes real numbers with an Excel number
  format rather than a pre-formatted string, so a client re-summing a column in the
  workbook gets the right answer without retyping it — the reason this mattered enough to
  be a rule rather than a convenience.

Two gaps this surfaced, both in `BACKLOG.md` → Surfaced 2026-08-06: no pptx/PDF renderer
(deferred, not blocked — the block model exists for exactly this), and no end-to-end route
test for a *populated* mitigation/ROI section (covered at the pure-section level and via
`roi_service`'s own suite, not together through the live routes).

### 2026-08-06 — `ACTIVE.md` drift is now a standing condition, not an incident

Fourth occurrence. This session opened with `claude/ACTIVE.md` claiming 695 tests (real:
815) and listing `sim_assembly.assemble()`'s missing scope filter as a blocker for 4.5 —
a gap that was, on inspection of the actual function, already closed, and 4.5 itself was
already shipped and tested on `main`. Neither prior "if this happens again" note
(2026-08-01, 2026-08-02) produced the mechanical check both proposed. Recorded here rather
than only in `BACKLOG.md` because the pattern itself is now the durable fact worth a dated
entry: **`ACTIVE.md`'s claims about what is done should be treated as a hypothesis to check
against `main`, not a fact to build on, every session** — not just when something feels
off. The fix that would actually stop this (a bootstrap step that runs or at least collects
the real test count against a fresh clone before trusting the file) is written up as a
concrete next action in `BACKLOG.md` → Watch items.

### 2026-08-07 — risk code drops the RBS: `<program>-<project>-<sequence>`, allocated from history

`ENV-030-0007` becomes `WTR-PLA-0001`. The identifier now says which project's register a
risk belongs to instead of which taxonomy branch it came from — the thing that stopped
scaling the moment a second project existed, since the code carried no scope information
at all and two projects' registers side by side were indistinguishable. Segments are the
program's (or portfolio's) abbreviation and the project's, from `ScopeNode.code` or a
name-derived fallback (`services/risk_code.py`); a parentless project gets two segments
rather than an invented program above it, matching `scope.py`'s existing position that a
lone project is the day-one shape.

**The RBS is out of the identifier, not out of the system.** Category is still stored,
filtered on, exported, and now returned explicitly as `subcategory_prefix` on every read —
without that field the register would have had no way to show or edit a category at all
once the code stopped carrying it. The consequence worth stating plainly: **recategorising
a risk in place is now safe**, and `RiskUpdate.subcategory_prefix` does it, audited as a
`subcategory` history entry. Previously the only way to fix a miscategorised risk was to
delete it and re-raise it under a new number, because the number *was* the category. This
was added without being asked for this session — flagged in `BACKLOG.md` → Surfaced
2026-08-07 pending confirmation, not presented as settled.

**Numbers are never reissued**, and this took two attempts to get right. `max(seq)` over
the live register looks sufficient and is not: delete the highest-numbered risk in a
project and the next create hands its number straight back, so a code can mean two
different risks depending on when you look — the same failure mode invariant 5 (append-only
audit) exists to prevent, arrived at from a different direction. `next_code()` now also
takes the high-water mark from `risk_history`, which is the only record that outlives a
deleted risk and is queryable by prefix now that the prefix identifies the scope (history
carries no `scope_id` of its own). Migration `0019` renumbers every existing risk
accordingly — not optional, since the old per-subcategory sequence meant two risks in one
project could legitimately both hold `seq = 1` under different subcategories, which the new
per-project scheme cannot represent as two different sequence numbers without a rewrite.

**Two widened columns, not one.** `risk.risk_code` widening 20→100 is the obvious half;
`risk_history.risk_code` needs the same widening and is easy to miss, because a narrow
history column doesn't fail the migration — it fails on the first `RiskHistory` insert
after deploy, which reads as an unrelated 500 in production rather than a migration bug.
History *values* are untouched by 0019; only the column widens. Recorded here because the
same shape of miss (a denormalised copy column that doesn't get the same treatment as the
column it copies) is generic enough to recur elsewhere.

**Verification gap, stated rather than hidden.** This session had MCP read access but no
repo clone, which the standing method (2026-07-30) assumes. The migration was executed for
real against a hand-built pre-0019 SQLite database (16 tests, all four hierarchy shapes);
the API routes were exercised against the real `risks.py` wired to reconstructed stub
modules for the files not touched, built from the column set `mitigations.py` already
implied rather than from the real file. That is genuine verification of the changed logic
and not a substitute for `pytest -q` / `ruff` / `vite build` against the actual tree. Said
directly in this delivery's `APPLY.md` and in `BACKLOG.md` → Surfaced 2026-08-07, rather
than left for the next session to discover the way `ACTIVE.md`'s staleness usually is.
