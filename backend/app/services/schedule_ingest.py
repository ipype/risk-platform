"""Ingestion orchestration: bytes in, persisted version out, gate run.

This is the only module that knows both the pure domain (``app.schedule``) and the
database. Keeping the seam here is what lets ``app.schedule`` stay property-testable and
lets a parse be reproduced from the stored bytes alone.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schedule import (
    DcmaRun,
    ScheduleActivity,
    ScheduleCalendar,
    ScheduleFile,
    ScheduleRelationship,
    ScheduleVersion,
    ScheduleWbs,
)
from app.schedule.dcma import DcmaThresholds, run_dcma
from app.schedule.model import (
    Activity,
    ActivityStatus,
    ActivityType,
    ConstraintType,
    Relationship,
    RelationshipType,
    Schedule,
    WbsNode,
    WorkCalendar,
    WorkingDuration,
)

#: Bump when a parsing change alters output. Stored on every version so a result can be
#: traced to the code that produced it, not just the bytes.
PARSER_VERSION = "1"

#: Rejected before anything is read into memory.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #


async def store_file(
    db: AsyncSession, *, filename: str, content: bytes, uploaded_by: str = "Unknown"
) -> tuple[ScheduleFile, bool]:
    """Store an upload, deduplicating on content hash.

    Returns ``(row, created)``. Re-uploading identical bytes is a no-op that returns the
    original row — the same export mailed round twice should not become two sources of
    truth.
    """
    digest = hashlib.sha256(content).hexdigest()
    existing = await db.scalar(
        select(ScheduleFile).where(ScheduleFile.content_sha256 == digest)
    )
    if existing is not None:
        return existing, False

    row = ScheduleFile(
        filename=filename,
        suffix=Path(filename).suffix.lower(),
        content=content,
        content_sha256=digest,
        size_bytes=len(content),
        uploaded_by=uploaded_by,
    )
    db.add(row)
    await db.flush()
    return row, True


# --------------------------------------------------------------------------- #
# versions
# --------------------------------------------------------------------------- #


def _duration_days(duration: WorkingDuration | None) -> float | None:
    return duration.days if duration is not None else None


async def create_version(
    db: AsyncSession,
    *,
    file: ScheduleFile,
    schedule: Schedule,
    created_by: str = "Unknown",
) -> ScheduleVersion:
    """Persist a parsed schedule as a new version and demote any previous one."""
    await db.execute(
        update(ScheduleVersion)
        .where(
            ScheduleVersion.source_project_id == schedule.project_id,
            ScheduleVersion.is_current.is_(True),
        )
        .values(is_current=False)
    )

    version = ScheduleVersion(
        file_id=file.id,
        source_project_id=schedule.project_id,
        project_name=schedule.project_name,
        source_format=schedule.source_format,
        parser_version=PARSER_VERSION,
        data_date=schedule.data_date,
        baseline_finish=schedule.baseline_finish,
        must_finish_by=schedule.must_finish_by,
        activity_count=len(schedule.activities),
        relationship_count=len(schedule.relationships),
        warnings=list(schedule.warnings),
        is_current=True,
        created_by=created_by,
    )
    db.add(version)
    await db.flush()

    db.add_all(
        ScheduleCalendar(
            version_id=version.id,
            source_id=calendar.id,
            name=calendar.name,
            hours_per_day=calendar.hours_per_day,
            workdays=sorted(calendar.workdays),
            holidays=sorted(d.isoformat() for d in calendar.holidays),
            extra_workdays=sorted(d.isoformat() for d in calendar.extra_workdays),
            is_default=calendar.is_default,
        )
        for calendar in schedule.calendars
    )

    db.add_all(
        ScheduleWbs(
            version_id=version.id,
            source_id=node.id,
            code=node.code,
            name=node.name,
            parent_source_id=node.parent_id,
            is_project_node=node.is_project_node,
        )
        for node in schedule.wbs
    )

    db.add_all(
        ScheduleActivity(
            version_id=version.id,
            source_id=activity.id,
            code=activity.code,
            name=activity.name,
            calendar_source_id=activity.calendar_id,
            wbs_source_id=activity.wbs_id,
            type=activity.type.value,
            status=activity.status.value,
            duration_calendar_id=activity.calendar_id,
            original_duration_days=_duration_days(activity.original_duration),
            remaining_duration_days=_duration_days(activity.remaining_duration),
            total_float_days=_duration_days(activity.total_float),
            free_float_days=_duration_days(activity.free_float),
            early_start=activity.early_start,
            early_finish=activity.early_finish,
            late_start=activity.late_start,
            late_finish=activity.late_finish,
            baseline_start=activity.baseline_start,
            baseline_finish=activity.baseline_finish,
            actual_start=activity.actual_start,
            actual_finish=activity.actual_finish,
            constraint_type=activity.constraint_type.value,
            constraint_date=activity.constraint_date,
            secondary_constraint_type=activity.secondary_constraint_type.value,
            secondary_constraint_date=activity.secondary_constraint_date,
            is_critical=activity.is_critical,
            has_resource_assignment=activity.has_resource_assignment,
            budgeted_cost=activity.budgeted_cost,
        )
        for activity in schedule.activities
    )

    db.add_all(
        ScheduleRelationship(
            version_id=version.id,
            source_id=relationship.id,
            predecessor_source_id=relationship.predecessor_id,
            successor_source_id=relationship.successor_id,
            type=relationship.type.value,
            lag_days=_duration_days(relationship.lag),
            lag_calendar_id=relationship.lag.calendar_id if relationship.lag else None,
        )
        for relationship in schedule.relationships
    )

    await db.flush()
    return version


async def hydrate(db: AsyncSession, version: ScheduleVersion) -> Schedule:
    """Rebuild the canonical model from stored rows.

    Everything downstream — the gate, mapping, simulation — runs off this rather than
    re-reading the source file, so the round trip has to be lossless. There is a test that
    asserts exactly that.
    """
    calendars = (
        await db.scalars(
            select(ScheduleCalendar)
            .where(ScheduleCalendar.version_id == version.id)
            .order_by(ScheduleCalendar.source_id)
        )
    ).all()
    wbs = (
        await db.scalars(
            select(ScheduleWbs)
            .where(ScheduleWbs.version_id == version.id)
            .order_by(ScheduleWbs.id)
        )
    ).all()
    activities = (
        await db.scalars(
            select(ScheduleActivity)
            .where(ScheduleActivity.version_id == version.id)
            .order_by(ScheduleActivity.id)
        )
    ).all()
    relationships = (
        await db.scalars(
            select(ScheduleRelationship)
            .where(ScheduleRelationship.version_id == version.id)
            .order_by(ScheduleRelationship.id)
        )
    ).all()

    def duration(days: float | None, calendar_id: str) -> WorkingDuration | None:
        if days is None:
            return None
        return WorkingDuration(days=days, calendar_id=calendar_id)

    return Schedule(
        project_id=version.source_project_id,
        project_name=version.project_name,
        data_date=version.data_date,
        baseline_finish=version.baseline_finish,
        must_finish_by=version.must_finish_by,
        source_format=version.source_format,
        calendars=tuple(
            WorkCalendar(
                id=row.source_id,
                name=row.name,
                hours_per_day=row.hours_per_day,
                workdays=frozenset(row.workdays or ()),
                holidays=frozenset(date.fromisoformat(d) for d in (row.holidays or ())),
                extra_workdays=frozenset(
                    date.fromisoformat(d) for d in (row.extra_workdays or ())
                ),
                is_default=row.is_default,
            )
            for row in calendars
        ),
        wbs=tuple(
            WbsNode(
                id=row.source_id,
                code=row.code,
                name=row.name,
                parent_id=row.parent_source_id,
                is_project_node=row.is_project_node,
            )
            for row in wbs
        ),
        activities=tuple(
            Activity(
                id=row.source_id,
                code=row.code,
                name=row.name,
                calendar_id=row.calendar_source_id,
                wbs_id=row.wbs_source_id,
                type=ActivityType(row.type),
                status=ActivityStatus(row.status),
                original_duration=duration(
                    row.original_duration_days, row.duration_calendar_id
                ),
                remaining_duration=duration(
                    row.remaining_duration_days, row.duration_calendar_id
                ),
                total_float=duration(row.total_float_days, row.duration_calendar_id),
                free_float=duration(row.free_float_days, row.duration_calendar_id),
                early_start=row.early_start,
                early_finish=row.early_finish,
                late_start=row.late_start,
                late_finish=row.late_finish,
                baseline_start=row.baseline_start,
                baseline_finish=row.baseline_finish,
                actual_start=row.actual_start,
                actual_finish=row.actual_finish,
                constraint_type=ConstraintType(row.constraint_type),
                constraint_date=row.constraint_date,
                secondary_constraint_type=ConstraintType(row.secondary_constraint_type),
                secondary_constraint_date=row.secondary_constraint_date,
                is_critical=row.is_critical,
                has_resource_assignment=row.has_resource_assignment,
                budgeted_cost=row.budgeted_cost,
            )
            for row in activities
        ),
        relationships=tuple(
            Relationship(
                id=row.source_id,
                predecessor_id=row.predecessor_source_id,
                successor_id=row.successor_source_id,
                type=RelationshipType(row.type),
                lag=duration(row.lag_days, row.lag_calendar_id or ""),
            )
            for row in relationships
        ),
        warnings=tuple(version.warnings or ()),
    )


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #


async def run_gate(
    db: AsyncSession,
    *,
    version: ScheduleVersion,
    thresholds: DcmaThresholds | None = None,
    run_by: str = "Unknown",
) -> DcmaRun:
    """Run the 14-point gate against a stored version and record the result."""
    schedule = await hydrate(db, version)
    report = run_dcma(schedule, thresholds)

    row = DcmaRun(
        version_id=version.id,
        gate_passed=report.gate_passed,
        passed_count=report.passed_count,
        failed_count=report.failed_count,
        not_assessed_count=report.not_assessed_count,
        blocking_failures=[c.number for c in report.blocking_failures],
        thresholds=report.thresholds.model_dump(mode="json"),
        report=report.model_dump(mode="json"),
        run_by=run_by,
    )
    db.add(row)
    await db.flush()
    return row


async def latest_gate(db: AsyncSession, version_id: int) -> DcmaRun | None:
    return await db.scalar(
        select(DcmaRun)
        .where(DcmaRun.version_id == version_id)
        .order_by(DcmaRun.created_at.desc(), DcmaRun.id.desc())
        .limit(1)
    )
