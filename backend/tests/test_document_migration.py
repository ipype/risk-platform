"""0022 executed, not inspected.

Two constraints carry the corpus's meaning and both are database constraints:
``uq_document_scope_sha256``, which is the difference between a corpus and a pile, and
``uq_chunk_document_ordinal``, which is what makes "chunk 4 of document 12" a stable
citation. A recording stub would confirm the DDL was emitted and prove neither, so this
builds the schema at 0021, runs the real ``upgrade()``, and tries to break both.

``alembic upgrade head`` remains unusable in this repo — 0001 issues an unconditional
``CREATE EXTENSION``. SQLite verification means executing one migration's ``upgrade()``
against a hand-built schema, which is what happens below. Postgres gets the offline render.
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
    / "0022_document_corpus.py"
)

# Only what 0022 points at. ``scope_node`` because ``document`` takes a foreign key onto
# it; nothing else in the tree is touched.
PRE_0022_DDL = [
    """
    CREATE TABLE scope_node (
        id INTEGER NOT NULL PRIMARY KEY,
        kind VARCHAR(20) NOT NULL,
        name VARCHAR(200) NOT NULL
    )
    """,
]

DOC = (
    "INSERT INTO document (scope_id, filename, suffix, sha256, byte_size) "
    "VALUES ({scope}, '{name}', '.pdf', '{sha}', 10)"
)
CHUNK = (
    "INSERT INTO document_chunk (document_id, ordinal, kind, text) "
    "VALUES ({doc}, {ordinal}, 'prose', 'text')"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0022", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated():
    engine = sa.create_engine("sqlite://")
    module = _load_migration()
    with engine.begin() as connection:
        for statement in PRE_0022_DDL:
            connection.exec_driver_sql(statement)
        for scope_id, name in ((1, "Terminal"), (2, "Depot")):
            connection.exec_driver_sql(
                f"INSERT INTO scope_node (id, kind, name) VALUES ({scope_id}, "
                f"'project', '{name}')"
            )
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
    return engine


class TestDeduplication:
    def test_the_same_bytes_twice_in_one_scope_collide(self, migrated) -> None:
        """Two copies of a source double its weight in any retrieval over the text."""
        with migrated.begin() as c:
            c.exec_driver_sql(DOC.format(scope=1, name="permit.pdf", sha="a" * 64))
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(DOC.format(scope=1, name="copy.pdf", sha="a" * 64))

    def test_two_projects_may_hold_the_same_standard(self, migrated) -> None:
        """Scoped and not global: each project's citations resolve within its own scope."""
        with migrated.begin() as c:
            c.exec_driver_sql(DOC.format(scope=1, name="std.pdf", sha="b" * 64))
            c.exec_driver_sql(DOC.format(scope=2, name="std.pdf", sha="b" * 64))
            assert c.exec_driver_sql("SELECT COUNT(*) FROM document").scalar_one() == 2


class TestChunkOrdinals:
    def test_an_ordinal_is_unique_within_a_document(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(DOC.format(scope=1, name="a.pdf", sha="c" * 64))
            c.exec_driver_sql(CHUNK.format(doc=1, ordinal=0))
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(CHUNK.format(doc=1, ordinal=0))

    def test_two_documents_both_start_at_zero(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(DOC.format(scope=1, name="a.pdf", sha="d" * 64))
            c.exec_driver_sql(DOC.format(scope=1, name="b.pdf", sha="e" * 64))
            c.exec_driver_sql(CHUNK.format(doc=1, ordinal=0))
            c.exec_driver_sql(CHUNK.format(doc=2, ordinal=0))
            assert (
                c.exec_driver_sql("SELECT COUNT(*) FROM document_chunk").scalar_one() == 2
            )


class TestDefaults:
    def test_a_new_document_is_active_and_uploaded(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(DOC.format(scope=1, name="a.pdf", sha="f" * 64))
            row = c.exec_driver_sql(
                "SELECT status, source_kind, chunk_count, uploaded_by, created_at "
                "FROM document"
            ).one()
        assert row[0] == "active"
        assert row[1] == "upload"
        assert row[2] == 0
        assert row[3] == "Unknown"
        # ``sa.func.now()`` and not ``sa.text("now()")``: the latter does not exist under
        # SQLite and this would be NULL rather than server-defaulted.
        assert row[4] is not None


class TestDowngrade:
    def test_downgrade_drops_both_tables(self, migrated) -> None:
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
        assert "document" not in tables
        assert "document_chunk" not in tables
        assert "scope_node" in tables


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
        assert "create table document" in rendered
        assert "create table document_chunk" in rendered
        assert "uq_document_scope_sha256" in rendered
        assert "uq_chunk_document_ordinal" in rendered
        assert "on delete restrict" in rendered
        assert "on delete cascade" in rendered

    def test_there_is_no_vector_column_yet(self) -> None:
        """``pgvector.sqlalchemy.Vector`` does not compile under the SQLite the suite runs
        on, so declaring it now would break the test engine for storage nothing writes.
        Adding it is one nullable column and a backfill, which keeps the provider decision
        off the critical path."""
        module = _load_migration()
        statements: list[str] = []
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": _Buffer(statements)},
        )
        with Operations.context(context):
            module.upgrade()
        assert "vector" not in "\n".join(statements).lower()


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
