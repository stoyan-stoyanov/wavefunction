"""OpenAI SDK adapters, loaded for inference after the workspace can be shown."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_openai import ChatOpenAI

from .codex import _item_text, with_cache_key


class ChatOpenAIDirect(ChatOpenAI):
    """OpenAI's own API, each request keyed to its conversation for the prompt cache."""

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        return with_cache_key(super()._get_request_payload(input_, stop=stop, **kwargs))


class ChatCodex(ChatOpenAI):
    """`ChatOpenAI` with the three things the ChatGPT backend does differently.

    Not a fork: everything about the Responses API — streaming, tool calls, reasoning items,
    structured output — is already right, and only the request envelope differs. So this is one
    override on the single method that builds it.

    1. The system prompt goes in `instructions`. Sent as an input item with role `system` it is
       accepted and then ignored, which is the worst kind of wrong: an agent with no system
       prompt still answers, just as though nobody had configured it.
    2. `store` must be false. The endpoint rejects true outright, and LangChain sends true by
       default whenever a reasoning model is in play.
    3. `max_output_tokens` is not accepted. The Codex client sends no ceiling at all, and this
       app's default is already no ceiling — but a spec with a deliberate one must not turn into
       a request the endpoint refuses.
    """

    def _get_request_payload(
        self, input_: LanguageModelInput, *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict:
        payload = with_cache_key(super()._get_request_payload(input_, stop=stop, **kwargs))
        if key := payload.get("prompt_cache_key"):
            # Per-request headers: mutating shared client defaults would mix concurrent chats.
            payload["extra_headers"] = {
                **(payload.get("extra_headers") or {}),
                "session-id": key,
                "thread-id": key,
            }
        payload["store"] = False
        payload.pop("max_output_tokens", None)

        items = payload.get("input")
        if isinstance(items, list):
            instructions: list[str] = []
            kept: list[Any] = []
            for item in items:
                if isinstance(item, dict) and item.get("role") in ("system", "developer"):
                    if text := _item_text(item.get("content")):
                        instructions.append(text)
                else:
                    kept.append(item)
            if instructions:
                # Joined rather than overwritten: middleware can add a second system message,
                # and dropping either one silently changes how the agent behaves.
                existing = payload.get("instructions")
                if isinstance(existing, str) and existing.strip():
                    instructions.insert(0, existing)
                payload["instructions"] = "\n\n".join(instructions)
                payload["input"] = kept
        return payload
