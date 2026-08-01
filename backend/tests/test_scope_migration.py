"""The 0014 backfill, executed rather than inspected.

0014 is the first migration that changes tables which already hold data: it adds a
non-nullable foreign key to `risk`, `schedule_file` and `simulation_run`, moves every
existing row into a project created by the migration itself, and swaps the register's two
uniqueness rules from global to per-scope. A recording stub of the kind
``test_schedule_migration.py`` uses would confirm the DDL and say nothing about any of
that, and the backfill is the part that can silently lose rows.

So this builds a database in the shape the tree had at 0013 — hand-written DDL, because
the models no longer describe that shape — fills it, and runs the real ``upgrade()``
against it. SQLite, because that is where batch mode does the most work: every
``alter_column`` to non-nullable and every constraint swap here is a table rebuild, and a
rebuild that drops rows or loses a column does it quietly.

Postgres executes these operations directly and is exercised by the offline SQL render in
``test_renders_offline``: the same migration, compiled for the production dialect without
a database. Between the two, no statement in 0014 is unexecuted and unrendered.
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
    / "0014_scope_hierarchy.py"
)

# The three tables as they stood at 0013, reduced to what 0014 touches or needs to carry
# through the rebuild. Enough columns to prove the rebuild preserved data, not a copy of
# the whole schema.
PRE_0014_DDL = [
    """
    CREATE TABLE rbs_subcategory (
        id INTEGER NOT NULL PRIMARY KEY,
        code VARCHAR(20) NOT NULL
    )
    """,
    """
    CREATE TABLE risk (
        id INTEGER NOT NULL PRIMARY KEY,
        subcategory_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        risk_code VARCHAR(20) NOT NULL,
        title VARCHAR(300) NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'Open',
        CONSTRAINT uq_risk_subcategory_seq UNIQUE (subcategory_id, seq)
    )
    """,
    "CREATE UNIQUE INDEX ix_risk_risk_code ON risk (risk_code)",
    "CREATE INDEX ix_risk_subcategory_id ON risk (subcategory_id)",
    """
    CREATE TABLE schedule_file (
        id INTEGER NOT NULL PRIMARY KEY,
        filename VARCHAR(500) NOT NULL,
        sha256 VARCHAR(64) NOT NULL
    )
    """,
    """
    CREATE TABLE schedule_version (
        id INTEGER NOT NULL PRIMARY KEY,
        file_id INTEGER NOT NULL,
        project_name VARCHAR(500) NOT NULL
    )
    """,
    """
    CREATE TABLE simulation_run (
        id INTEGER NOT NULL PRIMARY KEY,
        name VARCHAR(200) NOT NULL DEFAULT '',
        status VARCHAR(20) NOT NULL DEFAULT 'queued'
    )
    """,
]


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0014", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed(connection: sa.Connection, *, schedules: list[str]) -> None:
    for statement in PRE_0014_DDL:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql("INSERT INTO rbs_subcategory (id, code) VALUES (1, '030')")
    for i in (1, 2, 3):
        connection.exec_driver_sql(
            "INSERT INTO risk (id, subcategory_id, seq, risk_code, title) "
            f"VALUES ({i}, 1, {i}, 'ENV-030-000{i}', 'Risk {i}')"
        )
    connection.exec_driver_sql(
        "INSERT INTO schedule_file (id, filename, sha256) VALUES (1, 'a.xer', 'abc')"
    )
    for i, name in enumerate(schedules, start=1):
        connection.exec_driver_sql(
            "INSERT INTO schedule_version (id, file_id, project_name) "
            f"VALUES ({i}, 1, '{name}')"
        )
    connection.exec_driver_sql(
        "INSERT INTO simulation_run (id, name) VALUES (1, 'Sanction estimate')"
    )


@pytest.fixture
def migrated():
    """A pre-0014 database with three risks and one of everything else, upgraded."""
    engine = sa.create_engine("sqlite://")
    module = _load_migration()
    with engine.begin() as connection:
        _seed(connection, schedules=["Kitimat LNG Phase 2"])
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return engine


class TestBackfill:
    def test_every_existing_row_lands_in_the_default_project(self, migrated) -> None:
        with migrated.connect() as c:
            default_id = c.exec_driver_sql(
                "SELECT id FROM scope_node WHERE is_default = 1"
            ).scalar_one()
            for table in ("risk", "schedule_file", "simulation_run"):
                orphans = c.exec_driver_sql(
                    f"SELECT COUNT(*) FROM {table} WHERE scope_id IS NULL "  # noqa: S608
                    f"OR scope_id <> {default_id}"
                ).scalar_one()
                assert orphans == 0, f"{table} has rows outside the default project"

    def test_no_row_is_lost_to_the_table_rebuild(self, migrated) -> None:
        """Batch mode recreates the table. A rebuild that drops rows does it quietly."""
        with migrated.connect() as c:
            assert c.exec_driver_sql("SELECT COUNT(*) FROM risk").scalar_one() == 3
            codes = [
                r[0]
                for r in c.exec_driver_sql(
                    "SELECT risk_code FROM risk ORDER BY id"
                ).fetchall()
            ]
            assert codes == ["ENV-030-0001", "ENV-030-0002", "ENV-030-0003"]
            assert (
                c.exec_driver_sql("SELECT title FROM risk WHERE id = 2").scalar_one()
                == "Risk 2"
            )

    def test_the_default_project_is_named_after_the_loaded_schedule(self, migrated) -> None:
        with migrated.connect() as c:
            row = c.exec_driver_sql(
                "SELECT kind, name, parent_id FROM scope_node WHERE is_default = 1"
            ).one()
        assert row[0] == "project"
        assert row[1] == "Kitimat LNG Phase 2"
        # A single project with nothing above it is the shape of a fresh install. Wrapping
        # it in an invented portfolio would be ceremony nobody asked for.
        assert row[2] is None

    def test_a_generic_name_when_two_schedules_disagree(self) -> None:
        engine = sa.create_engine("sqlite://")
        module = _load_migration()
        with engine.begin() as connection:
            _seed(connection, schedules=["Alpha", "Bravo"])
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
        with engine.connect() as c:
            assert (
                c.exec_driver_sql(
                    "SELECT name FROM scope_node WHERE is_default = 1"
                ).scalar_one()
                == "Project"
            )

    def test_an_empty_database_still_gets_its_project(self) -> None:
        engine = sa.create_engine("sqlite://")
        module = _load_migration()
        with engine.begin() as connection:
            for statement in PRE_0014_DDL:
                connection.exec_driver_sql(statement)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT COUNT(*) FROM scope_node").scalar_one() == 1


class TestConstraints:
    def test_scope_id_is_not_nullable_afterwards(self, migrated) -> None:
        with migrated.connect() as c:
            for table in ("risk", "schedule_file", "simulation_run"):
                nullable = {
                    r[1]: r[3]
                    for r in c.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
                }
                assert nullable["scope_id"] == 1, f"{table}.scope_id is still nullable"

    def test_the_register_sequence_is_now_per_scope(self, migrated) -> None:
        """Two projects may both hold ENV-030-0001. That is the point of the change."""
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO scope_node (id, kind, name, sort_order, created_by) "
                "VALUES (99, 'project', 'Second project', 0, 'test')"
            )
            c.exec_driver_sql(
                "INSERT INTO risk (id, scope_id, subcategory_id, seq, risk_code, title) "
                "VALUES (10, 99, 1, 1, 'ENV-030-0001', 'Same code, other project')"
            )
            assert (
                c.exec_driver_sql(
                    "SELECT COUNT(*) FROM risk WHERE risk_code = 'ENV-030-0001'"
                ).scalar_one()
                == 2
            )

    def test_the_sequence_is_still_unique_inside_one_scope(self, migrated) -> None:
        with migrated.begin() as c:
            default_id = c.exec_driver_sql(
                "SELECT id FROM scope_node WHERE is_default = 1"
            ).scalar_one()
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(
                    "INSERT INTO risk "
                    "(id, scope_id, subcategory_id, seq, risk_code, title) VALUES "
                    f"(11, {default_id}, 1, 1, 'ENV-030-9999', 'Duplicate seq')"
                )

    def test_at_most_one_default_scope(self, migrated) -> None:
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(
                    "INSERT INTO scope_node (kind, name, is_default, created_by) "
                    "VALUES ('project', 'Rival default', 1, 'test')"
                )

    def test_many_non_default_scopes(self, migrated) -> None:
        """The nullable-unique trick has to permit unlimited nulls, or it is useless."""
        with migrated.begin() as c:
            for i in (101, 102, 103):
                c.exec_driver_sql(
                    "INSERT INTO scope_node (id, kind, name, created_by) "
                    f"VALUES ({i}, 'project', 'P{i}', 'test')"
                )
            assert c.exec_driver_sql("SELECT COUNT(*) FROM scope_node").scalar_one() == 4


class TestOffline:
    def test_renders_offline_for_postgres(self) -> None:
        """`alembic upgrade --sql` must produce reviewable DDL for the real dialect.

        A data migration that can only be watched at runtime is one nobody can review
        before it touches a production register, which is why the backfill is written as
        SQL over subqueries rather than as a Python read-then-write.
        """
        module = _load_migration()
        statements: list[str] = []
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": _Buffer(statements)},
        )
        with Operations.context(context):
            module.upgrade()
        rendered = "\n".join(statements).lower()
        assert "create table scope_node" in rendered
        assert "alter table risk add column scope_id" in rendered
        assert "update risk set scope_id" in rendered
        assert "uq_risk_scope_subcategory_seq" in rendered


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
