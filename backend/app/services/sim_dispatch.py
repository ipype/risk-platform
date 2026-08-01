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


async def dispatch(db: AsyncSession, run: SimulationRun) -> SimulationRun:
    """Start the run. Never raises: a run that cannot be queued is a failed run."""
    if settings.simulation_eager:
        from app.services.sim_execute import execute

        result = await execute(db, run.id)
        return result or run

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
