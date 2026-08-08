"""The ranker, with no database anywhere near it.

Two things are actually under test. The first is that BM25 behaves like BM25 — rarity
outranks frequency, length normalisation stops long documents winning on term count alone,
and the order is deterministic. The second, and the one that matters more, is the
abstention rule: a hit that matched only a near-universal term must not come back, because
returning it as the best available answer is how a retrieval layer teaches a generator to
cite noise.
"""

from __future__ import annotations

from app.retrieval.bm25 import MIN_IDF_SHARE, Corpus
from app.services.mapping_suggest import tokenize


def _corpus(documents: dict[str, str]) -> Corpus:
    corpus = Corpus()
    for ref, text in documents.items():
        corpus.add(ref, tokenize(text))
    return corpus


class TestRanking:
    def test_a_rare_term_outranks_a_common_one(self) -> None:
        """Rarity is why 'dewatering' beats 'concrete' on a project that is mostly
        concrete, and it only works measured against the corpus being searched."""
        corpus = _corpus(
            {
                "a": "concrete pour foundations",
                "b": "concrete pour walls",
                "c": "concrete pour slabs",
                "d": "dewatering of the excavation",
            }
        )
        hits = corpus.search(tokenize("concrete dewatering"))
        assert hits[0].ref == "d"

    def test_a_longer_document_does_not_win_on_term_count_alone(self) -> None:
        corpus = _corpus(
            {
                "short": "permit consent",
                "long": "permit consent " + "unrelated filler wording " * 40,
                # A third document without the terms, so neither is universal and both
                # keep a weight to be normalised against.
                "other": "dewatering excavation shoring",
            }
        )
        hits = corpus.search(tokenize("permit consent"))
        assert hits[0].ref == "short"

    def test_repeated_terms_saturate(self) -> None:
        """Twenty mentions is not twenty times the evidence of one."""
        corpus = _corpus(
            {
                "once": "the consent expires",
                "many": "consent " * 20,
                "other": "dewatering excavation",
            }
        )
        hits = {h.ref: h.score for h in corpus.search(tokenize("consent"))}
        assert hits["many"] < hits["once"] * 20

    def test_the_order_is_deterministic(self) -> None:
        """A ranker whose output moves between identical calls makes every suggestion
        built on it unreproducible."""
        documents = {f"d{i}": "permit consent delay" for i in range(20)}
        corpus = _corpus(documents)
        first = [h.ref for h in corpus.search(tokenize("permit consent"), limit=20)]
        second = [h.ref for h in corpus.search(tokenize("permit consent"), limit=20)]
        assert first == second

    def test_limit_is_respected(self) -> None:
        documents = {f"d{i}": f"permit consent dewatering variant {i}" for i in range(30)}
        # Not every document, or the query terms would be universal and worth nothing.
        documents.update({f"e{i}": f"unrelated wording {i}" for i in range(5)})
        corpus = _corpus(documents)
        assert len(corpus.search(tokenize("permit consent"), limit=5)) == 5


class TestAbstention:
    def test_a_hit_on_only_a_universal_term_is_dropped(self) -> None:
        """Matching 'permit' in a corpus where everything says 'permit' is not a match."""
        corpus = _corpus(
            {
                "a": "permit application submitted",
                "b": "permit application withdrawn",
                "c": "permit application resubmitted",
                "d": "permit dewatering discharge licence",
            }
        )
        hits = corpus.search(tokenize("permit dewatering"))
        assert [h.ref for h in hits] == ["d"]

    def test_nothing_matching_returns_nothing(self) -> None:
        corpus = _corpus({"a": "concrete pour foundations"})
        assert corpus.search(tokenize("dewatering excavation")) == []

    def test_a_query_of_only_universal_terms_returns_nothing(self) -> None:
        """There is nothing to rank on, and the corpus in arbitrary order is worse than
        an empty result — it looks like an answer."""
        corpus = _corpus({f"d{i}": "permit consent" for i in range(6)})
        assert corpus.search(tokenize("permit consent")) == []

    def test_an_empty_query_returns_nothing(self) -> None:
        corpus = _corpus({"a": "concrete pour"})
        assert corpus.search([]) == []

    def test_an_empty_corpus_returns_nothing(self) -> None:
        assert Corpus().search(tokenize("anything at all")) == []

    def test_the_idf_share_is_reported(self) -> None:
        """The rule is surfaced rather than hidden, so it can be argued with."""
        corpus = _corpus(
            {
                "a": "dewatering excavation shoring",
                "b": "concrete pour foundations",
            }
        )
        hits = corpus.search(tokenize("dewatering excavation"))
        assert hits[0].idf_share >= MIN_IDF_SHARE
        # Terms are stemmed by the shared tokenizer, so the matches are stems.
        assert set(hits[0].matched) == set(tokenize("dewatering excavation"))


class TestIdf:
    def test_a_universal_term_is_worth_nothing(self) -> None:
        corpus = _corpus({f"d{i}": "permit" for i in range(10)})
        assert corpus.idf("permit") == 0.0

    def test_a_merely_common_term_still_carries_some_weight(self) -> None:
        """Universality is the rule, not a majority threshold picked to fit an example."""
        documents = {f"d{i}": "permit" for i in range(9)}
        documents["other"] = "dewatering"
        corpus = _corpus(documents)
        assert 0.0 < corpus.idf("permit") < corpus.idf("dewatering")

    def test_an_unseen_term_scores_above_a_universal_one(self) -> None:
        corpus = _corpus({f"d{i}": "permit consent" for i in range(10)})
        assert corpus.idf("dewatering") > corpus.idf("permit")

    def test_idf_never_goes_negative(self) -> None:
        """A negative weight would let a document rank higher for lacking a query term."""
        documents = {f"d{i}": "permit" for i in range(10)}
        documents["other"] = "dewatering"
        corpus = _corpus(documents)
        assert corpus.idf("permit") >= 0.0

    def test_average_length_of_an_empty_corpus_is_zero(self) -> None:
        assert Corpus().average_length == 0.0
