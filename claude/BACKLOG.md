# BACKLOG.md — not yet done

Open when current work is finished, when asked what is pending, or when a watch item may
have fired.

## Blocked — needs a decision from Sam

- Embedding provider: Voyage (hosted, per-token) vs self-hosted BGE-M3 (GPU, or slow on
  CPU). Blocks the ingestion pipeline's index build.
- Deployment target (cloud, VPC, on-prem). MPXJ's JRE dependency constrains this.
- **No ruff config, and the tree is not format-clean at any width.** `make fmt` runs
  `ruff format .` with no line-length flag, so the effective width is ruff's default 88 —
  not the 100 `CLAUDE.md` claimed. At 88, 25 files would reformat; at 100, a different set
  would. The existing code is hand-wrapped at roughly 88. Anyone who runs `make fmt`
  alongside real work gets a large reformat mixed into their diff, which is exactly what
  happened mid-session on 2026-07-30 before it was reverted. Decide: add a config pinning
  the width and reformat the tree once in a dedicated commit, or stop claiming the repo is
  formatted.
- **No frontend test runner.** `CLAUDE.md` lists Vitest and Playwright;
  `frontend/package.json` has neither. Two deliveries running — the Gantt's row flattening
  and scale arithmetic (2.4), and the arrow geometry (2026-07-30) — have shipped their
  most test-worthy pure functions validated only by a throwaway `esbuild` + `node` script.
  Adding Vitest is a stack decision against a `package.json` deliberately held at two
  runtime dependencies, which is why it keeps deferring. See `REFERENCE.md` 2026-07-30.

**Resolved 2026-08-01**: single- vs multi-tenant data model. Decided as a strict
portfolio → program → project tree, one parent per node, no project shared across programs.
See `REFERENCE.md` 2026-08-01. Implementation is `ACTIVE.md` → 4.7 (schema + backfill) and
4.8 (scope tree sidebar + scoped routing), both pulled forward to land before P5. This also
unblocks the mapping suggestion engine's per-request corpus scoring noted in
`claude/ref/schedule.md` 2026-07-29 — it can now scope by node instead of guessing.

## Subsystems not yet designed in depth

- Monte Carlo reporting gaps left by P4: JCL scatter (cost against delay per
  iteration), schedule sensitivity index, and a histogram view. The engine already
  returns `histogram` on every `SeriesSummary` and `RunArrays` carries the
  per-iteration columns a JCL plot needs; neither is persisted or drawn.
- Mitigation planning with re-simulation ROI (mitigated vs unmitigated delta).
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
  of child runs), and the portfolio/program dashboards. Depends on 4.7/4.8 landing first.
  Design in `REFERENCE.md` 2026-08-01.

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
  the local index caps `pytest-asyncio` at `0.24.0`. Two clean-sandbox runs (2026-07-30,
  both sessions) installing the exact pins from `requirements.txt` + `requirements-dev.txt`
  off real PyPI resolved `0.25.2` without trouble on Python 3.12 and ran the full suite
  green, so this is the local index configuration and not the pin. Running in the `api`
  container is still the fastest path.
- `claude/ref/schedule.md` is at roughly 200 lines as of its first day. If the Gantt notes
  and the mapping notes both keep growing, split again on that seam rather than letting one
  file become the expensive one to open.
- **4.7's backfill migration touches existing tables** (`register`, `schedules`,
  `simulation_run`, and anything else that gains the scope foreign key), not just new ones.
  Needs the offline Alembic SQL check plus a genuinely separate `AsyncSession` round-trip
  test per the standing verification method — the SQLite `ondelete="CASCADE"` gotcha
  already on file (`REFERENCE.md`) applies directly to a hierarchy delete path.

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
- **The frontend test gap got materially worse.** `SimulationView.tsx` is 631 lines, and
  `SCurve`, `Tornado` and `CriticalityTable` all carry real arithmetic — scale mapping,
  variance-share bar geometry, percentile marker placement — with no runner to test it. This
  is the third delivery to land untested frontend logic. See `REFERENCE.md` 2026-07-30.
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
