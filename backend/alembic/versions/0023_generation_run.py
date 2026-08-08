"""generation runs, and the two columns a generated proposal needs

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-08

One new table and two nullable columns on ``proposal``. Nothing existing is rewritten, so
there is no backfill to review and the downgrade is a clean drop.

**``generation_run`` is append-only by convention, not by constraint.** There is no
``ck_genrun_immutable`` because SQL cannot express one, and the enforcement is where it
already is for simulation runs and the ledger: no delete route, no update route, and a
status CHECK whose vocabulary has no way back out of a terminal value. The table's job is
to be the parent a batch of proposals hangs off, so that "how many did the model offer and
how many were refused before a reviewer saw them" is one row rather than an aggregate over
an inbox that has since been worked.

**Status has four values and not five.** ``cancelled`` belongs to a cancel feature that is
not in this delivery, and putting it in the CHECK now would add a value nothing can set —
the same mistake ``simulation_run`` avoided by taking its cancel status in 0018 rather than
in 0009.

**``proposal.created_target_id`` is separate from ``target_id`` on purpose.** A creation
proposal carries ``target_id IS NULL``, which is what exempts it from the partial unique
index on pending rows. Back-filling ``target_id`` with the created row's id after
acceptance would silently move the row into the scope of that index and, worse, destroy
the only signal that says this proposal created something rather than changed it.

**Neither new column takes a foreign key.** ``generation_run_id`` would want one, and
adding it would require ``batch_alter_table`` to rebuild ``proposal`` under SQLite —
dropping and re-declaring ``uq_proposal_one_pending_per_field`` (partial, dialect-specific)
and both CHECK constraints in the process. Rebuilding the three constraints the ledger's
guarantees rest on, to gain referential integrity to a table that is never deleted from, is
a bad trade. The ORM model carries the same reasoning.

The index on ``generation_run_id`` is the one this actually needs: "show me every proposal
from run 12" is the inbox's grouping query.
"""

import sqlalchemy as sa

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "scope_id",
            sa.Integer(),
            sa.ForeignKey("scope_node.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0"),
        sa.Column("document_ids", sa.JSON(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "windows_truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("pack_sha256", sa.String(length=64), nullable=True),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("proposal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dropped", sa.JSON(), nullable=True),
        sa.Column("transcript", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "requested_by", sa.String(length=120), nullable=False, server_default="Unknown"
        ),
        sa.Column("task_id", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            # ``sa.func.now()`` and not ``sa.text("now()")``: the latter is not portable
            # to the SQLite the suite verifies migrations against.
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_genrun_status",
        ),
    )
    op.create_index("ix_generation_run_scope_id", "generation_run", ["scope_id"])
    op.create_index("ix_generation_run_status", "generation_run", ["status"])
    op.create_index("ix_generation_run_created_at", "generation_run", ["created_at"])
    op.create_index("ix_generation_run_pack_sha256", "generation_run", ["pack_sha256"])
    op.create_index("ix_genrun_scope_created", "generation_run", ["scope_id", "created_at"])

    op.add_column(
        "proposal", sa.Column("created_target_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "proposal", sa.Column("generation_run_id", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_proposal_generation_run_id", "proposal", ["generation_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_proposal_generation_run_id", table_name="proposal")
    op.drop_column("proposal", "generation_run_id")
    op.drop_column("proposal", "created_target_id")

    op.drop_index("ix_genrun_scope_created", table_name="generation_run")
    op.drop_index("ix_generation_run_pack_sha256", table_name="generation_run")
    op.drop_index("ix_generation_run_created_at", table_name="generation_run")
    op.drop_index("ix_generation_run_status", table_name="generation_run")
    op.drop_index("ix_generation_run_scope_id", table_name="generation_run")
    op.drop_table("generation_run")
