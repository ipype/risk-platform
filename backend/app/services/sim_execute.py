"""Running a queued run and writing the answer down.

The engine is pure and synchronous; everything impure about a run lives here. Three
things this module is responsible for and the reasons they are not spread around:

**Idempotence.** ``task_acks_late`` means a worker that dies mid-run hands the message
back, so a run can legitimately be delivered twice. A run already in a terminal state is
left alone rather than recomputed and overwritten — invariant 5 says the record is
append-only, and a second write would silently replace a result somebody may already have
quoted.

**The fingerprint check.** The stored request omits the schedule and carries the version
id instead, so replay rebuilds the network from ``schedule_version``. That table is
append-only, which makes the shortcut exact — and the check is what proves it. If the
rebuilt request hashes to something other than ``inputs_sha256``, something that was
supposed to be immutable moved, and the honest response is to fail rather than to publish
a number under a fingerprint that no longer describes it.

**Non-finite floats.** ``np.percentile`` on a degenerate series, or a variance share of a
constant total, can produce ``inf`` or ``nan``. Python's ``json`` writes those as bare
``NaN``/``Infinity`` tokens, which Postgres ``jsonb`` rejects outright — the run would
succeed, the commit would fail, and the traceback would point at the database rather than
at the arithmetic. They are converted to ``null`` on the way in.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.core.errors import RiskPlatformError
from app.models.simulation import TERMINAL_STATUSES, SimulationRun
from app.services.sim_assembly import rebuild
from app.sim import run as run_engine


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _finite(value: Any) -> Any:
    """Recursively replace non-finite floats with ``None``.

    ``null`` rather than a sentinel number: a P95 that came out infinite is a missing
    answer, and writing 0 or a large float would make it look like a measured one.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


async def load_run(db: AsyncSession, run_id: int) -> SimulationRun | None:
    """Fetch a run with its deferred payloads loaded.

    Deferred columns cannot be lazy-loaded on an async session — the attribute access
    happens outside the greenlet and raises rather than emitting SQL — so anything that
    needs them has to say so up front.
    """
    return await db.get(
        SimulationRun,
        run_id,
        options=[undefer(SimulationRun.request_json), undefer(SimulationRun.result_json)],
        # The row is usually already in the identity map from the POST that created it,
        # and ``get`` would hand it back untouched with the deferred columns still
        # unloaded. Reading one then raises MissingGreenlet rather than emitting SQL,
        # because the lazy load happens outside the async greenlet.
        populate_existing=True,
    )


async def execute(db: AsyncSession, run_id: int) -> SimulationRun | None:
    """Run one queued simulation to completion. Never raises for a domain failure."""
    run = await load_run(db, run_id)
    if run is None:
        return None
    if run.status in TERMINAL_STATUSES:
        return run

    run.status = "running"
    run.started_at = _now()
    await db.commit()

    started = time.perf_counter()
    try:
        request = await rebuild(
            db,
            request_json=run.request_json or {},
            version_id=run.schedule_version_id,
        )
        fingerprint = request.fingerprint()
        if run.inputs_sha256 and fingerprint != run.inputs_sha256:
            raise RiskPlatformError(
                "The inputs no longer hash to what this run recorded "
                f"({fingerprint[:12]} against {run.inputs_sha256[:12]}). Something that "
                "should have been immutable has changed, so the run cannot be reproduced "
                "and will not be published."
            )

        # Off the event loop: a ten-thousand-iteration CPM pass holds the GIL for seconds
        # and the eager path shares its loop with the request that started it.
        outcome = await asyncio.to_thread(run_engine, request)
        result = outcome.result

        run.result_json = _finite(result.model_dump(mode="json"))
        run.engine_version = result.manifest.engine_version
        run.chunk_size = result.manifest.chunk_size
        run.inputs_sha256 = result.manifest.inputs_sha256
        run.status = "succeeded"
        run.error = None
    except RiskPlatformError as exc:
        run.status = "failed"
        run.error = str(exc)
    except Exception as exc:  # noqa: BLE001 - a worker must record why, not disappear
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"

    run.finished_at = _now()
    run.duration_ms = int((time.perf_counter() - started) * 1000)
    await db.commit()
    return run
