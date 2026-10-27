"""Google's Gemini API, for Gemini models on the user's API key.

A key, not a sign-in: Google's terms forbid other apps from using the Gemini CLI's or
Antigravity's Google login, and suspends accounts that do. A Google AI Pro or Ultra plan helps
only through the Cloud credits it adds to the user's own key.

The native API rather than Google's OpenAI-compatible one: Gemini 3 and later refuse a tool call
replayed without the thought signature it came with, and LangChain's OpenAI model drops it.

The catalogue is the Models API, which needs the key. It gives each model's context window and
output limit and whether it thinks, but not which thinking levels it takes; those come from the
model profiles LangChain ships, and a model newer than them is left to its own default.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .reasoning import GEMINI_EFFORTS
from .secrets import SecretBackend

# Models that `generateContent` lists but an agent cannot run on: speech, image generation,
# embeddings and the live audio models.
UNSUITED = ("-tts", "-image", "embedding", "-live", "-audio", "-translate")


class GeminiModelOption(BaseModel):
    """The shape the OpenRouter and Vercel pickers already render, plus what a request needs."""

    id: str
    name: str
    description: str
    context_length: int
    input_modalities: list[str]
    output_modalities: list[str]
    supported_parameters: list[str]
    pricing: dict[str, str]
    reasoning_efforts: list[str] | None = None
    max_output_tokens: int | None = None


class GeminiCatalog:
    def __init__(self, secrets: SecretBackend):
        self.secrets = secrets

    async def list_models(self, api_key: str | None = None) -> list[dict[str, Any]]:
        """Every Gemini model the key can use for chat. Raises RuntimeError without a key."""
        from google import genai
        from google.genai import types

        key = api_key or self.secrets.get("gemini_api_key")
        if not key:
            raise RuntimeError("Connect a Gemini API key to list Gemini models")
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30_000))
        async with client.aio as aclient:
            raw = [
                model.model_dump(mode="json")
                async for model in await aclient.models.list(config={"page_size": 1000})
            ]
        return [model.model_dump(mode="json") for model in self.parse_models(raw)]

    @staticmethod
    def parse_models(models: list[dict[str, Any]]) -> list[GeminiModelOption]:
        from langchain_google_genai.data._profiles import _PROFILES

        options = []
        for raw in models:
            model_id = str(raw.get("name") or "").removeprefix("models/")
            if (
                not model_id.startswith("gemini-")
                or any(part in model_id for part in UNSUITED)
                or "generateContent" not in (raw.get("supported_actions") or [])
            ):
                continue
            profile = _PROFILES.get(model_id) or {}
            if profile.get("tool_calling") is False:
                continue
            if raw.get("thinking") is False:
                efforts: list[str] | None = []
            else:
                levels = profile.get("reasoning_effort_levels")
                efforts = [level for level in GEMINI_EFFORTS if level in levels] if levels else None
            options.append(
                GeminiModelOption(
                    id=model_id,
                    name=str(raw.get("display_name") or model_id),
                    description=str(raw.get("description") or ""),
                    context_length=int(raw.get("input_token_limit") or 0),
                    input_modalities=["text", "image"],
                    output_modalities=["text"],
                    supported_parameters=["tools"],
                    pricing={},
                    reasoning_efforts=efforts,
                    max_output_tokens=raw.get("output_token_limit") or None,
                )
            )
        return sorted(options, key=lambda option: (option.name.casefold(), option.id))
