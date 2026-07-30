# schedule-delete-and-gantt-links

17 files, folder-swap. Unpack over the repo root, paths intact. Verified against a clone
of `main` at `1aa99b9`.

**No migration.** No schema changed — the delete endpoints use existing tables and the
Gantt links are computed from `schedule_relationship` rows that were already stored.

## Apply

```bash
cd /path/to/Risk-Platform
unzip -o schedule-delete-and-gantt-links.zip
git status                 # expect 13 modified, 4 new
cd backend && pytest -q    # expect 283 passed, 3 skipped
cd ../frontend && npm run build
git add -A
git commit -m "feat: delete imported schedules, draw dependency arrows on the Gantt"
git push
```

## New files

| Path | What |
|---|---|
| `backend/app/services/schedule_delete.py` | Impact preview + version delete |
| `backend/tests/test_schedule_delete.py` | 17 tests |
| `backend/tests/test_schedule_links.py` | 10 tests |
| `frontend/src/components/schedule/DeleteVersionDialog.tsx` | Confirmation dialog |

## API added

- `GET /schedules/{id}/delete-impact` — counts everything a delete would remove, without
  removing it.
- `DELETE /schedules/{id}?force=&delete_file=` — 409 when accepted mappings would be lost
  and `force` is unset.
- `GET /schedules/{id}/gantt` now also returns `links[]` and `link_counts{}`.

## Verification run

- `pytest -q` → 283 passed, 3 skipped (was 256/3).
- `tsc --noEmit` clean, `vite build` clean.
- 33 geometry assertions against the real `gantt-util` module, including the property
  that every route arrives travelling in its entry direction across all eight
  exit/entry combinations.
- End-to-end on `sample-schedules/sample-clean.xer`: upload → 22 links, 0 dangling →
  WBS filter cuts 17 with no hanging arrows → delete with `delete_file=true` → re-import
  clean.

## Behaviour worth knowing before you review

**Accepted mappings block a delete.** Activities, links, WBS, calendars and gate runs are
reproducible from the stored bytes; an accepted risk-to-activity mapping is analyst
judgement and is not. The server refuses with 409 until `force=true`, and the dialog only
shows the acknowledgement checkbox when there is something to acknowledge.

**Deleted mappings still write `mapping_history`.** One `deleted` row each, actor
attributed. Invariant 5 holds: the mapping goes, the record of it going does not.
`mapping_suggestion_outcome` is untouched.

**Deleting the current version promotes the newest survivor** of the same
`source_project_id`. Without it, `current_only=true` returns nothing and the Mapping tab
reads as "no schedule imported".

**Child rows are deleted explicitly, not by FK cascade.** Postgres honours `ON DELETE
CASCADE`; SQLite ignores it unless `PRAGMA foreign_keys` is on. Explicit deletes make both
behave the same and make the counts reported to the analyst rows this code actually
removed.

**Arrows only join two drawn bars.** A link with one end filtered out, truncated away, or
pointing into another project is counted as `dangling` and not drawn — an arrow into empty
space reads as a schedule error rather than a display limit.

**`is_critical` on a link means both ends are critical.** It is not a claim the link is
driving; that needs the forward pass P3 has not built.

**Links default to on.** This view exists to answer whether the critical path is a chain
or a scatter, and that cannot be asked with the logic hidden. `Selected` is the way out on
a dense schedule.

## Two things I cut, for the backlog

- No `DELETE /schedules/files/{id}`. `delete_file=true` on the version delete covers the
  real case. This leaves one gap: an ambiguous multi-project upload that is never parsed
  strands a `ScheduleFile` with zero versions and no way to remove it.
- `tests/conftest.py` now imports `app.db.base` so every model is registered on the
  metadata. Without it this harness passed or failed depending on whether an earlier test
  module happened to import the risk models first — worth knowing if you touch it.
