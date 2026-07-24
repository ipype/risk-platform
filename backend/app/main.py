from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    custom_fields,
    health,
    history,
    matrix,
    mitigations,
    rbs,
    risks,
)
from app.core.config import settings
from app.db.redis import redis_client
from app.db.session import engine


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
    )

app.include_router(health.router)
app.include_router(rbs.router)
app.include_router(risks.router)
app.include_router(matrix.router)
app.include_router(history.router)
app.include_router(mitigations.router)
app.include_router(custom_fields.router)


@app.get("/", tags=["root"])
async def root() -> dict:
    return {"name": settings.app_name, "docs": "/docs", "health": "/health"}
