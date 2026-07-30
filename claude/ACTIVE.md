# ACTIVE.md — in-flight work

In-flight only. Target under 100 lines. Anything not being worked on right now goes to
`BACKLOG.md`.

## Now

- [ ] Apply and commit `schedule-delete-and-gantt-links.zip` (17 files, folder-swap) and
      push. **No migration** — no schema changed; the delete routes use existing tables and
      the Gantt links come from `schedule_relationship` rows already being stored. Verified
      by unpacking over a fresh clone: 283 passed / 3 skipped, `tsc` and `vite build`
      clean. Nothing else in flight depends on it landing first.
- [ ] Apply and commit the doc close from this session: `claude/ref/schedule.md` is new and
      `CLAUDE.md` gained the map row pointing at it. Safe to combine with the delivery
      above in one commit.

## Notes

- **P2 is complete except the parked `.mpp` work.** `2.2` `.xer` parse, `2.4` store +
  Gantt render, `2.5` DCMA gate and `2.6` mapping UI have all shipped, plus schedule
  deletion and dependency arrows on top of `2.4`. `2.1` (MPXJ Java bridge) and `2.3`
  (`.mpp` parse) are the only P2 items left and both are parked by decision.
- **Next scheduled item is P3, the Monte Carlo engine.** Per `CLAUDE.md` it must land as
  its own package (`backend/app/sim/` or `backend/sim/`) kept free of DB, network and
  logging side effects. Do not fold it into `services/`. Read `claude/ref/schedule.md`
  before touching anything that reads a parsed version, and `REFERENCE.md` for the
  percentile and correlation invariants that govern the engine itself.
- Verification for this repo runs against a **local clone** of the real tree, finishing
  with the delivered zip unpacked over a *fresh* clone. See `REFERENCE.md` 2026-07-30.
- Three of five original architecture questions remain open — see `BACKLOG.md` → Blocked.
  Gantt component was decided 2026-07-30 (built in-house); `.mpp` scope stays parked,
  `.xer`-only until further notice.
