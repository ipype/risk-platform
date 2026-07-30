"""Regression coverage for the naive/aware datetime bug in ``hydrate()``.

Deliberately **not** part of ``test_schedule_api.py``. That module's own docstring says
why: it runs against in-memory SQLite so the suite needs no live Postgres. That is the
right tradeoff for almost everything here — but every schedule datetime column is
``DateTime(timezone=True)``, and SQLite does not have a real ``timestamptz`` type. It
stores and returns whatever tzinfo it was given, naive in, naive out, every time. asyncpg
against real Postgres does not: a ``timestamptz`` column always comes back tz-aware,
regardless of what was inserted. That distinction is the entire bug, so a SQLite-backed
test cannot exercise it, fail on it, or protect against it coming back — a green run of
``test_schedule_api.py`` says nothing about this class of defect either way.

The bug: on first ingest, ``run_gate`` hydrates a ``Schedule`` from a ``version`` object
that is still the exact Python instance ``create_version`` built moments earlier in the
same session — reading ``version.data_date`` off it is a plain attribute access that
never touches the database, so it stays naive. The activities two lines later come from
a fresh ``select()`` in the same call, which genuinely round-trips through asyncpg and
comes back aware. DCMA check 9 compares one against the other and raises
``TypeError: can't compare offset-naive and offset-aware datetimes`` on any schedule with
real progress in it — which is most of them. ``hydrate()`` now normalizes every datetime
to naive on the way out specifically so this can't recur; this test pins that.

Requires a live Postgres reachable via ``DATABASE_URL`` (or ``postgresql+asyncpg://
risk:risk@localhost:5432/riskdb`` by default, matching ``docker compose``). Skips itself
if that's not reachable rather than failing the wider suite in an environment without one.
Runs its own, throwaway database — dropped at teardown regardless of outcome — so it never
touches whatever real data lives in the configured one.
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.base_class import Base
from app.models import schedule as schedule_models
from app.schedule.dcma import CheckStatus, run_dcma
from app.schedule.parsers import parse_schedule
from app.services.schedule_ingest import create_version, run_gate, store_file
from tests.schedule_fixtures import simple_xer

pytestmark = pytest.mark.asyncio

_BASE_URL = os.environ.get("DATABASE_URL", settings.database_url)
_ADMIN_URL = _BASE_URL.rsplit("/", 1)[0] + "/postgres"
_TEST_DB = f"pg_naive_regression_{uuid.uuid4().hex[:12]}"
_TEST_URL = _BASE_URL.rsplit("/", 1)[0] + f"/{_TEST_DB}"


def _asyncpg_dsn(url: str) -> str:
    """asyncpg wants a plain ``postgresql://``, not SQLAlchemy's ``+asyncpg`` dialect tag."""
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest_asyncio.fixture
async def pg_db():
    """A session against a fresh, disposable Postgres database, migrated to just the
    schedule tables — mirroring ``docker compose``'s ``db`` service, never the SQLite
    fixture the rest of this package uses."""
    try:
        admin = await asyncpg.connect(_asyncpg_dsn(_ADMIN_URL), timeout=3)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"No live Postgres reachable at {_ADMIN_URL!r}: {exc}")
        return

    try:
        await admin.execute(f'CREATE DATABASE "{_TEST_DB}"')
    finally:
        await admin.close()

    engine = create_async_engine(_TEST_URL, echo=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[
                    schedule_models.ScheduleFile.__table__,
                    schedule_models.ScheduleVersion.__table__,
                    schedule_models.ScheduleCalendar.__table__,
                    schedule_models.ScheduleWbs.__table__,
                    schedule_models.ScheduleActivity.__table__,
                    schedule_models.ScheduleRelationship.__table__,
                    schedule_models.DcmaRun.__table__,
                ],
            )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
        admin = await asyncpg.connect(_asyncpg_dsn(_ADMIN_URL), timeout=3)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{_TEST_DB}"')
        finally:
            await admin.close()


