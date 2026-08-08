"""What a generator is handed and what it produces, as data.

Same posture as ``app/ingest/types.py``: plain frozen dataclasses, no ORM rows, no session.
The service layer loads chunks and taxonomy from the database and builds these; the agent
module turns them into a prompt and turns a response back into candidates. Neither half
imports the other's dependencies, which is what lets the interesting logic — grounding,
completeness, deduplication — be tested without a database, a network or a model.

**A ``Drop`` is a first-class output, not an exception.** A window where the model invented
a citation, named a category that does not exist, or wrote three fields of a four-field
statement is a window that produced *information*: it says something about the prompt, the
model or the corpus. Raising would throw that away and take the rest of the batch with it.
Every drop carries its reason onto the generation run, where a reviewer can see that
eleven candidates were offered and four were refused before anyone reads the seven.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Candidate",
    "Drop",
    "PackChunk",
    "TaxonomyEntry",
    "Window",
    "DROP_REASONS",
    "UNPARSEABLE",
    "NOT_AN_ARRAY",
    "INCOMPLETE",
    "UNGROUNDED",
    "UNKNOWN_CATEGORY",
    "DUPLICATE_IN_BATCH",
    "ALREADY_IN_REGISTER",
]

#: The response was not JSON, or not JSON we could find inside the prose.
UNPARSEABLE = "unparseable"

#: Valid JSON of the wrong shape — an object, a string, a number.
NOT_AN_ARRAY = "not_an_array"

#: A required field of the cause-event-effect statement was missing or blank.
INCOMPLETE = "incomplete"

#: Every citation pointed at something that was not in the pack. This is the one that
#: matters most: it is a model describing a document it was not shown.
UNGROUNDED = "ungrounded"

#: The RBS prefix is not one this install's taxonomy holds.
UNKNOWN_CATEGORY = "unknown_category"

#: The same risk, twice, in one batch.
DUPLICATE_IN_BATCH = "duplicate_in_batch"

#: The register already carries it. Suppressed only on strong overlap — see
#: ``agents/dedupe.py`` for why the threshold is asymmetric.
ALREADY_IN_REGISTER = "already_in_register"

DROP_REASONS: tuple[str, ...] = (
    UNPARSEABLE,
    NOT_AN_ARRAY,
    INCOMPLETE,
    UNGROUNDED,
    UNKNOWN_CATEGORY,
    DUPLICATE_IN_BATCH,
    ALREADY_IN_REGISTER,
)


@dataclass(frozen=True, slots=True)
class PackChunk:
    """One piece of corpus, as it will be shown to the model.

    ``ref`` is the evidence service's reference string (``doc_chunk:88``) and it is what
    the model is told to cite. Rendering the real reference rather than a per-prompt index
    means a citation needs no translation table to become a ``evidence_refs`` entry, and a
    translation table is the exact place an off-by-one turns every citation in a batch into
    a confident pointer at the wrong paragraph.
    """

    ref: str
    text: str
    section: str | None = None
    locator: dict | None = None
    document_label: str = ""


@dataclass(frozen=True, slots=True)
class Window:
    """One model call's worth of corpus.

    Windows never span documents. They could — it would mean fewer calls — and the reason
    they do not is that a risk grounded half in a geotechnical report and half in a
    contract is a risk the reviewer has to reconstruct from two citations with no shared
    context, and the model produces those readily when the pack invites it.
    """

    document_id: int
    document_label: str
    chunks: tuple[PackChunk, ...]

    @property
    def refs(self) -> frozenset[str]:
        """Exactly what a candidate from this window is allowed to cite."""
        return frozenset(chunk.ref for chunk in self.chunks)

    @property
    def char_count(self) -> int:
        return sum(len(chunk.text) for chunk in self.chunks)


@dataclass(frozen=True, slots=True)
class TaxonomyEntry:
    """One RBS subcategory, as ``ENV-030`` plus its names."""

    prefix: str
    category_name: str
    name: str


@dataclass(frozen=True, slots=True)
class Candidate:
    """A draft risk that survived parsing. Not yet a proposal, and never a risk.

    ``confidence`` is ``None`` when the model did not give one or gave something that was
    not a number in range. Never coerced to zero: the ledger's rule is that a missing
    confidence is an abstention and a zero is a claim, and coercing here would launder the
    first into the second one layer before anyone could tell.
    """

    title: str
    cause: str
    event: str
    effect: str
    subcategory_prefix: str
    evidence_refs: tuple[str, ...]
    rationale: str
    confidence: float | None = None

    def statement(self) -> str:
        """The cause-event-effect sentence, in the order AACE RP 57R-09 writes it."""
        return (
            f"Because {self.cause.rstrip('.')}, {self.event.rstrip('.')}, "
            f"which would {self.effect.rstrip('.')}."
        )


@dataclass(frozen=True, slots=True)
class Drop:
    """A candidate that did not survive, and why."""

    reason: str
    detail: str
    #: The raw item as the model wrote it, when there was one. Kept so a reviewer looking
    #: at "four refused" can see what was refused rather than taking it on trust.
    raw: dict | None = None

    def as_dict(self) -> dict:
        return {"reason": self.reason, "detail": self.detail, "raw": self.raw}
