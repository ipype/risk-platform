# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] **Apply and commit `p4-structured-report.zip` (15 files, folder-swap). Ships 4.6**
      (first structured report: `backend/app/services/report/` package — data snapshot,
      section registry, HTML and XLSX renderers — plus `GET /reports/sections` and
      `/report.json|html|xlsx`, and `ReportView.tsx` on the frontend). **No migration.**
      `main.py` gains a router mount, so applying needs an API image rebuild:
      `docker compose up -d --build api web` (`web` alone hot-reloads the nav button but
      the routes won't exist without the `api` rebuild).
- [ ] **P4 (analytics + mitigation) is now feature-complete through 4.8**, pending Sam's
      review of the 4.6 apply above. 4.1–4.8 all verified present on `main` or delivered
      this session — see `claude/sessions/2026-08-06-structured-report.md` for what was
      re-confirmed. Next build target not yet chosen: candidates are P5 (AI risk agent,
      unbuilt) or closing an item in `BACKLOG.md` → Blocked. Sam picks.

## Notes

- **Corrected test count, verified 2026-08-06 against a fresh clone**: `main` at
  `1be6c5c` is **815 passed / 3 skipped**, not 695 — that figure was two sessions stale.
  This is the fourth time this file's claimed state has predated committed work; see
  `BACKLOG.md` → Watch items, "`ACTIVE.md` drift". Unpacking `p4-structured-report.zip`
  over that clone brings it to 876 passed / 3 skipped.
- **The `sim_assembly.assemble()` scope-filtering gap this file previously listed as
  blocking 4.5 is already fixed on `main`** — `scope_ids` is a real, exercised parameter.
  Whatever session closed it isn't reflected in this file's history; don't re-open it
  without checking the function first.
- Verification for this repo runs against a **local clone** of the real tree, finishing
  with the delivered zip(s) unpacked over a *fresh* clone. Check `git status` on that
  clone before trusting it — a directory that looks like a clone may not be one
  (`REFERENCE.md` 2026-08-01).
- `alembic upgrade head` against SQLite has never worked (Postgres-only `CREATE EXTENSION`
  in migration 0001). "SQLite end-to-end run" means executing one migration's `upgrade()`
  against a hand-built pre-migration database with real rows, not the literal CLI.
- Sam holds the current copy of `Risk_Platform_Build_Schedule.xlsx` locally — not tracked
  in the repo.
- **Do not trust a "pending Sam's local apply" line in this file once it predates the
  current session** without checking it against `main` first. This is now the fourth time
  it has drifted. If a fifth occurs, stop writing a reminder here and add the mechanical
  check `BACKLOG.md` already proposes: bootstrap diffs this file's claimed test count
  against a fresh clone.
