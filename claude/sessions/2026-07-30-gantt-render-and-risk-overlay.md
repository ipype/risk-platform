# 2026-07-30 — Gantt render (2.4) and the risk-landings overlay

Started from `fc10636` ("Schedule frontend debugged"). Build-schedule item **2.4 Store
schedule + render on Gantt**. Storage had already shipped in `0009`, so this was render
plus the two endpoints render needed.

## Commits

None by me. Delivered `gantt-2.4.zip` — 14 files, folder-swap, paths intact — for Sam to
apply and commit. No migration: every endpoint added is read-only and no schema changed.

GitHub MCP write access was **not** retested, per the standing instruction. No write was
attempted.

## Verification ran against a local clone — this is new and should stay

The repo is public and `github.com` / `codeload.github.com` are reachable from the
sandbox, so `git clone --depth 1` gets the real tree. Everything below ran against
`fc10636` itself rather than a harness with stubbed siblings, which is what left the
2026-07-29 frontend delta unconfirmed for two sessions. Prefer this from now on.

All Python deps installed at the exact pins in `requirements.txt` +
`requirements-dev.txt`, per the 2026-07-29 standing rule.

- 256 backend tests pass (204 baseline + 52 new). `ruff check` clean on all six touched
  files; the only three findings repo-wide are pre-existing and elsewhere.
- `tsc --noEmit` clean under `strict`; `vite build` succeeds. Bundle 244 KB — no new
  dependency.
- End-to-end against all three real `.xer` samples through the actual upload path.
  `sample-problems.xer` returns gate-failed on blocking checks 1, 7, 9 and the payload
  carries that. `limit=2` truncates to 2 of 27 while the branch header still reads 27.
- ~35 assertions on the pure frontend helpers (row flattening, subtree collapse, scale
  padding, tick-tier fallback, formatters) via a throwaway esbuild+node script. Not
  committed — there is no frontend test runner. 5,000 rows window to 36 DOM rows.

## What shipped

### Backend

`app/services/schedule_gantt.py` (new) builds the render payload from
`schedule_ingest.hydrate()` rather than reading ORM rows directly. Two reasons, both worth
the extra work: the chart draws the same network the DCMA gate assessed and the simulation
will read, so there is no second path for the picture to drift away from the numbers; and
`hydrate` already normalizes every datetime to naive, so a min/max across the set cannot
reproduce the naive/aware `TypeError` that took down every upload on 2026-07-29.

Payload carries resolved bar dates plus the rule that chose them (`actual` /
`in_progress` / `planned` / `undated`, via the domain's own `forecast_start` /
`forecast_finish`), baseline dates, slip in explicitly-named calendar days, float,
`duration_pct_complete`, milestone / summary / hard-constraint flags, a depth-first WBS
tree with subtree rollups, pre-truncation `counts`, and the gate verdict.

`routes/schedules.py`: `GET /{version_id}/gantt` with `wbs` (subtree), `critical_only`,
`q` and `limit` filters, ceiling 5,000. `touching` filter added to `/relationships`.

`routes/mappings.py` + `services/mapping_service.py`: `GET /mappings/activity-landings`,
`activity_landings()`, `MAX_LANDINGS_PER_ACTIVITY = 20`.

### Frontend

`views/GanttView.tsx`, `components/gantt/{gantt-util.ts,GanttChart.tsx,ActivityDetail.tsx}`,
`gantt.css`; additions to `types.ts`, `api.ts`, `App.tsx` (nav entry). 370 insertions,
zero deletions across the six modified files.

Renderer: one scroll container with a sticky header and sticky label column, so the panes
cannot drift — no scroll-sync code. Fixed-height windowed rows with spacers. `ROW_H` is
published to CSS as `--gt-row-h` so the windowing arithmetic and the rendered height have
one home.

## Bug found and fixed

**Dangling WBS reference silently dropped activities from the chart.** An activity whose
`wbs_source_id` was not among the export's WBS nodes got bucketed under a key nothing ever
read, so it vanished — no error, just a row count quietly disagreeing with the register.
Caught by its own test on first run. Fixed by bucketing against the set of nodes that
actually exist, falling back to the no-WBS group, while the bar keeps its raw
`wbs_source_id` so the bad reference stays visible. Proved not decorative by reverting the
fix and watching the test fail.

## Decisions

Recorded in full in `REFERENCE.md` under 2026-07-30. In short: no commercial Gantt
component; payload derives from `hydrate()`; risk landings stay out of the schedule read;
counts and rollups are computed before truncation; no dependency arrows.

## A process mistake worth not repeating

I ran `ruff format` over pre-existing files to settle a format question and it pulled ~300
lines of unrelated reflow into the diff. Reverted, restored the hand-wrapped originals, and
re-applied only my additions. The underlying finding is a live trap — see `REFERENCE.md`
gotchas and `BACKLOG.md`.

## Docs found stale

- `CLAUDE.md` `yeet` step 4 still said "One `push_files` call for every doc change",
  directly contradicting its own Token discipline section. The 2026-07-29 session notes
  claim this was corrected; the tree says otherwise, so that correction never landed.
  Fixed now.
- `CLAUDE.md` tech stack said "commercial Gantt". Now hand-rolled.
- `CLAUDE.md` claimed ruff line length 100. There is no ruff config in the repo, so the
  effective width is ruff's default 88.
- `CLAUDE.md` lists Vitest and Playwright; `frontend/package.json` has neither, nor any
  other test runner.

## 2026-07-29 loose ends, all now closed

Verified directly against the tree rather than assumed:

- The mapping frontend delta **is** in the tree and typechecks clean as part of a full
  `tsc` + `vite build` run.
- `mapping-load-failure-fix.zip` **was** applied: `RISK_FETCH_LIMIT = 500` and
  `Promise.allSettled` are present in both `MappingView.tsx` and `ScheduleView.tsx`, and
  `components/mapping/ExposurePanel.tsx` exists.
- The mapping delta was pushed; `fc10636` is the head.

## Surfaced for later

No ruff config and the tree is not format-clean at 88 or 100 (25 files would reformat) ·
no frontend test runner, and the most test-worthy code in this delivery ships untested ·
`ScheduleView` → Gantt cross-link cut · Postgres regression coverage for the Gantt's
naive/aware contract · no fixture produces genuinely undated activities (`sample-nodates.xer`
is missing the *project* data date, not activity dates) · a far-future `must_finish_by`
stretches the timeline and squeezes the bars · `GET /relationships?touching=` is the
relationship-browser primitive the `inserted_activity` picker was waiting on.
