"""risk to activity mapping

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-29

"""

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_activity_mapping",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("risk_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("mapping_type", sa.String(length=30), nullable=False),
        sa.Column("activity_source_id", sa.String(length=100), nullable=True),
        sa.Column("predecessor_source_id", sa.String(length=100), nullable=True),
        sa.Column("successor_source_id", sa.String(length=100), nullable=True),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("allocation_pct", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="proposed"),
        sa.Column("origin", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("suggestion_score", sa.Float(), nullable=True),
        sa.Column("suggestion_signals", sa.JSON(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("proposed_by", sa.String(length=120), nullable=False, server_default="Unknown"),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("carried_from_id", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(["version_id"], ["schedule_version.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_risk_activity_mapping_risk_id", "risk_activity_mapping", ["risk_id"])
    op.create_index("ix_risk_activity_mapping_version_id", "risk_activity_mapping", ["version_id"])
    op.create_index(
        "ix_risk_activity_mapping_mapping_type", "risk_activity_mapping", ["mapping_type"]
    )
    op.create_index("ix_risk_activity_mapping_status", "risk_activity_mapping", ["status"])
    op.create_index("ix_risk_activity_mapping_created_at", "risk_activity_mapping", ["created_at"])
    op.create_index("ix_ram_version_risk", "risk_activity_mapping", ["version_id", "risk_id"])
    op.create_index("ix_ram_version_status", "risk_activity_mapping", ["version_id", "status"])
    op.create_index(
        "ix_ram_version_activity", "risk_activity_mapping", ["version_id", "activity_source_id"]
    )

    op.create_table(
        "mapping_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mapping_id", sa.Integer(), nullable=False),
        sa.Column("risk_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False, server_default="Unknown"),
        sa.Column("changes", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_mapping_history_mapping_id", "mapping_history", ["mapping_id"])
    op.create_index("ix_mapping_history_risk_id", "mapping_history", ["risk_id"])
    op.create_index("ix_mapping_history_version_id", "mapping_history", ["version_id"])
    op.create_index("ix_mapping_history_created_at", "mapping_history", ["created_at"])

    op.create_table(
        "mapping_suggestion_outcome",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("risk_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("subcategory_id", sa.Integer(), nullable=False),
        sa.Column("activity_source_id", sa.String(length=100), nullable=False),
        sa.Column("activity_tokens", sa.JSON(), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("actor", sa.String(length=120), nullable=False, server_default="Unknown"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_mapping_suggestion_outcome_risk_id", "mapping_suggestion_outcome", ["risk_id"]
    )
    op.create_index(
        "ix_mapping_suggestion_outcome_version_id", "mapping_suggestion_outcome", ["version_id"]
    )
    op.create_index(
        "ix_mapping_suggestion_outcome_subcategory_id",
        "mapping_suggestion_outcome",
        ["subcategory_id"],
    )
    op.create_index(
        "ix_mapping_suggestion_outcome_created_at", "mapping_suggestion_outcome", ["created_at"]
    )
    op.create_index(
        "ix_mso_subcategory", "mapping_suggestion_outcome", ["subcategory_id", "outcome"]
    )


def downgrade() -> None:
    op.drop_table("mapping_suggestion_outcome")
    op.drop_table("mapping_history")
    op.drop_table("risk_activity_mapping")
