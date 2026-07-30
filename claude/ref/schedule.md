# ref/schedule.md — schedule ingestion, the DCMA gate, the Gantt, risk mapping

Open before editing `app/schedule/`, `app/services/schedule_*`, `app/api/routes/schedules.py`,
`app/api/routes/mappings.py`, or anything under `components/gantt/` and `components/mapping/`.

Split out of `REFERENCE.md` on 2026-07-30 when these notes passed the ~150-line threshold.
Cross-cutting invariants, gotchas and decisions stayed there; everything here is specific to
reading a schedule, gating it, drawing it, and landing risks on it.

Append, do not rewrite history.

## Invariants

### Gate visibility

Invariant 3 keeps a DCMA-failing schedule out of simulation. It does not stop that schedule
*looking* fine on the way there. Any view that renders a schedule version — the Gantt, and
later the S-curve, tornado, JCL scatter and every exported report — must carry and state
the gate verdict, because a chart that draws a failed schedule exactly as well as a passing
one is read as endorsement. `GET /schedules/{id}/gantt` returns `gate` for this reason.

### Derived versus judgement

Everything a parse produces — activities, relationships, WBS, calendars, gate runs — is
reproducible from the stored source bytes and may be deleted. A risk-to-activity mapping is
not: it is a decision an analyst made about where a risk lands on the network, recoverable
from no file. Any operation that would destroy the second class has to say so and be
confirmed; operations that only touch the first class do not.

## Gotchas

- `.xer` files carry multiple projects and baselines in one export. Always resolve which
  project ID is intended rather than taking the first.
- MPXJ returns constraint types and calendars that P6 and MS Project define differently.
  Normalise at the parse boundary, not downstream.
- **Never trust a foreign key that came out of a parse.** A `.xer` can reference a WBS id
  it does not contain. Bucketing activities by `wbs_source_id` without checking the id
  exists dropped those activities off the Gantt entirely — no error, just a row count that
  quietly disagreed with the register (found and fixed 2026-07-30). Bucket against the set
  of keys that actually exist, fall back to an explicit "unknown" group, and keep the raw
  value on the row so the bad reference stays visible rather than being laundered.
- **The Gantt overlay must mount inside `.gt-rows`, not beside it.** `.gt-rows` carries
  `z-index: 2` and therefore opens a stacking context. An overlay added as its sibling
  paints above the whole context including `.gt-label`, which is `position: sticky` and
  slides across the track on horizontal scroll — so the arrows would draw over the label
  column. Inside the context, `z-index: 2` puts them above the bars and below the labels at
  3. Anything else added over the chart later has the same constraint.
- **Bar geometry constants live in two places and must move together.** `.gt-bar` is
  `top: 6px; height: 13px`, so `BAR_MID = 12.5` in `gantt-util.ts` is where an arrow
  anchors; `.gt-milestone` is 11px wide offset `-5px`, so `MILESTONE_HALF = 5.5`. Change
  the CSS without the constants and every arrow floats off its bar.

## Decisions

### 2026-07-29 — risk-to-activity mapping design

Built the `.xer`-only risk-to-activity mapping subsystem (`.mpp`/MPXJ parked for now).
Locked in:

- **Mapping stores *where*, not *how much*.** No distribution parameters on
  `risk_activity_mapping` — that belongs to quantitative elicitation. Keeps re-mapping and
  re-eliciting independent of each other.
- **Three mapping types, one correlation semantic each.** `duration_driver` — one sampled
  factor shared across every activity it drives (the Hulett risk-driver method, and the
  reason those activities come out correlated without a hand-built correlation matrix), so
  `allocation_pct` is refused on it at the API edge. `inserted_activity` — allocation *is*
  meaningful here: 60 days over three insertion points is not 60 at each.
  `scoped_driver` — a filter resolved at read time against the current schedule version,
  never frozen at save time, so a WBS branch that gains activities gains coverage
  automatically.
- **Relevance and materiality never blend.** "Is this the right activity" and "does delay
  here move the finish date" are reported as separate axes and shown as separate chips in
  the UI. Blending them produces a ranker that prefers the critical path regardless of
  actual match quality — a real risk with a design that maps every risk onto the same
  dozen activities.
- **Signals abstain (`null`) rather than scoring zero** when there is no evidence — a
  fresh install with no acceptance/rejection history, or an RBS category outside the
  lexicon. The blend renormalises over whichever signals fired instead of treating an
  abstention as a zero, which would otherwise make every new install's suggestions read as
  weak regardless of how well the wording actually matched.
