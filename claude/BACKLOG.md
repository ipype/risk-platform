# BACKLOG.md — not yet done

Open when current work is finished, when asked what is pending, or when a watch item may
have fired.

## Blocked — needs a decision from Sam

- Gantt component choice (Bryntum vs DHTMLX vs Syncfusion). Licence cost and React API
  quality differ materially. Blocks Gantt-visualisation frontend work specifically — the
  2026-07-29 schedule-mapping workbench shipped without one (list/card UI, no timeline
  rendering needed), so this is narrower than it was originally scoped.
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
- Frontend delta from 2026-07-29 (mapping workbench) is unconfirmed against the real repo
  tree — only checked in an isolated harness with stubbed sibling views.
