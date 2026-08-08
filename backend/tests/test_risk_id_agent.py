"""The admission rules, tested with strings.

Everything here is pure, which is the point of putting it in ``app/agents/`` — the claims
this platform makes about its AI features are decided by this code, and all of them can be
checked with a string and a frozen dataclass rather than a session, a network and a model.

``TestGrounding`` is the file's centre. A model citing an extract it was never shown is the
single failure that would make the proposal inbox worthless, because a reviewer's whole
basis for trusting rows they did not write is that the citation resolves to something real.
The system prompt asks for compliance; this is what makes it true.
"""

from __future__ import annotations

import json

from app.agents.risk_id import (
    MAX_TITLE_CHARS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_messages,
    build_windows,
    parse,
    render_taxonomy,
    render_window,
)
from app.agents.types import (
    INCOMPLETE,
    NOT_AN_ARRAY,
    UNGROUNDED,
    UNKNOWN_CATEGORY,
    UNPARSEABLE,
    PackChunk,
    TaxonomyEntry,
    Window,
)

ALLOWED = frozenset({"doc_chunk:1", "doc_chunk:2"})
PREFIXES = frozenset({"ENV-030", "STG-010"})

TAXONOMY = [
    TaxonomyEntry(prefix="ENV-030", category_name="Environmental", name="Permitting"),
    TaxonomyEntry(prefix="STG-010", category_name="Stakeholder", name="Third parties"),
]


def _item(**overrides) -> dict:
    base = {
        "title": "Consent lapses before tie-in",
        "cause": "the consent is valid for ninety days",
        "event": "the tie-in slips past that window",
        "effect": "delay the commissioning date",
        "subcategory_prefix": "ENV-030",
        "evidence_refs": ["doc_chunk:1"],
        "rationale": "The validity period is stated in the extract.",
        "confidence": 0.6,
    }
    base.update(overrides)
    return base


def _chunk(ref: str, text: str, doc: str = "Consent") -> PackChunk:
    return PackChunk(ref=ref, text=text, document_label=doc)


