"""matched run pairs: what a mitigation package was measured against

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-03

One table, no data migration, nothing altered. Every existing plan and every existing run
is untouched: a package that was never measured simply has no row here, which is the
truthful state rather than an invented pairing.

The table stores the *pairing*, not the answer. Two simulation runs are immutable, so the
comparison between them is a pure function of rows that cannot change and re-deriving it
on read is exact. What is stored is what cannot be re-derived: that these two runs were a
matched pair for this plan, what the package cost at that moment, and the plan's
materialisation fingerprint at that moment so a later edit shows up as staleness rather
than silently rewriting history.

``RESTRICT`` on all three foreign keys. A run quoted by a comparison is not deletable, and
neither is the plan — the alternative is a row that says a measurement happened and cannot
say what it measured. Runs already have no DELETE route (invariant 5); this makes the same
promise at the schema level for the plan.

``sa.func.now()`` rather than ``sa.text("now()")``, per the convention 0014 set: the former
compiles to ``CURRENT_TIMESTAMP`` on SQLite so this migration can be *executed* in the
test suite rather than only rendered.
"""

import sqlalchemy as sa

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mitigation_roi",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("before_run_id", sa.Integer(), nullable=False),
        sa.Column("after_run_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("percentile", sa.Float(), nullable=False, server_default="80"),
        sa.Column("seed_shared", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("plan_budget", sa.Float(), nullable=False, server_default="0"),
        sa.Column("plan_sched_days", sa.Float(), nullable=False, server_default="0"),
        sa.Column("plan_unpriced_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by", sa.String(length=120), nullable=False, server_default="Unknown"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"], ["mitigation_plan.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["scope_id"], ["scope_node.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["before_run_id"], ["simulation_run.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["after_run_id"], ["simulation_run.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "plan_id", "before_run_id", "after_run_id", name="uq_roi_plan_runs"
        ),
        sa.CheckConstraint("before_run_id <> after_run_id", name="ck_roi_distinct_runs"),
        sa.CheckConstraint("percentile > 0 AND percentile < 100", name="ck_roi_percentile"),
        sa.CheckConstraint("plan_budget >= 0", name="ck_roi_plan_budget"),
    )
    op.create_index("ix_mitigation_roi_plan_id", "mitigation_roi", ["plan_id"])
    op.create_index("ix_mitigation_roi_scope_id", "mitigation_roi", ["scope_id"])
    op.create_index("ix_mitigation_roi_before_run_id", "mitigation_roi", ["before_run_id"])
    op.create_index("ix_mitigation_roi_after_run_id", "mitigation_roi", ["after_run_id"])
    op.create_index("ix_mitigation_roi_created_at", "mitigation_roi", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_mitigation_roi_created_at", table_name="mitigation_roi")
    op.drop_index("ix_mitigation_roi_after_run_id", table_name="mitigation_roi")
    op.drop_index("ix_mitigation_roi_before_run_id", table_name="mitigation_roi")
    op.drop_index("ix_mitigation_roi_scope_id", table_name="mitigation_roi")
    op.drop_index("ix_mitigation_roi_plan_id", table_name="mitigation_roi")
    op.drop_table("mitigation_roi")
