"""simulation runs

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-31

One table. The run's inputs and its result live in JSON columns rather than in a
normalised result schema, because a result is read whole or not at all: nothing queries
"every run whose P70 exceeds X" and inventing the tables to make that possible would be
inventing a requirement.

``schedule_version_id`` is ``ON DELETE SET NULL``. A run is an append-only record
(invariant 5) and deleting the schedule it ran against must not delete the evidence that
it happened; it only stops the run being replayable, which the null says.
"""

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "simulation_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column(
            "scenario",
            sa.String(length=20),
            nullable=False,
            server_default="pre_mitigation",
        ),
        sa.Column("schedule_version_id", sa.Integer(), nullable=True),
        sa.Column("dcma_run_id", sa.Integer(), nullable=True),
        sa.Column("gate_passed", sa.Boolean(), nullable=True),
        sa.Column(
            "gate_override", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("gate_override_reason", sa.Text(), nullable=True),
        sa.Column("iterations", sa.Integer(), nullable=False, server_default="10000"),
        sa.Column("seed", sa.Integer(), nullable=False, server_default="12345"),
        sa.Column("sampling", sa.String(length=10), nullable=False, server_default="lhs"),
        sa.Column("base_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("burn_rate_per_day", sa.Float(), nullable=False, server_default="0"),
        sa.Column("risk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mapped_risk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("activity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("excluded", sa.JSON(), nullable=True),
        sa.Column("assembly_notes", sa.JSON(), nullable=True),
        sa.Column("engine_version", sa.String(length=20), nullable=True),
        sa.Column("chunk_size", sa.Integer(), nullable=True),
        sa.Column("inputs_sha256", sa.String(length=64), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("task_id", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default="Unknown"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_simrun_status",
        ),
        sa.CheckConstraint("iterations >= 100", name="ck_simrun_iterations"),
        sa.CheckConstraint("base_cost >= 0", name="ck_simrun_base_cost"),
        sa.CheckConstraint("burn_rate_per_day >= 0", name="ck_simrun_burn_rate"),
        sa.ForeignKeyConstraint(
            ["schedule_version_id"], ["schedule_version.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulation_run_status", "simulation_run", ["status"])
    op.create_index("ix_simulation_run_created_at", "simulation_run", ["created_at"])
    op.create_index(
        "ix_simulation_run_schedule_version_id", "simulation_run", ["schedule_version_id"]
    )
    op.create_index("ix_simulation_run_inputs_sha256", "simulation_run", ["inputs_sha256"])
    op.create_index("ix_simrun_status_created", "simulation_run", ["status", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_simrun_status_created", table_name="simulation_run")
    op.drop_index("ix_simulation_run_inputs_sha256", table_name="simulation_run")
    op.drop_index("ix_simulation_run_schedule_version_id", table_name="simulation_run")
    op.drop_index("ix_simulation_run_created_at", table_name="simulation_run")
    op.drop_index("ix_simulation_run_status", table_name="simulation_run")
    op.drop_table("simulation_run")
