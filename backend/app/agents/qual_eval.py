"""The qualitative evaluation pass: score one known risk against this install's matrix.

Pure. Text and dataclasses in, text and dataclasses out — the same boundary ``app/sim/``,
``app/ingest/`` and ``agents/risk_id.py`` hold.

**A query, not a sweep.** The mirror image of identification. There, the corpus was walked
because there was no X to ask about; here there is exactly one X — a risk already in the
register — so the pass retrieves for it through ``services/evidence.py`` and asks about
what came back. This is the first caller of that interface, which is what it was built for.

**The one thing this must do well is refuse to score without a basis.** A probability is a
number that looks the same whether it was reasoned from a document or produced to fill a
field, and it does not stay decorative: it multiplies into a matrix, the matrix drives
triage, and triage decides which risks get an expensive quantitative elicitation. An
invented 4 is an invisible decision about where the whole analysis spends its attention.
So the refusal is enforced three times over — the service does not call at all when
retrieval abstains, the prompt below tells the model to omit rather than guess, and
:func:`parse` drops an answer that cited nothing it was shown or that scored nothing.

**The scale is configuration and is sent in full.** Probability levels, impact levels and
each area's descriptors come from ``matrix_config``. A five-point cost scale means
different money on a €40M water main than on a €4B rail programme, and a prompt carrying a
hard-coded 5×5 would produce scores that are wrong by a constant with no symptom.

**The model never chooses the overall impact.** This platform's overall impact is the worst
case across areas — the maximum, never an average — and that rule lives in
``models/matrix.overall_impact``. The prompt does not ask for an overall and :func:`parse`
ignores one if it arrives, because a model that supplies it is quietly proposing a
different aggregation rule than the one the register is scored on.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.agents._parsing import clean_text, confidence, decode, partition_refs
from app.agents.types import (
    NOT_AN_OBJECT,
    NOTHING_TO_SCORE,
    OUT_OF_RANGE,
    UNGROUNDED,
    UNKNOWN_AREA,
    UNPARSEABLE,
    Assessment,
    Drop,
    EvidenceItem,
    RiskSubject,
    Scale,
)

__all__ = [
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "CLOSING_LINE",
    "build_messages",
    "parse",
    "render_scale",
    "render_evidence",
]

#: Bumped whenever the wording, the output contract or the scale rendering changes.
#: Stored on every proposal (``generator_prompt_version``) so "which prompt version gets
#: edited most" is a GROUP BY rather than an archaeology exercise.
PROMPT_VERSION = "qual-eval/v1/2026-08-08"

#: The last line of the user turn, and a real part of the contract rather than a flourish:
#: identification asks for an array and this asks for an object. ``llm/fake.py`` dispatches
#: on the difference, which keeps the fake a function of the actual prompt instead of a
#: fixed string, and a test asserts the two renderers still disagree here.
CLOSING_LINE = "Return the JSON object now."

#: Rationale fields are shown in an inbox row, not in a report. Shorter than the general
#: field cap on purpose: a paragraph per impact area produces a proposal nobody reads,
#: and an unread rationale is the same as no rationale with more scrolling.
MAX_RATIONALE_CHARS = 600


SYSTEM_PROMPT = """\
You are a risk manager running the qualitative evaluation stage of a risk analysis for a \
capital infrastructure project, following AACE International RP 57R-09.

You are given one risk that is already on the register, and evidence retrieved for it. \
Your job is to judge, from that evidence, how likely the risk is and how bad it would be \
in each impact area.

Rules that matter more than completeness:
1. Score an area only where the evidence gives you a reason to. Omit an area rather than \
guessing at it. An omitted area is left for the people who own the work; a guessed one is \
indistinguishable from a judged one once it is in the register, and it will be multiplied \
into a matrix and used to decide what gets analysed further.
2. Cite the evidence you used. Use only the bracketed identifiers shown with the evidence, \
exactly as written. Never cite an identifier you were not shown.
3. Use only the numbered levels of the scales given below. Do not invent a level, do not \
score between two levels, and do not use a scale you have seen elsewhere.
4. Do not give an overall or combined impact. This register takes the worst case across \
areas, and it computes that itself.
5. Comparable risks from other projects' registers are other analysts' judgements, not \
observed outcomes. Treat them as one opinion among the evidence, not as a frequency.
6. If the evidence says nothing about how likely this risk is or how bad it would be, \
return null for probability and an empty object for impacts. Returning nothing is a \
correct and useful answer.

Answer with a single JSON object and nothing else. No prose before it, no prose after it, \
no markdown fences.

