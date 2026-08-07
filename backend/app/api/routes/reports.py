"""Structured report export.

Four endpoints over one build:

  ``GET /reports/sections``      what this report *could* contain, and why not otherwise
  ``GET /reports/report.json``   the document as structured blocks
  ``GET /reports/report.html``   one self-contained printable file
  ``GET /reports/report.xlsx``   the same document as a workbook

``/sections`` exists so the picker in the UI is driven by the data rather than by a
hardcoded list that drifts. It answers for the *same* parameters the render endpoints take,
so "Schedule outcome — this run simulated cost only" is a live answer about the selected
run, not a general statement about the platform.

Naming a run fixes the scope: see ``services/report/data.gather``. Nothing here refuses a
run that failed or is still queued — the basis section prints its status and every section
that needs a result marks itself unavailable, which is more use to somebody working out
what happened than a 409 is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services.matrix_export import OVERALL
from app.services.report import (
    SECTIONS,
    ReportData,
    build_document,
    gather,
    render_html,
    render_xlsx,
)
from app.services.report.model import Document

router = APIRouter(prefix="/reports", tags=["reports"])

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DEFAULT_TITLE = "Quantitative risk analysis report"


@dataclass(frozen=True)
class ReportParams:
    title: str
    prepared_by: str
    currency: str
    run_id: int | None
    scope_id: int | None
    roi_id: int | None
    plan_id: int | None
    lens: str
    basis: str
    sections: list[str] | None


def report_params(
    title: str = Query(default=DEFAULT_TITLE, max_length=160),
    prepared_by: str = Query(default="", max_length=120),
    currency: str = Query(
        default="",
        max_length=4,
        description="Symbol printed before money figures. Blank prints plain numbers.",
    ),
    run_id: int | None = Query(
        default=None,
        description="Simulation run to report on. Fixes the scope to that run's project.",
    ),
    scope_id: int | None = Query(
        default=None,
        description="Restrict to this scope and everything under it. Ignored when a run "
        "is named.",
    ),
    roi_id: int | None = Query(default=None, description="Mitigation ROI comparison"),
    plan_id: int | None = Query(
        default=None, description="Mitigation plan. Taken from the ROI comparison if omitted."
    ),
    lens: str = Query(default=OVERALL, description="Impact area code, or the overall lens"),
    basis: Literal["current", "target"] = Query(default="current"),
    section: list[str] | None = Query(
        default=None,
        description="Section ids to include. Repeatable. Omitted means every available "
        "section, in registry order.",
    ),
) -> ReportParams:
    return ReportParams(
        title=title.strip() or DEFAULT_TITLE,
        prepared_by=prepared_by.strip(),
        currency=currency.strip(),
        run_id=run_id,
        scope_id=scope_id,
        roi_id=roi_id,
        plan_id=plan_id,
        lens=lens,
        basis=basis,
        sections=section,
    )


class SectionOption(BaseModel):
    id: str
    title: str
    summary: str
    available: bool
    #: Present only when ``available`` is false. A sentence the picker can show as-is.
    reason: str | None = None


class SectionsResponse(BaseModel):
    #: Echoed so a UI can label the picker with what it is actually describing.
    scope_id: int | None = None
    run_id: int | None = None
    generated_on: date
    sections: list[SectionOption]
    notes: list[str] = []


async def _data(db: AsyncSession, params: ReportParams) -> ReportData:
    try:
        return await gather(
            db,
            title=params.title,
            prepared_by=params.prepared_by,
            currency=params.currency,
            generated_on=date.today(),
            run_id=params.run_id,
            scope_id=params.scope_id,
            roi_id=params.roi_id,
            plan_id=params.plan_id,
            lens=params.lens,
            basis=params.basis,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _document(data: ReportData, params: ReportParams) -> Document:
    document = build_document(data, params.sections)
    if not document.sections:
        raise HTTPException(
            status_code=422,
            detail="No requested section has anything to report. Call /reports/sections "
            "for what is available against these parameters.",
        )
    return document


def _slug(data: ReportData) -> str:
    name = data.scope.name if data.scope else "all-scopes"
    cleaned = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "report"


def _filename(data: ReportData, extension: str) -> str:
    return f"risk_report_{_slug(data)}_{data.generated_on.isoformat()}.{extension}"


@router.get("/sections", response_model=SectionsResponse)
async def sections(
    db: AsyncSession = Depends(get_db),
    params: ReportParams = Depends(report_params),
) -> SectionsResponse:
    """What this report can contain right now, and the reason for anything it cannot."""
    data = await _data(db, params)
    options = []
    for spec in SECTIONS:
        reason = spec.unavailable(data)
        options.append(
            SectionOption(
                id=spec.id,
                title=spec.title,
                summary=spec.summary,
                available=reason is None,
                reason=reason,
            )
        )
    return SectionsResponse(
        scope_id=data.scope.id if data.scope else None,
        run_id=data.run.id if data.run else None,
        generated_on=data.generated_on,
        sections=options,
        notes=list(data.notes),
    )


@router.get("/report.json", response_model=Document)
async def report_json(
    db: AsyncSession = Depends(get_db),
    params: ReportParams = Depends(report_params),
) -> Document:
    """The document as structured blocks — what a renderer that isn't ours would consume."""
    data = await _data(db, params)
    return _document(data, params)


@router.get("/report.html")
async def report_html(
    db: AsyncSession = Depends(get_db),
    params: ReportParams = Depends(report_params),
    download: bool = Query(
        default=False,
        description="Attach rather than display. The default renders in a browser tab or "
        "an iframe preview.",
    ),
) -> Response:
    data = await _data(db, params)
    html = render_html(_document(data, params))
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{_filename(data, "html")}"'
    return Response(content=html, media_type="text/html; charset=utf-8", headers=headers)


@router.get("/report.xlsx")
async def report_xlsx(
    db: AsyncSession = Depends(get_db),
    params: ReportParams = Depends(report_params),
) -> Response:
    data = await _data(db, params)
    payload = render_xlsx(_document(data, params))
    return Response(
        content=payload,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{_filename(data, "xlsx")}"'
        },
    )
