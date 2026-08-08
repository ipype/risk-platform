"""A provider that never leaves the process.

**This is what the whole suite runs against.** A test that reaches a real model is not a
test: it is slow, it costs money, it fails when a network does, and it asserts against text
that is allowed to change. Every property 5.4 actually needs to hold — the prompt carries
resolvable chunk ids, ungrounded citations are dropped, duplicates are suppressed, a
proposal is raised per surviving candidate — is a property of *our* code given some model
output, and this class supplies the output.

**The default mode reads the prompt rather than ignoring it.** With no script, the fake
scans the user message for the ``[doc_chunk:N]`` markers the prompt renderer emits and
answers citing the first one it finds. That makes the fake's output a function of the real
prompt, so a change that stops rendering chunk ids — the single most damaging thing that
could quietly happen to this pipeline, because every citation would then be invented —
fails a test instead of shipping. A fake that returned a fixed string would pass.

**A script is for the failure cases.** Malformed JSON, a citation to a chunk that was never
sent, an unknown RBS prefix, an empty array, a response cut off at ``max_tokens``: each is
a scripted string, because each is a thing a real model does occasionally and our parser
has to survive every time.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from app.llm.types import Completion, Message

__all__ = ["FakeProvider", "CHUNK_MARKER", "REF_MARKER", "OBJECT_CLOSING"]

#: Must match what ``agents/risk_id.py`` renders. Asserted against the real renderer in
#: ``tests/test_llm_providers.py`` rather than trusted, because the two live in different
#: packages and a silent drift here would make every fake-backed test meaningless.
CHUNK_MARKER = re.compile(r"\[(doc_chunk:\d+)\]")

#: Any evidence identifier, whichever substrate it came from. The qualitative evaluation
#: prompt cites risks and activities as well as chunks.
REF_MARKER = re.compile(r"\[((?:doc_chunk|risk|activity):\d+)\]")

#: How the fake tells the two contracts apart: identification asks for an array and
#: qualitative evaluation asks for an object, and each renderer says so in its last line.
#: Dispatching on that is dispatching on a genuine difference between the prompts rather
#: than on a sentinel planted for tests — which is the whole property this class is here
#: to preserve. A test asserts both renderers still end this way.
OBJECT_CLOSING = "Return the JSON object now."


class FakeProvider:
    """Deterministic answers. No network, no clock, no randomness."""

    name = "fake"

    def __init__(
        self,
        script: Sequence[str] | None = None,
        *,
        model: str = "fake-model",
        stop_reason: str = "end_turn",
    ) -> None:
        self._script = list(script) if script is not None else None
        self._model = model
        self._stop_reason = stop_reason
        #: Every call, in order. Tests assert on what was actually sent — the taxonomy
        #: made it into the prompt, the window was not silently empty — which is the other
        #: half of what a fake is for.
        self.calls: list[dict] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> Completion:
        self.calls.append(
            {
                "system": system,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )

        if self._script is not None:
            if not self._script:
                raise AssertionError(
                    "FakeProvider ran out of scripted responses. The generator made more "
                    "calls than the test expected, which is itself the finding."
                )
            text = self._script.pop(0)
        else:
            text = self._derive(messages)

        return Completion(
            text=text,
            model=self._model,
            stop_reason=self._stop_reason,
            input_tokens=sum(len(m.content) // 4 for m in messages),
            output_tokens=len(text) // 4,
        )

    def _derive(self, messages: list[Message]) -> str:
        """An answer in whichever shape the prompt actually asked for.

        Abstains when the prompt carries no evidence marker at all, which is the correct
        answer to a window with nothing in it and is also what a broken renderer would
        produce. Both surface as "no proposals raised", and the test that distinguishes
        them is the one asserting the marker is present.
        """
        body = "\n".join(m.content for m in messages if m.role == "user")
        if OBJECT_CLOSING in body:
            return self._derive_assessment(body)
        refs = CHUNK_MARKER.findall(body)
        if not refs:
            return "[]"
        prefix = _first_taxonomy_prefix(body) or "OTH-010"
        return json.dumps(
            [
                {
                    "title": f"Derived finding from {refs[0]}",
                    "cause": "the document records a constraint the plan does not carry",
                    "event": "the constraint is discovered during execution",
                    "effect": "rework and delay to the affected work",
                    "subcategory_prefix": prefix,
                    "evidence_refs": [refs[0]],
                    "rationale": f"Stated in the extract shown as {refs[0]}.",
                    "confidence": 0.5,
                }
            ]
        )


    def _derive_assessment(self, body: str) -> str:
        """A qualitative evaluation citing the first evidence the prompt actually named.

        Reads the impact areas and the level rungs out of the rendered scale rather than
        assuming a 5x5. That is the same property the array branch holds for chunk ids: a
        renderer that stopped sending the scale would produce answers full of area codes
        the model was never shown, the parser would drop every one of them, and the test
        that notices is this one rather than a client six months later.
        """
        refs = REF_MARKER.findall(body)
        if not refs:
            return json.dumps(
                {
                    "probability": None,
                    "probability_rationale": "Nothing retrieved bears on this.",
                    "impacts": {},
                    "impact_rationales": {},
                    "evidence_refs": [],
                }
            )
        areas = _area_codes(body)[:2]
        level = _lowest_level(body)
        return json.dumps(
            {
                "probability": level,
                "probability_rationale": f"Derived from the evidence shown as {refs[0]}.",
                "probability_confidence": 0.5,
                "impacts": {code: level for code in areas},
                "impact_rationales": {code: f"Derived from {refs[0]}." for code in areas},
                "impact_confidence": 0.4,
                "evidence_refs": [refs[0]],
            }
        )


_PREFIX = re.compile(r"\b([A-Z]{3}-\d{3})\b")


def _first_taxonomy_prefix(body: str) -> str | None:
    match = _PREFIX.search(body)
    return match.group(1) if match else None


#: ``COST — Cost:`` as ``agents/qual_eval.render_scale`` writes an area heading.
_AREA_HEAD = re.compile(r"^([A-Z][A-Z0-9_]{1,15}) \u2014 ", re.MULTILINE)

#: ``  1  Rare`` — one rung, as either scale renders it.
_LEVEL_ROW = re.compile(r"^ {2}(\d+)  ", re.MULTILINE)


def _area_codes(body: str) -> list[str]:
    seen: list[str] = []
    for code in _AREA_HEAD.findall(body):
        if code not in seen:
            seen.append(code)
    return seen


def _lowest_level(body: str) -> int:
    """The smallest rung the prompt rendered. Deliberately not a fixed 3.

    A fake that always answered the middle of a 5-point scale would pass on an install
    whose scale starts at 0 or runs to 4, and the parser's range check — the thing that
    stops an off-scale score reaching the register — would never be exercised by the
    suite against anything but the default matrix.
    """
    levels = [int(n) for n in _LEVEL_ROW.findall(body)]
    return min(levels) if levels else 1
