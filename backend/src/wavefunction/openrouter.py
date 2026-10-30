from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from .reasoning import catalogue_efforts
from .secrets import SecretBackend

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterModelOption(BaseModel):
    id: str
    name: str
    description: str = ""
    context_length: int = 0
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    supported_parameters: list[str] = Field(default_factory=list)
    pricing: dict[str, str] = Field(default_factory=dict)
    reasoning_efforts: list[str] | None = None


class OpenRouterCatalog:
    def __init__(self, secrets: SecretBackend):
        self.secrets = secrets

    async def list_models(self, api_key: str | None = None) -> list[dict[str, Any]]:
        key = api_key or self.secrets.get("openrouter_api_key")
        if not key:
            raise RuntimeError("Add an OpenRouter API key in Settings to load models")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{OPENROUTER_BASE_URL}/models/user",
                headers={
                    "Authorization": f"Bearer {key}",
                    "HTTP-Referer": "https://www.wavefunction.si",
                    "X-OpenRouter-Title": "Wavefunction",
                },
            )
            response.raise_for_status()
        return [model.model_dump(mode="json") for model in self.parse_models(response.json())]

    @staticmethod
    def parse_models(payload: dict[str, Any]) -> list[OpenRouterModelOption]:
        options: list[OpenRouterModelOption] = []
        for raw in payload.get("data", []):
            model_id = str(raw.get("id") or "").strip()
            if not model_id:
                continue
            supported = [str(value) for value in raw.get("supported_parameters") or []]
            # A coding task calls tools on every step; a model that cannot is no use here.
            if "tools" not in supported:
                continue
            architecture = raw.get("architecture") or {}
            output_modalities = [str(value) for value in architecture.get("output_modalities") or []]
            if output_modalities and "text" not in output_modalities:
                continue
            pricing = {
                str(key): str(value) for key, value in (raw.get("pricing") or {}).items() if value is not None
            }
            options.append(
                OpenRouterModelOption(
                    id=model_id,
                    name=str(raw.get("name") or model_id),
                    description=str(raw.get("description") or ""),
                    context_length=int(raw.get("context_length") or 0),
                    input_modalities=[str(value) for value in architecture.get("input_modalities") or []],
                    output_modalities=output_modalities,
                    supported_parameters=supported,
                    pricing=pricing,
                    reasoning_efforts=catalogue_efforts("openrouter", raw),
                )
            )
        return sorted(options, key=lambda option: (option.name.casefold(), option.id))
