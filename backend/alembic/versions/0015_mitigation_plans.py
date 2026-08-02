"""mitigation plans, declared residuals, and what an action costs

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-01

Two new tables and two new columns, and no data migration: every existing mitigation
action stays exactly where it is, outside any plan. That is deliberate. Actions written
before plans existed belong to a risk, not to a package, and inventing a package to put
them in would put a name on a decision nobody made.

``mitigation_plan`` carries its own materialisation record — when it last wrote the
post-mitigation scenario, who ran it, and a digest of what it wrote. Nothing else in the
schema can answer "is this residual register still the one this plan produced", and
without that a post-mitigation run cannot be attributed to the package it was supposed to
measure.

``mitigation_plan_risk`` holds factors and optional absolute residuals rather than a
second copy of ``risk_quant_estimate``. The residual is projected into that table under
``scenario='post_mitigation'`` — a column, a unique constraint and an assembly parameter
that have all existed since 0011 and 0013 waiting for exactly this.

Timestamp defaults are ``sa.func.now()`` rather than ``sa.text("now()")``: the former
is compiled by the dialect and lands as ``CURRENT_TIMESTAMP`` on SQLite, where opaque
text would try to call a function SQLite does not have and take the whole migration
test with it. 0014 set this convention; the older migrations predate it.

``op.batch_alter_table`` for the ``mitigation_action`` changes. SQLite cannot add a
foreign key in place; batch mode rebuilds the table and Postgres executes the same
operations directly.
"""

import sqlalchemy as sa

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mitigation_plan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("materialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("materialized_by", sa.String(length=120), nullable=True),
        sa.Column("materialized_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("materialized_risk_count", sa.Integer(), nullable=True),
        sa.Column("materialized_retired_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_by", sa.String(length=120), nullable=False, server_default="Unknown"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["scope_id"], ["scope_node.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("scope_id", "name", name="uq_mitigation_plan_scope_name"),
        sa.CheckConstraint(
            "status IN ('draft', 'proposed', 'approved', 'rejected', 'superseded')",
            name="ck_mitigation_plan_status",
        ),
    )
    op.create_index("ix_mitigation_plan_scope_id", "mitigation_plan", ["scope_id"])
    op.create_index("ix_mitigation_plan_status", "mitigation_plan", ["status"])

    op.create_table(
        "mitigation_plan_risk",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("risk_id", sa.Integer(), nullable=False),
        sa.Column(
            "treatment", sa.String(length=20), nullable=False, server_default="reduce"
        ),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="factor"),
        sa.Column("p_factor", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("cost_factor", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("sched_factor", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("residual_p", sa.Float(), nullable=True),
        sa.Column("residual_cost_min", sa.Float(), nullable=True),
        sa.Column("residual_cost_ml", sa.Float(), nullable=True),
        sa.Column("residual_cost_max", sa.Float(), nullable=True),
        sa.Column("residual_sched_min", sa.Float(), nullable=True),
        sa.Column("residual_sched_ml", sa.Float(), nullable=True),
        sa.Column("residual_sched_max", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["mitigation_plan.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["risk_id"], ["risk.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("plan_id", "risk_id", name="uq_mitigation_plan_risk"),
        sa.CheckConstraint(
            "treatment IN ('reduce', 'retire', 'accept')", name="ck_plan_risk_treatment"
        ),
        sa.CheckConstraint("mode IN ('factor', 'absolute')", name="ck_plan_risk_mode"),
        sa.CheckConstraint("p_factor > 0 AND p_factor <= 1", name="ck_plan_risk_p_factor"),
        sa.CheckConstraint(
            "cost_factor > 0 AND cost_factor <= 1", name="ck_plan_risk_cost_factor"
        ),
        sa.CheckConstraint(
            "sched_factor > 0 AND sched_factor <= 1", name="ck_plan_risk_sched_factor"
        ),
        sa.CheckConstraint(
            "residual_p IS NULL OR (residual_p > 0 AND residual_p <= 1)",
            name="ck_plan_risk_residual_p",
        ),
    )
    op.create_index("ix_mitigation_plan_risk_plan_id", "mitigation_plan_risk", ["plan_id"])
    op.create_index("ix_mitigation_plan_risk_risk_id", "mitigation_plan_risk", ["risk_id"])

    with op.batch_alter_table("mitigation_action") as batch:
        batch.add_column(sa.Column("plan_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("sched_days", sa.Float(), nullable=True))
        batch.create_foreign_key(
            "fk_mitigation_action_plan_id",
            "mitigation_plan",
            ["plan_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_mitigation_action_plan_id", "mitigation_action", ["plan_id"])


def downgrade() -> None:
    op.drop_index("ix_mitigation_action_plan_id", table_name="mitigation_action")
    with op.batch_alter_table("mitigation_action") as batch:
        batch.drop_constraint("fk_mitigation_action_plan_id", type_="foreignkey")
        batch.drop_column("sched_days")
        batch.drop_column("plan_id")

    op.drop_index("ix_mitigation_plan_risk_risk_id", table_name="mitigation_plan_risk")
    op.drop_index("ix_mitigation_plan_risk_plan_id", table_name="mitigation_plan_risk")
    op.drop_table("mitigation_plan_risk")

    op.drop_index("ix_mitigation_plan_status", table_name="mitigation_plan")
    op.drop_index("ix_mitigation_plan_scope_id", table_name="mitigation_plan")
    op.drop_table("mitigation_plan")
