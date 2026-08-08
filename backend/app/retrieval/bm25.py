"""Okapi BM25, and the rule that decides when a result is not worth returning.

Pure, in the sense ``app/sim/`` and ``app/ingest/`` are: tokens in, ranked references out.
No database, no network, no clock. It takes *tokens* rather than text, which is the one
structural thing worth noticing here — the tokenizer and its lexicon live in
``services/mapping_lexicon.py`` and ``services/mapping_suggest.py``, and a pure package
importing from a service would invert the dependency. Passing tokens keeps the arrow
pointing the right way and, more importantly, means the risk-to-activity suggester and the
evidence service share one vocabulary instead of drifting into two.

**Why BM25 and not cosine over embeddings.** Because there is no embedding provider chosen
yet, and because lexical retrieval is the right *first* adapter regardless: it needs no
model, it is deterministic, and its results are explainable — every hit can say which query
terms it matched, which is what lets a reviewer judge a citation rather than trust it. When
vectors arrive they go behind the same interface and the two are blended; this does not get
thrown away.

**Abstention is on term overlap, not on score.** BM25 scores are unbounded and
corpus-relative: a raw floor means one thing on a corpus of forty chunks and something else
on four thousand, so any absolute threshold is a number that looks principled and is not.
What is stable across corpora is *which* query terms a result matched and how much of the
query's meaning those terms carry. A chunk matching only "project" out of "project permit
consent delay" has told us nothing, and returning it as the best available answer is how a
retrieval layer teaches a generator to cite noise.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

__all__ = ["Corpus", "Hit", "K1", "B", "MIN_IDF_SHARE"]

#: Term-frequency saturation. Standard, and left standard deliberately: there is no
#: retrieval benchmark in this repo to tune against, and a hand-picked value with no
#: evaluation behind it is worse than the published default because it looks deliberate.
K1 = 1.2

#: Length normalisation. Also standard. Chunks here vary from a one-line table row to a
#: thousand-character block, so this one does real work — without it every table row loses
#: to every paragraph on term frequency alone.
B = 0.75

#: A hit must match query terms carrying at least this share of the query's total IDF
#: mass. Set where a single mid-rarity term clears it and a single near-universal one does
#: not. It is a judgement, not a measurement, and it is the number to move first if
#: retrieval is returning noise or abstaining too readily.
MIN_IDF_SHARE = 0.15


@dataclass(frozen=True, slots=True)
class Hit:
    ref: str
    score: float
    #: Which query terms this result actually contained. Carried through to the API so a
    #: reviewer sees *why* something was retrieved. A citation nobody can interrogate is a
    #: citation nobody should accept.
    matched: tuple[str, ...]
    #: Share of the query's IDF mass those terms carry, 0..1. The number the abstention
    #: rule is applied to, surfaced rather than hidden so the rule can be argued with.
    idf_share: float


@dataclass(slots=True)
class Corpus:
    """Document frequencies over one search's candidate set.

    Built per search rather than cached. Rarity is what makes "dewatering" outrank
    "concrete" on a project that is mostly concrete, and rarity is only meaningful against
    the corpus actually being searched — a cached global IDF would rank against a
    population the reviewer is not looking at. The same reasoning the activity suggester
    uses for building its IDF per schedule version.
    """

    _tokens: dict[str, list[str]] = field(default_factory=dict)
    _df: Counter = field(default_factory=Counter)
    _total_len: int = 0

    def add(self, ref: str, tokens: list[str]) -> None:
        self._tokens[ref] = tokens
        self._df.update(set(tokens))
        self._total_len += len(tokens)

    def __len__(self) -> int:
        return len(self._tokens)

    @property
    def average_length(self) -> float:
        return (self._total_len / len(self._tokens)) if self._tokens else 0.0

    def idf(self, term: str) -> float:
        """Lucene's BM25 IDF, with one addition: a universal term is worth nothing.

        The published form is used as published — ``+1`` inside the log, which keeps the
        weight positive where Robertson's original goes negative for a term in more than
        half the corpus. A negative weight would let a document rank *higher* for lacking
        a query term, which is indefensible however small the effect.

        The addition is the ``df >= n`` case. A term every candidate contains separates
        nothing, and the published form still gives it a small positive weight — enough
        that a query made entirely of such terms would return the whole corpus in
        arbitrary order, which is worse than returning nothing because it looks like an
        answer. Exact rather than a threshold: "in everything" is a fact about the corpus,
        while "in more than 60% of things" would be a number picked to make an example
        work.
        """
        n = len(self._tokens)
        df = self._df.get(term, 0)
        if n and df >= n:
            return 0.0
        return max(0.0, math.log((n - df + 0.5) / (df + 0.5) + 1.0))

    def search(self, query_tokens: list[str], *, limit: int = 10) -> list[Hit]:
        if not self._tokens or not query_tokens:
            return []

        query = list(dict.fromkeys(query_tokens))
        idf = {term: self.idf(term) for term in query}
        mass = sum(idf.values())
        if mass <= 0.0:
            # Every query term is in effectively every document. There is nothing to rank
            # on, and returning the corpus in arbitrary order would be worse than nothing.
            return []

        average = self.average_length or 1.0
        hits: list[Hit] = []
        for ref, tokens in self._tokens.items():
            if not tokens:
                continue
            counts = Counter(tokens)
            length = len(tokens)
            score = 0.0
            matched: list[str] = []
            matched_mass = 0.0
            for term in query:
                frequency = counts.get(term, 0)
                if frequency == 0:
                    continue
                weight = idf[term]
                if weight <= 0.0:
                    # Present, but the term is in nearly everything. It does not count
                    # towards the overlap rule either — matching "the" is not a match.
                    continue
                matched.append(term)
                matched_mass += weight
                numerator = frequency * (K1 + 1)
                denominator = frequency + K1 * (1 - B + B * length / average)
                score += weight * numerator / denominator

            if not matched:
                continue
            share = matched_mass / mass
            if share < MIN_IDF_SHARE:
                continue
            hits.append(
                Hit(
                    ref=ref,
                    score=round(score, 6),
                    matched=tuple(matched),
                    idf_share=round(share, 4),
                )
            )

        # Ties broken on ref so the same corpus and query always produce the same order.
        # A ranker whose output moves between identical calls makes every suggestion built
        # on it unreproducible, which is the property the whole platform is built around.
        hits.sort(key=lambda h: (-h.score, h.ref))
        return hits[:limit]
