"""Is this the risk we already have?

Asked twice, with different answers wanted each time.

**Within one batch, a repeat is noise.** A model given nine overlapping windows of the same
contract will find the same permitting risk in six of them. Six inbox rows saying one thing
is not six findings, and a reviewer who has to notice that themselves will stop reading the
inbox by the second run. Suppress.

**Against the register, a repeat is usually context.** The overlap between "consent lapses
before tie-in" and an existing "permit expiry delays commissioning" might be the same risk
in two vocabularies, or it might be two genuinely different risks in one subject area — and
the cost of getting it wrong is not symmetric. A false suppression means a real risk never
reaches anyone and nobody ever learns it was found; a false pass means one inbox row a
reviewer rejects in four seconds. So the register threshold sits well above the batch one,
and the band beneath it does something more useful than either: the matching risk is
attached to the proposal as evidence, so the reviewer sees "the register already carries
WTR-PLA-0007, which overlaps this" and merges, rejects or keeps it on their own judgement.

**Suppression is never silent.** Everything dropped here lands on the generation run with
the code of the risk it matched. "Fourteen candidates, three already in the register" is a
result; fourteen candidates becoming eleven rows with no explanation is a bug report
waiting to be filed.

Pure, and token-based rather than text-based, so the tokenizer is the caller's choice. That
is the same argument ``retrieval/bm25.py`` makes: this platform has one lexicon, in
``services/mapping_suggest.tokenize``, and a second one hidden inside a similarity function
would drift from it silently and make two subsystems disagree about what a word is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "SUPPRESS_AT",
    "PRECEDENT_AT",
    "Match",
    "similarity",
    "best_match",
]

#: At or above this, two statements are treated as the same risk. Jaccard over token sets,
#: so 0.75 means three quarters of the vocabulary is shared — reached by rewordings and
#: near-copies, not by two risks that merely share a subject.
SUPPRESS_AT = 0.75

#: At or above this and below :data:`SUPPRESS_AT`, related enough to show the reviewer and
#: not enough to decide for them. The gap between the two numbers is the whole design: it
#: is where "possibly the same thing" lives, and it resolves into a citation rather than
#: into a suppression or a silence.
PRECEDENT_AT = 0.45


@dataclass(frozen=True, slots=True)
class Match:
    """The closest thing already known, and how close it is."""

    key: str
    label: str
    score: float

    @property
    def suppresses(self) -> bool:
        return self.score >= SUPPRESS_AT

    @property
    def is_precedent(self) -> bool:
        return PRECEDENT_AT <= self.score < SUPPRESS_AT


def similarity(left: Sequence[str], right: Sequence[str]) -> float:
    """Jaccard over token sets. 0 when either side is empty.

    Set-based rather than sequence-based on purpose. "Permit expires before tie-in" and
    "tie-in blocked by expired permit" are the same risk written two ways, and any measure
    that respects word order scores them apart. Order carries almost no signal in a
    one-line risk title and quite a lot of noise.
    """
    a, b = set(left), set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def best_match(
    tokens: Sequence[str],
    known: Sequence[tuple[str, str, Sequence[str]]],
    *,
    floor: float = PRECEDENT_AT,
) -> Match | None:
    """The highest-scoring entry in ``known``, if anything clears ``floor``.

    ``known`` is ``(key, label, tokens)`` — the key identifies the row, the label is what a
    reviewer reads (a risk code, a batch position). Ties break on the label so a run over
    unchanged inputs reports the same match every time; a dedupe that names a different
    twin on each pass makes its own output impossible to diff.
    """
    best: Match | None = None
    for key, label, other in known:
        score = similarity(tokens, other)
        if score < floor:
            continue
        if best is None or (score, best.label) > (best.score, label):
            best = Match(key=key, label=label, score=score)
    return best
