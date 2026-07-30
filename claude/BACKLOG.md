# BACKLOG.md — not yet done

Open when current work is finished, when asked what is pending, or when a watch item may
have fired.

## Blocked — needs a decision from Sam

- Embedding provider: Voyage (hosted, per-token) vs self-hosted BGE-M3 (GPU, or slow on
  CPU). Blocks the ingestion pipeline's index build.
- Single-tenant vs multi-tenant data model. Cheap now, expensive to retrofit after the
  register schema lands. Also now relevant to the mapping suggestion engine's per-request
  corpus scoring — see `REFERENCE.md` 2026-07-29.
- Deployment target (cloud, VPC, on-prem). MPXJ's JRE dependency constrains this.

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

## Surfaced 2026-07-30

- **No ruff config, and the tree is not format-clean at any width.** `make fmt` runs
  `ruff format .` with no line-length flag, so the effective width is ruff's default 88 —
  not the 100 `CLAUDE.md` claimed. At 88, 25 files would reformat; at 100, a different set
  would. The existing code is hand-wrapped at roughly 88. Anyone who runs `make fmt`
  alongside real work gets a large reformat mixed into their diff, which is exactly what
  happened mid-session before it was reverted. Decide: add a config pinning the width and
  reformat the tree once in a dedicated commit, or stop claiming the repo is formatted.
- **No frontend test runner.** `CLAUDE.md` lists Vitest and Playwright;
  `frontend/package.json` has neither, nor any other runner. The Gantt's row flattening,
  subtree collapse and timeline scale are pure functions and the most test-worthy code in
  the 2026-07-30 delivery, and they ship with no committed test — validated only by a
  throwaway esbuild+node script. Adding Vitest is a stack decision, so it was left alone.
- `ScheduleView` → Gantt cross-link. Cut from 2.4: it needs prop plumbing through
  `App.tsx` and an edit to a 12 KB file to save one click, when the nav entry sits next to
  it. `GanttView` is self-contained with its own version picker.
- Postgres regression coverage for the Gantt's naive/aware contract.
  `tests/test_schedule_gantt.py` asserts every payload datetime comes back naive, but
  under SQLite that is trivially true. `tests/test_schedule_postgres_regression.py` is
  where it would actually bite.
- No fixture produces genuinely undated activities. `sample-nodates.xer` is missing the
  *project* data date, not activity dates — every activity in it parses with dates, so
  `counts.undated` reads 0. The `undated` bar path is unit-tested only.
- A far-future `must_finish_by` stretches the timeline and squeezes the bars. The window
  deliberately extends to cover it, because a contract date beyond the forecast finish is
  exactly the slack worth seeing; a data-entry error ten years out would make bars 1px.
  Left as-is — arguably correct feedback about a bad constraint date — but revisit if a
  real schedule makes it painful.
- `REFERENCE.md` is at 193 lines and its Decisions section is now mostly schedule and
  mapping. Next time it grows, split those to `claude/ref/schedule.md` per the split rule
  and add the map row to `CLAUDE.md`.
