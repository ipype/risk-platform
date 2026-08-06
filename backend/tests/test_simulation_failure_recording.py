"""A run must never sit in ``queued`` with nothing written against it.

This is the regression suite for the failure that produced exactly that. ``execute``
loaded the run, checked its status and claimed it in three statements that sat outside its
own ``try``, so any exception in them — a column the model declares and the database has
not got, a connection dropped mid-``SELECT``, an asyncpg pool inherited across the
worker's fork — propagated out untouched. Celery's ``task_acks_late`` acknowledges a task
that raised just as it acknowledges one that returned, so the message went, the row never
moved, and the run became unreachable: not queued for anything, not running, not failed,
and not cancellable, because cancel only acts on ``queued`` and the status was a lie.

The failure is provoked here by making ``load_run`` raise rather than by breaking a real
schema. The schema is one cause among several and the point under test is the response to
any of them.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base_class import Base
from app.models.scope import ScopeNode
from app.models.simulation import SimulationRun
from app.services import sim_dispatch, sim_execute
from app.services.sim_execute import execute, record_failure
from app.tasks import simulation as simulation_task

RUN_TABLES = [ScopeNode.__table__, SimulationRun.__table__]


def _run_row(run_id: int, status: str) -> SimulationRun:
    return SimulationRun(
        id=run_id,
        scope_id=1,
        name=f"run {run_id}",
        status=status,
        scenario="pre_mitigation",
        iterations=100,
        seed=1,
        sampling="lhs",
        base_cost=0.0,
        burn_rate_per_day=0.0,
        risk_count=0,
        mapped_risk_count=0,
        activity_count=0,
        gate_override=False,
        created_by="test",
        request_json={},
    )


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=RUN_TABLES)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            ScopeNode(
                id=1, kind="project", name="Test project", is_default=True, created_by="test"
            )
        )
        await session.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


async def _status(factory, run_id: int) -> tuple[str, str | None]:
    async with factory() as session:
        row = (
            await session.execute(
                select(SimulationRun.status, SimulationRun.error).where(
                    SimulationRun.id == run_id
                )
            )
        ).one()
        return row[0], row[1]


@pytest.mark.asyncio
async def test_a_run_that_cannot_be_claimed_is_recorded_not_abandoned(
    session_factory, monkeypatch
):
    async with session_factory() as session:
        session.add(_run_row(1, "queued"))
        await session.commit()

    async def _boom(db, run_id):
        raise OperationalError("SELECT ...", {}, Exception("no such column"))

    monkeypatch.setattr(sim_execute, "load_run", _boom)

    async with session_factory() as session:
        with pytest.raises(OperationalError):
            await execute(session, 1)

    status, error = await _status(session_factory, 1)
    assert status == "failed"
    # The message has to name the run's own problem, not just the driver's, because the
    # analyst reading it is looking at a run that will not start, not at a stack trace.
    assert "could not be started" in error
    assert "OperationalError" in error


@pytest.mark.asyncio
async def test_the_failure_is_re_raised_so_the_worker_log_keeps_the_traceback(
    session_factory, monkeypatch
):
    """Recorded *and* re-raised. Swallowing it would leave a broken deployment quiet."""
    async with session_factory() as session:
        session.add(_run_row(1, "queued"))
        await session.commit()

    async def _boom(db, run_id):
        raise RuntimeError("pool is not usable after fork")

    monkeypatch.setattr(sim_execute, "load_run", _boom)

    async with session_factory() as session:
        with pytest.raises(RuntimeError, match="after fork"):
            await execute(session, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
async def test_a_terminal_run_is_never_overwritten_by_a_failure_write(
    session_factory, terminal
):
    """Invariant 5. A run with an answer on it is not this path's to rewrite."""
    async with session_factory() as session:
        run = _run_row(1, terminal)
        run.error = "original" if terminal == "failed" else None
        session.add(run)
        await session.commit()

    async with session_factory() as session:
        moved = await record_failure(session, 1, "a later worker tried to fail this")

    assert moved is False
    status, error = await _status(session_factory, 1)
    assert status == terminal
    assert error == ("original" if terminal == "failed" else None)


@pytest.mark.asyncio
async def test_a_running_run_can_still_be_failed(session_factory):
    """A worker that died mid-run leaves ``running`` behind; that is claimable."""
    async with session_factory() as session:
        session.add(_run_row(1, "running"))
        await session.commit()

    async with session_factory() as session:
        assert await record_failure(session, 1, "worker died") is True

    status, error = await _status(session_factory, 1)
    assert status == "failed"
    assert "worker died" in error


@pytest.mark.asyncio
async def test_a_missing_run_moves_nothing(session_factory):
    async with session_factory() as session:
        assert await record_failure(session, 999, "nothing here") is False


@pytest.mark.asyncio
async def test_the_recorded_message_is_bounded(session_factory):
    """A driver traceback repeated across a thousand failed runs is not worth the table."""
    async with session_factory() as session:
        session.add(_run_row(1, "queued"))
        await session.commit()

    async with session_factory() as session:
        await record_failure(session, 1, "x" * 10_000)

    _, error = await _status(session_factory, 1)
    assert len(error) == sim_execute._ERROR_MAX


@pytest.mark.asyncio
async def test_a_missing_run_is_still_none_not_a_failure(session_factory):
    """``execute`` on a run id that does not exist is not an error worth recording."""
    async with session_factory() as session:
        assert await execute(session, 999) is None


@pytest.mark.asyncio
async def test_a_terminal_run_is_returned_untouched(session_factory):
    """Redelivery under ``acks_late`` must not recompute a published answer."""
    async with session_factory() as session:
        session.add(_run_row(1, "succeeded"))
        await session.commit()

    async with session_factory() as session:
        run = await execute(session, 1)

    assert run is not None
    assert run.status == "succeeded"
    assert run.started_at is None


