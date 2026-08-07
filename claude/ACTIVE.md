# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] **Apply `register-scoped-ids.zip` (15 files, folder-swap, `APPLY.md` included).**
      Risk ID becomes `<program>-<project>-<sequence>` (was `<RBS>-<sequence>`); mitigation
      actions can be added at risk creation instead of only after save-and-reopen; register
      rows are clickable. **Needs migration `0019`**, which renumbers every existing risk —
      back up the database first. Full detail, including a bug I introduced and caught
      before delivery (number reissue on delete) and a decision made without being asked
      (recategorisation is now possible), in `claude/sessions/2026-08-07-risk-code-scoping.md`.
- [ ] **This delivery has not been verified the standard way.** No repo clone was
      available this session. Migration and API changes were verified against hand-built
      SQLite harnesses and reconstructed stub modules, not the real test suite. Before or
      immediately after applying: run `pytest -q`, `ruff check`, `tsc --noEmit`,
      `vite build` against the real tree, per the standing verification method.
- [ ] **Audit not finished.** `export.py` was checked and is clean (Category/Subcategory
      come from the RBS join, never parsed from `risk_code`). `services/report/` and
      `mappings.py` were not opened — check both for any place that treats `risk_code` as
      more than an opaque label before or after applying.

## Notes

- **P4 is complete through 4.8** (JCL, SSI, delay tornado, mitigation module, ROI,
  structured report, scope hierarchy schema + tree UI + scoped reads). This file said
  otherwise for at least three sessions running — see `REFERENCE.md` 2026-08-06, which
  now treats that drift as a standing condition rather than a one-off. Test count is
  **not restated here**; the last session with clone access to check one against `main`
  was 2026-08-06, and the check this file needs (verify a claimed count before trusting
  it) is exactly what a session with no clone cannot do — see `BACKLOG.md` → Watch items
  for the still-unbuilt mechanical fix.
- P5 (AI risk agent) has not been started. Nothing in it is in flight.
- Sam holds the current copy of `Risk_Platform_Build_Schedule.xlsx` locally — not tracked
  in the repo.
- Verification for this repo runs against a **local clone** of the real tree, finishing
  with the delivered zip(s) unpacked over a *fresh* clone. Check `git status` on that clone
  before trusting it — a directory that looks like a clone may not be one
  (`REFERENCE.md` 2026-08-01). No clone was available this session; the above line is a
  known gap, not an assumption to build on.
- `alembic upgrade head` against SQLite has never worked (Postgres-only `CREATE EXTENSION`
  in migration 0001). "SQLite end-to-end run" means executing one migration's `upgrade()`
  against a hand-built pre-migration database with real rows, not the literal CLI.
- **Do not trust a "pending Sam's local apply" line in this file once it predates the
  current session** without checking it against `main` first. This is now a five-time
  pattern (`REFERENCE.md` 2026-08-06).
