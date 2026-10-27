"""Anthropic's own API, for Claude models on the user's API key.

A key, not a sign-in: Anthropic does not let other apps offer a claude.ai login or spend a Claude
subscription, and blocks the ones that try. Max and Team plans come with monthly API credit for a
key, which covers some of the same ground.

The catalogue is the Models API, which needs the key, and describes each model's context window,
output limit and the effort levels and thinking modes it takes. Those decide what a request may
send: an effort level the model lacks, or adaptive thinking on one that only has the older
budgeted kind, is a 400.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .reasoning import catalogue_efforts
from .secrets import SecretBackend


class AnthropicModelOption(BaseModel):
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
    adaptive_thinking: bool = False


class AnthropicCatalog:
    def __init__(self, secrets: SecretBackend):
        self.secrets = secrets

    async def list_models(self, api_key: str | None = None) -> list[dict[str, Any]]:
        """Every model the key can use. Raises RuntimeError without a key."""
        from anthropic import AsyncAnthropic

        key = api_key or self.secrets.get("anthropic_api_key")
        if not key:
            raise RuntimeError("Connect an Anthropic API key to list Claude models")
        async with AsyncAnthropic(api_key=key, max_retries=1, timeout=30) as client:
            raw = [model.model_dump(mode="json") async for model in client.models.list(limit=100)]
        return [model.model_dump(mode="json") for model in self.parse_models(raw)]

    @staticmethod
    def parse_models(models: list[dict[str, Any]]) -> list[AnthropicModelOption]:
        options = []
        for raw in models:
            model_id = str(raw.get("id") or "").strip()
            if not model_id:
                continue
            capabilities = raw.get("capabilities") or {}
            thinking = (capabilities.get("thinking") or {}).get("types") or {}
            options.append(
                AnthropicModelOption(
                    id=model_id,
                    name=str(raw.get("display_name") or model_id),
                    description="",
                    context_length=int(raw.get("max_input_tokens") or 0),
                    # Every Claude model in the catalogue reads images.
                    input_modalities=["text", "image"],
                    output_modalities=["text"],
                    supported_parameters=["tools"],
                    pricing={},
                    reasoning_efforts=catalogue_efforts("anthropic", raw),
                    max_output_tokens=raw.get("max_tokens") or None,
                    adaptive_thinking=bool((thinking.get("adaptive") or {}).get("supported")),
                )
            )
        # The API lists newest first, which is also the most useful order for the picker.
        return options
