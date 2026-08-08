"""The model seam, without a model.

The registry tests are the important ones. "No provider configured" and "provider
configured but no key" are the two states a real deployment sits in on its first day, and
both have to fail loudly rather than quietly picking something — a fake provider reached by
default would fill a reviewer's inbox with invented proposals indistinguishable from real
ones.

``TestMarkerContract`` is the load-bearing test in this file and it looks like a triviality.
It asserts that the regex ``llm/fake.py`` uses to find chunk ids matches what
``agents/risk_id.py`` actually renders. Every fake-backed test in the suite depends on that
agreement, and the two live in different packages with no import between them, so nothing
else would notice if the prompt format changed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.agents.risk_id import render_window
from app.agents.types import PackChunk, Window
from app.core.config import Settings
from app.core.errors import LlmCallFailed, LlmNotConfigured
from app.llm.anthropic import AnthropicProvider
from app.llm.fake import CHUNK_MARKER, FakeProvider
from app.llm.registry import get_provider
from app.llm.types import Message, USER

# No module-level ``pytest.mark.asyncio``: this file mixes sync and async tests, and
# ``pytest.ini`` already sets ``asyncio_mode = auto``, which collects the async ones
# without the mark and leaves the sync ones alone.


WINDOW = Window(
    document_id=1,
    document_label="Environmental consent",
    chunks=(
        PackChunk(
            ref="doc_chunk:12",
            text="The consent is valid for ninety days from issue.",
            section="Consents › Validity",
            locator={"page": 4},
            document_label="Environmental consent",
        ),
        PackChunk(
            ref="doc_chunk:13",
            text="Dewatering may not begin before the consent is granted.",
            document_label="Environmental consent",
        ),
    ),
)


class TestMarkerContract:
    def test_the_fake_finds_every_ref_the_prompt_renders(self) -> None:
        """If this fails, every fake-backed test in the suite is asserting nothing."""
        rendered = render_window(WINDOW)
        assert CHUNK_MARKER.findall(rendered) == ["doc_chunk:12", "doc_chunk:13"]

    def test_refs_are_rendered_in_window_order(self) -> None:
        found = CHUNK_MARKER.findall(render_window(WINDOW))
        assert found == [chunk.ref for chunk in WINDOW.chunks]


class TestFakeProvider:
    async def test_derives_a_candidate_citing_a_real_ref(self) -> None:
        provider = FakeProvider()
        completion = await provider.complete(
            system="s",
            messages=[Message(role=USER, content=render_window(WINDOW))],
            max_tokens=100,
            temperature=0.0,
        )
        payload = json.loads(completion.text)
        assert payload[0]["evidence_refs"] == ["doc_chunk:12"]

    async def test_abstains_when_the_prompt_carries_no_extracts(self) -> None:
        """An empty array is the correct answer to a window with nothing in it."""
        provider = FakeProvider()
        completion = await provider.complete(
            system="s",
            messages=[Message(role=USER, content="Project: Terminal")],
            max_tokens=100,
            temperature=0.0,
        )
        assert json.loads(completion.text) == []

    async def test_records_what_it_was_asked(self) -> None:
        provider = FakeProvider()
        await provider.complete(
            system="the system prompt",
            messages=[Message(role=USER, content="hello")],
            max_tokens=77,
            temperature=0.0,
        )
        assert provider.calls[0]["system"] == "the system prompt"
        assert provider.calls[0]["max_tokens"] == 77

    async def test_a_script_is_returned_in_order(self) -> None:
        provider = FakeProvider(script=["[]", '[{"title": "x"}]'])
        first = await provider.complete(
            system="", messages=[], max_tokens=10, temperature=0.0
        )
        second = await provider.complete(
            system="", messages=[], max_tokens=10, temperature=0.0
        )
        assert first.text == "[]"
        assert second.text.startswith("[{")

    async def test_running_out_of_script_is_the_finding(self) -> None:
        """More calls than the test expected is a fact about the generator, not a fixture
        problem to be papered over by cycling the script."""
        provider = FakeProvider(script=["[]"])
        await provider.complete(system="", messages=[], max_tokens=10, temperature=0.0)
        with pytest.raises(AssertionError, match="ran out of scripted"):
            await provider.complete(
                system="", messages=[], max_tokens=10, temperature=0.0
            )

    async def test_truncation_is_visible_on_the_completion(self) -> None:
        provider = FakeProvider(script=["["], stop_reason="max_tokens")
        completion = await provider.complete(
            system="", messages=[], max_tokens=1, temperature=0.0
        )
        assert completion.truncated is True


class TestRegistry:
    def test_unset_refuses_and_names_the_setting(self) -> None:
        with pytest.raises(LlmNotConfigured, match="LLM_PROVIDER"):
            get_provider(Settings(llm_provider=""))

    def test_anthropic_without_a_key_refuses(self) -> None:
        with pytest.raises(LlmNotConfigured, match="ANTHROPIC_API_KEY"):
            get_provider(
                Settings(llm_provider="anthropic", anthropic_api_key="", llm_model="m")
            )

    def test_anthropic_without_a_model_refuses(self) -> None:
        """The model string is a deployment choice; there is no default to fall back on."""
        with pytest.raises(LlmNotConfigured, match="LLM_MODEL"):
            get_provider(
                Settings(llm_provider="anthropic", anthropic_api_key="k", llm_model="")
            )

    def test_an_unknown_provider_names_the_known_ones(self) -> None:
        with pytest.raises(LlmNotConfigured, match="anthropic"):
            get_provider(Settings(llm_provider="wishful"))

    def test_fake_is_reachable_only_when_asked_for(self) -> None:
        provider = get_provider(Settings(llm_provider="fake"))
        assert provider.name == "fake"

    def test_anthropic_builds_when_fully_configured(self) -> None:
        provider = get_provider(
            Settings(llm_provider="anthropic", anthropic_api_key="k", llm_model="m")
        )
        assert provider.name == "anthropic"


def _provider(handler) -> AnthropicProvider:
    """An Anthropic provider whose transport is a function, so no socket is opened."""
    provider = AnthropicProvider(
        api_key="k",
        model="test-model",
        base_url="https://example.invalid",
        api_version="2023-06-01",
        timeout_seconds=1.0,
    )
    original = httpx.AsyncClient

    class _Client(original):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx.AsyncClient = _Client  # type: ignore[misc]
    provider._restore = lambda: setattr(httpx, "AsyncClient", original)  # type: ignore[attr-defined]
    return provider


class TestAnthropicProvider:
    async def test_reads_text_blocks_by_type_not_position(self) -> None:
        """``content[0]`` is the standard way to read this API wrongly."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": "msg_1",
                    "model": "test-model",
                    "stop_reason": "end_turn",
                    "content": [
                        {"type": "thinking", "thinking": "ignore me"},
                        {"type": "text", "text": "[]"},
                    ],
                    "usage": {"input_tokens": 11, "output_tokens": 2},
                },
            )

        provider = _provider(handler)
        try:
            completion = await provider.complete(
                system="s",
                messages=[Message(role=USER, content="u")],
                max_tokens=10,
                temperature=0.0,
            )
        finally:
            provider._restore()  # type: ignore[attr-defined]

        assert completion.text == "[]"
        assert completion.input_tokens == 11
        assert completion.stop_reason == "end_turn"

    async def test_an_error_status_carries_the_code_and_the_body(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rate limited, slow down")

        provider = _provider(handler)
        try:
            with pytest.raises(LlmCallFailed) as caught:
                await provider.complete(
                    system="s", messages=[], max_tokens=10, temperature=0.0
                )
        finally:
            provider._restore()  # type: ignore[attr-defined]

        assert caught.value.status_code == 429
        assert "rate limited" in str(caught.value)

    async def test_a_transport_failure_names_itself(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("no route")

        provider = _provider(handler)
        try:
            with pytest.raises(LlmCallFailed, match="ConnectTimeout"):
                await provider.complete(
                    system="s", messages=[], max_tokens=10, temperature=0.0
                )
        finally:
            provider._restore()  # type: ignore[attr-defined]

    async def test_content_with_no_text_is_a_failure_not_an_empty_answer(self) -> None:
        """An empty string would parse as "the model abstained", which it did not."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"model": "m", "content": [{"type": "tool_use", "name": "x"}]},
            )

        provider = _provider(handler)
        try:
            with pytest.raises(LlmCallFailed, match="no text"):
                await provider.complete(
                    system="s", messages=[], max_tokens=10, temperature=0.0
                )
        finally:
            provider._restore()  # type: ignore[attr-defined]

    async def test_a_key_is_required_at_construction(self) -> None:
        with pytest.raises(LlmCallFailed, match="No API key"):
            AnthropicProvider(
                api_key="",
                model="m",
                base_url="https://example.invalid",
                api_version="v",
                timeout_seconds=1.0,
            )
