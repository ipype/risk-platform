# Risk Platform — P0 scaffold

FastAPI + async SQLAlchemy + Postgres (pgvector) + Redis + Alembic. This is the Phase 0
foundation: the stack stands up, migrations apply, and a health endpoint reads the database.

## Prerequisites

- Docker and Docker Compose (v2, the `docker compose` command)

## Quickstart

```bash
cp .env.example .env
make up            # or: docker compose up --build
```

That builds the image, starts Postgres + Redis, runs `alembic upgrade head`, and serves the API.

Then open:

- http://localhost:8000/docs — interactive API docs
- http://localhost:8000/health — liveness (always 200 when the process is up)
- http://localhost:8000/health/ready — readiness (checks Postgres + Redis, 200 or 503)

## Project layout

```
risk-platform/
├── docker-compose.yml        db (pgvector) + redis + api
├── .env.example              copy to .env
├── Makefile                  up / down / migrate / revision / test / fmt / shell
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── alembic.ini
    ├── alembic/
    │   ├── env.py            async migration runner
    │   └── versions/0001_initial.py
    └── app/
        ├── main.py          FastAPI app + CORS + lifespan
        ├── core/config.py   pydantic-settings
        ├── db/
        │   ├── base_class.py   DeclarativeBase
        │   ├── base.py         imports models for Alembic
        │   ├── session.py      async engine + get_db dependency
        │   └── redis.py        async redis client
        ├── models/system.py    SystemMeta (trivial table)
        └── api/routes/health.py
```

## Migrations

Migrations apply automatically on `make up`. To add the next one (e.g. when you start P1):

```bash
make revision m="add risk register tables"   # autogenerate from your models
make migrate                                  # apply
```

New models must be imported in `app/db/base.py` so autogenerate sees them.

## Tests

```bash
make test        # runs pytest in the api container
```

The included tests hit `/health` and `/` over ASGI, so they pass without a running database.

## Notes

- `DATABASE_URL` uses the `postgresql+asyncpg://` driver — required for the async engine.
- The initial migration runs `CREATE EXTENSION IF NOT EXISTS vector`, so pgvector is ready
  for embeddings in P2/P5.
- CORS origins are comma-separated in `BACKEND_CORS_ORIGINS`.
