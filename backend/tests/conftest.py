"""Test harness for the schedule routes.

Builds a minimal app carrying only the schedules router against in-memory SQLite, so the
suite needs no live Postgres and no Redis. Only the schedule tables are created, which
keeps these tests independent of the rest of the app's metadata.

The mapping tables are in that set even though no mapping route is mounted: deleting a
schedule version has to remove the risk-to-activity mappings made against it and log
their removal, so a delete test that never sees those tables would be testing a
different code path from the one that ships.

``app.db.base`` is imported for its side effect of registering *every* model on the shared
metadata. ``RiskActivityMapping.risk_id`` is a foreign key onto ``risk``, and
``create_all`` cannot emit the constraint unless the target table object exists — even
though ``risk`` itself is deliberately not created here. Without the import this file
passes or fails depending on whether some earlier test module happened to import the risk
models first, which is not a property a harness should have.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import register_exception_handlers
from app.api.routes import schedules
from app.db.base_class import Base
from app.db.session import get_db
from app.db import base as _all_models  # noqa: F401  (registers every table)
from app.models import mapping as mapping_models
from app.models import schedule as schedule_models

SCHEDULE_TABLES = [
    schedule_models.ScheduleFile.__table__,
    schedule_models.ScheduleVersion.__table__,
    schedule_models.ScheduleCalendar.__table__,
    schedule_models.ScheduleWbs.__table__,
    schedule_models.ScheduleActivity.__table__,
    schedule_models.ScheduleRelationship.__table__,
    schedule_models.DcmaRun.__table__,
    mapping_models.RiskActivityMapping.__table__,
    mapping_models.MappingHistory.__table__,
]


@pytest_asyncio.fixture
async def session_factory():
    # a single shared in-memory database for the life of one test
    engine = create_async_engine("sqlite+aiosqlite://", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=SCHEDULE_TABLES)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def app(session_factory) -> FastAPI:
    test_app = FastAPI()
    register_exception_handlers(test_app)
    test_app.include_router(schedules.router)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_db] = override_get_db
    return test_app


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest_asyncio.fixture
async def db(session_factory):
    """A session for tests that exercise the service layer directly."""
    async with session_factory() as session:
        yield session