class TestNaiveAwareRegression:
    async def test_first_ingest_gate_run_does_not_raise_on_real_postgres(self, pg_db):
        """The exact shape of the crash: same session, ``create_version`` then
        immediately ``run_gate`` — no commit or reload in between, matching
        ``_ingest()``'s real order. ``simple_xer()`` already carries a completed
        activity with an ``act_start_date`` before the data date, which is sufficient
        to reach the comparison that raised."""
        data = simple_xer()
        parsed = parse_schedule(data, "pipeline.xer")

        file_row, _ = await store_file(pg_db, filename="pipeline.xer", content=data)
        await pg_db.commit()
        await pg_db.refresh(file_row)

        version = await create_version(pg_db, file=file_row, schedule=parsed, created_by="test")

        # This is the line that raised TypeError before the fix. If it regresses, it
        # raises here again rather than silently passing.
        run = await run_gate(pg_db, version=version, run_by="test")

        assert isinstance(run.gate_passed, bool)
        assert run.report["checks"][8]["number"] == 9

    async def test_check_nine_agrees_between_direct_and_hydrated(self, pg_db):
        """Belt and braces: not just "did not raise" but "produced the same verdict"."""
        data = simple_xer()
        parsed = parse_schedule(data, "pipeline.xer")

        file_row, _ = await store_file(pg_db, filename="pipeline.xer", content=data)
        await pg_db.commit()
        await pg_db.refresh(file_row)

        version = await create_version(pg_db, file=file_row, schedule=parsed, created_by="test")
        run = await run_gate(pg_db, version=version, run_by="test")

        direct_report = run_dcma(parsed)
        direct_check9 = next(c for c in direct_report.checks if c.number == 9)
        hydrated_check9 = next(c for c in run.report["checks"] if c["number"] == 9)

        assert hydrated_check9["status"] == direct_check9.status.value
        assert hydrated_check9["metric"] == direct_check9.metric

    async def test_a_later_gate_rerun_in_its_own_session_still_agrees(self, pg_db):
        """The other half of the accident: a version loaded fresh in a brand-new
        session (as ``POST /schedules/{id}/dcma`` does) already came back aware on
        both sides even before the fix, so it never crashed there. Confirms the fix
        doesn't disturb that path either.

        Deliberately does NOT reuse ``pg_db`` for the reload: calling ``pg_db.get()``
        again on the *same* session hits its identity map and hands back the same
        still-in-memory (naive) Python object rather than issuing a fresh ``SELECT`` —
        which would silently test nothing. A genuinely separate ``AsyncSession``, with
        an empty identity map, is what actually matches a new request through
        ``get_db()``.
        """
        from app.models.schedule import ScheduleVersion

        data = simple_xer()
        parsed = parse_schedule(data, "pipeline.xer")
        file_row, _ = await store_file(pg_db, filename="pipeline.xer", content=data)
        await pg_db.commit()
        await pg_db.refresh(file_row)
        version = await create_version(pg_db, file=file_row, schedule=parsed, created_by="test")
        await pg_db.commit()
        version_id = version.id

        fresh_engine = create_async_engine(_TEST_URL, echo=False)
        try:
            fresh_session_factory = async_sessionmaker(fresh_engine, expire_on_commit=False)
            async with fresh_session_factory() as fresh_db:
                fresh = await fresh_db.get(ScheduleVersion, version_id)
                # genuinely round-tripped through asyncpg, unlike the bug path
                assert fresh.data_date.tzinfo is not None

                run = await run_gate(fresh_db, version=fresh, run_by="test-rerun")
                assert run.report["checks"][8]["status"] in (
                    CheckStatus.PASS.value,
                    CheckStatus.FAIL.value,
                    CheckStatus.NOT_ASSESSED.value,
                )
        finally:
            await fresh_engine.dispose()
