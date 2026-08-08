"""Ranking. Pure: tokens in, ranked references out.

No database, no network, no clock — the same boundary ``app/sim/`` and ``app/ingest/``
hold. Takes tokens rather than text so the tokenizer can stay in ``services/`` with its
lexicon, shared with the risk-to-activity suggester instead of duplicated.
"""

from app.retrieval.bm25 import B, K1, MIN_IDF_SHARE, Corpus, Hit

__all__ = ["B", "Corpus", "Hit", "K1", "MIN_IDF_SHARE"]
