from contextlib import asynccontextmanager

from app.api.errors import register_exception_handlers
from app.core.config import settings
from app.db.redis import redis_client
from app.db.session import engine
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    custom_fields,
    documents,
    evidence,
    export,
    health,
    history,
    mappings,
    matrix,
    mitigation_plans,
    mitigations,
    proposals,
    quant,
    rbs,
    reports,
    risks,
    roi,
    schedules,
    scopes,
    simulations,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()
    await redis_client.aclose()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # the browser can only read this header cross-origin if it is exposed,
        # and the Excel export relies on it for the download filename
        expose_headers=["Content-Disposition"],
    )

# domain errors -> meaningful status codes, in one place instead of per-route try/except
register_exception_handlers(app)

app.include_router(health.router)
app.include_router(scopes.router)
app.include_router(rbs.router)
app.include_router(risks.router)
app.include_router(matrix.router)
app.include_router(history.router)
app.include_router(mitigations.router)
app.include_router(mitigation_plans.router)
app.include_router(custom_fields.router)
app.include_router(export.router)
app.include_router(schedules.router)
app.include_router(mappings.router)
app.include_router(quant.router)
app.include_router(simulations.router)
app.include_router(roi.router)
app.include_router(reports.router)
app.include_router(proposals.router)
app.include_router(documents.router)
app.include_router(evidence.router)


@app.get("/", tags=["root"])
async def root() -> dict:
    return {"name": settings.app_name, "docs": "/docs", "health": "/health"}
