# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] Apply and commit `gantt-2.4.zip` (14 files, folder-swap) and push. No migration —
      every endpoint added is read-only and no schema changed. Nothing else in flight
      depends on it landing first.
- [ ] Settle the ruff formatting question before anyone runs `make fmt`. There is no ruff
      config in the repo, so `ruff format .` uses the default 88 rather than the 100
      `CLAUDE.md` claims, and the tree is not clean at either width — 25 files would
      reformat. Whoever runs it next gets a reformat commit tangled into their own work.
      Either add a config pinning the width and reformat once in a dedicated commit, or
      drop the claim. See `BACKLOG.md` → Surfaced.
- [ ] Sam's local test environment: `backend/.venv` (Python 3.13) is missing dev deps and
      the local index caps `pytest-asyncio` at `0.24.0`. **2026-07-30 data point**: a clean
      sandbox installing the exact pins from `requirements.txt` + `requirements-dev.txt`
      off real PyPI resolved `0.25.2` without trouble and ran 256 tests green, so this is
      the local index configuration and not the pin. Running in the `api` container is
      still the fastest path.
- [ ] Three of five original architecture questions remain open — see `BACKLOG.md` →
      Blocked. Gantt component was decided 2026-07-30 (built in-house); `.mpp` scope stays
      parked, `.xer`-only until further notice.

## Notes

- **P2 is complete except the parked `.mpp` work.** `2.2` `.xer` parse, `2.4` store +
  Gantt render, `2.5` DCMA gate and `2.6` mapping UI have all shipped. `2.1` (MPXJ Java
  bridge) and `2.3` (`.mpp` parse) are the only P2 items left and both are parked by
  decision. Next scheduled item is **P3, the Monte Carlo engine** — which per `CLAUDE.md`
  must land as its own package (`backend/app/sim/` or `backend/sim/`) kept free of DB,
  network and logging side effects. Do not fold it into `services/`.
- Verification for this repo should now run against a **local clone** of the real tree.
  The repo is public and reachable from the sandbox, so `git clone --depth 1` gets `main`,
  and the full suite, `tsc`, `vite build` and a real `.xer` end-to-end can all run against
  actual code instead of a harness with stubbed siblings. That gap is what left the
  2026-07-29 frontend delta unconfirmed for two sessions.
- All three 2026-07-29 loose ends are closed, checked against the tree rather than
  assumed: the mapping frontend delta is present and typechecks, the
  `mapping-load-failure-fix` is applied (`RISK_FETCH_LIMIT = 500` plus
  `Promise.allSettled` in both views, `ExposurePanel.tsx` present), and the mapping delta
  was pushed — `fc10636` is the head.
