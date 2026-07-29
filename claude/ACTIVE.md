# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] Confirm the 2026-07-29 schedule-mapping frontend delta is applied to the real tree
      and builds cleanly: `views/MappingView.tsx`, `components/mapping/*`, `mapping.css`,
      edits to `types.ts` / `api.ts` / `App.tsx`. Verified only in an isolated harness
      with stubbed sibling views — not yet run against the actual repo.
- [ ] Sort Sam's local test environment: `backend/.venv` (Python 3.13) is missing dev
      deps, and `pip install -r requirements-dev.txt` resolves against a stale/mirrored
      index locally (caps at `pytest-asyncio==0.24.0`; real PyPI has 0.25.2+). Fastest
      path is running tests inside the `api` container, which already has the right
      pins — needs confirming that gives 67 passed.
- [ ] Push the 2026-07-29 risk-to-activity mapping delta to GitHub (12 new + 5 modified
      files across backend/frontend, plus this doc set). Currently applied to local disk
      only. Retest `push_files` on a throwaway path before assuming write access is
      fixed — last confirmed state was 403 (Contents: Read only); not retested this
      session per Sam's instruction to skip pushing.
- [ ] Four of five original architecture questions remain open — see `BACKLOG.md` →
      Blocked. `.mpp` scope is provisionally settled: parked, `.xer`-only until further
      notice.

## Notes

- The platform is not a scaffold. Backend has a working P0 spine, `.xer` ingest + DCMA
  gate, and now risk-to-activity mapping (migration `0010`, applied against Postgres in
  Sam's environment). Frontend has register / matrix / mapping / activity / fields /
  settings views. `docker compose up -d --build` plus `alembic upgrade head` runs clean
  through `0010` as of 2026-07-29.
- Previous entry here ("repo scaffold: api/, core/, sim/... nothing is real until this
  lands") was stale and has been removed — it shipped several sessions ago.
