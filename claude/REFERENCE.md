# REFERENCE.md — the why

Open before editing a subsystem documented here, or when unsure why the code is the way it
is. Invariants, gotchas, dated decisions. Append, do not rewrite history.

## Invariants

### Percentile arithmetic

Percentiles are not additive. Integrating cost contingency with schedule-driven cost must
happen inside each iteration:

```
for i in iterations:
    cost_i  = sample_cost_risks()
    delay_i = simulate_schedule()          # CPM over sampled durations
    total_i = cost_i + delay_i * burn_rate
percentiles(total)                          # once, at the end
```

`P80(cost) + P80(delay) * burn_rate` overstates contingency because it assumes perfect rank
correlation between the two tails. This is the most common error in QSRA output and the most
likely thing to be challenged in review.

### Correlation

Risks are not independent. Weather, labour productivity, and commodity escalation move
together. Iman-Conover rank correlation is applied to the sampled matrix before it reaches
the CPM pass. Independent sampling systematically understates P80/P90.

### Background uncertainty

Activity durations carry inherent variability separate from discrete risk events. Modelling
only discrete risks produces an unrealistically tight base distribution.

### Units

Durations in working days, always paired with the calendar ID used to compute them.
Calendar-agnostic day counts are a silent corruption source across `.xer` imports.

### Gate visibility

Invariant 3 keeps a DCMA-failing schedule out of simulation. It does not stop that schedule
*looking* fine on the way there. Any view that renders a schedule version — the Gantt, and
later the S-curve, tornado, JCL scatter and every exported report — must carry and state
the gate verdict, because a chart that draws a failed schedule exactly as well as a passing
one is read as endorsement. `GET /schedules/{id}/gantt` returns `gate` for this reason.

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
- **`make fmt` is not safe to run casually.** There is no ruff config in the repo, so
  `ruff format .` uses ruff's default 88 rather than the 100 this file's conventions once
  claimed, and the tree is clean at neither width — 25 files would reformat. Running it
  over pre-existing files pulls hundreds of lines of unrelated reflow into your diff. When
  editing an existing file, match its surrounding hand-wrapped style; new files can be
  format-clean at 88. See `BACKLOG.md` → Surfaced 2026-07-30.
- - Verify against the repo's *pinned* dependency versions (`requirements.txt` /
  `requirements-dev.txt`), not whatever a bare `pip install <pkg>` resolves to. An
  unpinned FastAPI silently guards a `-> None` + `status_code=204` edge case that the
  pinned `fastapi==0.115.6` does not — a route crashed on container boot despite passing
  67/67 tests, because the tests ran against a newer, unpinned FastAPI. See the
  2026-07-29 decision below for the exact mechanism.

## Decisions

### 2026-07-24 — doc architecture established

Hub-and-satellite adopted. `CLAUDE.md` is a map read every session; `SYSTEM.md` and
`ACTIVE.md` join it at bootstrap; everything else is trigger-read. Rationale: bootstrap cost
is paid every chat, so it must stay small, and a map means an unread file is never a lost
file. Split, never consolidate.

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

### 2026-07-29 — verify against pinned dependencies, not resolved-latest

`DELETE /mappings/{id}` crashed the API container on boot: an `async def ... -> None`
return annotation combined with `status_code=204` and no explicit `response_model=None`
resolves to a truthy `NoneType` response model under `fastapi==0.115.6` (the repo's actual
pin), and FastAPI's `assert is_body_allowed_for_status_code(...)` fires *at import time* —
before uvicorn can bind a port. The bug passed 67/67 tests in an earlier verification pass
because that pass ran against an unpinned, newer FastAPI version that silently guards this
exact case. Fix: `response_model=None` explicit in the decorator. Going forward,
verification for this repo must run against `requirements.txt` +
`requirements-dev.txt` pinned exactly — `pip install <pkg>` with no version pin is not a
substitute and can hide version-dependent bugs that only appear in the pinned production
environment.

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
- **No dependency arrows.** Across thousands of windowed rows an arrow to an off-screen row
  is a line you cannot follow. `GET /relationships?touching=<source_id>` feeds a named
  predecessor/successor list in the detail panel, each entry clickable to expand ancestors,
  scroll and select — which also carries relationship type and lag, as an arrow never does.
- **One scroll container, sticky header and sticky label column.** Scroll-sync between two
  scrollers is the classic Gantt bug and there was no reason to own it. Nothing between
  `.gt-chart` and a sticky child may set `overflow`: that kills stickiness silently, and the
  symptom is labels that scroll away rather than an error. Rows are fixed-height and
  windowed; `ROW_H` is published to CSS as `--gt-row-h` so the windowing arithmetic and the
  rendered height cannot diverge.

### 2026-07-30 — verify against a local clone of the real tree

The repo is public and `github.com` / `codeload.github.com` are reachable from the sandbox,
so `git clone --depth 1` gets `main` and verification can run against actual code: the full
pytest suite, `ruff`, `tsc --noEmit`, `vite build`, and a real `.xer` driven end-to-end
through the upload path. Prefer this to a hand-built harness. A harness with stubbed sibling
views is what left the 2026-07-29 mapping frontend delta unconfirmed across two sessions —
it was in fact fine, and one clone would have said so. Writes are still Sam's `git push`;
cloning is read-only and unrelated to the MCP write block.
