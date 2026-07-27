"""schedule ingestion: files, versions, activities, relationships, DCMA runs

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "schedule_file",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("suffix", sa.String(length=20), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=120), nullable=False, server_default="Unknown"),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_schedule_file_suffix", "schedule_file", ["suffix"])
    op.create_index("ix_schedule_file_uploaded_at", "schedule_file", ["uploaded_at"])
    # dedupe identical uploads: the same export mailed round twice is one source of truth
    op.create_index(
        "ix_schedule_file_content_sha256", "schedule_file", ["content_sha256"], unique=True
    )

    op.create_table(
        "schedule_version",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_id", sa.Integer(), nullable=False),
        sa.Column("source_project_id", sa.String(length=100), nullable=False),
        sa.Column("project_name", sa.String(length=500), nullable=False),
        sa.Column("source_format", sa.String(length=100), nullable=False),
        sa.Column("parser_version", sa.String(length=50), nullable=False),
        sa.Column("data_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_finish", sa.DateTime(timezone=True), nullable=True),
        sa.Column("must_finish_by", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relationship_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(length=120), nullable=False, server_default="Unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["schedule_file.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_schedule_version_file_id", "schedule_version", ["file_id"])
    op.create_index(
        "ix_schedule_version_source_project_id", "schedule_version", ["source_project_id"]
    )
    op.create_index("ix_schedule_version_is_current", "schedule_version", ["is_current"])
    op.create_index("ix_schedule_version_created_at", "schedule_version", ["created_at"])

    op.create_table(
        "schedule_calendar",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("hours_per_day", sa.Float(), nullable=False, server_default="8"),
        sa.Column("workdays", sa.JSON(), nullable=False),
        sa.Column("holidays", sa.JSON(), nullable=False),
        sa.Column("extra_workdays", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["version_id"], ["schedule_version.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("version_id", "source_id", name="uq_schedule_calendar_source"),
    )
    op.create_index("ix_schedule_calendar_version_id", "schedule_calendar", ["version_id"])

    op.create_table(
        "schedule_wbs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("parent_source_id", sa.String(length=100), nullable=True),
        sa.Column("is_project_node", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(["version_id"], ["schedule_version.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("version_id", "source_id", name="uq_schedule_wbs_source"),
    )
    op.create_index("ix_schedule_wbs_version_id", "schedule_wbs", ["version_id"])

    op.create_table(
        "schedule_activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("calendar_source_id", sa.String(length=100), nullable=False),
        sa.Column("wbs_source_id", sa.String(length=100), nullable=True),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        # a day count is only meaningful alongside the calendar that defines a day
        sa.Column("duration_calendar_id", sa.String(length=100), nullable=False),
        sa.Column("original_duration_days", sa.Float(), nullable=True),
        sa.Column("remaining_duration_days", sa.Float(), nullable=True),
        sa.Column("total_float_days", sa.Float(), nullable=True),
        sa.Column("free_float_days", sa.Float(), nullable=True),
        sa.Column("early_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("early_finish", sa.DateTime(timezone=True), nullable=True),
        sa.Column("late_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("late_finish", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_finish", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_finish", sa.DateTime(timezone=True), nullable=True),
        sa.Column("constraint_type", sa.String(length=40), nullable=False, server_default="none"),
        sa.Column("constraint_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("secondary_constraint_type", sa.String(length=40), nullable=False, server_default="none"),
        sa.Column("secondary_constraint_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_critical", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_resource_assignment", sa.Boolean(), nullable=False, server_default=sa.false()),
        # minor currency units; int32 overflows on a capital project in cents
        sa.Column("budgeted_cost", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["schedule_version.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("version_id", "source_id", name="uq_schedule_activity_source"),
    )
    op.create_index("ix_schedule_activity_version_id", "schedule_activity", ["version_id"])
    op.create_index("ix_schedule_activity_source_id", "schedule_activity", ["source_id"])
    op.create_index("ix_schedule_activity_code", "schedule_activity", ["code"])
    op.create_index("ix_schedule_activity_type", "schedule_activity", ["type"])
    op.create_index("ix_schedule_activity_status", "schedule_activity", ["status"])
    op.create_index("ix_schedule_activity_wbs_source_id", "schedule_activity", ["wbs_source_id"])
    op.create_index("ix_schedule_activity_is_critical", "schedule_activity", ["is_critical"])

    op.create_table(
        "schedule_relationship",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("predecessor_source_id", sa.String(length=100), nullable=False),
        sa.Column("successor_source_id", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=4), nullable=False, server_default="FS"),
        sa.Column("lag_days", sa.Float(), nullable=True),
        sa.Column("lag_calendar_id", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["version_id"], ["schedule_version.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("version_id", "source_id", name="uq_schedule_relationship_source"),
    )
    op.create_index("ix_schedule_relationship_version_id", "schedule_relationship", ["version_id"])
    op.create_index(
        "ix_schedule_relationship_predecessor", "schedule_relationship", ["predecessor_source_id"]
    )
    op.create_index(
        "ix_schedule_relationship_successor", "schedule_relationship", ["successor_source_id"]
    )

    op.create_table(
        "dcma_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("gate_passed", sa.Boolean(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_assessed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocking_failures", sa.JSON(), nullable=True),
        sa.Column("thresholds", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("run_by", sa.String(length=120), nullable=False, server_default="Unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["schedule_version.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_dcma_run_version_id", "dcma_run", ["version_id"])
    op.create_index("ix_dcma_run_gate_passed", "dcma_run", ["gate_passed"])
    op.create_index("ix_dcma_run_created_at", "dcma_run", ["created_at"])


def downgrade() -> None:
    op.drop_table("dcma_run")
    op.drop_table("schedule_relationship")
    op.drop_table("schedule_activity")
    op.drop_table("schedule_wbs")
    op.drop_table("schedule_calendar")
    op.drop_table("schedule_version")
    op.drop_table("schedule_file")
