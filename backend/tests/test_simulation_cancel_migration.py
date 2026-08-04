"""The 0018 constraint swap, executed rather than inspected.

0018 adds two nullable columns and widens ``ck_simrun_status`` to admit ``cancelled``. In
SQLite that widening is a table rebuild — the same operation 0017 exercises for
``schedule_file`` — and a rebuild that drops a row or silently loosens the constraint does
it quietly. This builds ``simulation_run`` as it stood at 0017, fills it with a run in
each pre-existing status, and runs the real ``upgrade()`` against it.

``test_simulations_api.py`` proves the API refuses to cancel anything but a queued run.
This file proves the schema underneath will actually hold a cancelled one, and will still
refuse a status the constraint was never widened for.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0018_simulation_run_cancel.py"
)

# simulation_run as it stood at 0017, reduced to what 0018 touches or needs to carry
# through the rebuild.
PRE_0018_DDL = """
CREATE TABLE simulation_run (
    id INTEGER NOT NULL PRIMARY KEY,
    scope_id INTEGER NOT NULL,
    name VARCHAR(200) NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    created_by VARCHAR(120) NOT NULL DEFAULT 'Unknown',
    task_id VARCHAR(64),
    error TEXT,
    finished_at DATETIME,
    CONSTRAINT ck_simrun_status CHECK (status IN ('queued', 'running', 'succeeded', 'failed'))
)
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0018", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed(connection: sa.Connection) -> None:
    connection.exec_driver_sql(PRE_0018_DDL)
    for i, status in enumerate(("queued", "running", "succeeded", "failed"), start=1):
        connection.exec_driver_sql(
            "INSERT INTO simulation_run (id, scope_id, name, status, created_by) "
            f"VALUES ({i}, 1, 'Run {i}', '{status}', 'Sam')"
        )


@pytest.fixture
def migrated():
    """A pre-0018 database with one run in each pre-existing status, upgraded."""
    engine = sa.create_engine("sqlite://")
    module = _load_migration()
    with engine.begin() as connection:
        _seed(connection)
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return engine


class TestUpgrade:
    def test_no_row_is_lost_to_the_table_rebuild(self, migrated) -> None:
        with migrated.connect() as c:
            assert (
                c.exec_driver_sql("SELECT COUNT(*) FROM simulation_run").scalar_one()
                == 4
            )
            names = [
                r[0]
                for r in c.exec_driver_sql(
                    "SELECT name FROM simulation_run ORDER BY id"
                ).fetchall()
            ]
            assert names == ["Run 1", "Run 2", "Run 3", "Run 4"]

    def test_the_new_columns_exist_and_default_to_null(self, migrated) -> None:
        with migrated.connect() as c:
            rows = c.exec_driver_sql(
                "SELECT cancelled_by, cancelled_at FROM simulation_run"
            ).fetchall()
            assert all(by is None and at is None for by, at in rows)

    def test_a_queued_run_can_now_be_cancelled(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(
                "UPDATE simulation_run SET status='cancelled', cancelled_by='Sam', "
                "cancelled_at=CURRENT_TIMESTAMP WHERE id=1"
            )
        with migrated.connect() as c:
            row = c.exec_driver_sql(
                "SELECT status, cancelled_by FROM simulation_run WHERE id=1"
            ).one()
            assert row == ("cancelled", "Sam")

    def test_the_four_pre_existing_statuses_still_hold(self, migrated) -> None:
        with migrated.connect() as c:
            statuses = [
                r[0]
                for r in c.exec_driver_sql(
                    "SELECT status FROM simulation_run ORDER BY id"
                ).fetchall()
            ]
            assert statuses == ["queued", "running", "succeeded", "failed"]

    def test_an_unrecognised_status_is_still_rejected(self, migrated) -> None:
        """The constraint was widened by one value, not removed."""
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(
                    "INSERT INTO simulation_run (id, scope_id, name, status, created_by) "
                    "VALUES (99, 1, 'bogus', 'archived', 'Sam')"
                )


class TestDowngrade:
    def test_downgrade_drops_the_columns_and_narrows_the_constraint(self) -> None:
        engine = sa.create_engine("sqlite://")
        module = _load_migration()
        with engine.begin() as connection:
            _seed(connection)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                module.downgrade()
        with engine.connect() as c:
            columns = {
                r[1]
                for r in c.exec_driver_sql(
                    "PRAGMA table_info(simulation_run)"
                ).fetchall()
            }
            assert "cancelled_by" not in columns
            assert "cancelled_at" not in columns
            assert (
                c.exec_driver_sql("SELECT COUNT(*) FROM simulation_run").scalar_one()
                == 4
            )


class TestOffline:
    def test_renders_offline_for_postgres(self) -> None:
        """`alembic upgrade --sql` must produce reviewable DDL for the real dialect."""
        module = _load_migration()
        statements: list[str] = []
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": _Buffer(statements)},
        )
        with Operations.context(context):
            module.upgrade()
        rendered = "\n".join(statements).lower()
        assert "add column cancelled_by" in rendered
        assert "add column cancelled_at" in rendered
        assert "ck_simrun_status" in rendered
        assert "cancelled" in rendered


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
