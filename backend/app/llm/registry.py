"""Which provider a deployment is pointed at.

**Unset is the default, and unset refuses.** Defaulting to the fake would let a real
deployment fill a reviewer's inbox with invented proposals that look exactly like real
ones; defaulting to a live provider would let a misconfigured one start spending money on
first use. Neither is a default worth having, so ``LLM_PROVIDER`` has to be said out loud
and a run that is dispatched without it fails immediately with the name of the setting.

The fake is registered here rather than only in tests so that a demo install can be told
to use it deliberately. It is not reachable by accident.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.errors import LlmNotConfigured
from app.llm.anthropic import AnthropicProvider
from app.llm.fake import FakeProvider
from app.llm.types import Provider

__all__ = ["PROVIDERS", "get_provider"]

ANTHROPIC = "anthropic"
FAKE = "fake"

PROVIDERS: tuple[str, ...] = (ANTHROPIC, FAKE)


def get_provider(settings: Settings | None = None) -> Provider:
    """Build the configured provider, or say exactly what is missing.

    Takes settings rather than reading the module-level singleton so a test can build one
    against an overridden configuration without touching global state — the same reason
    the sim assembly takes its inputs instead of fetching them.
    """
    config = settings or get_settings()
    choice = (config.llm_provider or "").strip().lower()

    if not choice:
        raise LlmNotConfigured(
            "No LLM provider is configured. Set LLM_PROVIDER to one of: "
            f"{', '.join(PROVIDERS)}. There is deliberately no default — a generator "
            "that silently picks one either invents proposals or spends money."
        )

    if choice == FAKE:
        return FakeProvider(model=config.llm_model or "fake-model")

    if choice == ANTHROPIC:
        if not config.anthropic_api_key:
            raise LlmNotConfigured(
                "LLM_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is empty."
            )
        if not config.llm_model:
            raise LlmNotConfigured(
                "LLM_PROVIDER is 'anthropic' but LLM_MODEL is empty. The model string is "
                "a deployment choice and is never hard-coded in application code."
            )
        return AnthropicProvider(
            api_key=config.anthropic_api_key,
            model=config.llm_model,
            base_url=config.anthropic_base_url,
            api_version=config.anthropic_api_version,
            timeout_seconds=config.llm_timeout_seconds,
        )

    raise LlmNotConfigured(
        f"{choice!r} is not a provider this build knows. One of: {', '.join(PROVIDERS)}."
    )
