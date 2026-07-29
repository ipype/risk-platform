# CLAUDE.md — iPype Risk Platform

Quantitative risk analysis platform for capital projects. Document ingestion → risk
identification → workshop facilitation → qualitative + quantitative elicitation → schedule
mapping → Monte Carlo (cost + schedule) → sensitivity → mitigation → reporting.
Methodology anchors: AACE RP 57R-09, DCMA 14-point, Hulett QSRA.

**This file is an index.** It is the only doc guaranteed to be read. Nothing is lost by
leaving a file unread, because this map always says what exists and when to open it.

## Prime directive

**Do not make any changes until you have 95% confidence in what you need to build. Ask me
follow up questions until you reach that confidence.**

State assumptions and proceed only when they are cheap to reverse. Anything touching
probabilistic math, schedule semantics, or the audit trail is never cheap to reverse — ask.

## The map

| File | Open when |
|---|---|
| `claude/SYSTEM.md` | Bootstrap. Stable primitives: IDs, env, ports, commands, standing rules. |
| `claude/ACTIVE.md` | Bootstrap. In-flight work only. Target < 100 lines. |
| `claude/BACKLOG.md` | Current work finished, I ask what is pending, or a watch item may have fired. |
| `claude/REFERENCE.md` | Before editing a subsystem it documents, or when unsure why the code is the way it is. Invariants, gotchas, dated decisions. |
| `claude/ref/<topic>.md` | Named directly. Split out when one subsystem's notes pass ~150 lines. |
| `claude/plans/<n>.md` | Named directly. One file per multi-session initiative. |
| `claude/sessions/` | Write-only archive. Never read unless I ask about a specific date. |

Split, never consolidate. When `ACTIVE.md` grows, move open work to `BACKLOG.md` and lessons
to `REFERENCE.md`. Adding a doc means adding a row here with its trigger.

Source code is authoritative. Docs lag reality. Read the file before stating a fact about
the code.

## Shortcuts

**`ehe`** — bootstrap. 1) `get_file_contents` on `package.json` (fallback `README.md`) at
`ipype/Risk-Platform` ref `main` as the connector probe; if it fails, say so and stop.
2) Read `CLAUDE.md`. 3) `claude/SYSTEM.md`. 4) `claude/ACTIVE.md`. Read nothing else.
  Confirm in one line, then wait.

**`yeet`** — close. 1) Compile the session: commits with SHAs, decisions, stale docs, items
surfaced. 2) Write `claude/sessions/YYYY-MM-DD-<slug>.md`. 3) Route changes, newest state
wins — `ACTIVE.md` drop shipped work, `BACKLOG.md` add deferred/blocked, `REFERENCE.md` add
a dated decision plus any new invariant, `CLAUDE.md` only if a file or trigger changed.
4) One `push_files` call for every doc change. 5) Recap in ≤ 5 lines.

**`continue`** — pick the next highest-value item and ship it. Do not ask which one.

## Tech stack

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, Pydantic v2, uvicorn |
| Data | PostgreSQL 16 + pgvector, SQLAlchemy 2.0, Alembic |
| Jobs | Celery + Redis (simulation runs are async, never in-request) |
| Schedule parsing | MPXJ via JPype — `.xer`, `.mpp`, `.xml`, P6 XML |
| Simulation | NumPy + Numba, Latin Hypercube Sampling, Iman-Conover correlation |
| LLM | Claude API — structured outputs + tool use for every elicitation agent |
| Retrieval | BGE-M3 or Voyage embeddings, HNSW index, hybrid + reciprocal rank fusion |
| Frontend | React 18, TypeScript, Vite, TanStack Query, Tailwind, commercial Gantt |
| Charts | Recharts / visx (S-curve, tornado, JCL scatter) |
| Test | pytest + hypothesis, Vitest, Playwright |
| Tooling | uv, ruff, mypy, pnpm, eslint, prettier |

## Repo layout

