"""0019 — the risk code becomes ``<program>-<project>-<sequence>``.

Until now a risk was identified by where it sat in the RBS: ``ENV-030-0007``. That reads
well with one project and stops reading at two, because the identifier says which taxonomy
branch a risk came from and nothing about which register it belongs to. Two projects'
registers laid side by side in a program report are indistinguishable, and the same code
can legitimately exist twice.

The code now leads with the scope: the program's abbreviation, the project's abbreviation,
then a sequence that starts at 0001 in every project. Category is still stored, filtered
on and exported — it just stops being what the identifier is for.

**Three things happen here.**

1. ``risk.risk_code`` widens from 20 to 100, and ``risk_history.risk_code`` with it. Two
   forty-character scope codes plus separators plus the sequence do not fit in 20, and a
   history insert against a narrow column is how a rename turns into a 500 on the next
   edit rather than on this migration. Existing history *values* are untouched: the column
   widens, the rows keep exactly what was recorded when they were written.

2. ``uq_risk_scope_subcategory_seq`` is dropped. It sequenced within a subcategory, which
   is what the old code needed and is meaningless now that a project has one sequence.
   Nothing replaces it: ``seq`` feeds ``risk_code`` and nothing else, so a duplicate
   sequence in a scope is already refused by ``uq_risk_scope_code``.

3. Every existing risk is renumbered and recoded, per scope, oldest first. A register
   half in the old format and half in the new is worse than either, and ``seq`` has to be
   rewritten anyway — two risks in one project under different subcategories can both
   currently hold ``seq = 1``, which the new scheme cannot represent.

**The rewrite runs in two passes.** Codes go to a temporary unique value first, then to
their final value. ``uq_risk_scope_code`` is live throughout, and a one-pass rewrite can
transiently collide — a project abbreviated ``ENV`` inside a program abbreviated ``030``
is absurd but not forbidden, and a migration that fails on absurd data is a migration that
fails at 2am.

**The abbreviation rules are inlined rather than imported from
``app/services/risk_code.py``.** A migration is pinned to the schema of its own moment;
importing live application code makes it silently re-interpret history the next time that
code changes. The two copies are allowed to drift, and if they do, this one is what the
existing rows were built with.

**Offline (``--sql``) renders the DDL only.** Renumbering needs to read ``scope_node`` and
``risk`` to know what to write, and there is no connection to read them with. The DDL is
reviewable; the data pass is skipped with a comment in the output saying so.

Downgrade rebuilds the RBS-derived codes and the per-subcategory sequence, then re-narrows
the columns. It will fail on any database holding a code longer than 20 characters, which
is correct — that is data the old column cannot carry.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

WIDE = sa.String(100)
NARROW = sa.String(20)
OLD_SEQ_CONSTRAINT = "uq_risk_scope_subcategory_seq"

SEQ_WIDTH = 4
DERIVED_MAX = 6
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def _abbreviate(code: str | None, name: str | None) -> str:
    """Frozen copy of ``services.risk_code.abbreviate``. See the module docstring."""
    if code and code.strip():
        return " ".join(code.split()).upper()
    words = _NON_ALNUM.sub(" ", name or "").split()
    if not words:
        return "SCOPE"
    if len(words) >= 2:
        return "".join(word[0] for word in words)[:DERIVED_MAX].upper()
    return words[0][:4].upper()


def _prefixes(connection: sa.Connection) -> dict[int, str]:
    """``scope_id -> "<program>-<project>"`` for every node that could own a register."""
    nodes = {
        row.id: row
        for row in connection.execute(
            sa.text("SELECT id, parent_id, name, code FROM scope_node")
        )
    }
    out: dict[int, str] = {}
    for node_id, node in nodes.items():
        own = _abbreviate(node.code, node.name)
        parent = nodes.get(node.parent_id) if node.parent_id is not None else None
        out[node_id] = own if parent is None else f"{_abbreviate(parent.code, parent.name)}-{own}"
    return out


def _renumber(connection: sa.Connection) -> None:
    prefixes = _prefixes(connection)
    rows = connection.execute(
        sa.text(
            "SELECT id, scope_id FROM risk ORDER BY scope_id, created_at, id"
        )
    ).fetchall()

    # Pass one: park every code somewhere nothing can collide with.
    for row in rows:
        connection.execute(
            sa.text("UPDATE risk SET risk_code = :code WHERE id = :id"),
            {"code": f"~{row.id}", "id": row.id},
        )

    # Pass two: the real codes, sequenced per scope in the order above.
    counters: dict[int, int] = {}
    for row in rows:
        seq = counters.get(row.scope_id, 0) + 1
        counters[row.scope_id] = seq
        prefix = prefixes.get(row.scope_id, "SCOPE")
        connection.execute(
            sa.text("UPDATE risk SET seq = :seq, risk_code = :code WHERE id = :id"),
            {"seq": seq, "code": f"{prefix}-{seq:0{SEQ_WIDTH}d}", "id": row.id},
        )


def _restore_rbs_codes(connection: sa.Connection) -> None:
    """Downgrade's mirror of ``_renumber``: back to ``CAT-SUB-NNNN`` per subcategory."""
    rows = connection.execute(
        sa.text(
            "SELECT r.id AS id, r.scope_id AS scope_id, r.subcategory_id AS subcategory_id, "
            "c.code AS cat, s.code AS sub "
            "FROM risk r "
            "JOIN rbs_subcategory s ON s.id = r.subcategory_id "
            "JOIN rbs_category c ON c.id = s.category_id "
            "ORDER BY r.scope_id, r.subcategory_id, r.seq, r.id"
        )
    ).fetchall()

    for row in rows:
        connection.execute(
            sa.text("UPDATE risk SET risk_code = :code WHERE id = :id"),
            {"code": f"~{row.id}", "id": row.id},
        )

    counters: dict[tuple[int, int], int] = {}
    for row in rows:
        key = (row.scope_id, row.subcategory_id)
        seq = counters.get(key, 0) + 1
        counters[key] = seq
        connection.execute(
            sa.text("UPDATE risk SET seq = :seq, risk_code = :code WHERE id = :id"),
            {"seq": seq, "code": f"{row.cat}-{row.sub}-{seq:04d}", "id": row.id},
        )


def upgrade() -> None:
    with op.batch_alter_table("risk") as batch:
        batch.drop_constraint(OLD_SEQ_CONSTRAINT, type_="unique")
        batch.alter_column(
            "risk_code", existing_type=NARROW, type_=WIDE, existing_nullable=False
        )
    with op.batch_alter_table("risk_history") as batch:
        batch.alter_column(
            "risk_code", existing_type=NARROW, type_=WIDE, existing_nullable=False
        )

    if op.get_context().as_sql:
        op.execute(
            "-- 0019: existing risk codes are rebuilt from scope_node by the online "
            "-- migration. Offline mode has no connection to read the hierarchy, so the "
            "-- data pass is not rendered here. Run `alembic upgrade head` against the "
            "-- database to renumber."
        )
        return
    _renumber(op.get_bind())


def downgrade() -> None:
    if not op.get_context().as_sql:
        _restore_rbs_codes(op.get_bind())

    with op.batch_alter_table("risk_history") as batch:
        batch.alter_column(
            "risk_code", existing_type=WIDE, type_=NARROW, existing_nullable=False
        )
    with op.batch_alter_table("risk") as batch:
        batch.alter_column(
            "risk_code", existing_type=WIDE, type_=NARROW, existing_nullable=False
        )
        batch.create_unique_constraint(
            OLD_SEQ_CONSTRAINT, ["scope_id", "subcategory_id", "seq"]
        )
