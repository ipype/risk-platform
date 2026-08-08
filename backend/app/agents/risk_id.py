"""The risk identification pass: read a window of project documents, draft risks.

Pure. Text and dataclasses in, text and dataclasses out. No database, no network, no clock,
no randomness — the same boundary ``app/sim/`` holds, and for a sharper reason: the only
part of this stage that can be *checked* is the part that decides whether a model's answer
is admissible, and a check tangled up with an HTTP client is a check nobody runs.

**A sweep, not a query.** Retrieval answers "what does the corpus say about X". Risk
identification has no X — the whole point is to surface what nobody has thought to ask
about yet — so the corpus is walked window by window and every chunk gets read exactly
once. ``services/evidence.py`` is still the right interface for the generators that *are*
query-shaped (a probability suggestion asks about a named risk), and this one uses its
reference format without using its search.

**Grounding is enforced here, on the way back.** The model is told to cite only the chunks
it was shown, and :func:`parse` then drops any candidate whose citations were not in the
pack. Both halves are needed: the instruction is what makes compliance likely, and the
check is what makes it true. A citation that resolves is the entire basis on which a
reviewer can trust an inbox they did not fill themselves, so the check is not a nicety —
it is the feature.

**Nothing here scores, ranks, or decides.** A candidate that parses is a candidate. What
becomes of it — deduplication, precedent, whether it is worth a reviewer's attention — is
the service's business, and keeping it out of this module is what stops the prompt version
becoming a version of the ranking too.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.agents._parsing import (
    MAX_FIELD_CHARS,
    MAX_REFS,
    clean_text,
    confidence,
    decode,
    partition_refs,
)
from app.agents.types import (
    INCOMPLETE,
    NOT_AN_ARRAY,
    UNGROUNDED,
    UNKNOWN_CATEGORY,
    UNPARSEABLE,
    Candidate,
    Drop,
    PackChunk,
    TaxonomyEntry,
    Window,
)

__all__ = [
    "PROMPT_VERSION",
    "CLOSING_LINE",
    "SYSTEM_PROMPT",
    "MAX_TITLE_CHARS",
    "build_messages",
    "build_windows",
    "parse",
    "render_window",
    "render_taxonomy",
]

#: Bumped whenever the wording, the output contract or the taxonomy rendering changes.
#: Stored on every proposal (``generator_prompt_version``) so "which prompt version gets
#: edited most" is a GROUP BY rather than an archaeology exercise. The date is part of it
#: because a version number alone stops being informative around the fourth revision.
PROMPT_VERSION = "risk-id/v1/2026-08-08"

#: The last line of the user turn. A real part of the contract rather than a flourish:
#: this stage asks for an array and qualitative evaluation asks for an object, and
#: ``llm/fake.py`` dispatches on the difference so its answers stay a function of the
#: actual prompt. A test asserts the two renderers still disagree here.
CLOSING_LINE = "Return the JSON array now."

#: ``Risk.title`` is ``String(300)``. Clipped rather than dropped: an over-long title is a
#: style failure, and refusing a well-evidenced risk over one is the wrong trade.
MAX_TITLE_CHARS = 300

#: ``MAX_FIELD_CHARS`` and ``MAX_REFS`` live in ``agents/_parsing.py`` and are re-exported
#: here because they were named here first and this module's tests address them by this
#: name. They are shared with every other generator rather than copied, which is the point
#: of the move: a guard that exists twice is a guard that gets fixed once.


SYSTEM_PROMPT = """\
You are a risk manager running the identification stage of a quantitative risk analysis \
for a capital infrastructure project, following AACE International RP 57R-09.

You are shown extracts from real project documents. Your job is to identify project risks \
that those extracts give you a concrete reason to believe in.

Write every risk as a cause-event-effect statement in three separate fields:
- cause: the condition that already exists, stated as fact.
- event: the uncertain thing that may happen because of it.
- effect: the consequence for project cost, schedule, scope or quality.

Rules that matter more than completeness:
1. Cite the extract that gives you the reason. Use only the bracketed identifiers shown \
with the extracts, exactly as written. Never cite an identifier you were not shown, and \
never invent one.
2. An issue that has already happened is not a risk. A generic risk that any project of \
this type carries is not a finding unless this document gives a specific reason for it.
3. If the extracts are boilerplate, a table of contents, a distribution list, or otherwise \
say nothing about how this project could go wrong, return an empty array. Returning \
nothing is a correct and useful answer. Padding is not.
4. Do not estimate probability or impact. That is a separate elicitation with the people \
who own the work.

