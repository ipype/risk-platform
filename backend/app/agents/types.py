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

from dataclasses import dataclass, field

__all__ = [
    "Assessment",
    "Candidate",
    "Drop",
    "EvidenceItem",
    "ImpactArea",
    "Level",
    "PackChunk",
    "RiskSubject",
    "Scale",
    "Skip",
    "TaxonomyEntry",
    "Window",
    "DROP_REASONS",
    "SKIP_REASONS",
    "UNPARSEABLE",
    "NOT_AN_ARRAY",
    "NOT_AN_OBJECT",
    "INCOMPLETE",
    "UNGROUNDED",
    "UNKNOWN_CATEGORY",
    "UNKNOWN_AREA",
    "OUT_OF_RANGE",
    "NOTHING_TO_SCORE",
    "DUPLICATE_IN_BATCH",
    "ALREADY_IN_REGISTER",
    "NO_EVIDENCE",
    "ALREADY_ASSESSED",
    "SUBJECT_LIMIT",
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

#: Valid JSON of the wrong shape where a single object was asked for.
NOT_AN_OBJECT = "not_an_object"

#: A level outside the scale this install actually configures — a 6 on a five-point
#: probability scale, or a 0. Never clamped into range: a model that answered off the
#: scale has misread the scale, and clamping turns that into a score somebody signs.
OUT_OF_RANGE = "out_of_range"

#: An impact area code that is not in the active matrix configuration.
UNKNOWN_AREA = "unknown_area"

#: The answer parsed and named neither a probability nor a single impact area. A correct
#: and useful outcome — the model was asked to omit rather than guess — and a drop rather
#: than a proposal, because there is nothing to propose.
NOTHING_TO_SCORE = "nothing_to_score"

DROP_REASONS: tuple[str, ...] = (
    UNPARSEABLE,
    NOT_AN_ARRAY,
    NOT_AN_OBJECT,
    INCOMPLETE,
    UNGROUNDED,
    UNKNOWN_CATEGORY,
    UNKNOWN_AREA,
    OUT_OF_RANGE,
    NOTHING_TO_SCORE,
    DUPLICATE_IN_BATCH,
    ALREADY_IN_REGISTER,
)

#: Why a pass declined to spend a call on a subject at all. Kept apart from
#: :data:`DROP_REASONS` because the two answer different questions: a drop says the model
#: was asked and its answer was refused, a skip says it was never asked. "Nine skipped for
#: want of evidence" and "nine answered off the scale" are different problems with
#: different fixes, and one list holding both would let each hide inside the other.
NO_EVIDENCE = "no_evidence"

#: A human has already scored this. Not re-suggested by default: proposing against a
#: judgement made in a workshop is the generator arguing with the people who were in the
#: room, and that is a thing to ask for deliberately rather than to get by default.
ALREADY_ASSESSED = "already_assessed"

#: The pass hit its per-run subject cap before reaching this one.
SUBJECT_LIMIT = "subject_limit"

SKIP_REASONS: tuple[str, ...] = (NO_EVIDENCE, ALREADY_ASSESSED, SUBJECT_LIMIT)


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


# --------------------------------------------------------------------------------------
# qualitative evaluation
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Level:
    """One rung of a scale: the number that gets stored and the words beside it."""

    level: int
    label: str


@dataclass(frozen=True, slots=True)
class ImpactArea:
    """One dimension the register scores, with this install's descriptors.

    ``descriptors`` maps a level to what that level *means here* — "$250k – $1M", "lost-time
    injury". Sent to the model in full, because a five-point cost scale means different
    money on a €40M water main and a €4B rail programme, and a model scoring against an
    imagined scale produces numbers that look identical to ones scored against the real one.
    """

    code: str
    name: str
    descriptors: dict[int, str]


@dataclass(frozen=True, slots=True)
class Scale:
    """The active matrix configuration, as the model is allowed to see it.

    Read from ``matrix_config`` rather than written here. The platform already treats the
    scale as configuration and not code — a client on a 4×4 with their own cost bands is a
    supported install, not a special case — and a scoring prompt carrying a hard-coded 5×5
    would be the one place that stopped being true, silently, in a way whose only symptom
    is scores that are wrong by a constant.
    """

    probability: tuple[Level, ...]
    impact: tuple[Level, ...]
    areas: tuple[ImpactArea, ...]

    @property
    def probability_levels(self) -> frozenset[int]:
        return frozenset(level.level for level in self.probability)

    @property
    def impact_levels(self) -> frozenset[int]:
        return frozenset(level.level for level in self.impact)

    @property
    def area_codes(self) -> frozenset[str]:
        return frozenset(area.code for area in self.areas)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One retrieved thing, as the model sees it and as a proposal will cite it.

    ``assessed`` carries what a comparable risk was actually scored, and is the whole
    reason the register is searched at all. It is a string rather than numbers because the
    model is being shown a precedent to weigh, not a value to copy, and a bare integer in
    a prompt is copied far more readily than a sentence saying who scored it and where.
    """

    kind: str
    ref: str
    excerpt: str
    label: str
    from_other_scope: bool = False
    assessed: str | None = None


@dataclass(frozen=True, slots=True)
class RiskSubject:
    """One register row a qualitative evaluation pass is about.

    ``scored_impacts`` holds what a human has already judged. Carried through rather than
    hidden, for two reasons: the model should not be asked to re-score an area somebody has
    already ruled on, and the proposal that lands has to contain those values or accepting
    it would erase them — ``impact_scores`` is one JSON column and the applier sets it
    whole.
    """

    risk_id: int
    risk_code: str
    title: str
    statement: str
    category: str
    scored_probability: int | None = None
    scored_impacts: dict[str, int] = field(default_factory=dict)

    def query(self) -> str:
        """What to retrieve evidence with. Title and statement, nothing else."""
        return " ".join(part for part in (self.title, self.statement) if part)


@dataclass(frozen=True, slots=True)
class Assessment:
    """A parsed qualitative evaluation. Not yet a proposal, and never a score.

    Two confidences and not one. A model can have a firm view on how bad a thing would be
    and no view at all on how likely it is — that is the ordinary case for a hazard whose
    consequence is documented and whose frequency is not — and a single number would force
    it to average the two into something that describes neither.
    """

    probability: int | None
    probability_rationale: str
    impacts: dict[str, int]
    impact_rationales: dict[str, str]
    evidence_refs: tuple[str, ...]
    probability_confidence: float | None = None
    impact_confidence: float | None = None

    @property
    def is_empty(self) -> bool:
        return self.probability is None and not self.impacts


@dataclass(frozen=True, slots=True)
class Skip:
    """A subject the pass declined to spend a call on, and why."""

    subject: str
    reason: str
    detail: str

    def as_dict(self) -> dict:
        return {"subject": self.subject, "reason": self.reason, "detail": self.detail}
