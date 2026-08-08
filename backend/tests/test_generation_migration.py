"""0023 executed, not inspected.

Same method as 0021 and 0022, for the same reason: ``alembic upgrade head`` has never
worked against SQLite in this repo (0001 issues an unconditional ``CREATE EXTENSION``,
which is Postgres-only), so verifying a migration under SQLite means building the
pre-migration schema by hand and running that one migration's real ``upgrade()`` against
it. Postgres gets the offline render below. Between the two, nothing in 0023 is both
unexecuted and unrendered.

Three things are worth executing rather than reading:

- The status CHECK has four values and not five. ``cancelled`` belongs to a cancel feature
  that is not in this delivery, and a value nothing can set is dead surface — the mistake
  ``simulation_run`` avoided by taking its cancel status in 0018 rather than at birth.
- ``proposal`` gains two columns and keeps its constraints. The columns were added with
  plain ``add_column`` precisely so SQLite would not rebuild the table; a rebuild would
  silently drop the partial unique index and both CHECKs, which are the three things on
  that table it would be worst to get subtly wrong. This asserts they still bite.
- The boolean server default renders as ``false`` and not ``0``. Postgres rejects an
  integer default on a boolean column, and a migration verified only under SQLite would
  not find that out until it reached a real database.
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
    / "0023_generation_run.py"
)

#: Only what 0023 touches or points at. ``scope_node`` because the new table takes a
#: foreign key onto it; ``proposal`` as 0021 left it, because that is the table gaining
#: columns and the point is that its constraints survive.
PRE_0023_DDL = [
    """
    CREATE TABLE scope_node (
        id INTEGER NOT NULL PRIMARY KEY,
        kind VARCHAR(20) NOT NULL,
        name VARCHAR(200) NOT NULL
    )
    """,
    """
    CREATE TABLE proposal (
        id INTEGER NOT NULL PRIMARY KEY,
        scope_id INTEGER NOT NULL REFERENCES scope_node(id),
        target_type VARCHAR(40) NOT NULL,
        target_id INTEGER,
        field_path VARCHAR(120) NOT NULL,
        proposed_value JSON NOT NULL,
        observed_value JSON,
        rationale TEXT NOT NULL,
        evidence_refs JSON NOT NULL,
        confidence FLOAT,
        generator_model VARCHAR(120) NOT NULL,
        generator_prompt_version VARCHAR(40) NOT NULL,
        status VARCHAR(20) DEFAULT 'pending' NOT NULL,
        parked BOOLEAN DEFAULT false NOT NULL,
        applied_value JSON,
        superseded_by INTEGER REFERENCES proposal(id),
        disposed_by VARCHAR(120),
        disposed_at DATETIME,
        disposition_note TEXT,
        created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
        CONSTRAINT ck_proposal_status CHECK (
            status IN ('pending', 'accepted', 'edited', 'rejected', 'superseded')
        ),
        CONSTRAINT ck_proposal_has_evidence CHECK (json_array_length(evidence_refs) >= 1)
    )
    """,
    """
    CREATE UNIQUE INDEX uq_proposal_one_pending_per_field
    ON proposal (target_type, target_id, field_path)
    WHERE status = 'pending' AND target_id IS NOT NULL
    """,
]

ONE_REF = '[{"kind": "doc_chunk", "ref": "doc_chunk:1"}]'

RUN_INSERT = (
    "INSERT INTO generation_run (scope_id, kind, prompt_version, provider, model, "
    "status) VALUES (1, 'risk_identification', 'risk-id/v1', 'fake', 'm', '{s}')"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0023", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated():
    """A pre-0023 database with one project and one proposal, upgraded."""
    engine = sa.create_engine("sqlite://")
    module = _load_migration()
    with engine.begin() as connection:
        for statement in PRE_0023_DDL:
            connection.exec_driver_sql(statement)
        connection.exec_driver_sql(
            "INSERT INTO scope_node (id, kind, name) VALUES (1, 'project', 'Tunnel')"
        )
        connection.exec_driver_sql(
            "INSERT INTO proposal (id, scope_id, target_type, target_id, field_path, "
            "proposed_value, rationale, evidence_refs, generator_model, "
            f"generator_prompt_version) VALUES (1, 1, 'risk', 5, 'title', '\"v\"', "
            f"'because', '{ONE_REF}', 'm', 'v1')"
        )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return engine


class TestProposalColumns:
    def test_the_existing_row_survives_with_nulls(self, migrated) -> None:
        """NULL is the right value for every pre-existing row. A proposal raised by hand
        was raised by no generation run, and stamping one on would claim a lineage nobody
        recorded."""
        with migrated.connect() as c:
            row = c.exec_driver_sql(
                "SELECT created_target_id, generation_run_id FROM proposal WHERE id = 1"
            ).one()
        assert row == (None, None)

    def test_both_columns_exist(self, migrated) -> None:
        with migrated.connect() as c:
            columns = {
                r[1] for r in c.exec_driver_sql("PRAGMA table_info(proposal)").fetchall()
            }
        assert {"created_target_id", "generation_run_id"} <= columns

    def test_the_evidence_check_still_bites(self, migrated) -> None:
        """``add_column`` was used rather than ``batch_alter_table`` so SQLite would not
        rebuild the table. A rebuild would have dropped this."""
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(
                    "INSERT INTO proposal (scope_id, target_type, target_id, field_path, "
                    "proposed_value, rationale, evidence_refs, generator_model, "
                    "generator_prompt_version) VALUES (1, 'risk', 6, 'title', '\"v\"', "
                    "'because', '[]', 'm', 'v1')"
                )

    def test_the_partial_unique_index_still_bites(self, migrated) -> None:
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(
                    "INSERT INTO proposal (scope_id, target_type, target_id, field_path, "
                    "proposed_value, rationale, evidence_refs, generator_model, "
                    f"generator_prompt_version) VALUES (1, 'risk', 5, 'title', '\"v\"', "
                    f"'because', '{ONE_REF}', 'm', 'v1')"
                )

    def test_creation_proposals_are_still_exempt_from_it(self, migrated) -> None:
        """The exemption is what makes deduplication the generator's job rather than the
        ledger's, so it has to survive a migration that adds a column about creations."""
        with migrated.begin() as c:
            for _ in range(3):
                c.exec_driver_sql(
                    "INSERT INTO proposal (scope_id, target_type, target_id, field_path, "
                    "proposed_value, rationale, evidence_refs, generator_model, "
                    f"generator_prompt_version) VALUES (1, 'risk', NULL, '*', '\"v\"', "
                    f"'because', '{ONE_REF}', 'm', 'v1')"
                )
            assert (
                c.exec_driver_sql(
                    "SELECT COUNT(*) FROM proposal WHERE target_id IS NULL"
                ).scalar_one()
                == 3
            )


