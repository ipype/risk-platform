"""Risk-to-activity mapping.

A mapping answers *where a risk lands on the network* and nothing else. It does not carry
distribution parameters — how much delay, with what shape, belongs to quantitative
elicitation. Keeping the two apart means re-mapping a risk never silently discards an
elicited range, and re-eliciting never invalidates a mapping decision.

Three mapping types, because a schedule risk reaches the network in exactly three ways:

``duration_driver``
    The risk stretches an existing activity. One sampled factor per risk per iteration,
    applied to every activity the risk drives — which is the Hulett risk-driver semantic
    and the reason those activities come out correlated without anyone building a
    correlation matrix by hand. There is deliberately no per-activity allocation here:
    splitting the factor across drivers would break exactly the property that makes the
    method work.

``inserted_activity``
    The risk adds work that is not in the schedule — a rework loop, a resubmission, a
    stand-down. Modelled as a conditional activity between a named predecessor and
    successor. Allocation *is* meaningful here: a risk quantified as "60 days total"
    spread over three insertion points is not 60 days at each.

``scoped_driver``
    A duration driver aimed at a filter rather than a list — a WBS branch, a calendar,
    an activity-code value. Resolved at read time, so a re-parse that adds activities to
    the branch picks them up instead of quietly under-covering. Same sampling semantic as
    ``duration_driver``.

State: every mapping starts ``proposed`` (invariant 4) whether a human or the suggestion
engine put it there, and only ``accepted`` rows are visible to simulation. The signals
that produced a suggestion are kept on the row after acceptance, so a reviewer can ask
why this activity six months later and get an answer.
"""

from __future__ import annotations

from datetime import datetime

from app.db.base_class import Base
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

# --------------------------------------------------------------------------- #
# vocabularies — plain strings, not native enums, so adding a value later is a
# code change rather than a migration against a live table
# --------------------------------------------------------------------------- #

MAPPING_TYPES = ("duration_driver", "inserted_activity", "scoped_driver")
MAPPING_STATUSES = ("proposed", "accepted", "rejected", "superseded")
MAPPING_ORIGINS = ("suggested", "manual", "carried_forward")

#: Fields whose change is worth a history row.
TRACKED_MAPPING_FIELDS = [
    "mapping_type",
    "status",
    "activity_source_id",
    "predecessor_source_id",
    "successor_source_id",
    "scope",
    "allocation_pct",
    "rationale",
]


class RiskActivityMapping(Base):
    """One landing point for one risk on one parsed schedule version."""

    __tablename__ = "risk_activity_mapping"
    __table_args__ = (
        Index("ix_ram_version_risk", "version_id", "risk_id"),
        Index("ix_ram_version_status", "version_id", "status"),
        Index("ix_ram_version_activity", "version_id", "activity_source_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    risk_id: Mapped[int] = mapped_column(ForeignKey("risk.id", ondelete="CASCADE"), index=True)
    #: Mappings are version-scoped. A re-parse writes a new version and mappings are
    #: carried forward explicitly rather than inherited, so nobody ever simulates against
    #: a mapping that was made on a different network.
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"), index=True
    )

    mapping_type: Mapped[str] = mapped_column(String(30), index=True)

    # -- duration_driver -------------------------------------------------- #
    #: ``ScheduleActivity.source_id`` within this version.
    activity_source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # -- inserted_activity ------------------------------------------------ #
    predecessor_source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    successor_source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # -- scoped_driver ---------------------------------------------------- #
    #: ``{"field": "wbs"|"activity_type"|"calendar"|"name", "op": "equals"|
    #: "starts_with"|"contains", "value": "..."}``. Resolved on read, never frozen,
    #: so the branch picking up new activities is a feature rather than a silent gap.
    scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    #: Share of the risk's schedule impact landing here. Only meaningful for
    #: ``inserted_activity``; null everywhere else on purpose, so a reader cannot mistake
    #: a driver mapping for something that gets divided up.
    allocation_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # -- proposal state --------------------------------------------------- #
    status: Mapped[str] = mapped_column(
        String(20), default="proposed", server_default="proposed", index=True
    )
    origin: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")

    #: Blended relevance at the moment of suggestion, 0..1. Kept after acceptance.
    suggestion_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Per-signal breakdown behind ``suggestion_score``. The audit answer to "why this
    #: activity", which a single blended number cannot give.
    suggestion_signals: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    proposed_by: Mapped[str] = mapped_column(String(120), default="Unknown")
    decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    #: Set when this row was produced by carrying a mapping onto a newer version.
    carried_from_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MappingHistory(Base):
    """One row per create / update / delete. Append-only, survives the mapping."""

    __tablename__ = "mapping_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    mapping_id: Mapped[int] = mapped_column(Integer, index=True)
    risk_id: Mapped[int] = mapped_column(Integer, index=True)
    version_id: Mapped[int] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(120), default="Unknown")
    changes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class MappingSuggestionOutcome(Base):
    """What the analyst did with a suggestion, kept whether or not a mapping resulted.

    Rejections are the valuable half. Without them the precedent signal only ever learns
    what was accepted and drifts into confirming its own past output.
    """

    __tablename__ = "mapping_suggestion_outcome"
    __table_args__ = (Index("ix_mso_subcategory", "subcategory_id", "outcome"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_id: Mapped[int] = mapped_column(Integer, index=True)
    version_id: Mapped[int] = mapped_column(Integer, index=True)
    subcategory_id: Mapped[int] = mapped_column(Integer, index=True)
    activity_source_id: Mapped[str] = mapped_column(String(100))
    #: Lowercased, stopworded name tokens of the activity — the unit the precedent
    #: signal actually learns over. Stored rather than recomputed because the activity
    #: belongs to a version that may later be superseded.
    activity_tokens: Mapped[list | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20))  # accepted / rejected
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    actor: Mapped[str] = mapped_column(String(120), default="Unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


def mapping_snapshot(m: RiskActivityMapping) -> dict:
    return {f: getattr(m, f, None) for f in TRACKED_MAPPING_FIELDS}


def diff_mapping(old: dict, new: dict) -> list[dict]:
    return [
        {"field": f, "old": old.get(f), "new": new.get(f)}
        for f in TRACKED_MAPPING_FIELDS
        if old.get(f) != new.get(f)
    ]
