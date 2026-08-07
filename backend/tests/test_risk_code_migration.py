"""The 0019 recode, executed rather than inspected.

0019 is the first migration in this repo that rewrites the *value* of a user-visible
identifier on every existing row. Widening two columns and dropping a constraint would be
unremarkable; renumbering a live register is not, and the ways it goes wrong are quiet
ones — a scope whose risks all collapse onto one number, a code built from the wrong end
of the hierarchy, a two-pass rewrite that trips the uniqueness constraint it was written
to avoid.

The database here is ``risk`` as it stood at 0018, with a hierarchy above it: a portfolio,
a program under it, two projects under the program, one project hanging off the portfolio
directly, and one project with no parent at all. Every code-generation branch in the
migration has a row that exercises it.

``risk_history`` is seeded too. Its ``risk_code`` copies must survive the upgrade
unchanged — the trail records what the code *was*, and a migration that helpfully updated
it would be rewriting history to agree with the present, which is the one thing an
append-only trail exists to prevent.
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
    / "0019_risk_code_scope_prefix.py"
)

PRE_0019_DDL = [
    """
    CREATE TABLE scope_node (
        id INTEGER NOT NULL PRIMARY KEY,
        kind VARCHAR(20) NOT NULL,
        parent_id INTEGER,
        name VARCHAR(200) NOT NULL,
        code VARCHAR(40),
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_by VARCHAR(120) NOT NULL DEFAULT 'Unknown'
    )
    """,
    """
    CREATE TABLE rbs_category (
        id INTEGER NOT NULL PRIMARY KEY,
        code VARCHAR(3) NOT NULL,
        name VARCHAR(120) NOT NULL
    )
    """,
    """
    CREATE TABLE rbs_subcategory (
        id INTEGER NOT NULL PRIMARY KEY,
        category_id INTEGER NOT NULL,
        code VARCHAR(3) NOT NULL,
        name TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE risk (
        id INTEGER NOT NULL PRIMARY KEY,
        scope_id INTEGER NOT NULL,
        subcategory_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        risk_code VARCHAR(20) NOT NULL,
        title VARCHAR(300) NOT NULL,
        status VARCHAR(30) NOT NULL DEFAULT 'Open',
        created_at DATETIME NOT NULL,
        CONSTRAINT uq_risk_scope_subcategory_seq UNIQUE (scope_id, subcategory_id, seq),
        CONSTRAINT uq_risk_scope_code UNIQUE (scope_id, risk_code)
    )
    """,
    """
    CREATE TABLE risk_history (
        id INTEGER NOT NULL PRIMARY KEY,
        risk_id INTEGER NOT NULL,
        risk_code VARCHAR(20) NOT NULL,
        action VARCHAR(20) NOT NULL,
        actor VARCHAR(120) NOT NULL DEFAULT 'Unknown',
        changes TEXT
    )
    """,
]

PORTFOLIO, PROGRAM, PROJECT_A, PROJECT_B, DEPOT, LONE = 1, 2, 3, 4, 5, 6

SCOPES = [
    # id, kind, parent, name, code
    (PORTFOLIO, "portfolio", None, "Capital Delivery", "CAP"),
    (PROGRAM, "program", PORTFOLIO, "Water Program", "WTR"),
    (PROJECT_A, "project", PROGRAM, "Plant A", "PLA"),
    (PROJECT_B, "project", PROGRAM, "Plant B", None),  # abbreviated from the name
    (DEPOT, "project", PORTFOLIO, "Depot", None),  # parent is a portfolio, not a program
    (LONE, "project", None, "Standalone", "SOLO"),  # no parent at all
]

# (id, scope, subcategory, seq, code, created_at) — deliberately including two risks in
# one scope that share `seq` under different subcategories, which is exactly the state the
# new scheme cannot represent and the reason the renumber is not optional.
RISKS = [
    (1, PROJECT_A, 1, 1, "ENV-030-0001", "2026-01-01 09:00:00"),
    (2, PROJECT_A, 2, 1, "CON-010-0001", "2026-01-02 09:00:00"),
    (3, PROJECT_A, 1, 2, "ENV-030-0002", "2026-01-03 09:00:00"),
    (4, PROJECT_B, 1, 1, "ENV-030-0001", "2026-01-04 09:00:00"),
    (5, DEPOT, 2, 1, "CON-010-0001", "2026-01-05 09:00:00"),
    (6, LONE, 1, 1, "ENV-030-0001", "2026-01-06 09:00:00"),
]


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0019", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _seed(connection: sa.Connection) -> None:
    for statement in PRE_0019_DDL:
        connection.exec_driver_sql(statement)

    for node_id, kind, parent, name, code in SCOPES:
        connection.execute(
            sa.text(
                "INSERT INTO scope_node (id, kind, parent_id, name, code) "
                "VALUES (:id, :kind, :parent, :name, :code)"
            ),
            {"id": node_id, "kind": kind, "parent": parent, "name": name, "code": code},
        )

    connection.exec_driver_sql(
        "INSERT INTO rbs_category (id, code, name) VALUES (1, 'ENV', 'Environmental'), "
        "(2, 'CON', 'Construction')"
    )
    connection.exec_driver_sql(
        "INSERT INTO rbs_subcategory (id, category_id, code, name) VALUES "
        "(1, 1, '030', 'Permitting'), (2, 2, '010', 'Productivity')"
    )

    for risk_id, scope, sub, seq, code, created in RISKS:
        connection.execute(
            sa.text(
                "INSERT INTO risk (id, scope_id, subcategory_id, seq, risk_code, title, "
                "created_at) VALUES (:id, :scope, :sub, :seq, :code, :title, :created)"
            ),
            {
                "id": risk_id,
                "scope": scope,
                "sub": sub,
                "seq": seq,
                "code": code,
                "title": f"Risk {risk_id}",
                "created": created,
            },
        )
        connection.execute(
            sa.text(
                "INSERT INTO risk_history (risk_id, risk_code, action, actor) "
                "VALUES (:rid, :code, 'created', 'Sam')"
            ),
            {"rid": risk_id, "code": code},
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


def _codes(engine) -> dict[int, str]:
    with engine.connect() as c:
        return {
            row.id: row.risk_code
            for row in c.exec_driver_sql("SELECT id, risk_code FROM risk").fetchall()
        }


class TestUpgrade:
    def test_no_row_is_lost_to_the_table_rebuild(self, migrated) -> None:
        with migrated.connect() as c:
            assert c.exec_driver_sql("SELECT COUNT(*) FROM risk").scalar_one() == 6

    def test_a_project_under_a_program_gets_all_three_segments(self, migrated) -> None:
        assert _codes(migrated)[1] == "WTR-PLA-0001"

    def test_the_sequence_is_per_project_and_ignores_the_taxonomy(self, migrated) -> None:
        """Risks 1, 2 and 3 share a project across two subcategories: 1, 2, 3."""
        codes = _codes(migrated)
        assert [codes[i] for i in (1, 2, 3)] == [
            "WTR-PLA-0001",
            "WTR-PLA-0002",
            "WTR-PLA-0003",
        ]

    def test_every_project_restarts_at_one(self, migrated) -> None:
        codes = _codes(migrated)
        assert codes[4].endswith("-0001")
        assert codes[5].endswith("-0001")
        assert codes[6].endswith("-0001")

    def test_a_scope_with_no_code_is_abbreviated_from_its_name(self, migrated) -> None:
        # "Plant B" -> PB, under the program's explicit code.
        assert _codes(migrated)[4] == "WTR-PB-0001"

    def test_a_portfolio_stands_in_when_there_is_no_program(self, migrated) -> None:
        # "Depot" is a single word, so it takes its first four characters.
        assert _codes(migrated)[5] == "CAP-DEPO-0001"

    def test_a_project_with_no_parent_gets_two_segments(self, migrated) -> None:
        assert _codes(migrated)[6] == "SOLO-0001"

    def test_seq_is_rewritten_to_match_the_code(self, migrated) -> None:
        with migrated.connect() as c:
            rows = dict(
                c.exec_driver_sql("SELECT id, seq FROM risk").fetchall()  # type: ignore[arg-type]
            )
        assert rows[1] == 1 and rows[2] == 2 and rows[3] == 3
        assert rows[4] == 1 and rows[5] == 1 and rows[6] == 1

    def test_history_rows_keep_the_code_they_were_written_with(self, migrated) -> None:
        """Append-only means the trail is not corrected to agree with the present."""
        with migrated.connect() as c:
            rows = sorted(
                c.exec_driver_sql(
                    "SELECT risk_id, risk_code FROM risk_history"
                ).fetchall()
            )
        assert [r[1] for r in rows] == [
            "ENV-030-0001",
            "CON-010-0001",
            "ENV-030-0002",
            "ENV-030-0001",
            "CON-010-0001",
            "ENV-030-0001",
        ]

    def test_a_code_too_long_for_the_old_column_now_fits(self, migrated) -> None:
        long_code = "PROGRAMME-ALPHA-PROJECT-BRAVO-CHARLIE-0042"
        with migrated.begin() as c:
            c.execute(
                sa.text(
                    "INSERT INTO risk (id, scope_id, subcategory_id, seq, risk_code, "
                    "title, created_at) VALUES (99, 1, 1, 99, :code, 'Long', "
                    "'2026-02-01 09:00:00')"
                ),
                {"code": long_code},
            )
        assert _codes(migrated)[99] == long_code

    def test_two_risks_in_one_project_may_now_share_a_subcategory_and_sequence_shape(
        self, migrated
    ) -> None:
        """The dropped constraint is really gone: same scope, same subcategory, seq 1."""
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO risk (id, scope_id, subcategory_id, seq, risk_code, title, "
                "created_at) VALUES (98, 3, 1, 1, 'WTR-PLA-9998', 'Dup seq', "
                "'2026-02-01 09:00:00')"
            )
        assert _codes(migrated)[98] == "WTR-PLA-9998"

    def test_the_per_scope_code_constraint_still_holds(self, migrated) -> None:
        with migrated.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(
                    "INSERT INTO risk (id, scope_id, subcategory_id, seq, risk_code, "
                    "title, created_at) VALUES (97, 3, 1, 97, 'WTR-PLA-0001', 'Clash', "
                    "'2026-02-01 09:00:00')"
                )

    def test_the_same_code_in_a_different_scope_is_fine(self, migrated) -> None:
        with migrated.begin() as c:
            c.exec_driver_sql(
                "INSERT INTO risk (id, scope_id, subcategory_id, seq, risk_code, title, "
                "created_at) VALUES (96, 4, 1, 96, 'WTR-PLA-0001', 'Other scope', "
                "'2026-02-01 09:00:00')"
            )
        assert _codes(migrated)[96] == "WTR-PLA-0001"


class TestDowngrade:
    def test_downgrade_restores_rbs_codes_and_the_old_constraint(self) -> None:
        engine = sa.create_engine("sqlite://")
        module = _load_migration()
        with engine.begin() as connection:
            _seed(connection)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                module.downgrade()

        codes = _codes(engine)
        assert codes[1] == "ENV-030-0001"
        assert codes[3] == "ENV-030-0002"
        assert codes[2] == "CON-010-0001"
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT COUNT(*) FROM risk").scalar_one() == 6

    def test_the_old_sequence_constraint_is_back(self) -> None:
        engine = sa.create_engine("sqlite://")
        module = _load_migration()
        with engine.begin() as connection:
            _seed(connection)
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
                module.downgrade()
        with engine.begin() as c:
            with pytest.raises(sa.exc.IntegrityError):
                c.exec_driver_sql(
                    "INSERT INTO risk (id, scope_id, subcategory_id, seq, risk_code, "
                    "title, created_at) VALUES (95, 3, 1, 1, 'ENV-030-9995', 'Clash', "
                    "'2026-02-01 09:00:00')"
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
        assert "uq_risk_scope_subcategory_seq" in rendered
        assert "varchar(100)" in rendered
        assert "risk_history" in rendered
        # The data pass cannot render without a connection, and says so rather than
        # leaving a reviewer to assume it ran.
        assert "renumber" in rendered


class _Buffer:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def write(self, text: str) -> None:
        if text.strip():
            self._sink.append(text)

    def flush(self) -> None:  # pragma: no cover - interface requirement
        pass
