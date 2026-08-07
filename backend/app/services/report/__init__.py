"""Structured reporting: one set of facts, one section registry, three renderings.

The shape of this package is the whole point of it. ``data.gather`` reads the database
once and freezes the answer; ``sections`` turns that snapshot into blocks with pure
functions; ``render_html`` and ``render_xlsx`` turn blocks into artifacts. Nothing skips a
step, so the workbook and the printed page cannot disagree about what the P80 was — and a
fourth renderer (pptx, PDF) is a new file rather than a second copy of the content.

Typical use::

    data = await gather(db, title="Monthly risk report", generated_on=date.today(),
                        run_id=42)
    document = build_document(data, sections=["cover", "basis", "cost"])
    html = render_html(document)
"""

from collections.abc import Sequence

from app.services.report.data import ReportData, gather
from app.services.report.model import Document, Section
from app.services.report.render_html import render_html
from app.services.report.render_xlsx import render_xlsx
from app.services.report.sections import (
    SECTIONS,
    SectionSpec,
    available_ids,
    build_sections,
    section_by_id,
)

__all__ = [
    "SECTIONS",
    "Document",
    "ReportData",
    "Section",
    "SectionSpec",
    "available_ids",
    "build_document",
    "build_sections",
    "gather",
    "render_html",
    "render_xlsx",
    "section_by_id",
]


def build_document(
    data: ReportData, sections: Sequence[str] | None = None
) -> Document:
    """Assemble the document. Pure — everything time-dependent is already in ``data``."""
    return Document(
        title=data.title,
        subtitle=data.subtitle,
        generated_on=data.generated_on,
        prepared_by=data.prepared_by,
        currency=data.currency,
        sections=build_sections(data, sections),
    )