- **Carry-forward matches on activity `code`, not `source_id`.** The P6 task id
  (`source_id`) is a database key of whichever P6 instance produced the export and does
  not survive a database move; the analyst-facing activity ID (`code`) does. Carried
  mappings land as `proposed` regardless of their prior status — the network changed, so
  it is a claim again, not a decision.
- Domain validation is split into two severities: milestone/completed-activity drivers and
  empty scopes are `error:` and block the write (422); float, hard constraints, and a
  missing predecessor/successor relationship are warnings — recorded, but the analyst's
  call to make.

### 2026-07-30 — Gantt render, and the answer to the Gantt component question

Build-schedule item 2.4. Storage had shipped in `0009`; this was render plus the two
endpoints render needed.

- **No commercial Gantt component. Built in-house, no new dependency.** This closes one of
  the five original architecture questions. Bryntum, DHTMLX and Syncfusion all sell
  drag-drop rescheduling, resource views and inline editing; this schedule is imported,
  read-only, and never edited in the app. What the platform actually needs is dense
  read-only rendering of thousands of rows plus custom overlays — risk landings now, P-band
  and criticality-index shading when P3 and P4 land — and custom overlay rendering is
  precisely where those components fight you. `frontend/package.json` also had exactly two
  dependencies (`react`, `react-dom`) before this, and the finished chart added none: 244 KB
  bundle total.
- **The Gantt payload derives from `hydrate()`, not from the ORM rows.** One read path, so
  the chart cannot drift from what the gate assessed and the simulation will read. It also
  inherits `hydrate`'s naive-datetime normalization for free, and a min/max over a mixed
  naive/aware set is the same comparison that took down every upload on 2026-07-29.
- **Risk landings stay out of the schedule read.** `GET /mappings/activity-landings` is a
  separate call the client joins client-side, for two reasons: a `scoped_driver` is a
  filter rather than a list and only resolves against the mapping tables, so folding it in
  would drag those tables into every schedule read and put scope semantics in two places;
  and a failure there should cost the risk badges, not the whole chart. The view uses
  `Promise.allSettled` and degrades to a chart with a banner.
- **Accepted and proposed landings are counted apart and never summed** (invariant 4). A
  bar carrying three proposals must not look like a bar carrying three decisions. Filled
  badge for accepted, dashed outline for proposed.
- **Counts and WBS rollups are computed on the filtered set before truncation.** A large
  schedule returns a truncated bar list with the true total; branch headers and totals keep
  describing the whole schedule. Shrinking them to match the returned page would make the
  numbers agree with each other and disagree with the project.
- **Bar dates are resolved server-side with the rule sent alongside them** — `actual`,
  `in_progress`, `planned` or `undated`, via the domain's own `forecast_start` /
  `forecast_finish`. The basis travels because `planned` on a schedule six months into
  execution is a finding, not a formatting detail. An activity with no usable dates is
  reported as `undated` rather than parked at the epoch.
- **Slip is in calendar days and named for it** (`baseline_slip_calendar_days`). There is no
  single calendar a slip between two activities could honestly be measured on, and the Units
  invariant forbids an unpaired working-day count. Same honesty applies to
  `duration_pct_complete`: it is remaining against original, not a physical or cost percent,
  because neither `.xer` nor `.mpp` carries one the parser keeps.
- ~~**No dependency arrows.**~~ **Superseded 2026-07-30** — see below. The reasoning at the
  time was that across thousands of windowed rows an arrow to an off-screen row is a line
  you cannot follow. That objection turned out to be an argument for bounding the arrows to
  the render window, not for omitting them. The named predecessor/successor list in the
  detail panel stays, and remains the only place that carries relationship type and lag as
  text.
- **One scroll container, sticky header and sticky label column.** Scroll-sync between two
  scrollers is the classic Gantt bug and there was no reason to own it. Nothing between
  `.gt-chart` and a sticky child may set `overflow`: that kills stickiness silently, and the
  symptom is labels that scroll away rather than an error. Rows are fixed-height and
  windowed; `ROW_H` is published to CSS as `--gt-row-h` so the windowing arithmetic and the
  rendered height cannot diverge.

### 2026-07-30 — deleting an imported schedule

Delivered as `schedule-delete-and-gantt-links.zip`. No schema change.

