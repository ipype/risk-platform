"""Excel export of the risk register, mitigation actions, risk matrix, and RBS reference."""

from datetime import date, datetime
from io import BytesIO

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.custom_fields import get_custom_fields
from app.models.matrix import get_active_config
from app.models.mitigation import MitigationAction
from app.models.rbs import RbsCategory, RbsSubcategory
from app.models.risk import Risk

router = APIRouter(prefix="/export", tags=["export"])

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")


def _fmt(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value if value is not None else ""


def _autosize(ws: Worksheet, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _header_row(ws: Worksheet, headers: list[str]) -> None:
    ws.append(headers)
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    ws.freeze_panes = "A2"


def _build_register_sheet(
    ws: Worksheet,
    risk_rows: list[tuple[Risk, RbsSubcategory, RbsCategory]],
    areas: list[dict],
    custom_fields: list[dict],
    action_counts: dict[int, int],
    band_color: dict[str, str],
) -> None:
    area_codes = [a["code"] for a in areas]
    area_names = [a["name"] for a in areas]

    headers = (
        [
            "Risk Code",
            "Category",
            "Subcategory",
            "Title",
            "Description",
            "Causes",
            "Consequences",
            "Status",
            "Owner",
            "Last Review Date",
            "Current Probability",
            "Current Overall Impact",
            "Current Risk Level",
        ]
        + [f"Current {name}" for name in area_names]
        + [
            "Target Probability",
            "Target Overall Impact",
            "Target Risk Level",
        ]
        + [f"Target {name}" for name in area_names]
        + [f["label"] for f in custom_fields]
        + ["Mitigation Actions", "Comments", "Created At", "Updated At"]
    )
    _header_row(ws, headers)

    idx_current_level = headers.index("Current Risk Level") + 1
    idx_target_level = headers.index("Target Risk Level") + 1
    wrap_cols = [
        headers.index("Description") + 1,
        headers.index("Causes") + 1,
        headers.index("Consequences") + 1,
        headers.index("Comments") + 1,
    ]

    for risk, subcat, cat in risk_rows:
        cur_scores = risk.impact_scores or {}
        tgt_scores = risk.target_impact_scores or {}
        row = (
            [
                risk.risk_code,
                cat.name,
                f"{subcat.code} - {subcat.name}",
                risk.title,
                risk.description or "",
                risk.causes or "",
                risk.consequences or "",
                risk.status,
                risk.owner or "",
                _fmt(risk.last_review_date),
                risk.probability,
                risk.impact,
                risk.risk_level,
            ]
            + [cur_scores.get(code) for code in area_codes]
            + [
                risk.target_probability,
                risk.target_impact,
                risk.target_risk_level,
            ]
            + [tgt_scores.get(code) for code in area_codes]
            + [(risk.custom_fields or {}).get(f["key"]) for f in custom_fields]
            + [
                action_counts.get(risk.id, 0),
                risk.comments or "",
                _fmt(risk.created_at),
                _fmt(risk.updated_at),
            ]
        )
        ws.append(row)
        r = ws.max_row
        if risk.risk_level in band_color:
            ws.cell(row=r, column=idx_current_level).fill = PatternFill(
                "solid", fgColor=band_color[risk.risk_level]
            )
        if risk.target_risk_level in band_color:
            ws.cell(row=r, column=idx_target_level).fill = PatternFill(
                "solid", fgColor=band_color[risk.target_risk_level]
            )
        for col in wrap_cols:
            ws.cell(row=r, column=col).alignment = WRAP

    widths = [16, 20, 28, 30] + [40, 30, 30] + [12, 14, 16] + [16, 18, 16]
    widths += [14] * len(area_names) * 2
    widths += [16] * len(custom_fields)
    widths += [16, 40, 18, 18]
    _autosize(ws, widths[: len(headers)])


def _build_mitigations_sheet(
    ws: Worksheet, actions: list[tuple[MitigationAction, Risk]]
) -> None:
    headers = [
        "Risk Code",
        "Risk Title",
        "Action",
        "Owner",
        "Due Date",
        "Budget",
        "Completion %",
        "Effectiveness",
        "Status",
    ]
    _header_row(ws, headers)
    for action, risk in actions:
        ws.append(
            [
                risk.risk_code,
                risk.title,
                action.action,
                action.owner or "",
                _fmt(action.due_date),
                action.budget,
                action.completion_pct,
                action.effectiveness or "",
                action.status,
            ]
        )
        ws.cell(row=ws.max_row, column=3).alignment = WRAP
    _autosize(ws, [14, 30, 40, 16, 12, 12, 12, 14, 14])


def _build_matrix_sheet(
    ws: Worksheet,
    risk_rows: list[tuple[Risk, RbsSubcategory, RbsCategory]],
    prob_levels: list[dict],
    impact_levels: list[dict],
    bands: list[dict],
) -> None:
    prob_sorted = sorted(prob_levels, key=lambda level: level["level"])
    impact_sorted = sorted(impact_levels, key=lambda level: level["level"], reverse=True)

    counts: dict[tuple[int, int], int] = {}
    for risk, _subcat, _cat in risk_rows:
        if risk.probability and risk.impact:
            key = (risk.probability, risk.impact)
            counts[key] = counts.get(key, 0) + 1

    def color_for(score: int) -> str:
        for band in bands:
            if band["min_score"] <= score <= band["max_score"]:
                return band["color"].lstrip("#")
        return "FFFFFF"

    ws.append(["Impact \\ Probability"] + [p["label"] for p in prob_sorted])
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT

    for il in impact_sorted:
        ws.append(
            [il["label"]] + [counts.get((pl["level"], il["level"]), 0) for pl in prob_sorted]
        )
        r = ws.max_row
        ws.cell(row=r, column=1).font = Font(bold=True)
        for c_idx, pl in enumerate(prob_sorted, start=2):
            score = pl["level"] * il["level"]
            cell = ws.cell(row=r, column=c_idx)
            cell.fill = PatternFill("solid", fgColor=color_for(score))
            cell.alignment = Alignment(horizontal="center")

    grid_end_row = ws.max_row
    ws.freeze_panes = "B2"
    _autosize(ws, [22] + [14] * len(prob_sorted))

    legend_row = grid_end_row + 2
    ws.cell(row=legend_row, column=1, value="Legend").font = Font(bold=True)
    for i, band in enumerate(bands, start=1):
        name_cell = ws.cell(row=legend_row + i, column=1, value=band["name"])
        name_cell.fill = PatternFill("solid", fgColor=band["color"].lstrip("#"))
        ws.cell(row=legend_row + i, column=2, value=f"{band['min_score']}-{band['max_score']}")


def _build_rbs_sheet(ws: Worksheet, categories: list[RbsCategory]) -> None:
    headers = [
        "Category Code",
        "Category Name",
        "Subcategory Code",
        "Subcategory Name",
        "Description",
        "Full Prefix",
    ]
    _header_row(ws, headers)
    for cat in categories:
        for sub in cat.subcategories:
            ws.append(
                [
                    cat.code,
                    cat.name,
                    sub.code,
                    sub.name,
                    sub.description or "",
                    f"{cat.code}-{sub.code}",
                ]
            )
            ws.cell(row=ws.max_row, column=5).alignment = WRAP
    _autosize(ws, [14, 26, 16, 40, 50, 14])


@router.get("/register.xlsx")
async def export_register(db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    risk_rows = (
        (
            await db.execute(
                select(Risk, RbsSubcategory, RbsCategory)
                .join(RbsSubcategory, RbsSubcategory.id == Risk.subcategory_id)
                .join(RbsCategory, RbsCategory.id == RbsSubcategory.category_id)
                .order_by(Risk.risk_code)
            )
        )
        .all()
    )

    actions = (
        (
            await db.execute(
                select(MitigationAction, Risk)
                .join(Risk, Risk.id == MitigationAction.risk_id)
                .order_by(Risk.risk_code, MitigationAction.sort_order)
            )
        )
        .all()
    )

    categories = (
        (
            await db.execute(
                select(RbsCategory)
                .options(selectinload(RbsCategory.subcategories))
                .order_by(RbsCategory.sort_order)
            )
        )
        .scalars()
        .all()
    )

    config = await get_active_config(db)
    custom_fields = (await get_custom_fields(db))["fields"]

    areas = config.get("impact_areas", [])
    prob_levels = config.get("probability_levels", [])
    impact_levels = config.get("impact_levels", [])
    bands = config.get("bands", [])
    band_color = {b["name"]: b["color"].lstrip("#") for b in bands}

    action_counts: dict[int, int] = {}
    for _action, risk in actions:
        action_counts[risk.id] = action_counts.get(risk.id, 0) + 1

    wb = Workbook()
    register_ws = wb.active
    register_ws.title = "Risk Register"
    _build_register_sheet(
        register_ws, risk_rows, areas, custom_fields, action_counts, band_color
    )

    _build_mitigations_sheet(wb.create_sheet("Mitigation Actions"), actions)
    _build_matrix_sheet(
        wb.create_sheet("Risk Matrix"), risk_rows, prob_levels, impact_levels, bands
    )
    _build_rbs_sheet(wb.create_sheet("RBS Reference"), categories)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"risk_register_{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
