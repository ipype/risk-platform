# iPype Risk Platform

Quantitative risk analysis for capital infrastructure projects. Document ingestion through
risk identification, qualitative and quantitative elicitation, CPM schedule import,
integrated cost and schedule Monte Carlo, sensitivity analysis, mitigation planning, and a
living risk register with outcome feedback.

Methodology anchors: **AACE RP 57R-09**, **DCMA 14-point schedule assessment**, **Hulett
QSRA**.

The build order is deliberate: **deterministic spine first, AI layers second.** A working
register and a correct simulation engine come before any agent features.

---

## Status

**Working now** — register, taxonomy, scoring, mitigations, history, export, `.xer` schedule
ingest with a DCMA quality gate, and risk-to-activity mapping with a suggestion engine.
Schema is at migration `0010`.

**Not built yet** — Monte Carlo simulation, sensitivity and criticality analysis, `.mpp`
ingest (MPXJ/JPype, parked), the risk identification and elicitation agents, auth, CI.

Auth is a skeleton designed for a one-line swap. Do not deploy this anywhere public yet.

---

## Prerequisites

- Docker and Docker Compose v2 (the `docker compose` command, not `docker-compose`)

Nothing else. Python and Node run inside containers, which is the supported path — see
*Local tooling* under Notes before trying to run the suite in a host venv.

## Quickstart

```bash
cp .env.example .env
make up            # or: docker compose up --build
```

That builds the API image, starts Postgres and Redis, runs `alembic upgrade head`, serves
the API, and starts the Vite dev server.

| Service | URL | Notes |
|---|---|---|
| Frontend | http://localhost:5173 | Vite dev server, hot reload |
| API docs | http://localhost:8000/docs | Interactive OpenAPI — authoritative for route shapes |
| Liveness | http://localhost:8000/health | 200 whenever the process is up |
| Readiness | http://localhost:8000/health/ready | Checks Postgres + Redis, 200 or 503 |
| Postgres | localhost:5432 | pgvector/pgvector:pg16 |
| Redis | localhost:6379 | redis:7-alpine |

## Make targets

```
make up         start db, redis, api, web and apply migrations
make down       stop and remove containers
make logs       tail api logs
make migrate    docker compose exec api alembic upgrade head
make revision   autogenerate a migration:  make revision m="add risks"
make test       pytest in the api container
make fmt        ruff check --fix and ruff format
make shell      bash into the api container
```

---

## Project layout

```
Risk-Platform/
├── docker-compose.yml          db (pgvector) + redis + api + web
├── .env.example                copy to .env
├── Makefile
├── CLAUDE.md                   docs map + session bootstrap/close sequences
├── .claude/                    SYSTEM.md, ACTIVE.md, BACKLOG.md, REFERENCE.md, sessions/, plans/
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt        runtime pins (FastAPI 0.115.6 — see Notes)
│   ├── requirements-dev.txt    test pins
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py              async migration runner
│   │   └── versions/           0001 … 0010
│   ├── app/
│   │   ├── main.py             app, CORS, lifespan, router registration
│   │   ├── core/config.py      pydantic-settings
│   │   ├── db/
│   │   │   ├── base_class.py   DeclarativeBase
│   │   │   ├── base.py         imports every model so autogenerate sees it
│   │   │   ├── session.py      async engine + get_db
│   │   │   └── redis.py        async redis client
│   │   ├── models/             rbs, risk, matrix, history, mitigation,
│   │   │                       custom_fields, schedule, mapping, system
│   │   ├── services/           mapping_suggest, mapping_lexicon, mapping_service,
│   │   │                       schedule parsing, DCMA checks, export
│   │   └── api/
│   │       ├── errors.py       domain errors → status codes, one place
│   │       └── routes/         health, rbs, risks, matrix, history, mitigations,
│   │                           custom_fields, export, schedules, mappings
│   └── tests/
└── frontend/
    ├── package.json
    └── src/
        ├── App.tsx             nav + view switch
        ├── api.ts, types.ts    API client and shared types
        ├── columns.ts          register column definitions
        ├── views/              RegisterView, MatrixView, MappingView, …
        ├── components/         register, matrix, mitigation, mapping/
        └── index.css, matrix.css, mapping.css
```

CSS is per-feature and scoped by prefix (`map-`, `mtx-`), self-contained rather than
globally cascading.

---

## What's implemented

### Risk breakdown structure

Exactly two levels: category and subcategory. Ten categories, 58 subcategories, seeded from
an industry-standard taxonomy. Risk codes are `CCC-DDD-XXXX`, where the `XXXX` counter runs
**per subcategory**, not globally.

### Risk register

Cause–event–effect schema. Current and residual probability and impact, with risk levels
computed from the active matrix configuration. Overall impact is the **worst case across
impact areas** — the maximum, never a weighted average, because a project-ending safety
consequence must not be averaged away by low cost and reputation scores.

Register table with a column picker, Excel-style per-column filters, and user-defined custom
columns (text, number, single-select) held as per-risk JSON against stable keys.

### Matrix scoring

Configurable probability × impact across Cost, Schedule, Safety, Reputation, Environment.
Heatmap view with a lens selector to score the same register through any single impact area.
Thresholds and band colours are configuration, not code.

### Mitigation actions

A child table, not a text field: owner, due date, budget, completion percentage,
effectiveness, status. Effectiveness is intended to be judged by **re-simulation** —
delta-contingency per dollar spent — not by static residual re-scoring. That closes when the
simulation engine lands.

### Change history

Append-only. Every create, edit, and delete is logged with actor attribution. Records are
never mutated or removed.