- **Derived data is deletable, judgement is guarded.** A version carrying `accepted`
  mappings refuses with 409 (`ScheduleDeleteBlocked`) until the caller re-sends
  `force=true`. `proposed` mappings do not block: nobody has ruled on them, so nothing is
  lost. Rejected and superseded rows are counted in the total but not called out — four
  numbers in a confirmation buries the one that matters.
- **The confirmation reads its numbers from the server.**
  `GET /schedules/{id}/delete-impact` counts every affected table before anything is
  touched. A dialog that cannot name what it is about to destroy asks the analyst to accept
  a cost nobody has measured, and discovering the number from a 409 means learning it after
  deciding. The route re-fetches the impact on a 409 so a mapping accepted while the dialog
  was open still surfaces its acknowledgement checkbox rather than dead-ending.
- **Deleted mappings write `mapping_history` on the way out.** One `deleted` row each,
  actor attributed, carrying the status and the version id that took it. Invariant 5 holds:
  the mapping goes, the record of it going does not. `mapping_history` and
  `mapping_suggestion_outcome` carry no foreign key precisely so they outlive what they
  describe, and neither is touched by a delete.
- **Deleting the current version promotes the newest survivor** of the same
  `source_project_id`, in the same transaction, *after* the delete so the lookup cannot
  pick the row on its way out. Nothing else moves `is_current` — it is set once at ingest —
  so without this every downstream read filtering on `current_only=true` quietly finds
  nothing, which is indistinguishable from "no schedule imported".
- **Child rows are deleted explicitly, in dependency order, not by FK cascade.** Every child
  table declares `ondelete="CASCADE"`, which Postgres honours and SQLite ignores unless
  `PRAGMA foreign_keys` is on. Deleting by hand makes both behave identically and makes the
  counts reported back rows this code actually removed rather than a number inferred from
  what the database was asked to do.
- **The stored file is opt-in and conditional.** `delete_file=true` removes the bytes only
  when no other version was parsed from them; otherwise it is kept and the response says
  why. Deduplication is by SHA-256, so deleting both version and file lets the same export
  be re-imported cleanly as a new file.
- **No `DELETE /schedules/files/{id}`.** The file-level delete rides on the version delete
  because that is the real case. Known gap: an ambiguous multi-project upload that is never
  parsed strands a `ScheduleFile` with zero versions and no route to remove it.

### 2026-07-30 — dependency arrows, reversing the 2.4 position

- **An arrow is drawn only when both of its endpoints are among the returned bars.**
  Everything else — a WBS or critical-only filter, the row limit, a link crossing into
  another project — is counted as `dangling` and left undrawn. An arrow terminating in
  empty space reads as a schedule error rather than as a display limit, which is a worse
  lie than showing nothing. The count is surfaced in the stats line so the omission is
  visible.
- **The window bounds the work, not the payload.** Geometry is built only for links with an
  endpoint inside the rendered row range, so cost scales with what is on screen rather than
  with a 5,000-row schedule. A link spanning the window from above to below still
  qualifies — those are exactly the ones worth seeing. `MAX_GANTT_LINKS = 10000` sits above
  the realistic link-to-activity ratio at the row cap, and truncation is reported.
- **Endpoints inside a collapsed branch are silently skipped.** Unlike a server-side filter
  this is a gap the analyst created and can undo, so it is not reported as a count.
- **`GanttLink.is_critical` means both endpoints are critical.** It is deliberately not a
  claim that the link is driving: that needs a forward pass this platform does not run
  until P3. The chain the analyst is looking for is inside that subset, which is all the
  highlight promises.
- **Arrows default to on.** The view exists to answer whether the parse is right and whether
  the critical path is a chain or a scatter, and neither question can be asked with the
  logic hidden. P6 defaults the same way. `Selected` — only the links touching the selected
  activity — is the escape hatch when a dense network stops being readable, and the
  selection also dims everything else in `All` mode.
- **The overlay is `aria-hidden` and `pointer-events: none`.** The same logic is already
  listed as text in the detail panel's predecessor and successor lists, so nothing is lost
  to a screen reader, an `<svg>` inside a `role="rowgroup"` would be a structure error, and
  a click must always reach the bar underneath.
- **Routing is orthogonal with a row-gap detour.** Three segments when the successor sits
  ahead of the direction of approach, five when it does not — a successor starting before
  its predecessor finishes needs the arrow to double back, and doing that at the row centre
  would run it through both bars. The invariant worth keeping: every route arrives
  travelling in its entry direction, verified across all eight exit/entry combinations.
