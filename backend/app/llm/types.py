"""What a model call looks like from this side of the seam.

**One method, text in and text out.** No streaming, no tool schemas, no message history
beyond what a single call needs. Every generator in P5 asks a model to read something and
answer in JSON, and a richer interface would be surface built for callers that do not
exist. When one arrives — a multi-turn workshop facilitator, most likely — it gets its own
method rather than a parameter on this one.

**The completion carries how it ended, not only what it said.** ``stop_reason`` is the
difference between a model that finished and a model that hit the output ceiling mid-array,
and the second produces text that parses cleanly right up to the point where it does not.
A generator that cannot tell those apart records a short answer as a confident one.

**Usage is recorded because a generator that costs money should say how much.** Nullable,
because a provider is allowed not to report it and a zero would be a claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = ["Completion", "Message", "Provider", "SYSTEM", "USER", "ASSISTANT"]

SYSTEM = "system"
USER = "user"
ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Message:
    """One turn. ``role`` is ``user`` or ``assistant``; the system prompt is separate.

    Separate because that is how the Anthropic API models it, and flattening a system
    prompt into the first user turn changes how the model weights it. A provider that
    wants them merged can merge them; one that does not cannot un-merge them.
    """

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class Completion:
    """What came back."""

    text: str
    model: str
    #: ``end_turn``, ``max_tokens``, ``stop_sequence`` — whatever the provider reported,
    #: unmapped. A vocabulary of our own would have to be kept in step with every
    #: provider's, and the only value any caller reads is "did this get cut off".
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: Provider-specific extras kept for the run transcript. Never read by logic.
    meta: dict = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        """The model ran out of room rather than finishing.

        Load-bearing: a truncated JSON array is either unparseable — which is caught — or,
        far worse, parseable as a shorter array that nobody knows was cut short.
        """
        return self.stop_reason == "max_tokens"


@runtime_checkable
class Provider(Protocol):
    """Anything that can answer a prompt.

    ``name`` is stored on the generation run beside ``model``: "which provider" and "which
    model" are different questions once a deployment can point at a proxy, and a run that
    records only the model string cannot answer the first one.
    """

    name: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> Completion: ...