{"probability": 3,
 "probability_rationale": "one or two sentences on what makes it this likely",
 "probability_confidence": 0.5,
 "impacts": {"COST": 4, "SCHED": 3},
 "impact_rationales": {"COST": "why this level", "SCHED": "why this level"},
 "impact_confidence": 0.4,
 "evidence_refs": ["doc_chunk:12", "risk:88"]}

Both confidence fields are 0 to 1 and say how sure you are of that half of the answer. \
Omit either field entirely if you cannot judge it. Never write 0 to mean "unsure" — \
omission means unsure, and 0 means "certainly not", which is a different claim.\
"""


# --------------------------------------------------------------------------------------
# prompt
# --------------------------------------------------------------------------------------


def render_scale(scale: Scale) -> str:
    """The active matrix, level by level, with each area's own descriptors."""
    lines = ["Probability scale:"]
    lines.extend(f"  {lvl.level}  {lvl.label}" for lvl in scale.probability)
    lines.append("")
    lines.append("Impact scale (same levels, different meaning per area):")
    lines.extend(f"  {lvl.level}  {lvl.label}" for lvl in scale.impact)
    for area in scale.areas:
        lines.append("")
        lines.append(f"{area.code} — {area.name}:")
        for lvl in scale.impact:
            descriptor = area.descriptors.get(lvl.level)
            lines.append(
                f"  {lvl.level}  {descriptor}" if descriptor else f"  {lvl.level}  —"
            )
    return "\n".join(lines)


def render_evidence(items: Sequence[EvidenceItem]) -> str:
    """The retrieved evidence, each headed by the identifier the model must cite.

    A comparable risk's own scores are printed beside it, and where the comparable comes
    from another project that is said out loud. A precedent whose provenance is hidden is
    a precedent that reads as this project's own history, which is the one thing it is not.
    """
    parts: list[str] = []
    for item in items:
        head = f"[{item.ref}]  {item.label}"
        if item.from_other_scope:
            head += "  (another project)"
        body = item.excerpt.strip()
        if item.assessed:
            body = f"{body}\n  Scored there as: {item.assessed}"
        parts.append(f"{head}\n{body}")
    return "\n\n".join(parts)


def build_messages(
    subject: RiskSubject,
    evidence: Sequence[EvidenceItem],
    scale: Scale,
    *,
    project_name: str,
    skip_areas: Sequence[str] = (),
) -> str:
    """The single user turn for one risk.

    ``skip_areas`` names the areas a human has already scored. Named rather than silently
    dropped from the scale: a model that cannot see the safety area at all will fold safety
    reasoning into the cost score, and telling it that somebody has already ruled on safety
    is both true and the thing that stops it.
    """
    sections = [
        f"Project: {project_name}",
        "",
        f"Risk {subject.risk_code} — {subject.title}",
        f"Category: {subject.category}",
    ]
    if subject.statement:
        sections.append(f"Statement: {subject.statement}")
    if subject.scored_probability is not None:
        sections.append(
            f"Probability already judged by a person: {subject.scored_probability}. "
            "Do not re-score it; return null for probability."
        )
    if skip_areas:
        sections.append(
            "Already judged by a person, do not score: " + ", ".join(sorted(skip_areas))
        )
    sections.extend(
        [
            "",
            render_scale(scale),
            "",
            "Evidence retrieved for this risk:",
            "",
            render_evidence(evidence),
            "",
            CLOSING_LINE,
        ]
    )
    return "\n".join(sections)


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------


