"""Gemini on the user's API key: thought signatures survive the turn, and errors read plainly."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from wavefunction.gemini_provider import GeminiCatalog
from wavefunction.models import ModelGateway, describe_provider_error, is_retryable_provider_error
from wavefunction.secrets import MemorySecretStore
from wavefunction.spec_common import ModelSpec


def reply(parts, usage):
    chunk = {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
        "usageMetadata": {
            "promptTokenCount": usage[0],
            "candidatesTokenCount": 8,
            "totalTokenCount": usage[0] + 8,
            "cachedContentTokenCount": usage[1],
        },
        "modelVersion": "gemini-3.6-flash",
    }
    return httpx.Response(
        200,
        content=f"data: {json.dumps(chunk)}\r\n\r\n".encode(),
        headers={"content-type": "text/event-stream"},
    )


async def test_gemini_replays_thought_signatures_reads_cache_and_explains_failures(monkeypatch):
    catalogue = GeminiCatalog.parse_models(
        [
            {
                "name": "models/gemini-3.6-flash",
                "display_name": "Gemini 3.6 Flash",
                "input_token_limit": 1_048_576,
                "output_token_limit": 65_536,
                "supported_actions": ["generateContent", "countTokens"],
                "thinking": True,
            },
            # Newer than LangChain's profiles: thinks, but which levels it takes is left to its default.
            {
                "name": "models/gemini-unreleased-test",
                "display_name": "Gemini Unreleased Test",
                "input_token_limit": 1_000_000,
                "output_token_limit": 65_536,
                "supported_actions": ["generateContent"],
                "thinking": True,
            },
            {
                "name": "models/gemini-2.0-flash",
                "display_name": "Gemini 2.0 Flash",
                "input_token_limit": 1_048_576,
                "output_token_limit": 8_192,
                "supported_actions": ["generateContent"],
                "thinking": False,
            },
            {"name": "models/gemini-3.1-flash-tts-preview", "supported_actions": ["generateContent"]},
            {"name": "models/gemini-embedding-001", "supported_actions": ["embedContent"]},
            {"name": "models/gemma-4-27b-it", "supported_actions": ["generateContent"]},
        ]
    )
    options = {option.id: option.model_dump() for option in catalogue}
    assert list(options) == ["gemini-2.0-flash", "gemini-3.6-flash", "gemini-unreleased-test"]
    assert options["gemini-3.6-flash"]["reasoning_efforts"] == ["minimal", "low", "medium", "high"]
    assert options["gemini-unreleased-test"]["reasoning_efforts"] is None
    assert options["gemini-2.0-flash"]["reasoning_efforts"] == []

    secrets = MemorySecretStore()
    gateway = ModelGateway(secrets, capabilities=lambda: {"gemini": options})
    with pytest.raises(RuntimeError, match="Gemini API key"):
        gateway.chat_model(ModelSpec(provider="gemini", model="gemini-3.6-flash"))
    secrets.set("gemini_api_key", "AIza-test")

    seen = []
    signature = base64.b64encode(b"sig-bound-to-turn-1").decode()

    async def respond(_self, request):
        seen.append((request.url, request.headers, json.loads(request.content)))
        if len(seen) == 1:
            return reply(
                [{"functionCall": {"name": "calculator", "args": {"a": 21}}, "thoughtSignature": signature}],
                (12, 0),
            )
        if len(seen) == 2:
            return reply([{"text": "42"}], (30, 4000))
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Quota exceeded for generate_content_free_tier_requests",
                }
            },
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)
    tools = [
        {
            "name": "calculator",
            "description": "Double a number.",
            "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
        }
    ]
    model = gateway.chat_model(
        ModelSpec(provider="gemini", model="gemini-3.6-flash", reasoning_effort="high")
    )
    assert model.profile["max_input_tokens"] == 1_048_576
    history = [SystemMessage("Stable instructions"), HumanMessage("Double 21")]
    first = await model.bind_tools(tools).ainvoke(history)
    history += [first, ToolMessage("42", tool_call_id=first.tool_calls[0]["id"])]
    second = await model.bind_tools(tools).ainvoke(history)

    url, headers, body = seen[0]
    assert (
        url.path == "/v1beta/models/gemini-3.6-flash:streamGenerateContent"
        and headers["x-goog-api-key"] == "AIza-test"
    )
    config = body["generationConfig"]
    # The temperature every ModelSpec carries is not sent; thinking goes as Gemini's own level.
    # (The SDK spells the nested field thinking_level; the API's JSON parser takes either form.)
    assert "temperature" not in config and list(config["thinkingConfig"].values()) == ["HIGH"]
    assert config["maxOutputTokens"] == 65_536
    # The next turn sends the function call back with the signature it came with.
    replayed = seen[1][2]["contents"][1]
    assert replayed["role"] == "model"
    assert (
        replayed["parts"][0]["functionCall"]["args"] == {"a": 21}
        and replayed["parts"][0]["thoughtSignature"] == signature
    )
    assert (
        seen[1][2]["systemInstruction"] == body["systemInstruction"] and seen[1][2]["tools"] == body["tools"]
    )
    assert second.text == "42" and second.usage_metadata["input_token_details"]["cache_read"] == 4000

    # Out of quota on the user's own key: not retried, and named for what it is.
    with pytest.raises(Exception) as failure:
        await gateway.chat_model(
            ModelSpec(provider="gemini", model="gemini-3.6-flash"), max_retries=0
        ).ainvoke("hi")
    assert not is_retryable_provider_error(failure.value)
    assert describe_provider_error(failure.value).startswith("Google Gemini account has hit its rate limit")
