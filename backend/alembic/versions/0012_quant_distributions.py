"""per-dimension distributions, cumulative and discrete points, per-point rationale

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-31

Moves ``dist_type`` and ``pert_lambda`` from the estimate onto each dimension. A risk's
cost and schedule impacts routinely take different shapes, and once one of them can be a
cumulative curve while the other is a three-point, a single shared shape column cannot
express the row at all.

Existing rows carry their old shape onto whichever dimensions actually hold numbers, so
nothing already elicited changes meaning. A dimension with no values becomes ``none``
rather than inheriting a shape it never had.
"""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- new per-dimension columns -------------------------------------------------
    for dim in ("cost", "sched"):
        op.add_column(
            "risk_quant_estimate",
            sa.Column(f"{dim}_dist", sa.String(length=20), nullable=False, server_default="none"),
        )
        op.add_column(
            "risk_quant_estimate",
            sa.Column(
                f"{dim}_pert_lambda", sa.Float(), nullable=False, server_default="4.0"
            ),
        )
        op.add_column("risk_quant_estimate", sa.Column(f"{dim}_points", sa.JSON(), nullable=True))
        op.add_column(
            "risk_quant_estimate", sa.Column(f"{dim}_rationale", sa.JSON(), nullable=True)
        )

    # -- carry the old shared shape onto the dimensions that hold numbers ----------
    op.execute(
        """
        UPDATE risk_quant_estimate
           SET cost_dist = CASE
                   WHEN cost_min IS NULL AND cost_ml IS NULL AND cost_max IS NULL THEN 'none'
                   WHEN dist_type IN ('none', 'discrete') THEN 'pert'
                   ELSE dist_type
               END,
               sched_dist = CASE
                   WHEN sched_min IS NULL AND sched_ml IS NULL AND sched_max IS NULL THEN 'none'
                   WHEN dist_type IN ('none', 'discrete') THEN 'pert'
                   ELSE dist_type
               END,
               cost_pert_lambda = pert_lambda,
               sched_pert_lambda = pert_lambda
        """
    )

    # -- retire the shared shape ---------------------------------------------------
    op.drop_constraint("ck_quant_pert_lambda", "risk_quant_estimate", type_="check")
    op.drop_column("risk_quant_estimate", "dist_type")
    op.drop_column("risk_quant_estimate", "pert_lambda")

    op.create_check_constraint(
        "ck_quant_cost_lambda", "risk_quant_estimate", "cost_pert_lambda > 0"
    )
    op.create_check_constraint(
        "ck_quant_sched_lambda", "risk_quant_estimate", "sched_pert_lambda > 0"
    )
    # Uniform leaves the mode NULL, so min <= ml <= max no longer chains min to max.
    op.create_check_constraint(
        "ck_quant_cost_min_max", "risk_quant_estimate", "cost_min <= cost_max"
    )
    op.create_check_constraint(
        "ck_quant_sched_min_max", "risk_quant_estimate", "sched_min <= sched_max"
    )


def downgrade() -> None:
    op.drop_constraint("ck_quant_sched_min_max", "risk_quant_estimate", type_="check")
    op.drop_constraint("ck_quant_cost_min_max", "risk_quant_estimate", type_="check")
    op.drop_constraint("ck_quant_sched_lambda", "risk_quant_estimate", type_="check")
    op.drop_constraint("ck_quant_cost_lambda", "risk_quant_estimate", type_="check")

    op.add_column(
        "risk_quant_estimate",
        sa.Column("dist_type", sa.String(length=20), nullable=False, server_default="pert"),
    )
    op.add_column(
        "risk_quant_estimate",
        sa.Column("pert_lambda", sa.Float(), nullable=False, server_default="4.0"),
    )
    # Cost wins the collapse: it is the dimension every estimate is most likely to hold.
    op.execute(
        """
        UPDATE risk_quant_estimate
           SET dist_type = CASE
                   WHEN cost_dist <> 'none' THEN cost_dist
                   WHEN sched_dist <> 'none' THEN sched_dist
                   ELSE 'pert'
               END,
               pert_lambda = cost_pert_lambda
        """
    )
    # Shapes that 0011 never knew about collapse to the nearest thing it did.
    op.execute(
        "UPDATE risk_quant_estimate SET dist_type = 'triangular' WHERE dist_type = 'trigen'"
    )
    op.execute(
        "UPDATE risk_quant_estimate SET dist_type = 'pert' "
        "WHERE dist_type IN ('cumulative', 'uniform')"
    )
    op.create_check_constraint("ck_quant_pert_lambda", "risk_quant_estimate", "pert_lambda > 0")

    for dim in ("cost", "sched"):
        op.drop_column("risk_quant_estimate", f"{dim}_rationale")
        op.drop_column("risk_quant_estimate", f"{dim}_points")
        op.drop_column("risk_quant_estimate", f"{dim}_pert_lambda")
        op.drop_column("risk_quant_estimate", f"{dim}_dist")
