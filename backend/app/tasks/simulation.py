"""The task the worker actually runs.

Celery is synchronous and the persistence layer is not, so the task owns an event loop for
the length of one run. The engine is created inside that loop and disposed at the end
rather than shared from ``app.db.session``: a worker process is forked, and an asyncpg
pool inherited across a fork carries sockets belonging to the parent. That failure is
intermittent, looks like a corrupted result, and is not worth the connection it saves on a
task that runs for minutes.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services.sim_execute import execute
from app.worker import celery_app


async def _run(run_id: int) -> str:
    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            run = await execute(session, run_id)
            return "missing" if run is None else run.status
    finally:
        await engine.dispose()


@celery_app.task(name="simulation.run")
def run_simulation(run_id: int) -> dict:
    """Execute one run. Returns its terminal status rather than the result.

    The result belongs in the database, where the API reads it from. Returning it through
    the Celery backend as well would give two copies of a megabyte of JSON and two places
    for them to disagree.
    """
    return {"run_id": run_id, "status": asyncio.run(_run(run_id))}
