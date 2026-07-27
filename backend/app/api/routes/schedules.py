"""Schedule upload, parsing, and the DCMA gate."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AmbiguousProjectError
from app.db.session import get_db
from app.models.schedule import (
    DcmaRun,
    ScheduleActivity,
    ScheduleFile,
    ScheduleRelationship,
    ScheduleVersion,
)
from app.schedule.dcma import DcmaThresholds
from app.schedule.parsers import parse_schedule, parser_for, supported_formats
from app.services.schedule_ingest import (
    MAX_UPLOAD_BYTES,
    create_version,
    latest_gate,
    run_gate,
    store_file,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])

MAX_PAGE_SIZE = 1000


# --------------------------------------------------------------------------- #
# response shapes
# --------------------------------------------------------------------------- #


class GateSummary(BaseModel):
    run_id: int
    gate_passed: bool
    passed: int
    failed: int
    not_assessed: int
    blocking_failures: list[int]


class VersionSummary(BaseModel):
    id: int
    file_id: int
    source_project_id: str
    project_name: str
    source_format: str
    parser_version: str
    data_date: datetime | None
    must_finish_by: datetime | None
    activity_count: int
    relationship_count: int
    warnings: list[str]
    is_current: bool
    created_by: str
    created_at: datetime


class UploadResult(BaseModel):
    version: VersionSummary
    gate: GateSummary
    file_created: bool = Field(
        description="False when these exact bytes had already been uploaded."
    )


class ThresholdOverride(BaseModel):
    """Optional tolerance overrides for a gate re-run.

    Only the fields supplied are changed; the rest keep DCMA's published values.
    """

    thresholds: dict = Field(default_factory=dict)
    actor: str = "Unknown"


def _version_summary(version: ScheduleVersion) -> VersionSummary:
    return VersionSummary(
        id=version.id,
        file_id=version.file_id,
        source_project_id=version.source_project_id,
        project_name=version.project_name,
        source_format=version.source_format,
        parser_version=version.parser_version,
        data_date=version.data_date,
        must_finish_by=version.must_finish_by,
        activity_count=version.activity_count,
        relationship_count=version.relationship_count,
        warnings=list(version.warnings or []),
        is_current=version.is_current,
        created_by=version.created_by,
        created_at=version.created_at,
    )


def _gate_summary(run: DcmaRun) -> GateSummary:
    return GateSummary(
        run_id=run.id,
        gate_passed=run.gate_passed,
        passed=run.passed_count,
        failed=run.failed_count,
        not_assessed=run.not_assessed_count,
        blocking_failures=list(run.blocking_failures or []),
    )


async def _get_version(db: AsyncSession, version_id: int) -> ScheduleVersion:
    version = await db.get(ScheduleVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail=f"No schedule version {version_id}")
    return version


# --------------------------------------------------------------------------- #
# static paths first — otherwise they get swallowed by /{version_id}
# --------------------------------------------------------------------------- #


@router.get("/formats")
async def list_formats() -> list[dict]:
    """Which schedule formats exist, and which this deployment can actually read."""
    return supported_formats()


# --------------------------------------------------------------------------- #
# upload and parse
# --------------------------------------------------------------------------- #


async def _ingest(
    db: AsyncSession,
    *,
    content: bytes,
    filename: str,
    project_id: str | None,
    actor: str,
    file_row: ScheduleFile | None = None,
    file_created: bool = False,
) -> UploadResult | JSONResponse:
    if file_row is None:
        file_row, file_created = await store_file(
            db, filename=filename, content=content, uploaded_by=actor
        )
        # Commit the source before parsing. If the file turns out to hold several
        # projects, the client can pick one and finish the job by id instead of
        # re-uploading tens of megabytes.
        await db.commit()
        await db.refresh(file_row)

    try:
        schedule = parse_schedule(content, filename, project_id=project_id)
    except AmbiguousProjectError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "error": "ambiguous_project",
                "detail": str(exc),
                "file_id": file_row.id,
                "projects": [
                    {"id": pid, "name": name, "activity_count": count}
                    for pid, name, count in exc.candidates
                ],
            },
        )

    version = await create_version(
        db, file=file_row, schedule=schedule, created_by=actor
    )
    run = await run_gate(db, version=version, run_by=actor)
    await db.commit()
    await db.refresh(version)
    await db.refresh(run)

    return UploadResult(
        version=_version_summary(version),
        gate=_gate_summary(run),
        file_created=file_created,
    )


@router.post("/upload")
async def upload_schedule(
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
    actor: str = Form(default="Unknown"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a schedule, parse it, and run the DCMA gate in one call.

    Returns 409 with the list of projects — and the stored ``file_id`` — when the export
    holds more than one. Finish with ``POST /schedules/files/{file_id}/parse``.
    """
    filename = file.filename or "upload"
    # Reject the format before reading the body into memory, so a 400 MB .mpp does not
    # get buffered only to be refused.
    parser_for(filename)

    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File is {len(content) / 1_048_576:.1f} MB; the limit is "
                f"{MAX_UPLOAD_BYTES // 1_048_576} MB."
            ),
        )

    return await _ingest(
        db, content=content, filename=filename, project_id=project_id, actor=actor
    )


