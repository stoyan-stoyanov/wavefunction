"""Reasoning settings and provider parameter mapping."""

from __future__ import annotations

import re
from typing import Any, Literal, get_args

ReasoningEffort = Literal["none", "enabled", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
EFFORTS = list(get_args(ReasoningEffort))
GATEWAY_EFFORTS = [value for value in EFFORTS if value not in {"enabled", "ultra"}]
# The efforts the Responses API takes. ChatGPT's model catalogue also lists modes the Codex
# CLI implements itself, such as "ultra" (maximum reasoning plus its automatic delegation),
# and marks nothing to tell them apart; sent, those are refused.
RESPONSES_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
# Claude's `output_config.effort`. Thinking itself is adaptive and, on current models, cannot be
# turned off, so there is no "none".
ANTHROPIC_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# Gemini's `thinking_level`. Which of them a model takes varies, and the Models API does not say.
GEMINI_EFFORTS = ["minimal", "low", "medium", "high"]


def catalogue_efforts(provider: str, raw: dict) -> list[str] | None:
    """None means unspecified, [] means no advertised control. Preserve that distinction."""
    if provider == "codex":
        levels = raw.get("supported_reasoning_levels")
        if not isinstance(levels, list):
            return None
        values = [item.get("effort") if isinstance(item, dict) else item for item in levels]
        values = [value for value in values if value in RESPONSES_EFFORTS]
    elif provider == "openrouter":
        reasoning = raw.get("reasoning")
        if not isinstance(reasoning, dict):
            return None if "reasoning" in raw.get("supported_parameters", []) else []
        if "supported_efforts" not in reasoning:
            return []
        values = reasoning["supported_efforts"]
        values = GATEWAY_EFFORTS if values is None else values
        if reasoning.get("mandatory"):
            values = [value for value in values if value != "none"]
    elif provider == "anthropic":
        # The Models API lists each effort level with a `supported` leaf; a model without the
        # control (Claude Haiku 4.5) says so at the top.
        effort = (raw.get("capabilities") or {}).get("effort")
        if not isinstance(effort, dict):
            return None
        values = [
            level
            for level in ANTHROPIC_EFFORTS
            if effort.get("supported") and (effort.get(level) or {}).get("supported")
        ]
    elif provider == "vercel":
        controls = raw.get("reasoning_options")
        if not isinstance(controls, list):
            return None
        values = []
        for control in controls:
            if control.get("type") == "effort":
                values.extend(value for value in control.get("values", []) if value in GATEWAY_EFFORTS)
            elif control.get("type") == "toggle":
                values.extend(["none", "enabled"])
    else:
        return None
    return [value for value in EFFORTS if value in values]


def reasoning_efforts(provider: str, model: str, known: dict | None = None) -> list[str] | None:
    known = known or {}
    if isinstance(known.get("reasoning_efforts"), list):
        return known["reasoning_efforts"]
    if provider == "demo":
        return []
    if provider == "ollama":
        if model.rsplit("/", 1)[-1].startswith("gpt-oss"):
            return ["low", "medium", "high"]
        if "capabilities" in known:
            return ["none", "enabled"] if "thinking" in known["capabilities"] else []
        return None
    # Known families without effort support. Unknown/custom ids stay unspecified.
    if provider in {"openai", "codex", "openai-compatible"} and re.match(
        r"^(gpt-[34]|o1-(mini|preview))", model
    ):
        return []
    return None


def validate_reasoning(provider: str, model: str, effort: str | None, known: dict | None = None) -> None:
    if effort is None:
        return
    allowed = reasoning_efforts(provider, model, known)
    if effort not in EFFORTS or (allowed is not None and effort not in allowed):
        options = ", ".join(allowed or []) or "provider default"
        raise ValueError(f"{model} does not support reasoning setting {effort!r}. Choose {options}.")
    if effort == "enabled" and provider not in {"ollama", "openrouter", "vercel"}:
        raise ValueError("This provider needs a reasoning effort level rather than an on/off setting.")


def reasoning_kwargs(provider: str, effort: str | None) -> dict[str, Any]:
    """Do not send an override for the default. Gateways use their documented extension."""
    if effort is None:
        return {}
    if provider == "ollama":
        return {"reasoning": False if effort == "none" else True if effort == "enabled" else effort}
    if provider in {"openrouter", "vercel"}:
        value = {"enabled": effort == "enabled"} if effort in {"none", "enabled"} else {"effort": effort}
        return {"extra_body": {"reasoning": value}}
    if provider == "codex":
        return {"reasoning": {"effort": effort}}
    if provider == "anthropic":
        return {"output_config": {"effort": effort}} if effort in ANTHROPIC_EFFORTS else {}
    if provider == "gemini":
        return {"thinking_level": effort} if effort in GEMINI_EFFORTS else {}
    return {"reasoning_effort": effort}
