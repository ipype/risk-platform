"""PDF extraction: tables first, then the prose that is left.

**Two passes per page, in that order.** ``find_tables()`` runs first and its bounding boxes
are then excluded from the word list the prose pass sees. Running prose first and tables
second would emit every table cell twice — once shredded into a sentence that reads as
nonsense, once as a row — and the shredded copy is the one that retrieves best, because it
is longer.

**A PDF with no text layer is refused, not ingested.** A scan is a picture of a document,
and pdfplumber returns an empty string for it rather than an error. Ingesting it would
create a document with zero chunks that looks successful in the list, retrieves nothing
forever, and gives a reviewer no reason to suspect the file was the problem. OCR is a
deliberate non-goal here: it is a different dependency, a different failure mode, and a
different quality conversation.

**Table detection is line-based, which is pdfplumber's default and the conservative
choice.** A text-alignment strategy finds tables in ordinary paragraphs, and a false
positive here does more damage than a false negative: a paragraph misread as a table is
emitted as garbage rows, while a borderless table missed is still extracted as prose and
still retrievable. When no table is found on a page carrying one, that is a warning on the
extraction rather than a silent difference.
"""

from __future__ import annotations

import io
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

SUFFIXES = (".pdf",)

#: A word is treated as part of a table when its midpoint falls inside a table's bbox.
#: Midpoint rather than full containment: pdfplumber's table bbox is drawn on the ruling
#: lines, and a word touching the line would otherwise escape the exclusion and be emitted
#: twice.
_TOLERANCE = 0.5


def extract(data: bytes, *, filename: str = "") -> Extraction:
    import pdfplumber

    out = Extraction()
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            out.page_count = len(pdf.pages)
            for index, page in enumerate(pdf.pages, start=1):
                _page(page, index, out)
    except (DocumentHasNoText, DocumentUnreadable):
        raise
    except Exception as exc:  # noqa: BLE001 - pdfminer raises a wide, undocumented family
        raise DocumentUnreadable(filename or "PDF", str(exc)) from exc

    if not out.chunks:
        raise DocumentHasNoText(
            filename or "PDF",
            "No text layer. This is almost certainly a scan; it needs OCR before it can "
            "be read, which this deployment does not do.",
        )
    return out


def _page(page: Any, number: int, out: Extraction) -> None:
    tables = page.find_tables()
    boxes = [t.bbox for t in tables]

    for table_index, table in enumerate(tables):
        rows = table.extract()
        if not rows:
            out.warnings.append(f"Page {number}: a table was found but held no rows.")
            continue
        headers = [(c or "").strip() for c in rows[0]]
        for row_index, cells in enumerate(rows[1:], start=1):
            text = row_text(headers, [(c or "") for c in cells])
            if not text:
                continue
            out.add(
                Chunk(
                    kind=TABLE_ROW,
                    text=text,
                    locator={
                        "page": number,
                        "table": table_index,
                        "row": row_index,
                        "bbox": [round(v, 2) for v in table.bbox],
                    },
                )
            )

    words = [w for w in page.extract_words() if not _inside(w, boxes)]
    if not words:
        return

    accumulator = ProseAccumulator()
    for line, extent in _lines(words):
        accumulator.add(line, extent)
        if accumulator.full:
            _emit(accumulator, number, out)
    # Closed at the page edge, always. A chunk that ran onto the next page could not be
    # rendered as one highlight, which is the whole rule.
    _emit(accumulator, number, out)


def _lines(words: list[dict]) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Group words into visual lines by their vertical position.

    Rounded to the nearest point rather than compared exactly: two words set on the same
    baseline routinely differ in ``top`` by a fraction, and an exact comparison turns one
    line into several one-word lines whose bboxes are each too small to highlight.
    """
    buckets: dict[int, list[dict]] = {}
    for word in words:
        buckets.setdefault(round(word["top"]), []).append(word)

    out = []
    for top in sorted(buckets):
        row = sorted(buckets[top], key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in row)
        extent = (
            min(w["x0"] for w in row),
            min(w["top"] for w in row),
            max(w["x1"] for w in row),
            max(w["bottom"] for w in row),
        )
        out.append((text, extent))
    return out


def _emit(accumulator: ProseAccumulator, page: int, out: Extraction) -> None:
    if not accumulator:
        return
    text, extents = accumulator.take()
    bbox = _union(extents)
    for part in split_oversized(text):
        out.add(
            Chunk(
                kind=PROSE,
                text=part,
                locator={"page": page, "bbox": bbox},
            )
        )


def _union(extents: list[tuple[float, float, float, float]]) -> list[float] | None:
    if not extents:
        return None
    return [
        round(min(e[0] for e in extents), 2),
        round(min(e[1] for e in extents), 2),
        round(max(e[2] for e in extents), 2),
        round(max(e[3] for e in extents), 2),
    ]


def _inside(word: dict, boxes: list[tuple[float, float, float, float]]) -> bool:
    x = (word["x0"] + word["x1"]) / 2
    y = (word["top"] + word["bottom"]) / 2
    return any(
        x0 - _TOLERANCE <= x <= x1 + _TOLERANCE and y0 - _TOLERANCE <= y <= y1 + _TOLERANCE
        for x0, y0, x1, y1 in boxes
    )
