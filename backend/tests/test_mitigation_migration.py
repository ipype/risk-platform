"""0015 executed against a pre-0015 database, and rendered for Postgres.

The interesting part is not the two new tables — a `CREATE TABLE` that fails, fails
loudly. It is the batch rebuild of `mitigation_action`, which already holds rows: SQLite
cannot add a foreign key in place, so the table is dropped and recreated, and a rebuild
that loses a column's data or an existing index does it quietly.

The offline Postgres render covers the other half. Production runs these statements
directly rather than through a rebuild, so the two dialects execute genuinely different
SQL and neither can stand in for the other.
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
    / "0015_mitigation_plans.py"
)

#: The tree as it stood at 0014, reduced to what 0015 touches or points at.
PRE_0015_DDL = [
    """
    CREATE TABLE scope_node (
        id INTEGER NOT NULL PRIMARY KEY,
        kind VARCHAR(20) NOT NULL,
        name VARCHAR(200) NOT NULL
    )
    """,
    """
    CREATE TABLE risk (
        id INTEGER NOT NULL PRIMARY KEY,
        scope_id INTEGER NOT NULL,
        risk_code VARCHAR(20) NOT NULL,
        title VARCHAR(300) NOT NULL
    )
    """,
    """
    CREATE TABLE mitigation_action (
        id INTEGER NOT NULL PRIMARY KEY,
        risk_id INTEGER NOT NULL,
        action TEXT NOT NULL DEFAULT '',
        owner VARCHAR(200),
        due_date DATE,
        budget FLOAT,
        completion_pct INTEGER,
        effectiveness VARCHAR(20),
        status VARCHAR(30) NOT NULL DEFAULT 'Proposed',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(risk_id) REFERENCES risk (id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ix_mitigation_action_risk_id ON mitigation_action (risk_id)",
]


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0015", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed(connection: sa.Connection) -> None:
    for statement in PRE_0015_DDL:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql(
        "INSERT INTO scope_node (id, kind, name) VALUES (1, 'project', 'Test project')"
    )
    connection.exec_driver_sql(
        "INSERT INTO risk (id, scope_id, risk_code, title) "
        "VALUES (1, 1, 'ENV-030-0001', 'Permit delay')"
    )
    for i, (owner, budget) in enumerate(
        [("Alice", 120000.0), ("Bob", None), ("Cara", 40000.0)], start=1
    ):
        budget_sql = "NULL" if budget is None else str(budget)
        connection.exec_driver_sql(
            "INSERT INTO mitigation_action "
            "(id, risk_id, action, owner, budget, status, sort_order) "
            f"VALUES ({i}, 1, 'Action {i}', '{owner}', {budget_sql}, 'Proposed', {i - 1})"
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


class TestUpgrade:
    def test_new_tables_exist(self, migrated) -> None:
        names = set(sa.inspect(migrated).get_table_names())
        assert {"mitigation_plan", "mitigation_plan_risk"} <= names

    def test_existing_actions_survive_the_rebuild(self, migrated) -> None:
        """Batch mode drops and recreates the table. Losing a row here is silent."""
        with migrated.connect() as c:
            rows = c.exec_driver_sql(
                "SELECT id, owner, budget, plan_id, sched_days "
                "FROM mitigation_action ORDER BY id"
            ).fetchall()
        assert [r[0] for r in rows] == [1, 2, 3]
        assert [r[1] for r in rows] == ["Alice", "Bob", "Cara"]
        assert [r[2] for r in rows] == [120000.0, None, 40000.0]
        # Every existing action starts outside any plan, and unpriced in days.
        assert [r[3] for r in rows] == [None, None, None]
        assert [r[4] for r in rows] == [None, None, None]

    def test_existing_index_survives_the_rebuild(self, migrated) -> None:
        indexes = {
            ix["name"] for ix in sa.inspect(migrated).get_indexes("mitigation_action")
        }
        assert "ix_mitigation_action_risk_id" in indexes
        assert "ix_mitigation_action_plan_id" in indexes

    def test_plan_name_is_unique_within_a_scope_only(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO scope_node (id, kind, name) VALUES (2, 'project', 'Other')"
            )
            c.exec_driver_sql(
                "INSERT INTO mitigation_plan (id, scope_id, name) VALUES (1, 1, 'Plan A')"
            )
            # Same name, different project: two projects may both have a "Plan A".
            c.exec_driver_sql(
                "INSERT INTO mitigation_plan (id, scope_id, name) VALUES (2, 2, 'Plan A')"
            )
        with pytest.raises(sa.exc.IntegrityError):
            with migrated.begin() as c:
                c.exec_driver_sql(
                    "INSERT INTO mitigation_plan (id, scope_id, name) "
                    "VALUES (3, 1, 'Plan A')"
                )

    def test_factor_bounds_are_enforced_by_the_database(self, migrated) -> None:
        """A factor above one is a secondary risk pretending to be a mitigation."""
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO mitigation_plan (id, scope_id, name) VALUES (9, 1, 'P')"
            )
        for column, value in (("cost_factor", 1.4), ("p_factor", 0.0)):
            with pytest.raises(sa.exc.IntegrityError):
                with migrated.begin() as c:
                    c.exec_driver_sql(
                        "INSERT INTO mitigation_plan_risk "
                        f"(plan_id, risk_id, treatment, mode, {column}) "
                        f"VALUES (9, 1, 'reduce', 'factor', {value})"
                    )


class TestDowngrade:
    def test_downgrade_restores_the_previous_shape(self) -> None:
        engine = sa.create_engine("sqlite://")
        module = _load_migration()
        with engine.begin() as connection:
            _seed(connection)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                module.downgrade()
        names = set(sa.inspect(engine).get_table_names())
        assert "mitigation_plan" not in names
        assert "mitigation_plan_risk" not in names
        columns = {c["name"] for c in sa.inspect(engine).get_columns("mitigation_action")}
        assert "plan_id" not in columns
        assert "sched_days" not in columns
        with engine.connect() as c:
            assert (
                c.exec_driver_sql("SELECT COUNT(*) FROM mitigation_action").scalar_one() == 3
            )


class TestOfflineRender:
    def test_renders_offline_for_postgres(self) -> None:
        """Production runs these statements directly, not through a table rebuild."""
        module = _load_migration()
        statements: list[str] = []
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": _Buffer(statements)},
        )
        with Operations.context(context):
            module.upgrade()
        rendered = "\n".join(statements).lower()
        assert "create table mitigation_plan" in rendered
        assert "create table mitigation_plan_risk" in rendered
        assert "alter table mitigation_action add column plan_id" in rendered
        assert "alter table mitigation_action add column sched_days" in rendered
        assert "fk_mitigation_action_plan_id" in rendered
        assert "on delete set null" in rendered


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
