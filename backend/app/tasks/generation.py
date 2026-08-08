"""The generation task the worker actually runs.

Structurally identical to ``tasks/simulation.py`` and for the same reasons: Celery is
synchronous and the persistence layer is not, so the task owns an event loop for the length
of one pass; the engine is built inside that loop and disposed at the end rather than
shared from ``app.db.session``, because a worker process is forked and an asyncpg pool
inherited across a fork carries sockets belonging to the parent.

The task is kind-agnostic: ``generation_execute.execute`` reads the run's ``kind`` and
picks the generator, so a new stage ships a service and a route and never a second task.

The recovery write below exists for one symptom: a run sitting in ``queued`` forever with
nothing against it. ``task_acks_late`` acknowledges a task that raised exactly as it
acknowledges one that returned, so a task dying before the executor claims the row leaves
no message on the broker and no trace in the database. For a generation that is
worse than for a simulation, because the calls it may already have made cost money and
nothing would record that they happened.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services.generation_execute import execute, record_failure
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _engine():
    return create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)


async def _run(run_id: int) -> str:
    engine = _engine()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            run = await execute(session, run_id)
            return "missing" if run is None else run.status
    finally:
        await engine.dispose()


async def _record(run_id: int, exc: BaseException) -> bool:
    """Second attempt at saying why, on a connection of its own.

    Separate engine because the first one is the prime suspect. If this fails too the run
    stays queued and the traceback is all there is, which is still better than silence.
    """
    engine = _engine()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            return await record_failure(
                session,
                run_id,
                f"The worker could not run this generation: {type(exc).__name__}: {exc}",
            )
    finally:
        await engine.dispose()


@celery_app.task(name="generation.run")
def run_generation(run_id: int) -> dict:
    """Execute one generation pass. Returns its terminal status, not its output.

    The proposals belong in the ledger and the transcript on the run; returning either
    through the Celery result backend would give two copies and two places for them to
    disagree about what the model said.
    """
    try:
        return {"run_id": run_id, "status": asyncio.run(_run(run_id))}
    except Exception as exc:  # noqa: BLE001 - a worker must record why, not disappear
        logger.exception("Generation run %s failed before it could complete", run_id)
        try:
            recorded = asyncio.run(_record(run_id, exc))
        except Exception:  # noqa: BLE001 - the database is unreachable; the log is all we have
            logger.exception(
                "Generation run %s could not be marked failed either; it will stay "
                "queued until something else touches it",
                run_id,
            )
            return {"run_id": run_id, "status": "unrecorded"}
        return {"run_id": run_id, "status": "failed" if recorded else "unchanged"}
