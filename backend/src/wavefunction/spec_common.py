"""The model a task runs on, shared by the task spec and the services that build models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .reasoning import ReasoningEffort

Provider = Literal[
    "demo", "openai", "openrouter", "vercel", "openai-compatible", "codex", "ollama", "anthropic", "gemini"
]


class ModelSpec(BaseModel):
    provider: Provider = "demo"
    model: str = "demo-reliable"
    base_url: str | None = None
    temperature: float = Field(default=0.1, ge=0, le=2)
    # None, and nothing is sent: the model's own maximum applies. A cap here only ever truncates
    # a reply — the model stops when it is done — and a reasoning model can spend the whole
    # allowance thinking and stop before writing a character, which is exactly what happened.
    # Kept as a field so a deliberate ceiling is still possible; it is simply not the default.
    max_tokens: int | None = Field(default=None, ge=1)
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="Provider default when omitted. Use a supported effort level, or enabled/none for a model with a thinking toggle.",
    )
