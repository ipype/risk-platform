"""0018 — a queued run can be cancelled.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-04

Invariant 5 says a run is never edited and never deleted through the API — the record of
what was asked and what came back is permanent, and ``test_a_run_cannot_be_deleted`` holds
the API to it. Cancellation does not touch that record. It only exists for a run still
sitting in ``queued``, before anything has come back, and it adds a fact rather than
removing one: the row still says what was asked, and now also says that it was withdrawn,
by whom, and when. ``cancelled`` joins ``succeeded`` and ``failed`` as a terminal state —
nothing transitions out of any of the three, and the cancel route enforces that a run must
still be ``queued`` to reach it.

Two nullable columns rather than reusing ``finished_at``/``error``: those are written by
the worker and mean "the engine reached a terminal state." A cancelled run was never
touched by the worker, and conflating the two would make ``finished_at`` non-null mean two
different things depending on which other column you check it against.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

TABLE = "simulation_run"
CONSTRAINT = "ck_simrun_status"

OLD_STATUSES = "status IN ('queued', 'running', 'succeeded', 'failed')"
NEW_STATUSES = "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(
            sa.Column("cancelled_by", sa.String(length=120), nullable=True)
        )
        batch.add_column(
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, NEW_STATUSES)


def downgrade() -> None:
    # A downgrade with a cancelled row present would violate the constraint it is about
    # to restore. That is correct: 'cancelled' is data this schema version cannot hold,
    # the same shape of refusal 0017's downgrade documents for its own constraint.
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(CONSTRAINT, type_="check")
        batch.create_check_constraint(CONSTRAINT, OLD_STATUSES)
        batch.drop_column("cancelled_at")
        batch.drop_column("cancelled_by")
