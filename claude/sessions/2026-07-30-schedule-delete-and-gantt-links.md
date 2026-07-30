# 2026-07-30 — schedule delete, and dependency arrows on the Gantt

Second session on 2026-07-30. Started from `1aa99b9` (`Session summary`), which already
carried the 2.4 Gantt render — the `ACTIVE.md` item asking for `gantt-2.4.zip` to be
applied had in fact landed before this chat opened.

## Shipped

`schedule-delete-and-gantt-links.zip` — 17 files, folder-swap, **no migration**. Delivered,
not committed; head is still `1aa99b9` until Sam applies and pushes.

**Delete an imported schedule** (13 modified, 4 new across both halves):

- `GET /schedules/{id}/delete-impact` — counts everything a delete would remove without
  removing it, so the confirmation states real numbers.
- `DELETE /schedules/{id}?force=&delete_file=` — 409 when accepted risk-to-activity
  mappings would be lost and `force` is unset.
- `app/services/schedule_delete.py` — new. Impact preview, explicit child deletes,
  promotion of the newest survivor when the current version goes.
- `components/schedule/DeleteVersionDialog.tsx` — new. Counts, an acknowledgement
  checkbox only when there is something to acknowledge, an optional "delete the stored
  file too", focus on Cancel rather than Delete.

**Dependency arrows on the Gantt**:

- `GET /schedules/{id}/gantt` now returns `links[]` and `link_counts{}`.
- Arrow geometry in `gantt-util.ts`: FS/SS/FF/SF edge selection, orthogonal routing with
  a row-gap detour when the successor sits behind the direction of approach.
- One SVG overlay inside `.gt-rows`, bounded to the render window.
- `Links: off / Selected / All` toolbar control, defaulting to All.

## Verification

- pytest 256 → **283 passed, 3 skipped** (17 delete tests, 10 link tests).
- `tsc --noEmit` clean, `vite build` clean, bundle unchanged in shape (254 KB).
- 33 geometry assertions run against the real `gantt-util` module via an ad-hoc
  esbuild+node harness, including a property over all eight exit/entry combinations that
  every route arrives travelling in its entry direction.
- End-to-end on `sample-schedules/sample-clean.xer`: 21 activities, 22 links, 0 dangling;
  a WBS filter cut 17 links with no hanging arrow; delete with `delete_file=true`;
  clean re-import afterwards.
- Zip unpacked over a **fresh clone** and the suite re-run — 283 passed, 17 files changed.

## Decisions made

Recorded in full in `claude/ref/schedule.md` (new file this session):

- Derived schedule data is deletable; analyst judgement is guarded. Accepted mappings
  block the delete until confirmed.
- Deleted mappings still write `mapping_history` rows. Invariant 5 holds.
- Deleting the current version promotes the newest survivor of the same source project.
- Child rows are deleted explicitly rather than by FK cascade.
- An arrow is drawn only when both endpoints are among the returned bars; everything else
  is counted as `dangling`.
- `GanttLink.is_critical` means both ends are critical — not a claim the link is driving.
- Dependency arrows default to on, reversing the 2.4 position.

## Found stale

- `ACTIVE.md` asked for `gantt-2.4.zip` to be applied; it already had been.
- `ACTIVE.md` said `fc10636` was the head; it was `1aa99b9`.
- `REFERENCE.md`'s 2026-07-30 Gantt decision said "No dependency arrows". Superseded.
- The `REFERENCE.md`-is-at-193-lines watch item fired. Schedule and mapping notes split
  out to `claude/ref/schedule.md`; `CLAUDE.md` gained the map row.

## Surfaced for later

- No `DELETE /schedules/files/{id}`, so an ambiguous multi-project upload that is never
  parsed strands a `ScheduleFile` with zero versions and no way to remove it.
- The 33 geometry assertions are not committed — same throwaway-harness problem the 2.4
  session logged. This is now the second delivery whose most test-worthy pure functions
  ship untested.
- In-memory SQLite is a `StaticPool`: one connection for the whole engine. A test session
  holding an open transaction deadlocks the next request through the client, with no
  traceback. Cost about twenty minutes here.
- `tests/conftest.py` needed `app.db.base` imported before `create_all` could resolve
  `risk_activity_mapping.risk_id`. Without it the harness passed or failed on test module
  import order.
