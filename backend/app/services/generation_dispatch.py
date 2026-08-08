"""Handing a queued generation to whatever is going to execute it.

The same seam ``sim_dispatch`` draws, for two of the same reasons and one new one. The API
must not import Celery, so a broker that is down costs a queued run its start rather than
the whole process its import. The tests must be able to run a pass end to end without
Redis. And — the new one — a generation makes paid outbound calls that take tens of seconds
each, so a twenty-window pass is minutes of wall clock behind whatever proxy sits in front
of the API. Running it in the request would time out somewhere in the middle, leaving a run
that is still going, still spending, and no longer connected to anyone waiting for it.

**The provider is checked before the run is queued.** ``LlmNotConfigured`` is raised out of
the route rather than recorded on a failed run, because a deployment with no ``LLM_PROVIDER``
would otherwise accumulate identical failed runs all saying the same thing about the same
missing setting. Everything that could still fail after that — a bad key, a wrong model
string, a provider outage — belongs on the run, because each is per-attempt.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.generation import FAILED, GenerationRun

__all__ = ["dispatch", "live_workers"]


def live_workers(timeout: float) -> list[str] | None:
    """Names of the workers answering the broker, or ``None`` if nobody could be asked.

    The distinction matters exactly as it does for simulations: ``[]`` means the question
    was asked and nobody was there, which is worth refusing over; ``None`` means there was
    no control channel to ask on, which ``delay`` will report more precisely than a guess
    made here.
    """
    try:
        from app.worker import celery_app

        replies = celery_app.control.ping(timeout=timeout) or []
    except Exception:  # noqa: BLE001 - an unanswerable question, not a failed run
        return None
    return [name for reply in replies for name in reply]


async def dispatch(db: AsyncSession, run: GenerationRun) -> GenerationRun:
    """Start the pass. Never raises: a run that cannot be queued is a failed run."""
    if settings.generation_eager:
        from app.services.generation_execute import execute

        result = await execute(db, run.id)
        return result or run

    if settings.generation_require_worker:
        workers = live_workers(settings.generation_worker_ping_seconds)
        if workers is not None and not workers:
            run.status = FAILED
            run.error = (
                "No worker answered, so the generation was not queued — it would have "
                "waited indefinitely. Start the worker (`docker compose up -d worker`) "
                "and run this again."
            )
            await db.commit()
            return run

    try:
        from app.tasks.generation import run_generation

        async_result = run_generation.delay(run.id)
        run.task_id = str(async_result.id)
    except Exception as exc:  # noqa: BLE001 - the broker being down is an outcome
        run.status = FAILED
        run.error = (
            "The generation could not be queued, so it never started: "
            f"{type(exc).__name__}: {exc}"
        )
    await db.commit()
    return run