### Export

Excel export of the register. `Content-Disposition` is in the CORS `expose_headers` list
because the browser cannot read the download filename cross-origin otherwise. PDF export is
open.

### Schedule ingest (`.xer`)

Parses Primavera `.xer` into activities, relationships, and calendars (migration `0009`).
Runs the **DCMA 14-point assessment**; a non-compliant schedule **blocks simulation**. A
human can override the gate, and the override is logged.

`.mpp` is deliberately out of scope for now. Nothing in the current parser layer touches it.

### Risk-to-activity mapping (`0010`)

Suggests which schedule activities each schedule-impacting risk should attach to.

- Pure scoring core in `mapping_suggest.py` — tokenizer and stemmer, IDF over the activity
  corpus, a four-signal blend, and **abstention** when no signal is strong enough. No DB and
  no network in that module, so it is property-testable in isolation.
- Schedule vocabulary per RBS category lives in `mapping_lexicon.py`, kept separate so
  tuning the vocabulary never touches the scoring engine.
- Three distinct mapping types, including inserted activities.
- Coverage reporting runs **both directions**: unmapped risks, and critical-path activities
  with no risk coverage.
- Carry-forward between schedule revisions matches on activity `code`, not `source_id`, so
  it survives a re-export.
- Bulk accept, reject-with-reason, per-mapping history.
- Three-pane workbench UI with `j` / `k` / `a` / `x` keyboard flow.

Acceptance and rejection outcomes are recorded to sharpen future suggestions.

---

## Platform invariants

Non-negotiable. Breaking any of these produces numbers that look authoritative and are
wrong, which is worse than producing nothing.

1. **No additive percentiles.** Cost and schedule integration happens *inside* each Monte
   Carlo iteration: `cost_i = base_i + risk_cost_i + burn_rate × delay_i`. Never sum P80s
   after the fact.
2. **Iman-Conover rank correlation** is applied before sampling. RBS category and shared
   driver tags build the correlation matrix.
3. **The DCMA gate blocks simulation** on a non-compliant schedule. Human override is
   allowed and is logged.
4. **AI output is always a proposal.** No autonomous write to the register without human
   review.
5. **Append-only audit trail.** Every change attributed, nothing mutated in place.
6. **Reproducible runs.** Seeds and parameters are stored so any run can be replayed
   exactly.

One more, for the elicitation subsystem when it lands: the agent's suggested probability is
**withheld until the SME commits their own estimate**. Showing it first anchors the SME and
destroys the independence of the judgement.

---

## Migrations

Applied automatically on `make up`. Current head is `0010`.

```
0001_initial                 base + CREATE EXTENSION vector
0002_rbs                     taxonomy tables
0003_risk                    register, cause-event-effect
0004_matrix_config           configurable scoring
0005_risk_history            attributed change log
0006_target_risk             residual probability and impact
0007_mitigation_actions      structured mitigations
0008_custom_fields           user-defined columns
0009_schedule                activities, relationships, calendars, DCMA
0010_risk_activity_mapping   mappings, history, suggestion outcomes
```

To add the next one:

```bash
make revision m="add simulation runs"
make migrate
```

Every new model must be imported in `app/db/base.py` or `alembic autogenerate` will not see
it and will silently emit an empty migration.

---

## Tests

```bash
make test                                    # whole suite in the container
docker compose exec api pytest tests/test_mapping_suggest.py -q
```

Run tests in the container. It has the correct pinned dependencies baked in from the
Dockerfile build.

---

## Working conventions

`CLAUDE.md` at the root is the documentation map: it names every file and the exact trigger
for opening it. `.claude/SYSTEM.md` holds stable primitives, `.claude/ACTIVE.md` in-flight
work only, `.claude/BACKLOG.md` deferred work, `.claude/REFERENCE.md` the invariants,
gotchas, and dated decision history. `.claude/sessions/` is a write-only archive.

Split, never consolidate. Source code is authoritative; docs lag reality.

---

## Notes and gotchas

- **`DATABASE_URL` must use `postgresql+asyncpg://`.** The async engine requires it.
- **CORS origins** are comma-separated in `BACKEND_CORS_ORIGINS`.
- **pgvector** is created by `0001` and sits unused until the embedding work starts.
- **Verify against the pinned versions.** A route returning `None` with `status_code=204`
  and no explicit `response_model=None` crashes FastAPI `0.115.6` **at import time** —
  before uvicorn binds a port, so it presents as "service api is not running" rather than as
  a route error. Newer FastAPI silently guards the same case. Test against
  `requirements.txt`, never against whatever a bare `pip install` resolves.
- **Local tooling.** A host venv needs `requirements-dev.txt` for `aiosqlite`, and some
  networks resolve `pytest-asyncio` against a stale index. The container sidesteps both.

---

## Roadmap

1. PDF export alongside Excel.
2. Monte Carlo engine — cost and schedule in one integrated iteration, Beta-PERT and Latin
   Hypercube sampling, burn-rate prompt applied per iteration, JCL scatter output.
3. Sensitivity and criticality — tornado charts, criticality index by activity, and
   mitigation recommendations ranked by delta-contingency per dollar.
4. Auth and CI.
5. `.mpp` ingest via MPXJ over JPype.
6. Risk identification agent — four parallel passes plus a self-critique premortem, and a
   Delphi-style workshop mode.
7. Quantitative elicitation subsystem with the anchoring guard above.

Open architecture decisions: Gantt component, tenancy model, deployment target, embedding
provider, `.mpp` scope.
