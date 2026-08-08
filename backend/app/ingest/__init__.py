"""Document extraction. Pure: bytes in, chunks out.

No database, no network, no clock, no logging — the same boundary ``app/sim/`` holds, for
the same reason. See ``app/ingest/types.py`` for the one rule every extractor obeys.
"""

from app.ingest.registry import SUPPORTED, extract, extractor_for
from app.ingest.types import PROSE, TABLE_ROW, Chunk, Extraction

__all__ = [
    "Chunk",
    "Extraction",
    "PROSE",
    "SUPPORTED",
    "TABLE_ROW",
    "extract",
    "extractor_for",
]
