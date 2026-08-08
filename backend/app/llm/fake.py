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

__all__ = ["FakeProvider", "CHUNK_MARKER"]

#: Must match what ``agents/risk_id.py`` renders. Asserted against the real renderer in
#: ``tests/test_llm_providers.py`` rather than trusted, because the two live in different
#: packages and a silent drift here would make every fake-backed test meaningless.
CHUNK_MARKER = re.compile(r"\[(doc_chunk:\d+)\]")


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
        """One well-formed candidate citing the first chunk the prompt actually named.

        Abstains — an empty array — when the prompt carries no chunk marker at all, which
        is the correct answer to a window with nothing in it and is also what a broken
        renderer would produce. Both surface as "no proposals raised", and the test that
        distinguishes them is the one asserting the marker is present.
        """
        body = "\n".join(m.content for m in messages if m.role == "user")
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


_PREFIX = re.compile(r"\b([A-Z]{3}-\d{3})\b")


def _first_taxonomy_prefix(body: str) -> str | None:
    match = _PREFIX.search(body)
    return match.group(1) if match else None
