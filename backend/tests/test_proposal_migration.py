"""0021 executed, not inspected.

The ledger's two load-bearing constraints are database constraints — one CHECK for
"evidence is mandatory", one partial unique index for "at most one pending proposal per
target field" — and a recording stub would confirm the DDL was emitted while saying
nothing about whether either actually holds. So this builds the schema at 0020, runs the
real ``upgrade()`` against it, and then tries to break both.

``alembic upgrade head`` is not usable here and never has been in this repo: 0001 issues an
unconditional ``CREATE EXTENSION``, which is Postgres-only. Verifying a migration under
SQLite means executing that one migration's ``upgrade()`` against a hand-built schema,
which is what happens below.

Postgres gets the offline render in ``TestOffline`` — the same migration compiled for the
production dialect without a database. Between the two, nothing in 0021 is both unexecuted
and unrendered.
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
    / "0021_proposal_ledger.py"
)

# Only what 0021 touches or needs to point at. ``scope_node`` because the new table takes
# a foreign key onto it, ``risk_history`` because it gains a column. Not a copy of the
# whole schema — a fuller one would drift from the real tree without anything noticing.
PRE_0021_DDL = [
    """
    CREATE TABLE scope_node (
        id INTEGER NOT NULL PRIMARY KEY,
        kind VARCHAR(20) NOT NULL,
        name VARCHAR(200) NOT NULL,
        created_by VARCHAR(120) NOT NULL DEFAULT 'Unknown'
    )
    """,
    """
    CREATE TABLE risk_history (
        id INTEGER NOT NULL PRIMARY KEY,
        risk_id INTEGER NOT NULL,
        risk_code VARCHAR(100) NOT NULL,
        action VARCHAR(20) NOT NULL,
        actor VARCHAR(120) NOT NULL DEFAULT 'Unknown',
        changes JSON
    )
    """,
]

INSERT = (
    "INSERT INTO proposal (scope_id, target_type, target_id, field_path, "
    "proposed_value, rationale, evidence_refs, generator_model, "
    "generator_prompt_version, status) VALUES "
    "(1, '{t}', {tid}, '{f}', '\"v\"', 'because', '{ev}', 'm', 'v1', '{s}')"
)

ONE_REF = '[{"kind": "doc_chunk", "ref": "chunk:1"}]'


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0021", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _insert(
    connection: sa.Connection,
    *,
    target_type: str = "risk",
    target_id: str | int = 1,
    field_path: str = "title",
    evidence: str = ONE_REF,
    status: str = "pending",
) -> None:
    connection.exec_driver_sql(
        INSERT.format(
            t=target_type, tid=target_id, f=field_path, ev=evidence, s=status
        )
    )


@pytest.fixture
def migrated():
    """A pre-0021 database with one project and one history row, upgraded."""
    engine = sa.create_engine("sqlite://")
    module = _load_migration()
    with engine.begin() as connection:
        for statement in PRE_0021_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO scope_node (id, kind, name) VALUES (1, 'project', 'Terminal')"
        )
        connection.exec_driver_sql(
            "INSERT INTO risk_history (id, risk_id, risk_code, action) "
            "VALUES (1, 1, 'TRM-001', 'created')"
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return engine


class TestProvenance:
    def test_history_written_before_the_ledger_reads_as_human(self, migrated) -> None:
        """NULL is the right value for every pre-existing row, so there is no backfill.

        Stamping old rows with anything else would claim a provenance nobody recorded.
        """
        with migrated.connect() as c:
            assert (
                c.exec_driver_sql(
                    "SELECT provenance FROM risk_history WHERE id = 1"
                ).scalar_one()
                is None
            )

    def test_the_row_survives_the_column_add(self, migrated) -> None:
        with migrated.connect() as c:
            assert (
                c.exec_driver_sql("SELECT COUNT(*) FROM risk_history").scalar_one() == 1
            )


class TestEvidenceConstraint:
    def test_one_reference_is_enough(self, migrated) -> None:
        with migrated.begin() as c:
            _insert(c)
            assert c.exec_driver_sql("SELECT COUNT(*) FROM proposal").scalar_one() == 1

    def test_an_empty_reference_list_is_refused(self, migrated) -> None:
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                _insert(c, evidence="[]")

    def test_the_status_vocabulary_is_closed(self, migrated) -> None:
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                _insert(c, status="maybe")


class TestPendingUniqueness:
    def test_two_pending_proposals_for_one_field_collide(self, migrated) -> None:
        with migrated.begin() as c:
            _insert(c)
            with pytest.raises(sa.exc.IntegrityError):
                _insert(c)

    def test_a_terminal_row_does_not_block_a_new_one(self, migrated) -> None:
        """A target accumulates decisions for its whole life. They must not collide."""
        with migrated.begin() as c:
            _insert(c, status="rejected")
            _insert(c, status="rejected")
            _insert(c, status="accepted")
            _insert(c, status="pending")
            assert c.exec_driver_sql("SELECT COUNT(*) FROM proposal").scalar_one() == 4

    def test_different_fields_on_one_target_coexist(self, migrated) -> None:
        with migrated.begin() as c:
            _insert(c, field_path="title")
            _insert(c, field_path="consequences")
            assert c.exec_driver_sql("SELECT COUNT(*) FROM proposal").scalar_one() == 2

    def test_creation_proposals_are_exempt(self, migrated) -> None:
        """No target row yet means no field for two suggestions to collide on."""
        with migrated.begin() as c:
            _insert(c, target_id="NULL", field_path="*")
            _insert(c, target_id="NULL", field_path="*")
            _insert(c, target_id="NULL", field_path="*")
            assert c.exec_driver_sql("SELECT COUNT(*) FROM proposal").scalar_one() == 3


class TestDefaults:
    def test_a_new_row_is_pending_and_unparked(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO proposal (scope_id, target_type, target_id, field_path, "
                "proposed_value, rationale, evidence_refs, generator_model, "
                f"generator_prompt_version) VALUES (1, 'risk', 1, 'title', '\"v\"', "
                f"'because', '{ONE_REF}', 'm', 'v1')"
            )
            row = c.exec_driver_sql(
                "SELECT status, parked, created_at FROM proposal"
            ).one()
        assert row[0] == "pending"
        assert row[1] in (0, False)
        # ``sa.func.now()`` and not ``sa.text("now()")``: the latter does not exist under
        # SQLite and the column would be NULL here rather than server-defaulted.
        assert row[2] is not None


class TestDowngrade:
    def test_downgrade_removes_both_changes(self, migrated) -> None:
        module = _load_migration()
        with migrated.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
        with migrated.connect() as c:
            tables = {
                r[0]
                for r in c.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            assert "proposal" not in tables
            columns = {
                r[1]
                for r in c.exec_driver_sql("PRAGMA table_info(risk_history)").fetchall()
            }
            assert "provenance" not in columns


class TestOffline:
    def test_renders_offline_for_postgres(self) -> None:
        """`alembic upgrade --sql` must produce reviewable DDL for the real dialect.

        The boolean default is the reason this matters here: Postgres rejects an integer
        default on a boolean column, and a migration that only ever ran against SQLite
        would not find that out until it reached a real database.
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
        assert "create table proposal" in rendered
        assert "ck_proposal_has_evidence" in rendered
        assert "json_array_length" in rendered
        assert "parked boolean default false not null" in rendered
        assert "where status = 'pending' and target_id is not null" in rendered
        assert "alter table risk_history add column provenance" in rendered


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
