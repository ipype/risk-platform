"""Reading a model's answer, on terms both generators share.

Extracted when the second generator arrived rather than guessed at when the first one did.
The three functions here are guards, not conveniences — a bool that would otherwise become
a confidence of 1.0, a citation that was never sent, a field holding an essay — and a guard
that exists twice is a guard that gets fixed once. The ``bool`` check in :func:`confidence`
is the concrete example: it is subtle enough that a second copy would not have it.

Pure, like everything in this package. No database, no network, no clock.
"""

from __future__ import annotations

import json

__all__ = [
    "MAX_FIELD_CHARS",
    "MAX_REFS",
    "clean_text",
    "confidence",
    "decode",
    "partition_refs",
]

#: Guards against a model answering with an essay in one field. Well above anything a
#: sensible answer needs, low enough that a runaway generation cannot write a novel into
#: ``proposed_value``.
MAX_FIELD_CHARS = 2000

#: Cap on citations kept per answer. An answer citing eleven things is not better
#: evidenced than one citing three; it is a model listing its input back.
MAX_REFS = 5


def decode(raw: str, *, opener: str, closer: str) -> object | None:
    """JSON out of whatever the model actually sent, or ``None``.

    Tries the whole string first, then the widest ``opener…closer`` span in it. The second
    pass is not politeness towards a sloppy model: a system prompt that forbids prose is
    followed almost always and not always, and one apologetic sentence should not cost a
    call. Anything beyond that — repairing truncated JSON, closing brackets — is declined
    on purpose: a repaired payload is one whose contents nobody can attest to, and the
    truncation it hides is recorded on the run instead.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    start = text.find(opener)
    end = text.rfind(closer)
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass
    return None


def clean_text(value: object, *, limit: int = MAX_FIELD_CHARS) -> str:
    """Whitespace-normalised string, or empty. Never ``None``, never a coerced number."""
    return " ".join(str(value).split())[:limit] if isinstance(value, str) else ""


def confidence(value: object) -> float | None:
    """A number in 0..1, or nothing.

    ``bool`` is excluded explicitly because it is an ``int`` in Python and ``True`` would
    otherwise become a confidence of 1.0 — a model writing ``"confidence": true`` would
    become the most confident row in the inbox. Out-of-range numbers become ``None``
    rather than being clamped: a model answering 7 on a 0–1 scale has misread the
    contract, and clamping it to 1.0 turns a mistake into the highest confidence there is.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def partition_refs(
    value: object, allowed: frozenset[str], *, limit: int = MAX_REFS
) -> tuple[tuple[str, ...], list[str]]:
    """Citations that were in the pack, and the ones that were not.

    Both halves are returned. The invented ones do not become evidence, and they do go in
    the drop's message: "cited doc_chunk:9001, which was never sent" is the sentence that
    tells an operator their prompt or their model has a problem, and without it the run
    reports only that something was refused.
    """
    if not isinstance(value, list):
        return (), []
    kept: list[str] = []
    invented: list[str] = []
    for item in value:
        ref = item.strip() if isinstance(item, str) else ""
        if not ref:
            continue
        if ref in allowed:
            if ref not in kept:
                kept.append(ref)
        elif ref not in invented:
            invented.append(ref)
    return tuple(kept[:limit]), invented[:limit]