@router.post("/files/{file_id}/parse")
async def parse_stored_file(
    file_id: int,
    project_id: str = Query(..., description="Which project in the file to parse"),
    actor: str = Query(default="Unknown"),
    db: AsyncSession = Depends(get_db),
):
    """Parse an already-uploaded file, choosing one project out of several."""
    file_row = await db.get(ScheduleFile, file_id)
    if file_row is None:
        raise HTTPException(status_code=404, detail=f"No schedule file {file_id}")

    return await _ingest(
        db,
        content=file_row.content,
        filename=file_row.filename,
        project_id=project_id,
        actor=actor,
        file_row=file_row,
    )


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #


@router.get("")
async def list_versions(
    current_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[VersionSummary]:
    stmt = select(ScheduleVersion).order_by(ScheduleVersion.id.desc())
    if current_only:
        stmt = stmt.where(ScheduleVersion.is_current.is_(True))
    rows = (await db.scalars(stmt.limit(limit).offset(offset))).all()
    return [_version_summary(row) for row in rows]


@router.get("/{version_id}")
async def get_version(
    version_id: int, db: AsyncSession = Depends(get_db)
) -> VersionSummary:
    return _version_summary(await _get_version(db, version_id))


@router.get("/{version_id}/activities")
async def list_activities(
    version_id: int,
    status: str | None = Query(default=None),
    type: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Substring match on code or name"),
    limit: int = Query(default=200, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _get_version(db, version_id)

    stmt = select(ScheduleActivity).where(ScheduleActivity.version_id == version_id)
    if status:
        stmt = stmt.where(ScheduleActivity.status == status)
    if type:
        stmt = stmt.where(ScheduleActivity.type == type)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            ScheduleActivity.code.ilike(pattern) | ScheduleActivity.name.ilike(pattern)
        )

    total = await db.scalar(
        select(func.count()).select_from(stmt.subquery())
    )
    rows = (
        await db.scalars(stmt.order_by(ScheduleActivity.id).limit(limit).offset(offset))
    ).all()

    return {
        "total": total or 0,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": row.id,
                "source_id": row.source_id,
                "code": row.code,
                "name": row.name,
                "type": row.type,
                "status": row.status,
                "wbs_source_id": row.wbs_source_id,
                # days are meaningless without the calendar they were measured on
                "duration_calendar_id": row.duration_calendar_id,
                "original_duration_days": row.original_duration_days,
                "remaining_duration_days": row.remaining_duration_days,
                "total_float_days": row.total_float_days,
                "free_float_days": row.free_float_days,
                "early_start": row.early_start,
                "early_finish": row.early_finish,
                "baseline_finish": row.baseline_finish,
                "actual_start": row.actual_start,
                "actual_finish": row.actual_finish,
                "constraint_type": row.constraint_type,
                "is_critical": row.is_critical,
                "has_resource_assignment": row.has_resource_assignment,
                "budgeted_cost": row.budgeted_cost,
            }
            for row in rows
        ],
    }


@router.get("/{version_id}/relationships")
async def list_relationships(
    version_id: int,
    limit: int = Query(default=500, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _get_version(db, version_id)
    stmt = select(ScheduleRelationship).where(
        ScheduleRelationship.version_id == version_id
    )
    total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = (
        await db.scalars(
            stmt.order_by(ScheduleRelationship.id).limit(limit).offset(offset)
        )
    ).all()
    return {
        "total": total or 0,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": row.id,
                "source_id": row.source_id,
                "predecessor_source_id": row.predecessor_source_id,
                "successor_source_id": row.successor_source_id,
                "type": row.type,
                "lag_days": row.lag_days,
                "lag_calendar_id": row.lag_calendar_id,
            }
            for row in rows
        ],
    }


@router.get("/{version_id}/source")
async def download_source(version_id: int, db: AsyncSession = Depends(get_db)) -> Response:
    """The original uploaded bytes, unchanged. Kept for audit and reproducibility."""
    version = await _get_version(db, version_id)
    file_row = await db.get(ScheduleFile, version.file_id)
    if file_row is None:  # pragma: no cover - FK prevents this
        raise HTTPException(status_code=404, detail="Source file missing")
    return Response(
        content=file_row.content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{file_row.filename}"',
            "X-Content-SHA256": file_row.content_sha256,
        },
    )


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #


@router.get("/{version_id}/dcma")
async def get_dcma(version_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """The most recent gate run for this version, with the full 14-check report."""
    await _get_version(db, version_id)
    run = await latest_gate(db, version_id)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail="No DCMA run for this version yet. POST to this path to run one.",
        )
    return {
        "run_id": run.id,
        "version_id": run.version_id,
        "gate_passed": run.gate_passed,
        "blocking_failures": run.blocking_failures,
        "thresholds": run.thresholds,
        "run_by": run.run_by,
        "created_at": run.created_at,
        "report": run.report,
    }


@router.post("/{version_id}/dcma")
async def rerun_dcma(
    version_id: int,
    body: ThresholdOverride | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Re-run the gate, optionally under different tolerances.

    Append-only: this never overwrites a previous run. A gate decision is a record of
    what was accepted, by whom, and under which thresholds.
    """
    version = await _get_version(db, version_id)
    payload = body or ThresholdOverride()

    try:
        thresholds = DcmaThresholds(**payload.thresholds) if payload.thresholds else None
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid thresholds: {exc}") from exc

    run = await run_gate(db, version=version, thresholds=thresholds, run_by=payload.actor)
    await db.commit()
    await db.refresh(run)
    return {
        "run_id": run.id,
        "version_id": run.version_id,
        "gate_passed": run.gate_passed,
        "blocking_failures": run.blocking_failures,
        "thresholds": run.thresholds,
        "report": run.report,
    }