# --------------------------------------------------------------------------------------
# refusing to queue into an empty cluster
# --------------------------------------------------------------------------------------
#
# The other half of the same symptom. Above, a worker took the run and died before it
# could claim the row. Here there is no worker at all: `delay` succeeds, the task id is
# real, and the run waits on nothing. Both used to end with a row stuck in `queued`.


@pytest.mark.asyncio
async def test_a_run_is_not_queued_when_no_worker_answers(session_factory, monkeypatch):
    monkeypatch.setattr(sim_dispatch.settings, "simulation_eager", False)
    monkeypatch.setattr(sim_dispatch, "live_workers", lambda timeout: [])

    async with session_factory() as session:
        run = _run_row(1, "queued")
        session.add(run)
        await session.commit()
        await sim_dispatch.dispatch(session, run)

    status, error = await _status(session_factory, 1)
    assert status == "failed"
    assert "No simulation worker answered" in error
    # Named so the message is actionable rather than merely accurate.
    assert "docker compose up -d worker" in error


@pytest.mark.asyncio
async def test_an_unreachable_broker_is_left_to_delay_to_report(
    session_factory, monkeypatch
):
    """``None`` is not ``[]``: an unanswerable question must not pre-empt the real error.

    ``delay`` raises a message naming the connection that failed, which is more use than
    a guess made before it was tried.
    """
    monkeypatch.setattr(sim_dispatch.settings, "simulation_eager", False)
    monkeypatch.setattr(sim_dispatch, "live_workers", lambda timeout: None)

    def _unreachable(run_id):
        raise ConnectionError("Error 111 connecting to redis:6379. Connection refused.")

    monkeypatch.setattr(simulation_task.run_simulation, "delay", _unreachable)

    async with session_factory() as session:
        run = _run_row(1, "queued")
        session.add(run)
        await session.commit()
        await sim_dispatch.dispatch(session, run)

    status, error = await _status(session_factory, 1)
    assert status == "failed"
    assert "No simulation worker answered" not in error
    assert "could not be queued" in error
    # The connection failure itself survives into the message, which is the whole reason
    # this case is left to ``delay`` rather than pre-empted by the preflight.
    assert "Connection refused" in error


@pytest.mark.asyncio
async def test_a_live_worker_lets_the_run_through(session_factory, monkeypatch):
    monkeypatch.setattr(sim_dispatch.settings, "simulation_eager", False)
    monkeypatch.setattr(sim_dispatch, "live_workers", lambda timeout: ["celery@host"])

    published: list[int] = []

    class _Result:
        id = "task-1"

    def _fake_delay(run_id):
        published.append(run_id)
        return _Result()

    monkeypatch.setattr(simulation_task.run_simulation, "delay", _fake_delay)

    async with session_factory() as session:
        run = _run_row(1, "queued")
        session.add(run)
        await session.commit()
        await sim_dispatch.dispatch(session, run)

    assert published == [1]
    status, _ = await _status(session_factory, 1)
    assert status == "queued"


@pytest.mark.asyncio
async def test_the_preflight_can_be_turned_off(session_factory, monkeypatch):
    monkeypatch.setattr(sim_dispatch.settings, "simulation_eager", False)
    monkeypatch.setattr(sim_dispatch.settings, "simulation_require_worker", False)

    def _explode(timeout):  # pragma: no cover - asserted by not being called
        raise AssertionError("the preflight should not have run")

    monkeypatch.setattr(sim_dispatch, "live_workers", _explode)
    monkeypatch.setattr(
        simulation_task.run_simulation, "delay", lambda run_id: type("R", (), {"id": "t"})()
    )

    async with session_factory() as session:
        run = _run_row(1, "queued")
        session.add(run)
        await session.commit()
        await sim_dispatch.dispatch(session, run)

    status, _ = await _status(session_factory, 1)
    assert status == "queued"


# --------------------------------------------------------------------------------------
# the worker must see every table
# --------------------------------------------------------------------------------------
#
# Run in a subprocess on purpose. By the time this suite is collected, some other test
# module has already imported `app.db.base` and every table is on the metadata, so an
# in-process assertion here would pass no matter what the worker does. The only honest
# way to ask what a worker sees is to start a process that imports what a worker imports
# and nothing else.


def _tables_after(statements: str) -> set[str]:
    source = (
        statements
        + "\nfrom app.db.base_class import Base"
        + "\nimport json, sys"
        + "\nsys.stdout.write(json.dumps(sorted(Base.metadata.tables)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        cwd=pathlib.Path(__file__).resolve().parent.parent,
        check=True,
    )
    return set(json.loads(out.stdout))


def test_the_worker_entry_point_registers_every_table():
    """``NoReferencedTableError``, not an ImportError, is what a missing table costs.

    SQLAlchemy resolves a ``ForeignKey`` against the metadata at the moment something
    first needs it. A table object the worker never imported therefore fails no import
    and no simple query — it fails whichever statement first forces resolution, in the
    worker only, and looks like a database fault when it is a Python one.
    """
    assert _tables_after("import app.worker") == _tables_after("import app.db.base")


def test_the_simulation_task_chain_can_resolve_its_own_foreign_keys():
    """The specific failure: ``simulation_run.scope_id`` had no ``scope_node`` to point at."""
    source = (
        "from app.tasks.simulation import run_simulation\n"
        "from app.models.simulation import SimulationRun\n"
        "for fk in SimulationRun.__table__.foreign_keys:\n"
        "    fk.column\n"
        "print('resolved')"
    )
    out = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        cwd=pathlib.Path(__file__).resolve().parent.parent,
    )
    assert out.returncode == 0, out.stderr[-1500:]
    assert "resolved" in out.stdout
