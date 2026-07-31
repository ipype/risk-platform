"""quantitative estimates, correlation drivers, quantification triage

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-30

"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "risk",
        sa.Column(
            "quantify", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_index("ix_risk_quantify", "risk", ["quantify"])

    op.create_table(
        "risk_quant_estimate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("risk_id", sa.Integer(), nullable=False),
        sa.Column(
            "scenario", sa.String(length=20), nullable=False, server_default="pre_mitigation"
        ),
        sa.Column("p_occurrence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "is_variability", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "bound_interpretation",
            sa.String(length=20),
            nullable=False,
            server_default="absolute",
        ),
        sa.Column("dist_type", sa.String(length=20), nullable=False, server_default="pert"),
        sa.Column("pert_lambda", sa.Float(), nullable=False, server_default="4.0"),
        sa.Column("cost_min", sa.Float(), nullable=True),
        sa.Column("cost_ml", sa.Float(), nullable=True),
        sa.Column("cost_max", sa.Float(), nullable=True),
        sa.Column(
            "cost_basis", sa.String(length=20), nullable=False, server_default="absolute"
        ),
        sa.Column("sched_min", sa.Float(), nullable=True),
        sa.Column("sched_ml", sa.Float(), nullable=True),
        sa.Column("sched_max", sa.Float(), nullable=True),
        sa.Column(
            "sched_day_basis", sa.String(length=20), nullable=False, server_default="working"
        ),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="sme"),
        sa.Column("confidence", sa.String(length=20), nullable=False, server_default="medium"),
        sa.Column(
            "estimated_by", sa.String(length=120), nullable=False, server_default="Unknown"
        ),
        sa.Column(
            "estimated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["risk_id"], ["risk.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("risk_id", "scenario", name="uq_quant_risk_scenario"),
        # NULL comparisons yield NULL and pass a CHECK, so ordering binds only on a
        # dimension that is actually populated.
        sa.CheckConstraint("cost_min <= cost_ml", name="ck_quant_cost_min_ml"),
        sa.CheckConstraint("cost_ml <= cost_max", name="ck_quant_cost_ml_max"),
        sa.CheckConstraint("sched_min <= sched_ml", name="ck_quant_sched_min_ml"),
        sa.CheckConstraint("sched_ml <= sched_max", name="ck_quant_sched_ml_max"),
        sa.CheckConstraint("p_occurrence > 0 AND p_occurrence <= 1", name="ck_quant_p_occurrence"),
        sa.CheckConstraint("pert_lambda > 0", name="ck_quant_pert_lambda"),
    )
    op.create_index("ix_risk_quant_estimate_risk_id", "risk_quant_estimate", ["risk_id"])
    op.create_index("ix_quant_risk_scenario", "risk_quant_estimate", ["risk_id", "scenario"])

    op.create_table(
        "risk_driver",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("correlation_default", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "correlation_default >= -1 AND correlation_default <= 1",
            name="ck_driver_correlation_range",
        ),
    )
    op.create_index("ix_risk_driver_name", "risk_driver", ["name"], unique=True)

    op.create_table(
        "risk_driver_link",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("risk_id", sa.Integer(), nullable=False),
        sa.Column("driver_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["risk_id"], ["risk.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["driver_id"], ["risk_driver.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("risk_id", "driver_id", name="uq_risk_driver_link"),
    )
    op.create_index("ix_risk_driver_link_risk_id", "risk_driver_link", ["risk_id"])
    op.create_index("ix_risk_driver_link_driver_id", "risk_driver_link", ["driver_id"])


def downgrade() -> None:
    op.drop_table("risk_driver_link")
    op.drop_table("risk_driver")
    op.drop_table("risk_quant_estimate")
    op.drop_index("ix_risk_quantify", table_name="risk")
    op.drop_column("risk", "quantify")