def parse(
    raw: str,
    *,
    allowed_refs: frozenset[str],
    scale: Scale,
    skip_areas: Sequence[str] = (),
    skip_probability: bool = False,
) -> tuple[Assessment | None, list[Drop]]:
    """Turn one response into an admissible assessment, and say what was refused.

    Never raises. A bad answer for one risk must not cost the other thirty-nine in the
    pass, so every failure mode comes back as a :class:`Drop` with a reason.

    Partial answers survive on purpose. A response that names a good probability and one
    impact area outside the scale keeps the probability, drops the area, and reports both —
    refusing the whole thing would throw away a judgement that was fine because a different
    one was not.

    ``skip_probability`` and ``skip_areas`` name what a person has already ruled on. Values
    for those are discarded here rather than filtered later, and quietly rather than as a
    drop: the prompt said not to score them, a model that did anyway is an
    instruction-following miss, and reporting it to a reviewer as a refusal would put noise
    in the one list that exists to tell them something is wrong.
    """
    payload = decode(raw, opener="{", closer="}")
    if payload is None:
        head = raw.strip()[:120]
        return None, [
            Drop(
                UNPARSEABLE,
                "No JSON object could be read from the response"
                + (" (it was empty)." if not head else f" (began: {head!r})."),
            )
        ]
    if not isinstance(payload, dict):
        return None, [
            Drop(
                NOT_AN_OBJECT,
                f"The response decoded to a {type(payload).__name__}, not an object.",
            )
        ]

    drops: list[Drop] = []
    blocked = {str(code).upper() for code in skip_areas}

    probability = (
        None
        if skip_probability
        else _level(
            payload.get("probability"),
            scale.probability_levels,
            "probability",
            drops,
            payload,
        )
    )

    impacts, impact_rationales = _impacts(payload, scale, blocked, drops)

    refs, invented = partition_refs(payload.get("evidence_refs"), allowed_refs)
    if not refs:
        drops.append(
            Drop(
                UNGROUNDED,
                (
                    "No citation pointed at evidence this risk was shown."
                    + (f" Cited instead: {', '.join(invented)}." if invented else "")
                ),
                raw=payload,
            )
        )
        return None, drops

    assessment = Assessment(
        probability=probability,
        probability_rationale=clean_text(
            payload.get("probability_rationale"), limit=MAX_RATIONALE_CHARS
        ),
        impacts=impacts,
        impact_rationales=impact_rationales,
        evidence_refs=refs,
        probability_confidence=confidence(payload.get("probability_confidence")),
        impact_confidence=confidence(payload.get("impact_confidence")),
    )
    if assessment.is_empty:
        drops.append(
            Drop(
                NOTHING_TO_SCORE,
                "The answer named neither a probability nor any impact area.",
                raw=payload,
            )
        )
        return None, drops
    return assessment, drops


def _impacts(
    payload: dict,
    scale: Scale,
    blocked: set[str],
    drops: list[Drop],
) -> tuple[dict[str, int], dict[str, str]]:
    """Per-area levels the scale actually holds, plus the reasoning for each.

    An area with a level and no rationale is kept. The rationale is what a reviewer reads
    to agree or disagree, and its absence is a poorer proposal — but a scored area with a
    citation behind it is still a judgement, and discarding it over missing prose would
    lose the part that carries the information.
    """
    raw_impacts = payload.get("impacts")
    if not isinstance(raw_impacts, dict):
        if raw_impacts is not None:
            drops.append(
                Drop(
                    NOT_AN_OBJECT,
                    f"'impacts' was a {type(raw_impacts).__name__}, not an object.",
                    raw=payload,
                )
            )
        return {}, {}

    raw_rationales = payload.get("impact_rationales")
    rationales_in = raw_rationales if isinstance(raw_rationales, dict) else {}

    levels = scale.impact_levels
    codes = scale.area_codes
    impacts: dict[str, int] = {}
    rationales: dict[str, str] = {}

    for key, value in raw_impacts.items():
        code = str(key).strip().upper()
        if code not in codes:
            drops.append(
                Drop(
                    UNKNOWN_AREA,
                    f"{code or '(blank)'} is not an impact area on this matrix.",
                    raw={"area": key, "level": value},
                )
            )
            continue
        if code in blocked:
            # Not a drop. A person has already ruled on this area and said so in the
            # prompt; the model answering anyway is a instruction-following miss worth
            # ignoring quietly rather than a defect worth reporting to a reviewer.
            continue
        level = _level(value, levels, f"impacts.{code}", drops, payload)
        if level is None:
            continue
        impacts[code] = level
        text = clean_text(rationales_in.get(key), limit=MAX_RATIONALE_CHARS)
        if not text:
            text = clean_text(rationales_in.get(code), limit=MAX_RATIONALE_CHARS)
        if text:
            rationales[code] = text

    return impacts, rationales


def _level(
    value: object,
    allowed: frozenset[int],
    where: str,
    drops: list[Drop],
    payload: dict,
) -> int | None:
    """An integer the scale holds, or nothing.

    ``None`` passes through silently — omission is the contract's way of abstaining. A
    number off the scale is reported, because it means the model did not read the scale it
    was given and every other number in the same answer is worth less for it.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        drops.append(
            Drop(
                OUT_OF_RANGE,
                f"{where} was {value!r}, which is not a level number.",
                raw=payload,
            )
        )
        return None
    if isinstance(value, float) and not value.is_integer():
        drops.append(
            Drop(
                OUT_OF_RANGE,
                f"{where} was {value!r}. This scale has no levels between its rungs.",
                raw=payload,
            )
        )
        return None
    level = int(value)
    if level not in allowed:
        drops.append(
            Drop(
                OUT_OF_RANGE,
                f"{where} was {level}, and this install's scale runs "
                f"{min(allowed)}–{max(allowed)}.",
                raw=payload,
            )
        )
        return None
    return level
