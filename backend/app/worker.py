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
