"""Word extraction: paragraphs and tables, in document order, carrying heading paths.

**The heading path is the point.** A permit clause cited as "paragraph 214" is a citation
nobody can check; the same clause cited as "4.2 Consents › Validity" is one a reviewer
resolves without opening the file. Word is the only format here that reliably carries that
structure, so it is tracked as a stack and stamped onto every chunk beneath it.

**A heading closes the open block.** Not because a heading is 1000 characters from the
last one, but because text under a different heading is about a different thing, and a
chunk that straddles the boundary cites to whichever heading the accumulator happened to
be holding.

**Body order, not object order.** ``python-docx`` exposes ``paragraphs`` and ``tables`` as
two separate sequences, so reading them in turn puts every table at the end of the
document regardless of where it sat. The body's XML children are walked instead, which is
the only way a table that interrupts a clause stays interrupting it.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import DocumentHasNoText, DocumentUnreadable
from app.ingest.types import (
    PROSE,
    TABLE_ROW,
    Chunk,
    Extraction,
    ProseAccumulator,
    row_text,
    split_oversized,
)

SUFFIXES = (".docx",)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract(data: bytes, *, filename: str = "") -> Extraction:
    import io

    from docx import Document as WordDocument
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = WordDocument(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - python-docx raises several unrelated types
        raise DocumentUnreadable(filename or "Word document", str(exc)) from exc

    out = Extraction()
    accumulator = ProseAccumulator()
    headings: list[tuple[int, str]] = []
    paragraph_index = 0
    table_index = 0

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == f"{_W}p":
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            level = _heading_level(paragraph)
            if level is not None and text:
                _emit_prose(accumulator, out)
                headings = [h for h in headings if h[0] < level]
                headings.append((level, text))
                accumulator.set_section(_path(headings))
                # The heading itself is emitted as prose. It is often the most searchable
                # sentence in the section and always the shortest way to describe it.
                out.add(
                    Chunk(
                        kind=PROSE,
                        text=text,
                        locator={"paragraph": paragraph_index},
                        section=_path(headings),
                    )
                )
            elif text:
                accumulator.add(text, paragraph_index)
                if accumulator.full:
                    _emit_prose(accumulator, out)
            paragraph_index += 1

        elif child.tag == f"{_W}tbl":
            _emit_prose(accumulator, out)
            _table(Table(child, document), table_index, _path(headings), out)
            table_index += 1

    _emit_prose(accumulator, out)

    if not out.chunks:
        raise DocumentHasNoText(
            filename or "Word document",
            "The file opened but holds no text. An empty document, or one whose content "
            "is entirely images.",
        )
    return out


def _heading_level(paragraph: Any) -> int | None:
    """Depth from the paragraph style, or ``None`` for body text.

    Read from the style name rather than from ``outlineLvl``: a document that has been
    through three authors usually has its headings applied as named styles and its outline
    levels left at whatever the template said.
    """
    name = (paragraph.style.name or "") if paragraph.style is not None else ""
    if name == "Title":
        return 0
    if name.startswith("Heading "):
        try:
            return int(name.split(" ", 1)[1])
        except (IndexError, ValueError):
            return None
    return None


def _path(headings: list[tuple[int, str]]) -> str | None:
    return " › ".join(text for _, text in headings) or None


def _emit_prose(accumulator: ProseAccumulator, out: Extraction) -> None:
    if not accumulator:
        return
    section = accumulator.section
    text, extents = accumulator.take()
    locator: dict[str, Any] = {}
    if extents:
        locator = {"paragraph": extents[0], "paragraph_end": extents[-1]}
    for part in split_oversized(text):
        out.add(Chunk(kind=PROSE, text=part, locator=dict(locator), section=section))


def _table(table: Any, index: int, section: str | None, out: Extraction) -> None:
    rows = [[cell.text for cell in row.cells] for row in table.rows]
    if not rows:
        out.warnings.append(f"Table {index} holds no rows.")
        return
    headers = [c.strip() for c in rows[0]]
    for row_index, cells in enumerate(rows[1:], start=1):
        text = row_text(headers, cells)
        if not text:
            continue
        out.add(
            Chunk(
                kind=TABLE_ROW,
                text=text,
                locator={"table": index, "row": row_index},
                section=section,
            )
        )
