"""Plain text and Markdown, and the paste path that stands in for web ingestion.

Fetching a web page is deliberately not built. It drags in a fetcher, a robots policy, auth
walls, and a rendering question (half of what a standards body publishes is behind a login
or assembled by JavaScript), and it buys very little that this does not: the real case is a
person reading a permit condition or a specification clause and wanting it in the corpus.
Pasting it takes them four seconds and carries no failure modes at all. If a crawler is
ever wanted, it produces bytes and calls this.

Markdown headings are read as section paths, so a pasted specification keeps its structure.
Nothing else about Markdown is interpreted — the text is evidence, not a document to render.
"""

from __future__ import annotations

import re

from app.core.errors import DocumentHasNoText
from app.ingest.types import (
    PROSE,
    Chunk,
    Extraction,
    ProseAccumulator,
    split_oversized,
)

SUFFIXES = (".txt", ".md", ".markdown")

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def extract(data: bytes, *, filename: str = "") -> Extraction:
    # ``errors="replace"`` rather than a chain of codec guesses: a document with a handful
    # of mangled characters is still evidence, and refusing the whole file over one bad
    # byte helps nobody. Anything worse than that is visible in the excerpt.
    text = data.decode("utf-8", errors="replace")
    return extract_text(text, filename=filename)


def extract_text(text: str, *, filename: str = "") -> Extraction:
    out = Extraction()
    accumulator = ProseAccumulator()
    headings: list[tuple[int, str]] = []
    start_line = 1

    lines = text.splitlines()
    for number, raw in enumerate(lines, start=1):
        match = _HEADING.match(raw)
        if match is not None:
            _emit(accumulator, start_line, number - 1, out)
            level = len(match.group(1))
            headings = [h for h in headings if h[0] < level]
            headings.append((level, match.group(2)))
            accumulator.set_section(_path(headings))
            out.add(
                Chunk(
                    kind=PROSE,
                    text=match.group(2),
                    locator={"line_start": number, "line_end": number},
                    section=_path(headings),
                )
            )
            start_line = number + 1
            continue

        if not raw.strip():
            if accumulator.full:
                _emit(accumulator, start_line, number - 1, out)
                start_line = number + 1
            continue

        if not accumulator:
            start_line = number
        accumulator.add(raw)

    _emit(accumulator, start_line, len(lines), out)

    if not out.chunks:
        raise DocumentHasNoText(filename or "text", "The text is empty.")
    return out


def _path(headings: list[tuple[int, str]]) -> str | None:
    return " › ".join(t for _, t in headings) or None


def _emit(
    accumulator: ProseAccumulator, start: int, end: int, out: Extraction
) -> None:
    if not accumulator:
        return
    section = accumulator.section
    body, _ = accumulator.take()
    for part in split_oversized(body):
        out.add(
            Chunk(
                kind=PROSE,
                text=part,
                locator={"line_start": start, "line_end": max(start, end)},
                section=section,
            )
        )
