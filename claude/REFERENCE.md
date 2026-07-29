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

## Gotchas

- `.xer` files carry multiple projects and baselines in one export. Always resolve which
  project ID is intended rather than taking the first.
- MPXJ returns constraint types and calendars that P6 and MS Project define differently.
  Normalise at the parse boundary, not downstream.
- Verify against the repo's *pinned* dependency versions (`requirements.txt` /
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
