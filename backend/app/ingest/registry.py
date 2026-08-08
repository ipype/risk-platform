"""Suffix to extractor, and nothing more.

Dispatch is on the filename suffix rather than on sniffed content. Sniffing would identify
a ``.docx`` and a ``.xlsx`` as the same thing — both are Zip archives — and the two need
entirely different readers, so the suffix is the only signal that separates them without
opening the archive and inspecting its parts. A file with a wrong extension fails at the
reader with a message naming the format that could not be read, which is a better error
than a confident misidentification.

Schedules are deliberately absent. ``.xer`` is parsed by ``app/schedule/`` into activities
and relationships, and the evidence service reads those relationally. Routing a schedule
through here would produce prose chunks of data the platform already holds in a form that
answers questions this one cannot.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable, Protocol

from app.core.errors import UnsupportedDocumentFormat
from app.ingest import pdf, plain, word, workbook
from app.ingest.types import Extraction

__all__ = ["extract", "SUPPORTED", "extractor_for"]


class Extractor(Protocol):
    def __call__(self, data: bytes, *, filename: str = "") -> Extraction: ...


_REGISTRY: dict[str, Extractor] = {}
for module in (pdf, word, workbook, plain):
    for suffix in module.SUFFIXES:
        _REGISTRY[suffix] = module.extract

#: What an upload form should advertise, and what an error message lists.
SUPPORTED: tuple[str, ...] = tuple(sorted(_REGISTRY))


def extractor_for(filename: str) -> Callable[..., Extraction]:
    suffix = PurePosixPath(filename).suffix.lower()
    handler = _REGISTRY.get(suffix)
    if handler is None:
        raise UnsupportedDocumentFormat(suffix or "(none)", list(SUPPORTED))
    return handler


def extract(data: bytes, *, filename: str) -> Extraction:
    return extractor_for(filename)(data, filename=filename)
