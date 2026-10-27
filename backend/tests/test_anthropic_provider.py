"""Claude on the user's Anthropic key: what the catalogue allows is what the request sends."""

from __future__ import annotations

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from wavefunction.anthropic_provider import AnthropicCatalog
from wavefunction.execution_policy import model_output_reserve
from wavefunction.models import ModelGateway
from wavefunction.secrets import MemorySecretStore
from wavefunction.spec_common import ModelSpec


def sse(*events):
    return b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)


def reply(index, blocks, stop_reason, usage):
    # The final usage, cache reads included, arrives with message_delta.
    usage = {
        "input_tokens": usage[0],
        "output_tokens": 40,
        "cache_read_input_tokens": usage[1],
        "cache_creation_input_tokens": usage[2],
    }
    events = [
        {
            "type": "message_start",
            "message": {
                "id": f"msg_{index}",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {**usage, "output_tokens": 1},
            },
        }
    ]
    for position, (start_block, deltas) in enumerate(blocks):
        events.append({"type": "content_block_start", "index": position, "content_block": start_block})
        events.extend({"type": "content_block_delta", "index": position, "delta": delta} for delta in deltas)
        events.append({"type": "content_block_stop", "index": position})
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": usage,
        }
    )
    events.append({"type": "message_stop"})
    return httpx.Response(200, content=sse(*events), headers={"content-type": "text/event-stream"})


async def test_claude_requests_cache_think_and_replay_reasoning_as_the_catalogue_allows(monkeypatch):
    catalogue = AnthropicCatalog.parse_models(
        [
            {
                "id": "claude-opus-5-5",
                "display_name": "Claude Opus 5.5",
                "max_input_tokens": 1_000_000,
                "max_tokens": 128_000,
                "capabilities": {
                    "thinking": {
                        "supported": True,
                        "types": {"enabled": {"supported": False}, "adaptive": {"supported": True}},
                    },
                    "effort": {
                        "supported": True,
                        **{level: {"supported": True} for level in ("low", "medium", "high", "xhigh", "max")},
                    },
                },
            },
            {
                "id": "claude-haiku-4-5",
                "display_name": "Claude Haiku 4.5",
                "max_input_tokens": 200_000,
                "max_tokens": 64_000,
                "capabilities": {
                    "thinking": {
                        "supported": True,
                        "types": {"enabled": {"supported": True}, "adaptive": {"supported": False}},
                    },
                    "effort": {"supported": False},
                },
            },
        ]
    )
    opus, haiku = (option.model_dump() for option in catalogue)
    assert (
        opus["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"] and opus["adaptive_thinking"]
    )
    assert haiku["reasoning_efforts"] == [] and not haiku["adaptive_thinking"]
    assert opus["context_length"] == 1_000_000 and opus["max_output_tokens"] == 128_000

    secrets = MemorySecretStore()
    gateway = ModelGateway(
        secrets, capabilities=lambda: {"anthropic": {"claude-opus-5-5": opus, "claude-haiku-4-5": haiku}}
    )
    with pytest.raises(RuntimeError, match="Anthropic API key"):
        gateway.chat_model(ModelSpec(provider="anthropic", model="claude-opus-5-5"))
    secrets.set("anthropic_api_key", "sk-ant-test")

    seen = []

    async def respond(_self, request):
        seen.append((request.headers, json.loads(request.content)))
        if len(seen) == 1:
            return reply(
                1,
                [
                    (
                        {"type": "thinking", "thinking": "", "signature": ""},
                        [
                            {"type": "thinking_delta", "thinking": ""},
                            {"type": "signature_delta", "signature": "sig-bound-to-turn-1"},
                        ],
                    ),
                    (
                        {"type": "tool_use", "id": "toolu_1", "name": "calculator", "input": {}},
                        [{"type": "input_json_delta", "partial_json": '{"a": 21}'}],
                    ),
                ],
                "tool_use",
                (12, 0, 4000),
            )
        return reply(
            len(seen),
            [({"type": "text", "text": ""}, [{"type": "text_delta", "text": "42"}])],
            "end_turn",
            (30, 4000, 20),
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)
    tools = [
        {
            "name": "calculator",
            "description": "Double a number.",
            "input_schema": {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
        }
    ]
    model = gateway.chat_model(
        ModelSpec(provider="anthropic", model="claude-opus-5-5", reasoning_effort="high")
    )
    assert model.profile["max_input_tokens"] == 1_000_000 and model.profile["image_inputs"]
    # Claude stops at the window rather than refusing, so its 128k ceiling is not taken from history.
    assert model_output_reserve(model) == 8192
    history = [SystemMessage("Stable instructions"), HumanMessage("Double 21")]
    first = await model.bind_tools(tools).ainvoke(history)
    history += [first, ToolMessage("42", tool_call_id=first.tool_calls[0]["id"])]
    second = await model.bind_tools(tools).ainvoke(history)

    headers, body = seen[0]
    assert (
        headers["x-api-key"] == "sk-ant-test"
        and "thinking-binding-controls-2026-08-01" in headers["anthropic-beta"]
    )
    assert body["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert (
        body["output_config"] == {"effort": "high"}
        and body["max_tokens"] == 128_000
        and body["stream"] is True
    )
    # The conversation prefix is cached, and the temperature every ModelSpec carries is not sent.
    assert body["cache_control"] == {"type": "ephemeral"} and "temperature" not in body
    # The second turn replays the first one's reasoning byte for byte, ahead of its tool call.
    replayed = seen[1][1]["messages"][1]["content"]
    assert replayed[0] == {"type": "thinking", "thinking": "", "signature": "sig-bound-to-turn-1"}
    assert replayed[1]["type"] == "tool_use" and replayed[1]["input"] == {"a": 21}
    assert seen[1][1]["system"] == body["system"] and seen[1][1]["tools"] == body["tools"]
    assert second.text == "42" and second.usage_metadata["input_token_details"]["cache_read"] == 4000

    # A model without adaptive thinking or effort gets neither.
    await gateway.chat_model(ModelSpec(provider="anthropic", model="claude-haiku-4-5")).ainvoke(
        [HumanMessage("hi")]
    )
    headers, body = seen[-1]
    assert "thinking" not in body and "output_config" not in body and "anthropic-beta" not in headers
