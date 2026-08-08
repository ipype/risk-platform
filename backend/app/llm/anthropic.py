"""The Messages API, over one HTTP POST.

**Raw ``httpx`` rather than the vendor SDK.** The SDK is a good library and it is the wrong
dependency here: this platform makes exactly one shape of call, the wire format for it is
four fields, and an SDK brings retry policy, its own timeout semantics and a release
cadence to track in exchange for saving about thirty lines. The deciding argument is the
run transcript — every generation records what was sent and what came back, and a thin
client makes that record literally the request and response rather than an SDK's rendering
of them.

**No retries.** A generation run is already a queued, resumable, append-only object with a
status and an error field; a failure that gets recorded and shown beats one that gets
silently retried three times and then recorded anyway, and a retry over a non-idempotent
paid call is a decision for the operator rather than a default. Re-running is one POST.

**No streaming.** Nothing here renders tokens as they arrive; the parser needs the whole
array before it can do anything at all.

**Truncation is surfaced, not repaired.** ``stop_reason == "max_tokens"`` comes back on the
completion and the generator records the window as truncated. Asking for a continuation
would produce a second array with no guarantee it joins the first, and quietly accepting a
cut-off array is how a window that found nine risks gets recorded as having found four.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.errors import LlmCallFailed
from app.llm.types import Completion, Message

__all__ = ["AnthropicProvider"]

#: How much of an error body is kept on the exception. Enough to see the API's own
#: message, short enough that a stray HTML error page does not become the error string.
ERROR_BODY_CHARS = 600


class AnthropicProvider:
    """One call to ``POST /v1/messages``."""

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        api_version: str,
        timeout_seconds: float,
    ) -> None:
        if not api_key:
            # Constructed only by the registry, which checks this too. Repeated here
            # because a provider built by hand in a script should fail at construction
            # rather than at the first call, halfway through a run that has already
            # written rows.
            raise LlmCallFailed(
                self.name, "No API key is configured, so no call can be made."
            )
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._timeout = timeout_seconds

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._api_version,
            "content-type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/v1/messages", json=payload, headers=headers
                )
        except httpx.HTTPError as exc:
            # The connection never completed. Named rather than wrapped in a generic
            # message because "read timeout after 120s" and "name resolution failed" send
            # an operator to two different places.
            raise LlmCallFailed(
                self.name, f"The request did not complete: {type(exc).__name__}: {exc}"
            ) from exc

        if response.status_code != httpx.codes.OK:
            raise LlmCallFailed(
                self.name,
                f"HTTP {response.status_code}: {response.text[:ERROR_BODY_CHARS]}",
                status_code=response.status_code,
            )

        return self._parse(response.json())

    def _parse(self, body: dict) -> Completion:
        """Pull the text out of the content blocks.

        The response is a *list* of blocks and only ``text`` ones carry prose. Joining
        them rather than taking ``content[0]`` is not defensive coding: the position of a
        text block is not guaranteed, and indexing by position is the standard way to read
        this API wrongly.
        """
        blocks = body.get("content")
        if not isinstance(blocks, list):
            raise LlmCallFailed(
                self.name, "The response carried no content blocks to read."
            )
        text = "\n".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        if not text:
            raise LlmCallFailed(
                self.name,
                "The response carried content blocks but no text in any of them.",
            )

        usage = body.get("usage") or {}
        return Completion(
            text=text,
            model=str(body.get("model") or self._model),
            stop_reason=body.get("stop_reason"),
            input_tokens=_as_int(usage.get("input_tokens")),
            output_tokens=_as_int(usage.get("output_tokens")),
            meta={"id": body.get("id"), "stop_sequence": body.get("stop_sequence")},
        )


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None
