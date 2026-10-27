"""Vercel's AI Gateway, as a second way to reach the same models.

OpenRouter routes through a shared pool, and when that pool is busy every model behind it is
busy at once — which is how `openai/gpt-5.6-luna` came to fail nine runs in an evening with
nothing wrong on this machine. A second gateway to the same model is the answer to that, not a
different model.

The API is OpenAI-compatible and the model ids are provider-prefixed in the same shape
(`openai/gpt-5.6-luna`), so a spec moves between the two by changing its provider and nothing
else. The model list is public — no key needed to browse — which means the picker can show what
is available before anyone has pasted anything.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from .reasoning import catalogue_efforts
from .secrets import SecretBackend

VERCEL_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"


class VercelModelOption(BaseModel):
    """Normalised to the same shape the OpenRouter picker already renders."""

    id: str
    name: str
    description: str
    context_length: int
    input_modalities: list[str]
    output_modalities: list[str]
    supported_parameters: list[str]
    pricing: dict[str, str]
    reasoning_efforts: list[str] | None = None


class VercelGatewayCatalog:
    def __init__(self, secrets: SecretBackend):
        self.secrets = secrets

    async def list_models(self, api_key: str | None = None) -> list[dict[str, Any]]:
        """Every language model the gateway offers.

        The key is sent when there is one and the call works without it: the catalogue is public,
        and being able to see the models before connecting is the difference between "add a key
        and hope" and "add a key to use this one".
        """
        key = api_key or self.secrets.get("vercel_api_key")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{VERCEL_GATEWAY_BASE_URL}/models", headers=headers)
            response.raise_for_status()
        return [model.model_dump(mode="json") for model in self.parse_models(response.json())]

    @staticmethod
    def parse_models(payload: dict[str, Any]) -> list[VercelModelOption]:
        options: list[VercelModelOption] = []
        for raw in payload.get("data", []):
            model_id = str(raw.get("id") or "").strip()
            # `type` separates language models from embedding and image ones, which an agent
            # cannot be built on and which would pad the picker with unusable rows.
            if not model_id or str(raw.get("type") or "language") != "language":
                continue
            pricing = {
                str(key): str(value) for key, value in (raw.get("pricing") or {}).items() if value is not None
            }
            # The gateway does not publish a per-model parameter list the way OpenRouter does.
            # It is OpenAI-compatible throughout, so the parameters the app relies on are the
            # ones the OpenAI API defines, and the picker's filter has nothing to filter on.
            options.append(
                VercelModelOption(
                    id=model_id,
                    name=str(raw.get("name") or model_id),
                    description=str(raw.get("description") or ""),
                    context_length=int(raw.get("context_window") or 0),
                    input_modalities=["text"],
                    output_modalities=["text"],
                    supported_parameters=["tools"],
                    pricing=pricing,
                    reasoning_efforts=catalogue_efforts("vercel", raw),
                )
            )
        return sorted(options, key=lambda option: (option.name.casefold(), option.id))
