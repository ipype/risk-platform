"""What an extractor produces, and the rule every extractor obeys.

``app/ingest/`` is pure in the same sense ``app/sim/`` is: bytes in, chunks out. No
database, no network, no clock, no logging. It may import ``app.core.errors`` and nothing
else from the app. Persisting an extraction belongs to
``services/document_ingest.py``; deciding what to do with a document that yields nothing
belongs to the route. The boundary is worth keeping for the same reason it was worth
keeping around the simulation engine — an extractor that can be handed a byte string and
compared against an expected list of chunks is one that can be tested at all.

**The one rule: a chunk never spans a locator boundary.** Not a page, not a sheet, not a
table. This is the constraint that the whole corpus design falls out of, and it comes from
the ledger rather than from anything about documents: a proposal must carry at least one
evidence reference, and a reference that cannot be rendered as a single highlight in the
source is not evidence — it is a citation-shaped string. Chunking to a target character
count and letting blocks run across a page break would produce better retrieval and worse
review, and review is the thing this subsystem exists to make possible.

**Tables become rows, not prose.** A table flattened into running text loses the column a
number sat under, which is usually the entire meaning of the number. Each row is emitted
carrying its own header, so ``Consent: Env permit | Days: 90`` reads standalone in a
retrieval result and cites back to one row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Chunk",
    "Extraction",
    "PROSE",
    "TABLE_ROW",
    "TARGET_CHARS",
    "HARD_MAX_CHARS",
    "ProseAccumulator",
    "row_text",
]

PROSE = "prose"
TABLE_ROW = "table_row"

#: Where a prose block is closed if the source offers no better boundary. Not tuned
#: against a retrieval benchmark — there is no retrieval yet — so it is deliberately a
#: round number that keeps a chunk readable in a review panel without scrolling.
TARGET_CHARS = 1000

#: A single paragraph longer than this is split rather than emitted whole. Two thousand
#: characters is already past the point where a reviewer can see the cited sentence.
HARD_MAX_CHARS = 2000


@dataclass(frozen=True, slots=True)
class Chunk:
    """One citable piece of a document."""

    kind: str
    text: str
    #: Format-specific and JSON-serialisable: ``{"page", "bbox"}`` for PDF,
    #: ``{"paragraph"}`` or ``{"table", "row"}`` for Word, ``{"sheet", "cells"}`` for a
    #: workbook. Deliberately not normalised into one shape — a page bbox and a cell range
    #: have nothing in common, and a lowest-common-denominator locator would be unable to
    #: render either highlight.
    locator: dict[str, Any]
    #: Heading path where the format carries one. The single most useful thing a reviewer
    #: sees before the text itself — "§4.2 Consents" is most of a citation's worth.
    section: str | None = None
    ordinal: int = 0


@dataclass(slots=True)
class Extraction:
    """Everything one file yielded, plus what the extractor could not do."""

    chunks: list[Chunk] = field(default_factory=list)
    page_count: int | None = None
    #: Declared on the face of the result, never swallowed: a table strategy that found
    #: nothing, a sheet skipped for being empty, a paragraph split mid-sentence. The route
    #: puts these on the document so they are visible next to what was extracted.
    warnings: list[str] = field(default_factory=list)

    def add(self, chunk: Chunk) -> None:
        self.chunks.append(
            Chunk(
                kind=chunk.kind,
                text=chunk.text,
                locator=chunk.locator,
                section=chunk.section,
                ordinal=len(self.chunks),
            )
        )

    @property
    def text_chars(self) -> int:
        return sum(len(c.text) for c in self.chunks)


class ProseAccumulator:
    """Groups consecutive pieces of running text into chunks.

    Held open across pieces and closed by the caller at every locator boundary, which is
    what enforces the module's one rule. The accumulator does not know what a boundary is;
    the extractor does, and calling :meth:`flush` is how it says so.
    """

    def __init__(self, section: str | None = None) -> None:
        self._parts: list[str] = []
        self._extents: list[Any] = []
        self._section = section

    def __bool__(self) -> bool:
        return bool(self._parts)

    @property
    def chars(self) -> int:
        return sum(len(p) for p in self._parts) + max(0, len(self._parts) - 1)

    def add(self, text: str, extent: Any = None) -> None:
        text = text.strip()
        if not text:
            return
        self._parts.append(text)
        if extent is not None:
            self._extents.append(extent)

    @property
    def full(self) -> bool:
        return self.chars >= TARGET_CHARS

    def take(self) -> tuple[str, list[Any]]:
        text = "\n".join(self._parts)
        extents = list(self._extents)
        self._parts.clear()
        self._extents.clear()
        return text, extents

    @property
    def section(self) -> str | None:
        return self._section

    def set_section(self, section: str | None) -> None:
        self._section = section


def row_text(headers: list[str], cells: list[str]) -> str:
    """One table row, carrying its own headers.

    ``Consent: Env permit | Days: 90`` rather than ``Env permit | 90``. A row that has to
    be read next to a header row it was separated from is a row that reads wrong in every
    retrieval result it ever appears in, and the duplication costs a few dozen bytes.
    """
    pairs = []
    for i, cell in enumerate(cells):
        value = (cell or "").strip()
        if not value:
            continue
        header = headers[i].strip() if i < len(headers) and headers[i] else ""
        pairs.append(f"{header}: {value}" if header else value)
    return " | ".join(pairs)


def split_oversized(text: str) -> list[str]:
    """Break a single run of text that no boundary in the source ever closed.

    Split on sentence ends where there are any and on whitespace where there are not,
    because a hard character cut lands mid-word and the excerpt a reviewer sees is then
    unreadable at exactly the moment they are deciding whether to trust it.
    """
    if len(text) <= HARD_MAX_CHARS:
        return [text]

    out: list[str] = []
    remaining = text
    while len(remaining) > HARD_MAX_CHARS:
        window = remaining[:HARD_MAX_CHARS]
        cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
        if cut < HARD_MAX_CHARS // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = HARD_MAX_CHARS
        else:
            cut += 1
        out.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        out.append(remaining)
    return out