Answer with a JSON array and nothing else. No prose before it, no prose after it, no \
markdown fences. Each element:

{"title": "short name for the risk, under 20 words",
 "cause": "the existing condition",
 "event": "the uncertain occurrence",
 "effect": "the consequence to the project",
 "subcategory_prefix": "one code from the taxonomy given below",
 "evidence_refs": ["doc_chunk:12"],
 "rationale": "one or two sentences saying what in the extract supports this",
 "confidence": 0.6}

confidence is 0 to 1 and is how sure you are that this is a real risk for this project on \
this evidence. Omit the field entirely if you cannot judge it. Never write 0 to mean \
"unsure" — omission means unsure, and 0 means "certainly not a risk", which would be a \
reason not to raise it at all.\
"""


# --------------------------------------------------------------------------------------
# windowing
# --------------------------------------------------------------------------------------


def build_windows(
    chunks: Sequence[PackChunk],
    *,
    document_ids: Sequence[int],
    max_chars: int,
    max_windows: int,
) -> tuple[list[Window], bool]:
    """Group chunks into per-document packs no larger than ``max_chars``.

    ``chunks`` and ``document_ids`` are parallel sequences — the chunk dataclass carries a
    document *label* for rendering but not an id, because an id is a database fact and this
    package does not hold those. Returning the truncation flag rather than logging it is
    the declared-approximation rule: a run that read six documents out of nine has to say
    so on its own face, not in a log line nobody reads next to the number.

    A single chunk larger than ``max_chars`` gets a window to itself rather than being
    split. Splitting would put half a clause in one call and half in another, and the
    locator boundary rule that 5.2 enforced on ingestion exists precisely so that a chunk
    is the smallest thing worth citing.
    """
    windows: list[Window] = []
    current: list[PackChunk] = []
    current_id: int | None = None
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars, current_id
        if current and current_id is not None:
            windows.append(
                Window(
                    document_id=current_id,
                    document_label=current[0].document_label,
                    chunks=tuple(current),
                )
            )
        current = []
        current_chars = 0

    for chunk, document_id in zip(chunks, document_ids, strict=True):
        size = len(chunk.text)
        if current_id is not None and (
            document_id != current_id or (current and current_chars + size > max_chars)
        ):
            flush()
        current_id = document_id
        current.append(chunk)
        current_chars += size
    flush()

    truncated = len(windows) > max_windows
    return windows[:max_windows], truncated


# --------------------------------------------------------------------------------------
# prompt
# --------------------------------------------------------------------------------------


def render_taxonomy(taxonomy: Iterable[TaxonomyEntry]) -> str:
    """The RBS as the model is allowed to see it.

    Sent in full rather than pre-filtered to a likely category. Filtering would decide the
    categorisation before the model has read the extract, and the categorisation is one of
    the things being asked for.
    """
    lines: list[str] = []
    last_category = None
    for entry in taxonomy:
        if entry.category_name != last_category:
            lines.append(f"{entry.category_name}:")
            last_category = entry.category_name
        lines.append(f"  {entry.prefix}  {entry.name}")
    return "\n".join(lines)


def render_window(window: Window) -> str:
    """The extracts, each headed by the identifier the model must cite.

    The bracketed identifier is the contract with :func:`parse` and with
    ``llm/fake.py``'s marker regex. Changing its shape is a prompt version bump and breaks
    a test on purpose.
    """
    parts = [f"Document: {window.document_label}"]
    for chunk in window.chunks:
        head = f"[{chunk.ref}]"
        if chunk.section:
            head += f"  (section: {chunk.section})"
        if chunk.locator:
            head += f"  ({_locator_label(chunk.locator)})"
        parts.append(f"{head}\n{chunk.text.strip()}")
    return "\n\n".join(parts)


def build_messages(
    window: Window,
    taxonomy: Sequence[TaxonomyEntry],
    *,
    project_name: str,
) -> str:
    """The single user turn for one window.

    Returns the content rather than a ``Message`` so the caller decides how to wrap it —
    the provider seam owns the message shape and this module owns the words.
    """
    return (
        f"Project: {project_name}\n\n"
        "Risk breakdown structure — use exactly one of these codes as "
        "subcategory_prefix:\n"
        f"{render_taxonomy(taxonomy)}\n\n"
        "Extracts to read:\n\n"
        f"{render_window(window)}\n\n"
        f"{CLOSING_LINE}"
    )


def _locator_label(locator: dict) -> str:
    if "page" in locator:
        return f"page {locator['page']}"
    if "sheet" in locator:
        return f"sheet {locator['sheet']}"
    return ", ".join(f"{k} {v}" for k, v in sorted(locator.items()))


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------


def parse(
    raw: str,
    *,
    allowed_refs: frozenset[str],
    known_prefixes: frozenset[str],
) -> tuple[list[Candidate], list[Drop]]:
    """Turn one response into admissible candidates, and say what was refused.

    Never raises. Every failure mode a model has — prose around the array, a fenced block,
    a single object instead of a list, a missing field, an invented citation — comes back
    as a :class:`Drop` with a reason, because a generation over twenty windows must not
    lose nineteen good ones to the twentieth's formatting.
    """
    payload, failure = _decode(raw)
    if failure is not None:
        return [], [failure]

    if not isinstance(payload, list):
        return [], [
            Drop(
                NOT_AN_ARRAY,
                f"The response decoded to a {type(payload).__name__}, not an array.",
            )
        ]

    kept: list[Candidate] = []
    drops: list[Drop] = []

    for item in payload:
        if not isinstance(item, dict):
            drops.append(
                Drop(NOT_AN_ARRAY, f"An array element was a {type(item).__name__}.")
            )
            continue

        fields = {
            name: _text(item.get(name))
            for name in ("title", "cause", "event", "effect", "rationale")
        }
        missing = [name for name, value in fields.items() if not value]
        if missing:
            drops.append(
                Drop(
                    INCOMPLETE,
                    "Missing or blank: " + ", ".join(missing) + ".",
                    raw=item,
                )
            )
            continue

        prefix = _text(item.get("subcategory_prefix")).upper()
        if prefix not in known_prefixes:
            drops.append(
                Drop(
                    UNKNOWN_CATEGORY,
                    f"{prefix or '(blank)'} is not in this install's RBS.",
                    raw=item,
                )
            )
            continue

        refs, invented = _refs(item.get("evidence_refs"), allowed_refs)
        if not refs:
            drops.append(
                Drop(
                    UNGROUNDED,
                    (
                        "No citation pointed at an extract in this window."
                        + (f" Cited instead: {', '.join(invented)}." if invented else "")
                    ),
                    raw=item,
                )
            )
            continue

        kept.append(
            Candidate(
                title=fields["title"][:MAX_TITLE_CHARS],
                cause=fields["cause"],
                event=fields["event"],
                effect=fields["effect"],
                subcategory_prefix=prefix,
                evidence_refs=refs,
                rationale=fields["rationale"],
                confidence=_confidence(item.get("confidence")),
            )
        )

    return kept, drops


def _decode(raw: str) -> tuple[object, Drop | None]:
    """JSON out of whatever the model actually sent.

    The shared decoder in ``agents/_parsing.py`` does the work; this adds the identification
    stage's own drop reason, because the caller needs a :class:`Drop` and the decoder is
    used by generators whose failure vocabulary differs.
    """
    payload = decode(raw, opener="[", closer="]")
    if payload is not None:
        return payload, None
    text = raw.strip()
    return None, Drop(
        UNPARSEABLE,
        "No JSON array could be read from the response"
        + (" (it was empty)." if not text else f" (began: {text[:120]!r})."),
    )


def _text(value: object) -> str:
    return clean_text(value, limit=MAX_FIELD_CHARS)


def _refs(value: object, allowed: frozenset[str]) -> tuple[tuple[str, ...], list[str]]:
    """Citations that were in the pack, and the ones that were not.

    Both halves are returned. The invented ones do not become evidence, and they do go in
    the drop's message: "cited doc_chunk:9001, which was never sent" is the sentence that
    tells an operator their prompt or their model has a problem, and without it the run
    reports only that something was refused.
    """
    return partition_refs(value, allowed, limit=MAX_REFS)


def _confidence(value: object) -> float | None:
    return confidence(value)