class TestGrounding:
    def test_a_candidate_citing_only_the_pack_survives(self) -> None:
        kept, drops = parse(
            json.dumps([_item()]), allowed_refs=ALLOWED, known_prefixes=PREFIXES
        )
        assert len(kept) == 1
        assert kept[0].evidence_refs == ("doc_chunk:1",)
        assert drops == []

    def test_a_citation_that_was_never_sent_kills_the_candidate(self) -> None:
        kept, drops = parse(
            json.dumps([_item(evidence_refs=["doc_chunk:9001"])]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept == []
        assert drops[0].reason == UNGROUNDED

    def test_the_invented_reference_is_named_in_the_drop(self) -> None:
        """"Something was refused" is a number. "It cited doc_chunk:9001, which was never
        sent" is what tells an operator their prompt or model has a problem."""
        _, drops = parse(
            json.dumps([_item(evidence_refs=["doc_chunk:9001"])]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert "doc_chunk:9001" in drops[0].detail

    def test_real_citations_survive_alongside_invented_ones(self) -> None:
        """Partial compliance keeps the candidate and drops the fiction, rather than
        letting one bad ref cost a well-evidenced finding."""
        kept, drops = parse(
            json.dumps([_item(evidence_refs=["doc_chunk:9001", "doc_chunk:2"])]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept[0].evidence_refs == ("doc_chunk:2",)
        assert drops == []

    def test_no_citations_at_all_is_ungrounded(self) -> None:
        kept, drops = parse(
            json.dumps([_item(evidence_refs=[])]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept == []
        assert drops[0].reason == UNGROUNDED

    def test_duplicate_citations_collapse(self) -> None:
        kept, _ = parse(
            json.dumps([_item(evidence_refs=["doc_chunk:1", "doc_chunk:1"])]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept[0].evidence_refs == ("doc_chunk:1",)


class TestAdmission:
    def test_an_unknown_rbs_prefix_is_refused(self) -> None:
        kept, drops = parse(
            json.dumps([_item(subcategory_prefix="ZZZ-999")]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept == []
        assert drops[0].reason == UNKNOWN_CATEGORY

    def test_a_lowercase_prefix_is_accepted(self) -> None:
        kept, _ = parse(
            json.dumps([_item(subcategory_prefix="env-030")]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept[0].subcategory_prefix == "ENV-030"

    def test_a_blank_field_is_incomplete(self) -> None:
        kept, drops = parse(
            json.dumps([_item(effect="   ")]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept == []
        assert drops[0].reason == INCOMPLETE
        assert "effect" in drops[0].detail

    def test_the_refused_item_is_kept_on_the_drop(self) -> None:
        """A reviewer told four were refused and not which four has a number, not a
        record."""
        _, drops = parse(
            json.dumps([_item(cause="")]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert drops[0].raw is not None
        assert drops[0].raw["title"] == "Consent lapses before tie-in"

    def test_an_over_long_title_is_clipped_not_dropped(self) -> None:
        kept, drops = parse(
            json.dumps([_item(title="x" * 900)]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert drops == []
        assert len(kept[0].title) == MAX_TITLE_CHARS

    def test_good_and_bad_candidates_in_one_response_are_separated(self) -> None:
        kept, drops = parse(
            json.dumps([_item(), _item(evidence_refs=["nope:1"]), _item(title="")]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert len(kept) == 1
        assert {d.reason for d in drops} == {UNGROUNDED, INCOMPLETE}


class TestConfidence:
    def test_a_number_in_range_is_kept(self) -> None:
        kept, _ = parse(
            json.dumps([_item(confidence=0.25)]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept[0].confidence == 0.25

    def test_a_missing_confidence_is_none_not_zero(self) -> None:
        """The ledger's rule: NULL is an abstention, zero is a claim."""
        item = _item()
        del item["confidence"]
        kept, _ = parse(
            json.dumps([item]), allowed_refs=ALLOWED, known_prefixes=PREFIXES
        )
        assert kept[0].confidence is None

    def test_out_of_range_becomes_abstention(self) -> None:
        kept, _ = parse(
            json.dumps([_item(confidence=7)]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept[0].confidence is None

    def test_true_does_not_become_maximum_confidence(self) -> None:
        """``bool`` is an ``int`` in Python, so ``"confidence": true`` would otherwise be
        the most confident row in the inbox."""
        kept, _ = parse(
            json.dumps([_item(confidence=True)]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept[0].confidence is None

    def test_a_string_confidence_abstains(self) -> None:
        kept, _ = parse(
            json.dumps([_item(confidence="high")]),
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept[0].confidence is None


class TestDecoding:
    def test_a_fenced_block_is_read(self) -> None:
        raw = "```json\n" + json.dumps([_item()]) + "\n```"
        kept, drops = parse(raw, allowed_refs=ALLOWED, known_prefixes=PREFIXES)
        assert len(kept) == 1 and drops == []

    def test_one_apologetic_sentence_does_not_cost_a_window(self) -> None:
        raw = "Here is what I found:\n" + json.dumps([_item()]) + "\nHope that helps."
        kept, _ = parse(raw, allowed_refs=ALLOWED, known_prefixes=PREFIXES)
        assert len(kept) == 1

    def test_an_empty_array_is_an_answer_not_a_failure(self) -> None:
        kept, drops = parse("[]", allowed_refs=ALLOWED, known_prefixes=PREFIXES)
        assert kept == [] and drops == []

    def test_prose_with_no_array_is_unparseable(self) -> None:
        kept, drops = parse(
            "I could not find any risks in this document.",
            allowed_refs=ALLOWED,
            known_prefixes=PREFIXES,
        )
        assert kept == []
        assert drops[0].reason == UNPARSEABLE

    def test_a_bare_object_is_not_an_array(self) -> None:
        kept, drops = parse(
            json.dumps(_item()), allowed_refs=ALLOWED, known_prefixes=PREFIXES
        )
        assert kept == []
        assert drops[0].reason == NOT_AN_ARRAY

    def test_truncated_json_is_refused_not_repaired(self) -> None:
        """A repaired array is one whose contents nobody can attest to."""
        raw = json.dumps([_item()])[:-8]
        kept, drops = parse(raw, allowed_refs=ALLOWED, known_prefixes=PREFIXES)
        assert kept == []
        assert drops[0].reason == UNPARSEABLE

    def test_an_empty_response_says_it_was_empty(self) -> None:
        _, drops = parse("", allowed_refs=ALLOWED, known_prefixes=PREFIXES)
        assert "empty" in drops[0].detail


class TestWindowing:
    def test_windows_never_span_documents(self) -> None:
        chunks = [_chunk("doc_chunk:1", "a"), _chunk("doc_chunk:2", "b", doc="Other")]
        windows, truncated = build_windows(
            chunks, document_ids=[1, 2], max_chars=10_000, max_windows=10
        )
        assert [w.document_id for w in windows] == [1, 2]
        assert truncated is False

    def test_a_document_is_split_at_the_character_budget(self) -> None:
        chunks = [_chunk(f"doc_chunk:{i}", "x" * 60) for i in range(5)]
        windows, _ = build_windows(
            chunks, document_ids=[1] * 5, max_chars=150, max_windows=10
        )
        assert len(windows) == 3
        assert [len(w.chunks) for w in windows] == [2, 2, 1]

    def test_an_oversized_chunk_gets_its_own_window_rather_than_being_split(self) -> None:
        """5.2 made a chunk the smallest thing worth citing; splitting one here would put
        half a clause in each of two calls."""
        chunks = [_chunk("doc_chunk:1", "x" * 5_000)]
        windows, _ = build_windows(
            chunks, document_ids=[1], max_chars=100, max_windows=10
        )
        assert len(windows) == 1
        assert windows[0].chunks[0].ref == "doc_chunk:1"

    def test_the_cap_truncates_and_says_so(self) -> None:
        chunks = [_chunk(f"doc_chunk:{i}", "x") for i in range(6)]
        windows, truncated = build_windows(
            chunks, document_ids=list(range(6)), max_chars=10, max_windows=2
        )
        assert len(windows) == 2
        assert truncated is True

    def test_an_empty_corpus_produces_no_windows(self) -> None:
        windows, truncated = build_windows(
            [], document_ids=[], max_chars=100, max_windows=5
        )
        assert windows == [] and truncated is False

    def test_a_window_reports_exactly_what_may_be_cited(self) -> None:
        window = Window(
            document_id=1,
            document_label="d",
            chunks=(_chunk("doc_chunk:1", "a"), _chunk("doc_chunk:2", "b")),
        )
        assert window.refs == frozenset({"doc_chunk:1", "doc_chunk:2"})


class TestPrompt:
    def test_the_prompt_carries_every_ref_in_the_window(self) -> None:
        window = Window(
            document_id=1,
            document_label="Consent",
            chunks=(_chunk("doc_chunk:1", "a"), _chunk("doc_chunk:2", "b")),
        )
        rendered = render_window(window)
        assert "[doc_chunk:1]" in rendered and "[doc_chunk:2]" in rendered

    def test_the_taxonomy_is_sent_whole_rather_than_pre_filtered(self) -> None:
        """Filtering would decide the categorisation before the model read the extract,
        and the categorisation is one of the things being asked for."""
        rendered = render_taxonomy(TAXONOMY)
        assert "ENV-030" in rendered and "STG-010" in rendered

    def test_the_locator_travels_with_the_extract(self) -> None:
        window = Window(
            document_id=1,
            document_label="Consent",
            chunks=(
                PackChunk(
                    ref="doc_chunk:1",
                    text="a",
                    section="Consents › Validity",
                    locator={"page": 4},
                ),
            ),
        )
        rendered = render_window(window)
        assert "Consents › Validity" in rendered and "page 4" in rendered

    def test_the_user_turn_names_the_project_and_the_taxonomy(self) -> None:
        window = Window(
            document_id=1, document_label="Consent", chunks=(_chunk("doc_chunk:1", "a"),)
        )
        content = build_messages(window, TAXONOMY, project_name="North Shore Tunnel")
        assert "North Shore Tunnel" in content
        assert "ENV-030" in content
        assert "[doc_chunk:1]" in content

    def test_the_system_prompt_forbids_scoring(self) -> None:
        """Probability and impact belong to an elicitation with the people who own the
        work, not to an identification sweep."""
        assert "probability" in SYSTEM_PROMPT.lower()

    def test_the_prompt_version_is_stamped_and_dated(self) -> None:
        assert PROMPT_VERSION.startswith("risk-id/")
        assert len(PROMPT_VERSION) <= 40  # the ledger column is String(40)
