# BACKLOG.md — not yet done

Open when current work is finished, when asked what is pending, or when a watch item may
have fired.

## Blocked — needs a decision from Sam

- Embedding provider: Voyage (hosted, per-token) vs self-hosted BGE-M3 (GPU, or slow on
  CPU). Blocks the ingestion pipeline's index build.
- Single-tenant vs multi-tenant data model. Cheap now, expensive to retrofit after the
  register schema lands. Also now relevant to the mapping suggestion engine's per-request
  corpus scoring — see `claude/ref/schedule.md` 2026-07-29.
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

## Subsystems not yet designed in depth

- Monte Carlo engine: LHS, Beta-PERT fitting, JCL scatter, criticality index, SSI.
- Mitigation planning with re-simulation ROI (mitigated vs unmitigated delta).
- Living risk register and the realized-outcome learning loop.
- Report export: template engine, section registry, xlsx/pptx/pdf targets.
- Workshop facilitation mode: Delphi anonymous voting, convergence detection, quorum.
- `inserted_activity` mapping UI: API and row-level editing exist (2026-07-29), but there
  is no predecessor/successor picker in the workbench yet — needs a relationship browser.
  **Partly unblocked 2026-07-30**: `GET /schedules/{id}/relationships?touching=<source_id>`
  and the predecessor/successor list in `components/gantt/ActivityDetail.tsx` are the
  primitive it was waiting on. The picker is a reuse job now, not a new endpoint.

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
