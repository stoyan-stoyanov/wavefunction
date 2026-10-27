"""Context windows for models the app has no live source for, from the models.dev registry.

OpenRouter, Vercel and Ollama report each model's context length when the app lists them.
OpenAI, the ChatGPT sign-in and OpenAI-compatible servers do not, and LangChain's bundled
profiles are a copy of models.dev frozen at the library's release. A model newer than that
copy fell back to 32k tokens, which starved an agent into re-reading the same skill for a
quarter of an hour. The registry is fetched at startup, kept on disk, and refreshed daily.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
REFRESH_AFTER = 24 * 60 * 60
# Which models.dev providers serve each of the app's providers. A ChatGPT sign-in and a plain
# OpenAI key both serve OpenAI's ids. An OpenAI-compatible server can serve anything, so it is
# looked up everywhere, first-party providers first: the vendor's own figure beats a reseller's.
PROVIDERS = {
    "openai": ("openai",),
    "codex": ("openai",),
    "openrouter": ("openrouter",),
    "vercel": ("vercel",),
    "anthropic": ("anthropic",),
    "gemini": ("google",),
}
FIRST_PARTY = ("openai", "anthropic", "google", "xai", "mistral", "deepseek", "moonshotai", "zai")

Fetch = Callable[[], Awaitable[bytes]]


async def _download() -> bytes:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(MODELS_DEV_URL)
        response.raise_for_status()
        return response.content


def _index(raw: Any) -> dict[str, dict[str, int]]:
    """Provider id → model id → input token limit, from the registry's own shape."""
    limits: dict[str, dict[str, int]] = {}
    if not isinstance(raw, dict):
        return limits
    for provider_id, provider in raw.items():
        models = provider.get("models") if isinstance(provider, dict) else None
        for model_id, model in (models or {}).items():
            limit = model.get("limit") if isinstance(model, dict) else None
            # `input` is what the model accepts; `context` includes the output it will write.
            value = (limit or {}).get("input") or (limit or {}).get("context")
            if isinstance(value, int) and value > 0:
                limits.setdefault(provider_id, {})[model_id] = value
    return limits


def _tail(model: str) -> str:
    """`openai/gpt-6-astra:official` and `gpt-6-astra` are the same model."""
    return model.rsplit("/", 1)[-1].split(":", 1)[0]


class ModelRegistry:
    """The models.dev limits, loaded from a snapshot beside the app's data."""

    def __init__(self, path: Path):
        self.path = path
        self.limits: dict[str, dict[str, int]] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_bytes())
        except (OSError, ValueError):
            self.limits = {}
            return
        self.limits = _index(raw)

    def stale(self) -> bool:
        try:
            return time.time() - self.path.stat().st_mtime > REFRESH_AFTER
        except OSError:
            return True

    async def refresh(self, fetch: Fetch | None = None) -> bool:
        """Fetch a fresh copy once the one on disk is a day old.

        Never raises. The registry improves on the fallback; the app does not refuse to start,
        or to run offline, without it.
        """
        if not self.stale():
            return False
        try:
            content = await (fetch or _download)()
            limits = _index(json.loads(content))
            if not limits:
                raise ValueError("the registry listed no models")
        except Exception as error:  # noqa: BLE001 - every failure means "keep what we have"
            logger.warning("Could not refresh the model registry from %s: %s", MODELS_DEV_URL, error)
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(self.path)
        self.limits = limits
        return True

    def context_length(self, provider: str, model: str) -> int | None:
        """The input limit for a model, or None when the registry does not know it."""
        if provider in PROVIDERS:
            pools = [self.limits.get(name, {}) for name in PROVIDERS[provider]]
        elif provider == "openai-compatible":
            order = sorted(self.limits, key=lambda name: (name not in FIRST_PARTY, name))
            pools = [self.limits[name] for name in order]
        else:
            # Ollama reports its own window and the demo model has none.
            return None
        for pool in pools:
            if model in pool:
                return pool[model]
        tail = _tail(model)
        for pool in pools:
            for name, limit in pool.items():
                if _tail(name) == tail:
                    return limit
        return None
