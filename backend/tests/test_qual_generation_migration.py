"""0024 executed, not inspected.

Same method as 0021 through 0023, for the same reason: ``alembic upgrade head`` has never
worked against SQLite in this repo (0001 issues an unconditional ``CREATE EXTENSION``,
which is Postgres-only), so verifying a migration under SQLite means building the
pre-migration schema by hand and running that one migration's real ``upgrade()`` against
it. Postgres gets the offline render below.

0024 is two ``add_column`` calls, which is close to the smallest migration that can be
written and still worth a test file. Three things are worth executing rather than reading:

- **An existing 5.4 run reads identically afterwards, with both columns NULL.** A sweep has
  no subjects and skipped nothing, and NULL is the honest value for both. A default of
  ``[]`` would say "this pass considered nothing and skipped nothing", which is a claim,
  where NULL says "this kind of pass does not answer that question".
- **``proposal`` is untouched.** 0023 added its columns to that table; this one must not go
  near it, and a rebuild there would drop the partial unique index and both CHECKs.
- **The columns render as ``json`` for Postgres**, matching every other JSON column on the
  table rather than arriving as ``jsonb`` for two columns out of five.
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
    / "0024_generation_subjects.py"
)

#: ``generation_run`` as 0023 left it, trimmed to the columns this test reads or writes.
#: The full table is exercised by ``test_generation_migration.py``; repeating it here would
#: give two copies of the same DDL to keep in step.
PRE_0024_DDL = [
    """
    CREATE TABLE scope_node (
        id INTEGER NOT NULL PRIMARY KEY,
        kind VARCHAR(20) NOT NULL,
        name VARCHAR(200) NOT NULL
    )
    """,
    """
    CREATE TABLE generation_run (
        id INTEGER NOT NULL PRIMARY KEY,
        scope_id INTEGER NOT NULL REFERENCES scope_node(id),
        kind VARCHAR(40) NOT NULL,
        status VARCHAR(20) DEFAULT 'queued' NOT NULL,
        prompt_version VARCHAR(40) NOT NULL,
        provider VARCHAR(40) NOT NULL,
        model VARCHAR(120) NOT NULL,
        temperature FLOAT DEFAULT 0 NOT NULL,
        document_ids JSON,
        chunk_count INTEGER DEFAULT 0 NOT NULL,
        window_count INTEGER DEFAULT 0 NOT NULL,
        windows_truncated BOOLEAN DEFAULT false NOT NULL,
        pack_sha256 VARCHAR(64),
        candidate_count INTEGER DEFAULT 0 NOT NULL,
        proposal_count INTEGER DEFAULT 0 NOT NULL,
        dropped JSON,
        transcript JSON,
        input_tokens INTEGER,
        output_tokens INTEGER,
        error TEXT,
        requested_by VARCHAR(120) DEFAULT 'Unknown' NOT NULL,
        task_id VARCHAR(80),
        created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
        started_at DATETIME,
        finished_at DATETIME,
        CONSTRAINT ck_genrun_status CHECK (
            status IN ('queued', 'running', 'succeeded', 'failed')
        )
    )
    """,
]


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0024", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated():
    """A pre-0024 database holding one finished 5.4 run, upgraded."""
    engine = sa.create_engine("sqlite://")
    module = _load_migration()
    with engine.begin() as connection:
        for statement in PRE_0024_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO scope_node (id, kind, name) VALUES (1, 'project', 'Tunnel')"
        )
        connection.exec_driver_sql(
            "INSERT INTO generation_run (id, scope_id, kind, status, prompt_version, "
            "provider, model, document_ids, chunk_count, proposal_count) VALUES "
            "(1, 1, 'risk_identification', 'succeeded', 'risk-id/v1', 'fake', 'm', "
            "'[7]', 12, 3)"
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return engine


class TestUpgrade:
    def test_both_columns_exist(self, migrated) -> None:
        with migrated.connect() as c:
            columns = {
                r[1]
                for r in c.exec_driver_sql(
                    "PRAGMA table_info(generation_run)"
                ).fetchall()
            }
        assert {"subject_ids", "skipped"} <= columns

    def test_an_existing_sweep_survives_unchanged_with_nulls(self, migrated) -> None:
        """NULL, not ``[]``. An empty list says "considered nothing, skipped nothing",
        which is a claim about a pass that was never asked the question."""
        with migrated.connect() as c:
            row = c.exec_driver_sql(
                "SELECT kind, status, document_ids, chunk_count, proposal_count, "
                "subject_ids, skipped FROM generation_run WHERE id = 1"
            ).one()
        assert row[:5] == ("risk_identification", "succeeded", "[7]", 12, 3)
        assert row[5] is None and row[6] is None

    def test_a_query_shaped_run_round_trips_both_columns(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO generation_run (id, scope_id, kind, prompt_version, "
                "provider, model, subject_ids, skipped) VALUES "
                "(2, 1, 'qualitative_evaluation', 'qual-eval/v1', 'fake', 'm', "
                "'[4, 9]', '[{\"subject\": \"NST-TUN-0002\", \"reason\": \"no_evidence\", "
                "\"detail\": \"nothing matched\"}]')"
            )
            row = c.exec_driver_sql(
                "SELECT subject_ids, skipped FROM generation_run WHERE id = 2"
            ).one()
        assert row[0] == "[4, 9]"
        assert "no_evidence" in row[1]

    def test_the_status_check_still_bites(self, migrated) -> None:
        """``add_column`` and not ``batch_alter_table``: SQLite must not rebuild the
        table, because a rebuild drops the CHECK that keeps the status vocabulary closed.
        """
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(
                    "INSERT INTO generation_run (scope_id, kind, prompt_version, "
                    "provider, model, status) VALUES "
                    "(1, 'qualitative_evaluation', 'v1', 'fake', 'm', 'paused')"
                )

    def test_kind_is_still_open(self, migrated) -> None:
        """No CHECK on ``kind``, deliberately. One migration per new generator is the cost
        0023 declined, and a second stage shipping is when that decision gets tested."""
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO generation_run (scope_id, kind, prompt_version, provider, "
                "model) VALUES (1, 'qualitative_evaluation', 'qual-eval/v1', 'fake', 'm')"
            )


class TestDowngrade:
    def test_downgrade_removes_both_columns_and_keeps_the_table(self, migrated) -> None:
        module = _load_migration()
        with migrated.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
        with migrated.connect() as c:
            columns = {
                r[1]
                for r in c.exec_driver_sql(
                    "PRAGMA table_info(generation_run)"
                ).fetchall()
            }
            assert "subject_ids" not in columns and "skipped" not in columns
            assert (
                c.exec_driver_sql("SELECT COUNT(*) FROM generation_run").scalar_one()
                == 1
            )


class TestOffline:
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
        assert "alter table generation_run add column subject_ids json" in rendered
        assert "alter table generation_run add column skipped json" in rendered
        # Nothing goes near the ledger. 0023 touched it; this one must not.
        assert "proposal" not in rendered

    def test_the_downgrade_renders_too(self) -> None:
        module = _load_migration()
        statements: list[str] = []
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": _Buffer(statements)},
        )
        with Operations.context(context):
            module.downgrade()
        rendered = "\n".join(statements).lower()
        assert "drop column skipped" in rendered
        assert "drop column subject_ids" in rendered


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