```
backend/
├── app/
│   ├── main.py          FastAPI app + CORS + lifespan
│   ├── core/             config, domain errors, cross-cutting logic
│   ├── db/               async engine, session, base declarative class, redis client
│   ├── models/           SQLAlchemy models — import into db/base.py for Alembic autogenerate
│   ├── api/               routers
│   ├── schedule/          schedule ingestion (MPXJ bridge, .xer/.mpp parsing) — P2
│   ├── services/          application services
│   └── seed_rbs.py        RBS taxonomy seed script
├── alembic/               migrations (alembic.ini + versions/)
├── scripts/                one-off + CI-adjacent scripts (CI logic lives here, not .github/workflows)
├── tests/                  mirrors app/ tree
├── requirements.txt / requirements-dev.txt
├── pytest.ini
└── Dockerfile

frontend/
├── src/                    React app
├── package.json            npm — package-lock.json present, not pnpm
├── index.html, vite.config.ts, tsconfig.json

sample-schedules/            reference .xer / .mpp fixtures for parser tests
```
No dedicated `sim/` package exists yet. When `P3` (Monte Carlo engine) starts, it must land
as its own package — `backend/app/sim/` or `backend/sim/` — kept free of DB, network, and
logging side effects per the sim-purity invariant. Do not fold it into `services/`; that
boundary is easy to blur once real code is there and hard to unwind after.

## Build commands

Target surface (see `claude/ACTIVE.md` for what's actually wired up):

```bash
make dev            # docker compose up: postgres, redis, api, worker, web
make install        # uv sync && npm --prefix frontend install
make test           # pytest + vitest
make test-sim       # sim/ only, includes statistical regression tests
make lint           # ruff + mypy + eslint + prettier --check
make fmt            # ruff format + prettier --write
make migrate        # alembic upgrade head
make migration m="..."  # alembic revision --autogenerate
```

## Coding conventions

- Python: ruff + ruff-format, line length 100. `mypy --strict` on `core/`, `sim/`, `parse/`.
- No bare `except`. Domain errors subclass `core.errors.RiskPlatformError`.
- Pydantic models at every boundary. No raw dicts crossing a module edge.
- `sim/` is pure: no DB, no network, no logging side effects. Seed in, arrays out.
- Money as integer minor units. Never float currency. Durations in **working days**, always
  paired with the calendar ID they were computed against.
- TypeScript strict. Named exports. API types generated from OpenAPI — never hand-written.
- Tests mirror the source path. Any bug fix lands with the failing test first.
- Commit messages: `type: imperative summary`. Combine related edits into one commit.

## Non-negotiable invariants

Violating any of these is a correctness bug, not a style opinion. Detail in `REFERENCE.md`.

1. **Never add percentiles.** Cost contingency and schedule-driven burn-rate cost are
   integrated *inside each iteration*, then percentiled once at the end. P80 cost + P80
   delay × burn rate is wrong and will be rejected in review.
2. **Correlation before sampling.** Risk draws pass through Iman-Conover rank correlation.
   Independent sampling understates tail contingency.
3. **Quality gate before simulation.** No schedule enters Monte Carlo without a DCMA
   14-point pass. Garbage in, credible-looking garbage out.
4. **Every AI output is a proposal.** Identified risks, suggested probabilities, impact
   ranges, activity mappings — all land as `proposed` and require human acceptance to become
   register state. A human analyst can override at every gate.
5. **Audit trail is append-only.** Every value records who set it, when, and whether it was
   agent-suggested or human-entered. Never mutate history; write a new revision.
6. **Runs are reproducible.** Every simulation persists its seed, inputs hash, and engine
   version. Same inputs must reproduce the same distribution, bit for bit.

## Token discipline

- GitHub MCP for every read. Never the web editor.
- `get_file_contents` on exact paths. Do not crawl directories to browse.
- Never re-read a file already read this chat. Do not echo file contents back into chat —
  say what changed, not what the file says.
- Avoid files over 30KB unless the task requires it. Editing a large file costs it twice.
- do not `push_files` (multi-file, atomic). 
- provide the files and instructions to update and push them manually
- `search_code` is unreliable. For audits, read specific files directly.
- One chat, one theme. When the topic changes, `yeet` and open a new chat.

## Working style

Direct and autonomous. Execute without asking permission between steps. Ship complete
implementations, not plans. No preamble, no options lists, no hedging, no filler. Push back
when I am wrong. Do not be sycophantic.

Before any non-trivial build or fix: question what I am actually trying to achieve underneath
the request, cut everything that does not earn its place, name the one thing this must do
well, build the simplest version that does it, then stress the edges — errors, empty states,
narrow screens, slow networks, accessibility — before calling it done.
