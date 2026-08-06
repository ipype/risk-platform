"""Handing a queued run to whatever is going to execute it.

One function so there is one seam. The API must not import Celery — a broker that is down
should cost a queued run its start, not the whole process its import — and the tests must
be able to run an assembly end to end without standing up Redis. Both fall out of keeping
the import inside the call.

``simulation_eager`` runs the engine in the request instead of queueing it. That is a
development and test convenience, not a deployment mode: a real network at ten thousand
iterations will hold the connection open for minutes and time out at whatever proxy sits
in front of it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.simulation import SimulationRun


async def revoke(run: SimulationRun) -> None:
    """Tell the broker to drop a queued task before a worker claims it.

    Best-effort and non-fatal by design, matching ``dispatch``: the caller has already
    committed ``status='cancelled'`` under invariant 4's human-override rule, so a broker
    that cannot be reached here should not undo that decision. ``sim_execute.execute``
    is the real backstop — it refuses to touch a run that already reached a terminal
    state, so a task that slips past this revoke and gets claimed anyway is a no-op, not
    a race that overwrites the cancellation.

    Nothing to revoke in the eager path or for a run that was never dispatched with a
    task id — both leave silently.
    """
    if settings.simulation_eager or not run.task_id:
        return
    try:
        from app.worker import celery_app

        celery_app.control.revoke(run.task_id)
    except Exception:  # noqa: BLE001 - the broker being unreachable is not this call's job
        pass


def live_workers(timeout: float) -> list[str] | None:
    """Names of the workers answering on the broker right now.

    ``None`` means the question could not be asked — no broker, no control channel — as
    opposed to ``[]``, which means it was asked and nobody was there. The two get
    different treatment in :func:`dispatch`: a broker that cannot be reached is left to
    ``delay`` to report, because it raises a message naming the actual connection
    failure, and guessing ahead of it would replace that with a vaguer one.
    """
    try:
        from app.worker import celery_app

        replies = celery_app.control.ping(timeout=timeout) or []
    except Exception:  # noqa: BLE001 - an unanswerable question, not a failed run
        return None
    return [name for reply in replies for name in reply]


async def dispatch(db: AsyncSession, run: SimulationRun) -> SimulationRun:
    """Start the run. Never raises: a run that cannot be queued is a failed run."""
    if settings.simulation_eager:
        from app.services.sim_execute import execute

        result = await execute(db, run.id)
        return result or run

    # Queueing into an empty cluster is the one failure this module used to hide. The
    # publish succeeds, the task id is real, and the run waits on a worker that does not
    # exist — for hours, looking exactly like a slow run, with nothing written anywhere
    # to say otherwise. Better to refuse now and say why.
    if settings.simulation_require_worker:
        workers = live_workers(settings.simulation_worker_ping_seconds)
        if workers is not None and not workers:
            run.status = "failed"
            run.error = (
                "No simulation worker answered, so the run was not queued — it would "
                "have waited indefinitely. Start the worker "
                "(`docker compose up -d worker`) and run this again."
            )
            await db.commit()
            return run

    try:
        from app.tasks.simulation import run_simulation

        async_result = run_simulation.delay(run.id)
        run.task_id = str(async_result.id)
    except Exception as exc:  # noqa: BLE001 - the broker being down is an outcome, not a crash
        run.status = "failed"
        run.error = (
            "The run could not be queued, so it never started: "
            f"{type(exc).__name__}: {exc}"
        )
    await db.commit()
    return run
