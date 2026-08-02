# BACKLOG.md — not yet done

Open when current work is finished, when asked what is pending, or when a watch item may
have fired.

## Blocked — needs a decision from Sam

- Embedding provider: Voyage (hosted, per-token) vs self-hosted BGE-M3 (GPU, or slow on
  CPU). Blocks the ingestion pipeline's index build.
- Deployment target (cloud, VPC, on-prem). MPXJ's JRE dependency constrains this.
- **Should the activity feed be scoped?** `risk_history` carries no `scope_id` and is
  designed to outlive the risk it describes — deleting a risk keeps its history row, per
  invariant 5. The only filter available today is a join back to `risk`, which would drop
  every deleted risk's history the moment a scope was selected, quietly breaking that
  invariant. Scoping it properly means denormalising `scope_id` onto `risk_history` in a
  migration, captured at write time from the risk it was created against. Left unscoped
  in 4.8 (`api.ts` `getActivity`, `history.py`), with a comment explaining why, pending
  this decision. Low urgency until a second project makes the platform-wide feed noisy.
- **No ruff config, and the tree is not format-clean at any width.** `make fmt` runs
  `ruff format .` with no line-length flag, so the effective width is ruff's default 88 —
  not the 100 `CLAUDE.md` claimed. At 88, 25 files would reformat; at 100, a different set
  would. The existing code is hand-wrapped at roughly 88. Anyone who runs `make fmt`
  alongside real work gets a large reformat mixed into their diff, which is exactly what
  happened mid-session on 2026-07-30 before it was reverted. Decide: add a config pinning
  the width and reformat the tree once in a dedicated commit, or stop claiming the repo is
  formatted.
- **No frontend test runner.** `CLAUDE.md` lists Vitest and Playwright;
  `frontend/package.json` has neither. Seven deliveries running have now shipped their most
  test-worthy pure functions validated only by throwaway harnesses: the Gantt's row
  flattening and arrow geometry (2.4, 2026-07-30), `SCurve`/`Tornado`/`CriticalityTable`'s
  arithmetic (4.1–4.3), `JointScatter`'s frontier and path construction (4.1 JCL), the
  scope tree's fold/path/subtree/placement functions (4.7/4.8), and now `TreatmentEditor`'s
  factor-bound validation and `ResidualTable`'s delta arithmetic (4.4, 2026-08-02). Adding
  Vitest is a stack decision against a `package.json` deliberately held at two runtime
  dependencies, which is why it keeps deferring. See `REFERENCE.md` 2026-07-30.
- **`sim_assembly.assemble()` is not scope-filtered.** 4.8's read-filtering pass covered
  `list_risks`, `list_versions`, `list_runs`, `options`, both matrix exports, the register
  export, and quant triage/coverage — it did not touch simulation assembly itself. A run
  requested for one project currently reads every project's `risk_quant_estimate` rows
  (and, via mapping, every project's schedule risk-to-activity mappings). Pre-existing
  since before scoping landed; surfaced acutely by 4.4 (2026-08-02) because a mitigation
  plan's residual register is inherently per-project, so an unscoped post-mitigation run
  would read other projects' residuals into a supposedly single-project ROI. **Should
  block the start of 4.5** — see `claude/ACTIVE.md`.

**Resolved 2026-08-01**: single- vs multi-tenant data model. Decided as a strict
portfolio → program → project tree, one parent per node, no project shared across programs.
See `REFERENCE.md` 2026-08-01. 4.7 (schema, backfill, CRUD API) and 4.8 (scope tree
sidebar, scoped routing, scoped reads) both shipped and verified 2026-08-01. This also
unblocks the mapping suggestion engine's per-request corpus scoring noted in
`claude/ref/schedule.md` 2026-07-29 — it can now scope by node instead of guessing.

**Resolved 2026-08-01**: reads were unfiltered by scope even after 4.7's writes were.
`resolve_read_scope` now backs `list_risks`, `list_versions`, `list_runs`, `options`, both
matrix exports, the register export, `quant/triage`, `quant/coverage`. 12 tests in
`test_scoped_reads.py`, reverted against unfiltered code to confirm 4 of them fail without
the filter. The window this created — a second project's rows indistinguishable from the
first's in every list endpoint — no longer exists for those endpoints. (Simulation
assembly was outside this pass — see the new Blocked item above.)

## Subsystems not yet designed in depth

- Living risk register and the realized-outcome learning loop.
- Report export: template engine, section registry, xlsx/pptx/pdf targets.
- Workshop facilitation mode: Delphi anonymous voting, convergence detection, quorum.
- `inserted_activity` mapping UI: API and row-level editing exist (2026-07-29), but there
  is no predecessor/successor picker in the workbench yet — needs a relationship browser.
  **Partly unblocked 2026-07-30**: `GET /schedules/{id}/relationships?touching=<source_id>`
  and the predecessor/successor list in `components/gantt/ActivityDetail.tsx` are the
  primitive it was waiting on. The picker is a reuse job now, not a new endpoint.
