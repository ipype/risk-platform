# SYSTEM.md — stable primitives

Read at bootstrap. Changes rarely. If something here changes weekly, it belongs in
`ACTIVE.md` instead.

## IDs

- Repo: `ipype/Risk-Platform`, default branch `main`
- Package namespace: `ipype_risk`
- Docker compose project: `ipype-risk`

## Services and ports (local)

| Service | Port |
|---|---|
| api (FastAPI) | 8000 |
| web (Vite) | 5173 |
| postgres | 5432 |
| redis | 6379 |
| flower (Celery UI) | 5555 |

## Environment

`.env.example` is the source of truth for required vars. Never commit `.env`. Secrets are
referenced by name only in docs. Required: `DATABASE_URL`, `REDIS_URL`, `ANTHROPIC_API_KEY`,
`EMBEDDING_PROVIDER`, `JAVA_HOME` (MPXJ needs a JRE).

## Standing rules

- Simulation work runs in Celery, never inside a request handler.
- `sim/` stays pure and dependency-light so it can be property-tested with hypothesis.
- Schedule files are stored immutably on upload; parsed output is a derived artifact and may
  be regenerated at any time.
- Any change to sampling, correlation, or percentile logic requires a statistical regression
  test and a dated entry in `REFERENCE.md`.
- CI logic lives in `scripts/`, invoked by thin workflow files added manually (MCP cannot
  push to `.github/workflows/`).

## Reference standards

- AACE International RP 57R-09 (integrated cost/schedule risk analysis)
- DCMA 14-point schedule assessment
- Hulett QSRA method for schedule risk
