"""per-dimension bound interpretation, and a per-risk base for percentage costs

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-07

Two independent gaps, one migration because they are two columns on one table and
splitting them would cost a second deploy for nothing.

**Per-dimension bounds.** ``bound_interpretation`` records how a session was run and stays
where it is, but it could not express a pair the shape split already permits: a schedule
bound that is a contract milestone and therefore absolute, beside a cost bound the SME gave
as a defensible P10/P90. That combination is not merely awkward under one shared value, it
is *rejected* — ``triangular`` refuses a percentile interpretation and ``trigen`` refuses an
absolute one, so there is no legal way to encode it. The new columns are overrides and are
deliberately left NULL: NULL means "however the session was run", so every existing row
keeps the exact interpretation it was validated and simulated under, and no run recorded
before today changes its answer.

**A base for percentage costs.** ``cost_basis = 'pct_of_base'`` has been storable since
0011 with nothing to be a percentage *of*; the engine has always fallen back to the run's
``base_cost``. That is right for a risk scaling with the whole project and wrong, by the
ratio between them, for one scaling with a single package — a 10% overrun on a 2m civils
package charged against a 40m project base comes out twenty times too large, and nothing in
the output says so. ``RiskInput.cost_base_reference`` already existed to receive this; it
simply had no source. NULL keeps the old fallback, so again nothing already stored moves.

Additive and nullable throughout, so the downgrade is a clean drop with no data to
reconstruct.
"""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for dim in ("cost", "sched"):
        op.add_column(
            "risk_quant_estimate",
            sa.Column(f"{dim}_bound_interpretation", sa.String(length=20), nullable=True),
        )

    op.add_column(
        "risk_quant_estimate", sa.Column("cost_base_value", sa.Float(), nullable=True)
    )
    # NULL passes a CHECK, which is exactly the semantic wanted: no base recorded means
    # "use the run's", and a recorded base of zero would make the risk contribute nothing.
    op.create_check_constraint(
        "ck_quant_cost_base_value", "risk_quant_estimate", "cost_base_value > 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_quant_cost_base_value", "risk_quant_estimate", type_="check")
    op.drop_column("risk_quant_estimate", "cost_base_value")
    for dim in ("cost", "sched"):
        op.drop_column("risk_quant_estimate", f"{dim}_bound_interpretation")
