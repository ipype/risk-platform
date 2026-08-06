"""The task the worker actually runs.

Celery is synchronous and the persistence layer is not, so the task owns an event loop for
the length of one run. The engine is created inside that loop and disposed at the end
rather than shared from ``app.db.session``: a worker process is forked, and an asyncpg
pool inherited across a fork carries sockets belonging to the parent. That failure is
intermittent, looks like a corrupted result, and is not worth the connection it saves on a
task that runs for minutes.

Everything below the first ``try`` exists for one symptom: a run sitting in ``queued``
forever with nothing written against it. ``task_acks_late`` acknowledges a task that
*raised* just as it acknowledges one that returned, so a task dying before
``sim_execute.execute`` claims the row leaves no message on the broker and no trace in the
database. The run is then unreachable — not queued for anything, not running, not failed,
and not cancellable by anything that inspects its status. The recovery write below is the
last chance to say why.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.services.sim_execute import execute, record_failure
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

    Separate engine because the first one is the prime suspect: a pool that cannot
    connect, a fork that inherited someone else's socket, a URL pointing at nothing. If
    this fails too the run stays queued and the traceback below is all there is, which is
    still better than the silence this replaces.
    """
    engine = _engine()
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            return await record_failure(
                session,
                run_id,
                f"The worker could not run this simulation: {type(exc).__name__}: {exc}",
            )
    finally:
        await engine.dispose()


@celery_app.task(name="simulation.run")
def run_simulation(run_id: int) -> dict:
    """Execute one run. Returns its terminal status rather than the result.

    The result belongs in the database, where the API reads it from. Returning it through
    the Celery backend as well would give two copies of a megabyte of JSON and two places
    for them to disagree.

    Returns rather than re-raising once the failure is written down: the run record is the
    authority on what happened, and a Celery task marked FAILURE next to a run marked
    ``failed`` is the same fact stored twice. The traceback is logged either way.
    """
    try:
        return {"run_id": run_id, "status": asyncio.run(_run(run_id))}
    except Exception as exc:  # noqa: BLE001 - a worker must record why, not disappear
        logger.exception("Simulation run %s failed before it could complete", run_id)
        try:
            recorded = asyncio.run(_record(run_id, exc))
        except Exception:  # noqa: BLE001 - the database is unreachable; the log is all we have
            logger.exception(
                "Simulation run %s could not be marked failed either; it will stay "
                "queued until something else touches it",
                run_id,
            )
            return {"run_id": run_id, "status": "unrecorded"}
        # ``False`` means the ``WHERE`` matched nothing: the run had already reached a
        # terminal state, or it is gone. Either way it is not this task's to change.
        return {"run_id": run_id, "status": "failed" if recorded else "unchanged"}