- Program/portfolio rollup (P8): register rollup with source-project provenance column,
  shared/escalated risk promotion across projects, quantified-impact + rank-badge surfacing
  in register rows, Method A (master-schedule QRA) and Method B (per-iteration aggregation
  of child runs), and the portfolio/program dashboards. 4.7/4.8 have now landed, so this is
  unblocked. Design in `REFERENCE.md` 2026-08-01.

**Resolved 2026-08-02**: mitigation planning (actions, cost, declared residual). Shipped
as 4.4 — `mitigation_plan` / `mitigation_plan_risk`, materialising into
`RiskQuantEstimate.scenario="post_mitigation"`. Re-simulation ROI (the "with
re-simulation ROI" half of the original line) remains open as 4.5, now the `ACTIVE.md`
build target, and depends on the scope-filtering fix above.

## Watch items

- MPXJ `.mpp` support varies by MS Project version — validate against 2016, 2019, and 365
  files before promising format coverage. **Parked as of 2026-07-29**: risk-to-activity
  mapping work assumes `.xer` only per Sam's direction; revisit when `.mpp` resumes.
- Risk-to-activity mapping lexicon (`app/services/mapping_lexicon.py`) uses guessed RBS
  category codes (`ENV REG ENG PRC CON COM GEO STK ORG EXT`). Swap in Sam's real codes to
  sharpen the `taxonomy` signal — it currently degrades to a name-substring fallback for
  anything that doesn't match.
- Confirm `alembic autogenerate` doesn't emit unexpected diffs now that
  `app.models.schedule` is finally imported in `db/base.py` (2026-07-29 fix — it was
  missing before, so schedule tables were invisible to autogenerate).
- `mapping_suggestion_outcome` precedent signal never decays and is scoped per-subcategory
  only. Fine until there's enough real acceptance/rejection data to evaluate against.
- **Sam's local test environment.** `backend/.venv` (Python 3.13) is missing dev deps and
  the local index caps `pytest-asyncio` at `0.24.0`. Repeated clean-sandbox runs installing
  the exact pins from `requirements.txt` + `requirements-dev.txt` off real PyPI resolve
  `0.25.2` without trouble on Python 3.12 and run the full suite green, so this is the
  local index configuration and not the pin. Running in the `api` container is still the
  fastest path.
- `claude/ref/schedule.md` is at roughly 200 lines as of its first day. If the Gantt notes
  and the mapping notes both keep growing, split again on that seam rather than letting one
  file become the expensive one to open.
- **`ACTIVE.md` drift, twice now.** 2026-08-01: three items marked "pending Sam's local
  apply" had already been committed to `main`. 2026-08-02: recurred — three more pending
  items and a stale test-count baseline (637 vs the real 649) both predated commits already
  on `main`. Treat any "pending apply" line in `ACTIVE.md` older than the current session as
  unconfirmed until checked against `main` — don't assume a prior session's TODO still
  describes reality. Two occurrences is a pattern; if a third happens, this needs a
  mechanical check (e.g. bootstrap diffs `ACTIVE.md`'s claimed test count against a fresh
  clone) rather than a written reminder.
- **New gotcha, 2026-08-02**: `sa.text("now()")` in a migration's `server_default` is not
  portable — SQLite has no `now()`, so such a migration can only be rendered offline, never
  executed under test. `sa.func.now()` compiles correctly on both dialects. 0014 already
  used the portable form; 0007 predates the convention and is the one migration in the
  tree that has never been executed under test as a result. Not urgent to fix retroactively
  — 0007 has no data-migration logic to hide a silent bug — but any new migration should
  use `sa.func.now()`, and 0007 is a candidate if the convention ever needs demonstrating.

## Surfaced 2026-07-30

- **No `DELETE /schedules/files/{id}`.** File deletion rides on the version delete
  (`?delete_file=true`), which covers the real case. Gap: an ambiguous multi-project upload
  that is never parsed strands a `ScheduleFile` with zero versions and no route to remove
  it. Needs a files list in the UI to be worth an endpoint.
- `ScheduleView` → Gantt cross-link. Cut from 2.4: it needs prop plumbing through
  `App.tsx` and an edit to a 12 KB file to save one click, when the nav entry sits next to
  it. `GanttView` is self-contained with its own version picker.
- Postgres regression coverage for the Gantt's naive/aware contract.
  `tests/test_schedule_gantt.py` asserts every payload datetime comes back naive, but
  under SQLite that is trivially true. `tests/test_schedule_postgres_regression.py` is
  where it would actually bite. **Now also true of the delete path**: the promotion and
  cascade ordering in `schedule_delete.py` are exercised only under SQLite, where foreign
  keys are off and the explicit deletes are doing all the work.
- No fixture produces genuinely undated activities. `sample-nodates.xer` is missing the
  *project* data date, not activity dates — every activity in it parses with dates, so
  `counts.undated` reads 0. The `undated` bar path is unit-tested only, and the same now
  applies to the "an undated activity is not linkable" rule.
- A far-future `must_finish_by` stretches the timeline and squeezes the bars. The window
  deliberately extends to cover it, because a contract date beyond the forecast finish is
  exactly the slack worth seeing; a data-entry error ten years out would make bars 1px.
  Left as-is — arguably correct feedback about a bad constraint date — but revisit if a
  real schedule makes it painful.
- Dependency arrows have no real-schedule density test. `sample-clean.xer` draws 22 links
  across 21 activities; nothing in the fixtures approaches the thousands-of-links case the
  `Selected` mode and `MAX_GANTT_LINKS` ceiling exist for. Worth generating a large
  synthetic `.xer` before trusting either.

## Surfaced 2026-08-01

- **Calendar conversion is a measured density, not a date walk.** Exact working-to-elapsed
  conversion is date-dependent — ten working days before a shutdown span more elapsed time
  than ten in June — and doing it properly means carrying dates through the CPM with
  calendar-aware addition per activity per iteration. Not vectorisable, orders of magnitude
  slower, for a correction usually under a percent. Revisit only if a real schedule shows a
  long shutdown that some activities cross and others do not, which is the case where the
  single factor is least accurate.
- **Mandatory-finish constraints are still not enforced.** Start-on-or-after now converts to
  `min_start_day` and the forward pass honours it. A mandatory finish would need the pass to
  push work *earlier*, which it cannot do, so those are still only counted into a warning.
- **No activity duration uncertainty exists in the schema.** Every activity duration reaches
  the engine deterministic, so only discrete risk events move the network and the engine's
  "unrealistically tight base distribution" warning fires on every schedule run. The warning
  is correct. Fixing it means eliciting three-point durations per activity, which is a
  subsystem, not a field.
- **A risk driving activities on several calendars converts with the slowest.** One delay is
  drawn per risk and shared across driven activities (the Hulett semantic), so there is one
  conversion, and the slowest calendar is the conservative choice. Per-activity conversion
  would need the mapping to carry its own factor and would break the shared-draw property.
- **`flower` is in `SYSTEM.md`'s port table (5555) but not in `docker-compose.yml`.** Either
  add the service or drop the row; a port table that lists something that does not exist is
  worse than one that omits it.
- **Runs cannot be deleted, by design and by test.** `test_a_run_cannot_be_deleted` pins it,
  on the append-only invariant. If a register accumulates hundreds of exploratory runs this
  becomes a UI problem (the list is capped at 50) rather than a data one. Archive/hide before
  delete, if it ever needs solving.
- **Portfolio heat view** — when the portfolio node is selected, render the risk matrix as
  small multiples, one mini-matrix per project, so a portfolio manager sees at a glance
  which project's profile is deteriorating. Reuses the existing matrix component in a loop;
  no new visualization primitive needed. Not scheduled — no WBS line yet.
- **Cross-project dedup on rollup** — when the program register unions child registers,
  cluster near-duplicate risks (same name/RBS code across projects) as a cheap preview of
  the P5 dedup work. Often *is* the discovery mechanism for a program-native risk: four
  projects independently carrying "permit delay" usually means it should be promoted, not
  left duplicated. Not scheduled — no WBS line yet.
- **`result_json` payload size is growing.** ~84 kB at 10k iterations with the joint scatter
  included, roughly double pre-JCL. Fine for a JSONB column and a per-run fetch; worth
  revisiting if P6's report export ends up embedding several runs' results at once.
- **Scope delete cascade is untested under Postgres.** `ScopeNode.parent_id` is
  `ondelete="RESTRICT"`, and the API's own refusal (children/rows in the way) makes the
  database-level behaviour mostly unreachable in practice — but "mostly" is doing work
  there, and the existing SQLite `ondelete="CASCADE"` gotcha (foreign keys off by default)
  means the restrict is unverified under the engine that actually enforces it. Add to
  `test_schedule_postgres_regression.py`'s sibling once a scope-focused Postgres regression
  file exists, or fold into that one.
- **`ScopeUpdate` has no code-uniqueness check on rename**, only on create. Renaming a
  scope's `code` to one already in use hits the same `IntegrityError` path `create_scope`
  now guards against. Same fix, not yet applied — low priority until the UI exposes
  renaming a code at all.

## Surfaced 2026-08-02

- **`MitigationAction.plan_id` allows an action to belong to a plan in a different
  scope than the risk it's on.** Nothing in the schema or the service enforces
  `action.risk.scope_id == plan.scope_id` — the API only checks it when *treating* a
  risk (`set_treatment`), not when assigning an action to a plan via
  `PATCH /risks/{id}/actions/{id}`. Low practical risk today (the UI only offers
  actions already in the selected scope), but worth a constraint or a service-level
  check if the actions-across-scopes endpoint (`GET /mitigation/actions`) ever grows
  a cross-scope assignment path.
- **`TreatmentEditor`'s absolute-mode fields have no client-side ordering check.** The
  server refuses an unordered absolute residual and falls back to the baseline
  (`residual_fields`'s conservative-failure design), but the UI doesn't warn before
  save — an analyst finds out only after submitting that their numbers were silently
  not applied. Small: the `issues` array comes back in the residual preview and is
  shown per-row, just not inline in the editor at entry time.
