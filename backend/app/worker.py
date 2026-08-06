"""The Celery application.

A run is minutes of CPU on a five-thousand-activity network, so it never happens inside a
request — the standing rule in ``CLAUDE.md``, and the reason the ``worker`` service exists
in the compose file.

Settings worth their line:

``task_acks_late`` with ``worker_prefetch_multiplier = 1`` means a worker that dies takes
its message back to the broker instead of losing it, and holds exactly one at a time. Both
matter for a task that runs for minutes: the default prefetch would let one worker sit on
a queue of runs it has no chance of starting soon, and early acknowledgement would drop a
run whose worker was killed halfway.

Redelivery is safe because ``sim_execute.execute`` refuses to touch a run that already
reached a terminal state.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

#: Imported for its side effect: it registers *every* model on the shared metadata.
#:
#: The worker's ``include`` below pulls in one task module, and that module's import chain
#: reaches only the tables it happens to touch — eighteen of them, without ``scope_node``,
#: which nothing outside the API routers imports. SQLAlchemy resolves a ``ForeignKey`` by
#: looking its target up in the metadata at the moment something first needs it, so a
#: missing table object is not an import error. It is a ``NoReferencedTableError`` raised
#: later, from whichever query first forces resolution, in the worker only, and not at all
#: under a query simple enough to avoid the join. That is why this line looks unnecessary
#: and is not.
import app.db.base  # noqa: F401,E402  (must follow settings; registers all tables)

celery_app = Celery(
    "risk_platform",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.simulation"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    #: Hard ceiling so a pathological network cannot hold a worker forever. Well above
    #: any run the memory guard in the engine would allow through.
    task_time_limit=settings.simulation_time_limit_seconds,
    task_soft_time_limit=settings.simulation_time_limit_seconds - 60,
    result_expires=60 * 60 * 24 * 7,
)
