"""Web search on Anthropic, Gemini and the Vercel AI Gateway: each provider's own search tool."""

from __future__ import annotations

import json

import httpx
import pytest

from wavefunction.models import ModelGateway
from wavefunction.secrets import MemorySecretStore
from wavefunction.spec_common import ModelSpec


@pytest.mark.asyncio
async def test_anthropic_gemini_and_vercel_search_with_their_own_tool_on_the_chat_key(monkeypatch) -> None:
    """No Tavily key: each provider's own search runs on the key the chat uses, and comes back in
    the shape the other searches use, with its usage counted on the task."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        host = request.url.host
        if host == "api.anthropic.com" and len([r for r in requests if r.url.host == host]) == 1:
            # A long search turn pauses; the paused turn goes back to finish it.
            return httpx.Response(
                200,
                json={
                    "stop_reason": "pause_turn",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                    "content": [
                        {
                            "type": "server_tool_use",
                            "id": "srv_1",
                            "name": "web_search",
                            "input": {"query": "q"},
                        },
                        {
                            "type": "web_search_tool_result",
                            "tool_use_id": "srv_1",
                            "content": [
                                {
                                    "type": "web_search_result",
                                    "url": "https://a.example/found",
                                    "title": "Found",
                                }
                            ],
                        },
                    ],
                },
            )
        if host == "api.anthropic.com":
            citation = {
                "type": "web_search_result_location",
                "url": "https://a.example/cited",
                "title": "Cited",
                "cited_text": "Released on 3 May.",
            }
            return httpx.Response(
                200,
                json={
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 5,
                        "server_tool_use": {"web_search_requests": 1},
                    },
                    "content": [{"type": "text", "text": "Anthropic answer.", "citations": [citation]}],
                },
            )
        if host == "generativelanguage.googleapis.com":
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"text": "Thinking.", "thought": True}, {"text": "Gemini answer."}]
                            },
                            "groundingMetadata": {
                                "groundingChunks": [
                                    {"web": {"uri": "https://g.example/redirect", "title": "g.example"}}
                                ]
                            },
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 7,
                        "candidatesTokenCount": 3,
                        "totalTokenCount": 10,
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Gateway answer, from https://v.example/page."}}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *a, **kw: original(*a, **{**kw, "transport": httpx.MockTransport(handler)}),
    )
    reported = []
    monkeypatch.setattr("wavefunction.models.report_usage", reported.append)
    secrets = MemorySecretStore()
    for name in ("anthropic_api_key", "gemini_api_key", "vercel_api_key"):
        secrets.set(name, f"{name}-value")
    gateway = ModelGateway(secrets)
    assert gateway.search_provider(ModelSpec(provider="openai", model="gpt-x")) == "none"

    results = {}
    for spec in (
        ModelSpec(provider="anthropic", model="claude-x"),
        ModelSpec(provider="gemini", model="gemini-x"),
        ModelSpec(provider="vercel", model="google/gemini-x"),
    ):
        assert gateway.search_provider(spec) == "built-in"
        results[spec.provider] = await gateway.web_search(
            spec, "synthetic query", 5, zero_data_retention=True
        )

    first, resumed, gemini, vercel = requests
    assert first.headers["x-api-key"] == "anthropic_api_key-value"
    sent = json.loads(first.content)
    assert sent["tools"] == [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]
    assert json.loads(resumed.content)["messages"][1]["role"] == "assistant"
    # Cited sources first, with what the answer drew from them; then the rest the search found.
    assert results["anthropic"] == (
        "Anthropic answer.\n\nSOURCE: https://a.example/cited\nCited\nReleased on 3 May."
        "\n\nSOURCE: https://a.example/found\nFound"
    )

    assert gemini.url.path == "/v1beta/models/gemini-x:generateContent"
    assert gemini.headers["x-goog-api-key"] == "gemini_api_key-value"
    assert json.loads(gemini.content)["tools"] == [{"google_search": {}}]
    assert results["gemini"] == "Gemini answer.\n\nSOURCE: https://g.example/redirect\ng.example"

    assert vercel.headers["Authorization"] == "Bearer vercel_api_key-value"
    body = json.loads(vercel.content)
    assert body["tools"] == [
        {"type": "vercel:perplexity_search", "config": {"query": "synthetic query", "max_results": 5}}
    ]
    assert body["tool_choice"] == "required"
    assert body["providerOptions"] == {"gateway": {"zeroDataRetention": True}}
    assert results["vercel"] == "Gateway answer, from https://v.example/page."

    assert [usage["total_tokens"] for usage in reported] == [37, 10, 6]
