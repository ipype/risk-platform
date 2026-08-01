"""Schedule persistence.

Shape of the data, and why:

``schedule_file`` holds the uploaded bytes and never changes — ``SYSTEM.md`` requires the
source to be immutable and the parse to be a derived artifact. There is no ``updated_at``
here on purpose. Files are deduplicated by SHA-256, so re-uploading the same export
returns the existing row rather than a second copy.

``schedule_version`` is one parse of one project out of one file, and it is append-only.
Re-parsing never mutates a version; it writes a new one and demotes the old (invariant 5).
Each version records the parser version alongside the file hash, so any downstream result
can be traced back to exactly the bytes and the code that produced it (invariant 6).

Durations are stored as ``*_days`` floats paired with the calendar id they were measured
against, mirroring ``WorkingDuration``. Storing a bare day count would break the invariant
the moment two calendars are involved, which on a real project is immediately.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ScheduleFile(Base):
    """An uploaded schedule export. Write once, never update."""

    __tablename__ = "schedule_file"

    id: Mapped[int] = mapped_column(primary_key=True)

    #: The project that uploaded this file. Scope lands on the file rather than on every
    #: parse of it: ``ScheduleVersion`` reaches it through ``file_id``, and one owner for
    #: an immutable upload and all its derived parses beats two that can disagree.
    scope_id: Mapped[int] = mapped_column(
        ForeignKey("scope_node.id", ondelete="RESTRICT"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500))
    suffix: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(String(120), default="Unknown")
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ScheduleVersion(Base):
    """One parse of one project from one file. Append-only."""

    __tablename__ = "schedule_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_file.id", ondelete="RESTRICT"), index=True
    )

    source_project_id: Mapped[str] = mapped_column(String(100), index=True)
    project_name: Mapped[str] = mapped_column(String(500))
    source_format: Mapped[str] = mapped_column(String(100))
    #: Bumped whenever parsing changes in a way that alters output. Part of the
    #: reproducibility record, together with the file hash.
    parser_version: Mapped[str] = mapped_column(String(50))

    data_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baseline_finish: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    must_finish_by: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    activity_count: Mapped[int] = mapped_column(Integer, default=0)
    relationship_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Non-fatal parse problems, surfaced to the analyst rather than swallowed.
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True)

    #: Most recent parse for this source project. Older versions stay readable.
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    created_by: Mapped[str] = mapped_column(String(120), default="Unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ScheduleCalendar(Base):
    """A working-time calendar. Without it a stored duration in days is meaningless."""

    __tablename__ = "schedule_calendar"
    __table_args__ = (
        UniqueConstraint("version_id", "source_id", name="uq_schedule_calendar_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    hours_per_day: Mapped[float] = mapped_column(Float, default=8.0)
    #: Python weekday numbers, 0 = Monday.
    workdays: Mapped[list] = mapped_column(JSON, default=list)
    #: ISO date strings.
    holidays: Mapped[list] = mapped_column(JSON, default=list)
    extra_workdays: Mapped[list] = mapped_column(JSON, default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class ScheduleWbs(Base):
    __tablename__ = "schedule_wbs"
    __table_args__ = (
        UniqueConstraint("version_id", "source_id", name="uq_schedule_wbs_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(500), default="")
    parent_source_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_project_node: Mapped[bool] = mapped_column(Boolean, default=False)


class ScheduleActivity(Base):
    __tablename__ = "schedule_activity"
    __table_args__ = (
        UniqueConstraint("version_id", "source_id", name="uq_schedule_activity_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(500), default="")

    calendar_source_id: Mapped[str] = mapped_column(String(100))
    wbs_source_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)

    type: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)

    #: The calendar every ``*_days`` value below was measured against. Kept explicit so a
    #: day count can never be read without knowing what a day means here.
    duration_calendar_id: Mapped[str] = mapped_column(String(100))
    original_duration_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    remaining_duration_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_float_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    free_float_days: Mapped[float | None] = mapped_column(Float, nullable=True)

    early_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    early_finish: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    late_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    late_finish: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baseline_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    baseline_finish: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_finish: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    constraint_type: Mapped[str] = mapped_column(String(40), default="none")
    constraint_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    secondary_constraint_type: Mapped[str] = mapped_column(String(40), default="none")
    secondary_constraint_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    is_critical: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    has_resource_assignment: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Minor currency units. BigInteger because a capital project in cents overflows int32.
    budgeted_cost: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ScheduleRelationship(Base):
    __tablename__ = "schedule_relationship"
    __table_args__ = (
        UniqueConstraint(
            "version_id", "source_id", name="uq_schedule_relationship_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(String(200))
    predecessor_source_id: Mapped[str] = mapped_column(String(100), index=True)
    successor_source_id: Mapped[str] = mapped_column(String(100), index=True)
    type: Mapped[str] = mapped_column(String(4), default="FS")
    lag_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    lag_calendar_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class DcmaRun(Base):
    """One execution of the 14-point gate against one version. Append-only.

    The full report is stored rather than recomputed on read: thresholds are adjustable,
    so a run is a record of what was decided at a point in time and under which tolerances.
    Re-running writes a new row.
    """

    __tablename__ = "dcma_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("schedule_version.id", ondelete="CASCADE"), index=True
    )
    gate_passed: Mapped[bool] = mapped_column(Boolean, index=True)
    passed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    not_assessed_count: Mapped[int] = mapped_column(Integer, default=0)
    blocking_failures: Mapped[list | None] = mapped_column(JSON, nullable=True)
    thresholds: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    run_by: Mapped[str] = mapped_column(String(120), default="Unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
