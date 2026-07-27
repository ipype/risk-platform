"""Parser registry — the single place that maps a filename to a reader."""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ParserUnavailable, UnsupportedScheduleFormat
from app.schedule.model import Schedule
from app.schedule.parsers.base import ScheduleParser
from app.schedule.parsers.mpp import MppParser
from app.schedule.parsers.xer import XerParser

__all__ = [
    "ScheduleParser",
    "XerParser",
    "MppParser",
    "PARSERS",
    "supported_formats",
    "parser_for",
    "parse_schedule",
    "list_projects",
]

PARSERS: tuple[ScheduleParser, ...] = (XerParser(), MppParser())

_BY_SUFFIX: dict[str, ScheduleParser] = {
    suffix: parser for parser in PARSERS for suffix in parser.suffixes
}


def supported_formats() -> list[dict[str, object]]:
    """Every registered format and whether this deployment can actually read it.

    Surfaced by the API so the upload UI can grey out ``.mpp`` with the real reason
    rather than letting a user upload a 40 MB file and then rejecting it.
    """
    formats: list[dict[str, object]] = []
    for parser in PARSERS:
        ok, reason = parser.available()
        formats.append(
            {
                "suffixes": list(parser.suffixes),
                "name": parser.format_name,
                "available": ok,
                "reason": reason,
            }
        )
    return formats


def parser_for(filename: str) -> ScheduleParser:
    suffix = Path(filename).suffix.lower()
    parser = _BY_SUFFIX.get(suffix)
    if parser is None:
        raise UnsupportedScheduleFormat(suffix or filename, sorted(_BY_SUFFIX))
    ok, reason = parser.available()
    if not ok:
        raise ParserUnavailable(suffix, reason)
    return parser


def parse_schedule(
    data: bytes, filename: str, *, project_id: str | None = None
) -> Schedule:
    return parser_for(filename).parse(data, filename=filename, project_id=project_id)


def list_projects(data: bytes, filename: str) -> list[tuple[str, str, int]]:
    return parser_for(filename).list_projects(data)
