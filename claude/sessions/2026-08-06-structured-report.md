# 2026-08-06 — first structured report (4.6)

## What shipped

`p4-structured-report.zip` delivered, folder-swap, **not yet applied by Sam** — this
session ends with it in his queue, not on `main`. No migration. `main.py` gains a router
mount, so applying it needs an API image rebuild (`docker compose up -d --build api web`);
`web` alone would hot-reload the nav button but the `/reports/*` routes wouldn't exist yet.

- `backend/app/services/report/` — new package. `model.py` (Document/Section/block
  primitives + shared value formatting), `data.py` (the only file that touches the
  database — `gather()` freezes one `ReportData` snapshot), `sections.py` (12-section
  registry, pure functions of the snapshot), `render_html.py` (self-contained printable
  file, no new dependency), `render_xlsx.py` (sheet per section, figures stay numbers with
  Excel formats rather than pre-formatted strings).
- `backend/app/api/routes/reports.py` — `GET /reports/sections`,
  `/report.json|html|xlsx`. Mounted in `main.py`.
- `frontend/src/report-types.ts`, `report-api.ts`, `report.css`, `views/ReportView.tsx` —
  server-driven section picker, live HTML preview in a sandboxed iframe, download in
  either format. Nav entry added to `App.tsx` between ROI and Schedule.
- Tests: `test_report_sections.py` (46, DB-free — builds `ReportData` by hand, covers
  every section including the mitigation/ROI one added this session) and
  `test_reports_api.py` (route-level, eager runs, scope isolation, all three renderings
  built from one document).

## Decisions

- **Naming a run fixes the scope.** Any `scope_id` sent alongside a `run_id` is ignored
  and recorded as a note printed in the basis section, rather than silently obeyed. A
  report whose contingency came from one project and whose register came from the
  portfolio above it is invisibly inconsistent — see `REFERENCE.md` for the full
  reasoning.
- **A failed or queued run is not a 409.** The basis prints its status; every
  result-dependent section states why it's unavailable via a per-section
  `unavailable(data) -> str | None` in the registry, driving `GET /reports/sections` so
  the picker's reasons come from the data rather than a hardcoded list.
- Requested sections filter but never reorder — registry order is document order,
  always.
- pptx and PDF renderers are out of scope for 4.6 (BACKLOG line said "xlsx/pptx/pdf
  targets"; only html and xlsx shipped). The block model is renderer-agnostic by
  design specifically so a third renderer is a new file, not a rewrite — noted as
  deferred, not forgotten.

## Verified

Fresh clone (`1be6c5c`) → baseline **815 passed / 3 skipped** (this is the real number;
`ACTIVE.md` going in said 695, which was two sessions stale — 4.4 and 4.5 both already on
`main`, see below). Unpacked the zip over that same fresh clone → **876 passed / 3
skipped**, `ruff check` clean on every new file (3 pre-existing F401s elsewhere, unchanged
before/after), `tsc --noEmit` clean, `vite build` clean.

## Docs found stale

- **`ACTIVE.md`'s "known gap to fix first" (`sim_assembly.assemble()` not scope-filtered)
  is already resolved on `main`.** Confirmed by reading the function directly —
  `scope_ids` is a real parameter, filters both the schedule-file join and the risk
  query. This was the blocker 4.5 needed; 4.5 has shipped (ROI routes, `MitigationRoi`
  model, migration `0016`, `roi.py` service and API all present and tested on `main`).
  Whatever fixed this and shipped 4.5 happened in a session this file's index doesn't
  reflect — `ACTIVE.md` and the real test count (695 vs 815) were both stale entering
  this session, for the third time. See `BACKLOG.md` → Watch items; this session adds a
  fourth occurrence rather than a new pattern.
- **`BACKLOG.md` → Subsystems not yet designed in depth → "Report export"** is resolved
  by this session for the html/xlsx half. pptx/PDF stay open.

## Surfaced

- `ReportView.tsx` (359 lines) is the eighth delivery verified only by `tsc`/`vite build`
  with no committed test — the frontend-test-runner gap keeps compounding one delivery at
  a time. See `BACKLOG.md`.
- No pptx or PDF renderer yet. The block model was built renderer-agnostic on purpose so
  either is additive later.
- No end-to-end test exercises a *populated* mitigation/ROI section through the live
  routes (`test_reports_api.py` covers cost/schedule/basis end-to-end; the ROI half is
  covered at the pure-section level in `test_report_sections.py` plus `roi.compare`'s own
  suite, not through `/reports/report.*` with a real `MitigationRoi` row).
