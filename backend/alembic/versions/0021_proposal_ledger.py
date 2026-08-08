"""the proposal ledger, and provenance on the risk audit trail

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-08

Two changes, one migration, because the second is meaningless without the first: a
``provenance`` value of ``proposal:17`` refers to a row that does not exist until
``proposal`` does.

**The ledger.** One table for everything a generator suggests, addressed polymorphically by
``(target_type, target_id, field_path)``. The reasoning for a single table over one per
subsystem is in ``app/models/proposal.py``; what matters at the schema level is that two
constraints are carried by the database rather than by the application:

- ``ck_proposal_has_evidence`` — at least one evidence reference. Enforced here and not
  only at the Pydantic boundary because a generator writing through the service bypasses
  that boundary entirely, and an unevidenced suggestion is the precise failure this
  subsystem exists to prevent. ``json_array_length`` exists in SQLite's built-in JSON1 and
  in Postgres for the ``json`` type ``sa.JSON`` renders to, so the same expression compiles
  under both. There is deliberately no equivalent CHECK on ``confidence``: NULL is a
  meaningful value (the generator abstained) and any range constraint would have to permit
  it, leaving nothing worth constraining.
- ``uq_proposal_one_pending_per_field`` — a partial unique index, so a second pass over the
  same field supersedes rather than duplicating. Partial on two counts: ``status =
  'pending'`` because a target accumulates terminal rows for its whole life and they must
  not collide, and ``target_id IS NOT NULL`` because a creation proposal has no field yet
  for two of them to collide on. Both dialects support partial indexes; SQLite has since
  3.8.0, which predates every Python this runs on.

**Provenance.** ``risk_history`` gains a nullable string. NULL reads as human, which is the
correct value for every row that already exists, so there is no backfill and no risk of
mislabelling history written before the ledger existed. The column goes on the *history*
table rather than on ``risk`` itself because "who decided this" is a question about an
event, not about a current value — a provenance column on the domain row would be
overwritten by the next edit and would answer only for the most recent one. It is left off
``mapping_history`` and the mitigation trail until each has a generator that could populate
it; adding a column nothing writes is a schema claim the code cannot back.

Additive throughout. The downgrade drops a table and a column with nothing to reconstruct.
"""

import sqlalchemy as sa

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

PENDING_PREDICATE = "status = 'pending' AND target_id IS NOT NULL"


def upgrade() -> None:
    op.create_table(
        "proposal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("field_path", sa.String(length=120), nullable=False),
        sa.Column("proposed_value", sa.JSON(), nullable=False),
        sa.Column("observed_value", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("generator_model", sa.String(length=120), nullable=False),
        sa.Column("generator_prompt_version", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        # ``sa.false()`` and not ``"0"``: Postgres rejects an integer default on a
        # boolean column, and SQLAlchemy renders this correctly for each dialect.
        sa.Column("parked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("applied_value", sa.JSON(), nullable=True),
        sa.Column("superseded_by", sa.Integer(), nullable=True),
        sa.Column("disposed_by", sa.String(length=120), nullable=True),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition_note", sa.Text(), nullable=True),
        # ``sa.func.now()`` and not ``sa.text("now()")``: the latter is a Postgres-only
        # spelling and this migration is executed against SQLite by its own test.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["scope_id"], ["scope_node.id"], name="fk_proposal_scope_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"], ["proposal.id"], name="fk_proposal_superseded_by",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'edited', 'rejected', 'superseded')",
            name="ck_proposal_status",
        ),
        sa.CheckConstraint(
            "json_array_length(evidence_refs) >= 1", name="ck_proposal_has_evidence"
        ),
    )
    op.create_index("ix_proposal_scope_id", "proposal", ["scope_id"])
    op.create_index("ix_proposal_status", "proposal", ["status"])
    op.create_index("ix_proposal_created_at", "proposal", ["created_at"])
    op.create_index("ix_proposal_target", "proposal", ["target_type", "target_id"])
    op.create_index(
        "uq_proposal_one_pending_per_field",
        "proposal",
        ["target_type", "target_id", "field_path"],
        unique=True,
        sqlite_where=sa.text(PENDING_PREDICATE),
        postgresql_where=sa.text(PENDING_PREDICATE),
    )

    op.add_column(
        "risk_history", sa.Column("provenance", sa.String(length=160), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("risk_history", "provenance")
    op.drop_index("uq_proposal_one_pending_per_field", table_name="proposal")
    op.drop_index("ix_proposal_target", table_name="proposal")
    op.drop_index("ix_proposal_created_at", table_name="proposal")
    op.drop_index("ix_proposal_status", table_name="proposal")
    op.drop_index("ix_proposal_scope_id", table_name="proposal")
    op.drop_table("proposal")
