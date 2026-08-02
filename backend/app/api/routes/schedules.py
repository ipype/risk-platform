"""Schedule upload, parsing, and the DCMA gate."""

from __future__ import annotations

from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
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
from app.services.schedule_gantt import (
    DEFAULT_GANTT_ROWS,
    MAX_GANTT_ROWS,
    GanttPayload,
    build_gantt,
)
from app.services.schedule_delete import delete_impact, delete_version
from app.services.scope import resolve_read_scope, resolve_write_scope
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


class DeleteImpactSummary(BaseModel):
    """What deleting a version would remove. Read before the confirmation, not after."""

    version_id: int
    project_name: str
    source_project_id: str
    is_current: bool
    activities: int
    relationships: int
    wbs_nodes: int
    calendars: int
    dcma_runs: int
    mappings_total: int
    mappings_accepted: int
    mappings_proposed: int
    #: Which version takes over as current. Null when this is the project's only one.
    promotes_version_id: int | None
    file_id: int
    filename: str
    file_size_bytes: int
    file_versions_remaining: int
    #: The stored bytes can go too once this version has.
    file_removable: bool
    #: Accepted mappings would be lost, so the delete needs ``force=true``.
    needs_force: bool


class DeleteResult(BaseModel):
    version_id: int
    project_name: str
    #: Rows removed, per table. Counted from the delete itself, not inferred.
    deleted: dict[str, int]
    #: ``mapping_history`` rows written on the way out. History outlives the mapping.
    mapping_history_kept: int
    promoted_version_id: int | None
    file_deleted: bool
    file_retained: str | None


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
    scope_id: int | None = None,
    file_row: ScheduleFile | None = None,
    file_created: bool = False,
) -> UploadResult | JSONResponse:
    if file_row is None:
        scope = await resolve_write_scope(db, scope_id)
        file_row, file_created = await store_file(
            db,
            filename=filename,
            content=content,
            uploaded_by=actor,
            scope_id=scope.id,
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
    scope_id: int | None = Form(default=None),
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
        db,
        content=content,
        filename=filename,
        project_id=project_id,
        actor=actor,
        scope_id=scope_id,
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
    scope_id: int | None = Query(default=None, description="Restrict to this scope and everything under it. Omitted means unfiltered."),
    db: AsyncSession = Depends(get_db),
) -> list[VersionSummary]:
    stmt = select(ScheduleVersion).order_by(ScheduleVersion.id.desc())
    if current_only:
        stmt = stmt.where(ScheduleVersion.is_current.is_(True))
    scope_ids = await resolve_read_scope(db, scope_id)
    if scope_ids is not None:
        # The scope is on the stored file, not the parse: one uploaded file can be parsed
        # more than once and every version of it belongs to whoever owns the bytes.
        stmt = stmt.join(ScheduleFile, ScheduleFile.id == ScheduleVersion.file_id).where(
            ScheduleFile.scope_id.in_(scope_ids)
        )
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


@router.get("/{version_id}/gantt")
async def get_gantt(
    version_id: int,
    wbs: str | None = Query(
        default=None, description="Restrict to this WBS node and everything under it"
    ),
    critical_only: bool = Query(default=False),
    q: str | None = Query(default=None, description="Substring match on code or name"),
    limit: int = Query(default=DEFAULT_GANTT_ROWS, ge=1, le=MAX_GANTT_ROWS),
    db: AsyncSession = Depends(get_db),
) -> GanttPayload:
    """Everything needed to draw the schedule, in display order.

    One request rather than a page walk: a Gantt is only readable when the whole ordering
    is settled, and ordering the rows client-side across pages means the WBS tree assembles
    differently depending on how far the user scrolled. Large schedules come back truncated
    with the true total, and the filters above are the way through — not a bigger page.
    """
    version = await _get_version(db, version_id)
    return await build_gantt(
        db, version, wbs=wbs, critical_only=critical_only, q=q, limit=limit
    )


@router.get("/{version_id}/relationships")
async def list_relationships(
    version_id: int,
    touching: str | None = Query(
        default=None,
        description=(
            "Activity source id. Returns only the links on either side of it — the "
            "answer to 'why does this bar sit here', without pulling the whole network."
        ),
    ),
    limit: int = Query(default=500, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _get_version(db, version_id)
    stmt = select(ScheduleRelationship).where(
        ScheduleRelationship.version_id == version_id
    )
    if touching:
        stmt = stmt.where(
            (ScheduleRelationship.predecessor_source_id == touching)
            | (ScheduleRelationship.successor_source_id == touching)
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


# --------------------------------------------------------------------------- #
# deleting
# --------------------------------------------------------------------------- #


@router.get("/{version_id}/delete-impact")
async def get_delete_impact(
    version_id: int, db: AsyncSession = Depends(get_db)
) -> DeleteImpactSummary:
    """Everything a delete would remove, counted without removing any of it.

    Separate from the ``DELETE`` on purpose. A confirmation that cannot name what it is
    about to destroy is not a confirmation, and finding out from a 409 means the analyst
    only learns the number after deciding.
    """
    version = await _get_version(db, version_id)
    impact = await delete_impact(db, version)
    return DeleteImpactSummary(
        version_id=impact.version_id,
        project_name=impact.project_name,
        source_project_id=impact.source_project_id,
        is_current=impact.is_current,
        activities=impact.activities,
        relationships=impact.relationships,
        wbs_nodes=impact.wbs_nodes,
        calendars=impact.calendars,
        dcma_runs=impact.dcma_runs,
        mappings_total=impact.mappings_total,
        mappings_accepted=impact.mappings_accepted,
        mappings_proposed=impact.mappings_proposed,
        promotes_version_id=impact.promotes_version_id,
        file_id=impact.file_id,
        filename=impact.filename,
        file_size_bytes=impact.file_size_bytes,
        file_versions_remaining=impact.file_versions_remaining,
        file_removable=impact.file_removable,
        needs_force=impact.needs_force,
    )


@router.delete("/{version_id}")
async def delete_schedule_version(
    version_id: int,
    force: bool = Query(
        default=False,
        description="Confirm that accepted risk-to-activity mappings may be deleted.",
    ),
    delete_file: bool = Query(
        default=False,
        description=(
            "Also delete the stored source bytes, if no other version was parsed from "
            "them. Ignored when another version still references the file."
        ),
    ),
    actor: str = Header(default="Unknown", alias="X-Actor"),
    db: AsyncSession = Depends(get_db),
) -> DeleteResult:
    """Delete a parsed version and everything derived from it.

    Returns 409 when accepted mappings would go with it and ``force`` is unset. The
    derived rows — activities, relationships, WBS, calendars, gate runs — are all
    reproducible from the stored file; the mappings are not, which is the only reason
    this is guarded at all.
    """
    version = await _get_version(db, version_id)
    outcome = await delete_version(
        db, version, actor=actor, force=force, delete_file=delete_file
    )
    await db.commit()
    return DeleteResult(
        version_id=outcome.version_id,
        project_name=outcome.project_name,
        deleted=outcome.deleted,
        mapping_history_kept=outcome.mapping_history_kept,
        promoted_version_id=outcome.promoted_version_id,
        file_deleted=outcome.file_deleted,
        file_retained=outcome.file_retained,
    )
