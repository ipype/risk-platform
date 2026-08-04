"""0016 executed against a pre-0016 database, and rendered for Postgres.

One `CREATE TABLE` is not much to get wrong, so the tests are aimed at the parts that are:
the constraints. Every guard 4.5 relies on lives in the schema rather than only in the API,
because a comparison is a record somebody will quote, and a route is not the only thing
that can write a row.

The four ``RESTRICT`` foreign keys are the interesting half. They are what stops a run or a
plan being deleted out from under a comparison that quotes it, and SQLite enforces them
only with ``PRAGMA foreign_keys`` on — which is exactly why they are asserted here with the
pragma explicitly enabled rather than assumed to be doing something.
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
    / "0016_mitigation_roi.py"
)

#: The tree as it stood at 0015, reduced to what 0016 points at.
PRE_0016_DDL = [
    """
    CREATE TABLE scope_node (
        id INTEGER NOT NULL PRIMARY KEY,
        kind VARCHAR(20) NOT NULL,
        name VARCHAR(200) NOT NULL
    )
    """,
    """
    CREATE TABLE mitigation_plan (
        id INTEGER NOT NULL PRIMARY KEY,
        scope_id INTEGER NOT NULL,
        name VARCHAR(200) NOT NULL,
        materialized_fingerprint VARCHAR(64),
        FOREIGN KEY(scope_id) REFERENCES scope_node (id)
    )
    """,
    """
    CREATE TABLE simulation_run (
        id INTEGER NOT NULL PRIMARY KEY,
        scope_id INTEGER NOT NULL,
        scenario VARCHAR(20) NOT NULL DEFAULT 'pre_mitigation',
        status VARCHAR(20) NOT NULL DEFAULT 'queued',
        seed INTEGER NOT NULL DEFAULT 12345,
        FOREIGN KEY(scope_id) REFERENCES scope_node (id)
    )
    """,
]


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0016", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed(connection: sa.Connection) -> None:
    for statement in PRE_0016_DDL:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql(
        "INSERT INTO scope_node (id, kind, name) VALUES (1, 'project', 'Project A')"
    )
    connection.exec_driver_sql(
        "INSERT INTO mitigation_plan (id, scope_id, name, materialized_fingerprint) "
        "VALUES (1, 1, 'Package 1', 'abc123')"
    )
    for run_id, scenario in ((1, "pre_mitigation"), (2, "post_mitigation")):
        connection.exec_driver_sql(
            "INSERT INTO simulation_run (id, scope_id, scenario, status) "
            f"VALUES ({run_id}, 1, '{scenario}', 'succeeded')"
        )


@pytest.fixture
def migrated():
    engine = sa.create_engine("sqlite://")
    module = _load_migration()
    with engine.begin() as connection:
        _seed(connection)
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return engine


def _pair(connection: sa.Connection, **overrides) -> None:
    row = {
        "id": 1,
        "plan_id": 1,
        "scope_id": 1,
        "before_run_id": 1,
        "after_run_id": 2,
        "percentile": 80,
        "plan_budget": 0,
    }
    row.update(overrides)
    columns = ", ".join(row)
    values = ", ".join(str(v) for v in row.values())
    connection.exec_driver_sql(
        f"INSERT INTO mitigation_roi ({columns}) VALUES ({values})"
    )


class TestUpgrade:
    def test_the_table_exists_with_its_snapshot_columns(self, migrated) -> None:
        columns = {c["name"] for c in sa.inspect(migrated).get_columns("mitigation_roi")}
        assert {
            "plan_id",
            "scope_id",
            "before_run_id",
            "after_run_id",
            "percentile",
            "seed_shared",
            "plan_fingerprint",
            "plan_budget",
            "plan_sched_days",
            "plan_unpriced_count",
        } <= columns

    def test_nothing_that_already_existed_was_touched(self, migrated) -> None:
        """No data migration: an unmeasured package has no row, which is the truth."""
        with migrated.connect() as c:
            assert c.exec_driver_sql("SELECT COUNT(*) FROM mitigation_plan").scalar_one() == 1
            assert c.exec_driver_sql("SELECT COUNT(*) FROM simulation_run").scalar_one() == 2
            assert c.exec_driver_sql("SELECT COUNT(*) FROM mitigation_roi").scalar_one() == 0

    def test_a_pair_inserts_and_defaults_sensibly(self, migrated) -> None:
        with migrated.begin() as c:
            _pair(c)
        with migrated.connect() as c:
            row = c.exec_driver_sql(
                "SELECT percentile, seed_shared, plan_budget, plan_sched_days, "
                "plan_unpriced_count, created_by FROM mitigation_roi"
            ).one()
        assert row[0] == 80.0
        assert bool(row[1]) is True
        assert (row[2], row[3], row[4]) == (0.0, 0.0, 0)
        assert row[5] == "Unknown"

    def test_the_same_two_runs_cannot_be_paired_twice_against_one_plan(self, migrated) -> None:
        with migrated.begin() as c:
            _pair(c)
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.begin() as c:
                _pair(c, id=2)

    def test_the_same_runs_may_be_paired_against_a_different_plan(self, migrated) -> None:
        """Two packages can be measured against one baseline; that is not a duplicate."""
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO mitigation_plan (id, scope_id, name) VALUES (2, 1, 'Package 2')"
            )
            _pair(c)
            _pair(c, id=2, plan_id=2)
        with migrated.connect() as c:
            assert c.exec_driver_sql("SELECT COUNT(*) FROM mitigation_roi").scalar_one() == 2

    def test_a_run_cannot_be_paired_with_itself_at_the_database_level(self, migrated) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.begin() as c:
                _pair(c, after_run_id=1)

    @pytest.mark.parametrize("value", [0, 100, 120, -5])
    def test_the_percentile_must_lie_strictly_inside_the_range(self, migrated, value) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.begin() as c:
                _pair(c, percentile=value)

    def test_a_negative_plan_budget_is_refused(self, migrated) -> None:
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.begin() as c:
                _pair(c, plan_budget=-1)

    def test_a_quoted_run_cannot_be_deleted(self, migrated) -> None:
        """``RESTRICT``, not ``CASCADE``: a comparison that cannot say what it compared is
        worse than a delete that fails."""
        with migrated.begin() as c:
            c.exec_driver_sql("PRAGMA foreign_keys=ON")
            _pair(c)
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.begin() as c:
                c.exec_driver_sql("PRAGMA foreign_keys=ON")
                c.exec_driver_sql("DELETE FROM simulation_run WHERE id = 1")

    def test_a_measured_plan_cannot_be_deleted(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql("PRAGMA foreign_keys=ON")
            _pair(c)
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.begin() as c:
                c.exec_driver_sql("PRAGMA foreign_keys=ON")
                c.exec_driver_sql("DELETE FROM mitigation_plan WHERE id = 1")


class TestDowngrade:
    def test_downgrade_leaves_the_previous_shape_untouched(self) -> None:
        engine = sa.create_engine("sqlite://")
        module = _load_migration()
        with engine.begin() as connection:
            _seed(connection)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                module.downgrade()
        names = set(sa.inspect(engine).get_table_names())
        assert "mitigation_roi" not in names
        assert {"mitigation_plan", "simulation_run"} <= names
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT COUNT(*) FROM simulation_run").scalar_one() == 2


class TestOfflineRender:
    def test_renders_offline_for_postgres(self) -> None:
        module = _load_migration()
        statements: list[str] = []
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": _Buffer(statements)},
        )
        with Operations.context(context):
            module.upgrade()
        rendered = "\n".join(statements).lower()
        assert "create table mitigation_roi" in rendered
        assert "uq_roi_plan_runs" in rendered
        assert "ck_roi_distinct_runs" in rendered
        assert "ck_roi_percentile" in rendered
        # Four restrict clauses: plan, scope, and both runs.
        assert rendered.count("on delete restrict") == 4
        # The convention 0014 set, and the reason this migration is executable under test.
        assert "now()" in rendered


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
