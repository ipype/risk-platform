"""The 0009 migration must match the ORM models exactly.

Migrations here are hand-written, so nothing enforces that the DDL and the models agree.
They diverge silently: everything passes against a database built by
``Base.metadata.create_all`` in tests, then breaks against one built by Alembic in
production. This test executes the migration module with a recording stub in place of
``op`` and diffs the result against the models.

The diff is against 0009's shape, not today's. A column a later migration adds — 0014's
``scope_id``, most recently — is real on the model and absent from 0009 on purpose, so it
is excluded here rather than failing a test that is checking the wrong migration for it.
Each entry names the migration responsible, so removing a table from this file means
finding and updating this list too.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models import schedule as schedule_models

MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0009_schedule.py"
)

#: column -> migration that introduced it, for every model column 0009 does not know about.
ADDED_AFTER_0009: dict[str, str] = {
    "scope_id": "0014",
}

MODEL_TABLES = {
    model.__tablename__: model.__table__
    for model in (
        schedule_models.ScheduleFile,
        schedule_models.ScheduleVersion,
        schedule_models.ScheduleCalendar,
        schedule_models.ScheduleWbs,
        schedule_models.ScheduleActivity,
        schedule_models.ScheduleRelationship,
        schedule_models.DcmaRun,
    )
}


class _RecordingOp:
    """Stands in for ``alembic.op``, capturing DDL instead of emitting it."""

    def __init__(self) -> None:
        self.tables: dict[str, list[sa.Column]] = {}
        self.indexes: list[tuple[str, str, list[str]]] = []
        self.dropped: list[str] = []

    def create_table(self, name, *args, **kwargs):
        self.tables[name] = [c for c in args if isinstance(c, sa.Column)]

    def create_index(self, index_name, table_name, columns, **kwargs):
        self.indexes.append((index_name, table_name, list(columns)))

    def drop_table(self, name, **kwargs):
        self.dropped.append(name)

    def drop_index(self, *args, **kwargs):
        pass

    def add_column(self, *args, **kwargs):
        pass

    def drop_column(self, *args, **kwargs):
        pass


@pytest.fixture(scope="module")
def migration():
    spec = importlib.util.spec_from_file_location("migration_0009", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    recorder = _RecordingOp()
    spec.loader.exec_module(module)
    module.op = recorder
    module.upgrade()
    return recorder


def test_revision_chain_is_correct():
    spec = importlib.util.spec_from_file_location("migration_0009_meta", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0009"
    assert module.down_revision == "0008"


def test_migration_creates_exactly_the_model_tables(migration):
    assert set(migration.tables) == set(MODEL_TABLES)


def test_downgrade_removes_everything_upgrade_added(migration):
    spec = importlib.util.spec_from_file_location("migration_0009_down", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    recorder = _RecordingOp()
    spec.loader.exec_module(module)
    module.op = recorder
    module.downgrade()
    assert set(recorder.dropped) == set(MODEL_TABLES)


@pytest.mark.parametrize("table_name", sorted(MODEL_TABLES))
def test_columns_match_the_model(migration, table_name):
    migration_columns = {c.name: c for c in migration.tables[table_name]}
    model_columns = {
        c.name: c
        for c in MODEL_TABLES[table_name].columns
        if c.name not in ADDED_AFTER_0009
    }
    assert set(migration_columns) == set(model_columns), (
        f"{table_name}: migration and model disagree on which columns exist"
    )


@pytest.mark.parametrize("table_name", sorted(MODEL_TABLES))
def test_column_types_and_nullability_match_the_model(migration, table_name):
    migration_columns = {c.name: c for c in migration.tables[table_name]}
    model_columns = {
        c.name: c
        for c in MODEL_TABLES[table_name].columns
        if c.name not in ADDED_AFTER_0009
    }

    mismatches = []
    for name, model_column in model_columns.items():
        migration_column = migration_columns.get(name)
        if migration_column is None:
            continue
        model_type = type(model_column.type).__name__
        migration_type = type(migration_column.type).__name__
        if model_type != migration_type:
            mismatches.append(f"{name}: type {migration_type} vs model {model_type}")
        if bool(model_column.nullable) != bool(migration_column.nullable):
            mismatches.append(
                f"{name}: nullable {migration_column.nullable} vs model "
                f"{model_column.nullable}"
            )
        model_length = getattr(model_column.type, "length", None)
        migration_length = getattr(migration_column.type, "length", None)
        if model_length != migration_length:
            mismatches.append(
                f"{name}: length {migration_length} vs model {model_length}"
            )

    assert not mismatches, f"{table_name}:\n  " + "\n  ".join(mismatches)


def test_hot_path_columns_are_indexed(migration):
    """Every version-scoped child table gets filtered by version_id on every read."""
    indexed = {(table, tuple(cols)) for _, table, cols in migration.indexes}
    for table_name in (
        "schedule_calendar",
        "schedule_wbs",
        "schedule_activity",
        "schedule_relationship",
        "dcma_run",
    ):
        assert (table_name, ("version_id",)) in indexed, (
            f"{table_name}.version_id is not indexed"
        )
