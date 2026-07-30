# REFERENCE.md — the why

Open before editing a subsystem documented here, or when unsure why the code is the way it
is. Invariants, gotchas, dated decisions. Append, do not rewrite history.

Schedule ingestion, the DCMA gate, the Gantt and risk-to-activity mapping split out to
`claude/ref/schedule.md` on 2026-07-30. What stays here is cross-cutting: it applies
whatever subsystem you are in.

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

- **`make fmt` is not safe to run casually.** There is no ruff config in the repo, so
  `ruff format .` uses ruff's default 88 rather than the 100 this file's conventions once
  claimed, and the tree is clean at neither width — 25 files would reformat. Running it
  over pre-existing files pulls hundreds of lines of unrelated reflow into your diff. When
  editing an existing file, match its surrounding hand-wrapped style; new files can be
  format-clean at 88. See `BACKLOG.md` → Surfaced 2026-07-30.
- Verify against the repo's *pinned* dependency versions (`requirements.txt` /
  `requirements-dev.txt`), not whatever a bare `pip install <pkg>` resolves to. An
  unpinned FastAPI silently guards a `-> None` + `status_code=204` edge case that the
  pinned `fastapi==0.115.6` does not — a route crashed on container boot despite passing
  67/67 tests, because the tests ran against a newer, unpinned FastAPI. See the
  2026-07-29 decision below for the exact mechanism.
- **In-memory SQLite is a single connection, and a held transaction deadlocks the suite.**
  SQLAlchemy backs `sqlite+aiosqlite://` with a `StaticPool` — one DBAPI connection for the
  whole engine. A test that reads through a session fixture and then calls the ASGI client
  hangs forever rather than failing: the fixture's session still holds an open transaction,
  the request waits for the only connection, and there is no traceback to read. Note that
  `expire_on_commit` makes this easy to hit by accident, because touching any attribute
  after a commit opens a *new* transaction. Scope every direct database read to its own
  `async with session_factory() as db:` block so the connection is always released
  (found 2026-07-30, cost about twenty minutes).
- **A test harness that creates a subset of tables still needs the whole metadata.**
  `create_all(tables=[...])` cannot emit a foreign key unless the *target* `Table` object
  is registered, even when the target table is deliberately not created. Import
  `app.db.base` in `conftest.py` for its side effect. Without it, whether the harness works
  depends on whether some earlier test module happened to import the missing model first —
  which is how `tests/conftest.py` passed a full-suite run and failed when its own file was
  run alone (found 2026-07-30).
- **`ondelete="CASCADE"` is a Postgres promise, not a portable one.** SQLite ignores foreign
  keys entirely unless `PRAGMA foreign_keys=ON`, so a delete that leans on the database to
  clean up children behaves differently under test than in production. Delete children
  explicitly in dependency order where the result matters — it also lets the code report
  rows it actually removed rather than a number it assumed.

## Decisions

### 2026-07-24 — doc architecture established

Hub-and-satellite adopted. `CLAUDE.md` is a map read every session; `SYSTEM.md` and
`ACTIVE.md` join it at bootstrap; everything else is trigger-read. Rationale: bootstrap cost
is paid every chat, so it must stay small, and a map means an unread file is never a lost
file. Split, never consolidate.

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

### 2026-07-30 — verify against a local clone of the real tree

The repo is public and `github.com` / `codeload.github.com` are reachable from the sandbox,
so `git clone --depth 1` gets `main` and verification can run against actual code: the full
pytest suite, `ruff`, `tsc --noEmit`, `vite build`, and a real `.xer` driven end-to-end
through the upload path. Prefer this to a hand-built harness. A harness with stubbed sibling
views is what left the 2026-07-29 mapping frontend delta unconfirmed across two sessions —
it was in fact fine, and one clone would have said so. Writes are still Sam's `git push`;
cloning is read-only and unrelated to the MCP write block.

**Extended 2026-07-30 (second session):** finish by unpacking the delivered zip over a
*fresh* clone and re-running the suite there. Verifying in the working tree proves the code
is right; verifying in a fresh clone proves the zip is, which is the artefact Sam actually
applies. Catches a file staged from the wrong path or omitted from the archive — neither of
which the working tree can tell you about.

### 2026-07-30 — pure frontend logic is verified but not committed

Twice now — the Gantt's row flattening and scale arithmetic in 2.4, the arrow geometry in
this session — the most test-worthy code in a delivery has been validated by a throwaway
`esbuild` + `node` script and shipped with no committed test. The script is real
verification against the real module, not a mock, and the arrow work ran 33 assertions
including a property over all eight routing combinations. But it lives in `/tmp` and dies
with the session, so the third change to that code has nothing to run.

This is a stack decision, not a delivery decision, which is why it keeps getting deferred:
adding Vitest means adding a dev dependency and a `make test` target to a repo that has
deliberately kept `frontend/package.json` at two runtime dependencies. Recorded here so the
cost is visible rather than rediscovered. See `BACKLOG.md` → Surfaced.