class TestGenerationRun:
    def test_a_row_defaults_to_queued_and_untruncated(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO generation_run (scope_id, kind, prompt_version, provider, "
                "model) VALUES (1, 'risk_identification', 'risk-id/v1', 'fake', 'm')"
            )
            row = c.exec_driver_sql(
                "SELECT status, windows_truncated, candidate_count, proposal_count, "
                "created_at FROM generation_run"
            ).one()
        assert row[0] == "queued"
        assert row[1] in (0, False)
        assert row[2] == 0 and row[3] == 0
        # ``sa.func.now()`` and not ``sa.text("now()")``: the latter does not exist under
        # SQLite and the column would be NULL here rather than server-defaulted.
        assert row[4] is not None

    @pytest.mark.parametrize(
        "status", ["queued", "running", "succeeded", "failed"]
    )
    def test_every_real_status_is_accepted(self, migrated, status) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(RUN_INSERT.format(s=status))

    def test_the_status_vocabulary_is_closed(self, migrated) -> None:
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(RUN_INSERT.format(s="paused"))

    def test_cancelled_is_deliberately_not_a_status_yet(self, migrated) -> None:
        """Cancel is a real feature for a run that fires twenty paid calls, and it is not
        this delivery's. A value nothing can set is dead surface."""
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(RUN_INSERT.format(s="cancelled"))


class TestDowngrade:
    def test_downgrade_removes_the_table_and_both_columns(self, migrated) -> None:
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
            assert "generation_run" not in tables
            columns = {
                r[1] for r in c.exec_driver_sql("PRAGMA table_info(proposal)").fetchall()
            }
            assert "created_target_id" not in columns
            assert "generation_run_id" not in columns


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
        assert "create table generation_run" in rendered
        assert "ck_genrun_status" in rendered
        # Postgres rejects an integer default on a boolean column.
        assert "windows_truncated boolean default false not null" in rendered
        assert "alter table proposal add column created_target_id" in rendered
        assert "alter table proposal add column generation_run_id" in rendered
        # No table rebuild: the ledger's constraints must not appear in this migration.
        assert "uq_proposal_one_pending_per_field" not in rendered

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
        assert "drop table generation_run" in rendered


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
