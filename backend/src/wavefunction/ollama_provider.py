"""Discovery for the native Ollama API; model execution lives in ModelGateway."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


def ollama_context_window(config: dict, model: str, base_url: str | None = None) -> int:
    """Use only the selected server's catalogue to cap the configured context."""
    catalogue = (
        config.get("models", [])
        if not base_url or base_url == config.get("base_url", DEFAULT_OLLAMA_URL)
        else []
    )
    known = next((item for item in catalogue if item["id"] == model), {})
    requested = config.get("num_ctx", 32768)
    return min(requested, known["context_length"]) if known.get("context_length") else requested


class OllamaConnection(BaseModel):
    base_url: str = DEFAULT_OLLAMA_URL
    num_ctx: int = Field(default=32768, ge=1)

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path
        ):
            raise ValueError(
                "Use an Ollama server address such as http://127.0.0.1:11434, without /api or /v1"
            )
        try:
            _ = parsed.port
        except ValueError as error:
            raise ValueError("The server address has an invalid port") from error
        return value


async def discover_models(base_url: str) -> list[dict]:
    # Avoid environment proxies for the user's local server; remote addresses still work directly.
    async with httpx.AsyncClient(base_url=base_url, timeout=10, trust_env=False) as client:
        response = await client.get("/api/tags")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
            raise ValueError("The server did not return an Ollama model list")
        limit = asyncio.Semaphore(4)

        async def describe(item: dict) -> dict:
            name = item.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("The server returned a model without a name")
            async with limit:
                details = await client.post("/api/show", json={"model": name})
                details.raise_for_status()
            info = details.json()
            capabilities = info.get("capabilities", [])
            if not isinstance(capabilities, list):
                capabilities = []
            context = max(
                (
                    v
                    for k, v in info.get("model_info", {}).items()
                    if k.endswith(".context_length") and isinstance(v, int)
                ),
                default=0,
            )
            return {
                "id": name,
                "name": name,
                "capabilities": capabilities,
                "context_length": context,
                "size": item.get("size", 0),
                "description": "Tool calling supported"
                if "tools" in capabilities
                else "Tool calling unavailable — not suitable for agents",
            }

        return sorted(
            await asyncio.gather(*(describe(item) for item in payload["models"])),
            key=lambda item: item["name"],
        )
