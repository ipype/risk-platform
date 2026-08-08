"""Which generator runs a given run.

A one-function seam, added when the second generator arrived. ``generation_dispatch`` and
``tasks/generation.py`` both hold a run id and no opinion about what kind of pass it is,
and before this module they held a hard import of ``risk_generate`` instead — which worked
for exactly as long as there was one generator.

**The lookup is by ``kind`` and an unknown kind fails the run rather than passing it.** A
run row whose kind nothing recognises is a deployment that shipped a route without its
executor, and the honest outcome is a failed run naming the kind. Falling back to the
identification generator would run the wrong pass over the wrong inputs and record it as a
success.

**The imports are deferred.** Both executors pull in the model seam and, through it,
``httpx``; the dispatch functions here are imported by the Celery task module at worker
boot and by the API at request time, and neither should pay for a provider it may not use.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.types import Provider
from app.models.generation import (
    QUALITATIVE_EVALUATION,
    RISK_IDENTIFICATION,
    GenerationRun,
)

__all__ = ["execute", "record_failure", "EXECUTORS"]

#: ``kind`` -> the module holding its ``execute``. A dict rather than a chain of ``if``s
#: so that "which kinds can actually run" is one readable line at import time.
EXECUTORS: dict[str, str] = {
    RISK_IDENTIFICATION: "app.services.risk_generate",
    QUALITATIVE_EVALUATION: "app.services.qual_generate",
}


async def execute(
    db: AsyncSession,
    run_id: int,
    *,
    settings: Settings | None = None,
    provider: Provider | None = None,
) -> GenerationRun | None:
    """Run one queued generation of whatever kind it is. ``None`` if the run is gone."""
    run = await db.get(GenerationRun, run_id)
    if run is None:
        return None

    module_path = EXECUTORS.get(run.kind)
    if module_path is None:
        await record_failure(
            db,
            run_id,
            f"Nothing in this build knows how to run a {run.kind!r} generation. "
            f"Runnable kinds: {', '.join(sorted(EXECUTORS))}.",
        )
        return await db.get(GenerationRun, run_id)

    from importlib import import_module

    module = import_module(module_path)
    return await module.execute(db, run_id, settings=settings, provider=provider)


async def record_failure(db: AsyncSession, run_id: int, message: str) -> bool:
    """Mark a run failed from outside the normal path. ``False`` if it was already done.

    Kind-independent — it touches only ``generation_run`` — so it is defined once here and
    ``risk_generate.record_failure`` remains as the identification generator's own name for
    the same operation rather than being deleted out from under 5.4's tests.
    """
    from app.services.risk_generate import record_failure as _record

    return await _record(db, run_id, message)
