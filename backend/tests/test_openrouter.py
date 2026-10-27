from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import replace

import httpx
import pytest
from langchain.agents.middleware import ModelRequest, ModelResponse
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)
from langchain_core.runnables.config import set_config_context
from langchain_core.tools import tool

from wavefunction import execution_policy
from wavefunction.execution_policy import ProviderPolicyMiddleware
from wavefunction.gateway_chat import gateway_diagnostics
from wavefunction.models import ModelGateway, is_retryable_provider_error
from wavefunction.secrets import MemorySecretStore
from wavefunction.spec_common import ModelSpec
from wavefunction.spec_v2 import AgentSpecV2, ToolBinding
from wavefunction.toolkit import TOOL_REGISTRY, ToolContext, _build_web_search


async def test_openrouter_session_and_prefixes_survive_streamed_history_retries_and_turns(
    monkeypatch,
) -> None:
    """The actual SDK body retains reasoning and owns routing without restricting providers."""
    secrets = MemorySecretStore()
    secrets.set("openrouter_api_key", "sk-or-v1-test")
    secrets.set("openai_api_key", "openai-test")
    secrets.set("vercel_api_key", "vercel-test")
    gateway = ModelGateway(secrets)
    models, seen, events = [], [], []
    tools = [
        {
            "type": "function",
            "function": {
                "name": "calculator",
                "description": "Add one number to itself.",
                "parameters": {"type": "object", "properties": {"a": {"type": "integer"}}, "required": ["a"]},
            },
        }
    ]

    class Fragments(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __init__(self, body):
            self.body = body

        def __iter__(self):
            return iter(self.body[i : i + 37] for i in range(0, len(self.body), 37))

        async def __aiter__(self):
            for part in self:
                await asyncio.sleep(0)
                yield part

    def respond(request):
        body = json.loads(request.content)
        seen.append((dict(request.headers), body))
        index = len(seen)
        if index == 2:
            # A gateway that could not retry its own upstream attempt; the request is retried here.
            return httpx.Response(507, text="exceeded request buffer limit while retrying upstream")
        base = {
            "id": f"chatcmpl-{index}",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": body["model"],
        }
        chunks = [
            {
                **base,
                "provider": "Synthetic endpoint",
                "choices": [{"index": 0, "delta": {"role": "assistant"}}],
            }
        ]
        if index == 1:
            chunks.extend(
                [
                    {
                        **base,
                        "provider": "Synthetic endpoint",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "reasoning_content": "Think ",
                                    "reasoning_details": [
                                        {
                                            "index": 0,
                                            "id": "r1",
                                            "type": "reasoning.encrypted",
                                            "format": "synthetic",
                                            "data": "opaque-",
                                        }
                                    ],
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {"name": "calculator", "arguments": '{"a":'},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    {
                        **base,
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "tool_calls",
                                "delta": {
                                    "reasoning_content": "carefully",
                                    "reasoning_details": [
                                        {
                                            "index": 0,
                                            "id": "r1",
                                            "type": "reasoning.encrypted",
                                            "format": "synthetic",
                                            "data": "state",
                                        }
                                    ],
                                    "tool_calls": [{"index": 0, "function": {"arguments": "21}"}}],
                                },
                            }
                        ],
                    },
                ]
            )
        else:
            chunks.append(
                {**base, "choices": [{"index": 0, "finish_reason": "stop", "delta": {"content": "42"}}]}
            )
        chunks.append(
            {
                **base,
                "choices": [],
                "usage": {
                    "prompt_tokens": 5000,
                    "completion_tokens": 20,
                    "total_tokens": 5020,
                    "cost": 0.012,
                    "prompt_tokens_details": {"cached_tokens": 4096},
                    "completion_tokens_details": {"reasoning_tokens": 10},
                },
            }
        )
        stream = (
            b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks)
            + b"data: [DONE]\n\n"
        )
        return httpx.Response(200, stream=Fragments(stream), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _self, request: respond(request))

    async def async_response(_self, request):
        return respond(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_response)
    monkeypatch.setattr(
        execution_policy, "emit_policy", lambda event, **payload: events.append((event, payload))
    )
    monkeypatch.setattr(execution_policy, "retry_delay", lambda *_: 0)
    # The retry decision the app's agents use, not the summary's.
    policy = ProviderPolicyMiddleware(retry_on=is_retryable_provider_error)

    def build(provider="openrouter", base_url=None):
        model = gateway.chat_model(
            ModelSpec(
                provider=provider,
                model="vendor/structured-agent",
                base_url=base_url,
                reasoning_effort="high" if provider == "openrouter" else None,
            ),
            max_retries=0,
        )
        model.streaming = True
        models.append(model)
        return model

    async def invoke(
        model, messages, chat="chat-a", *, sync=False, instructions="Stable instructions", tool_list=tools
    ):
        async def handler(request):
            bound = request.model.bind_tools(request.tools) if request.tools else request.model
            messages = [request.system_message, *request.messages]
            reply = await asyncio.to_thread(bound.invoke, messages) if sync else await bound.ainvoke(messages)
            return ModelResponse(result=[reply])

        with set_config_context({"configurable": {"thread_id": chat}}) as context:
            result = await asyncio.create_task(
                policy.awrap_model_call(
                    ModelRequest(
                        model=model,
                        messages=messages,
                        tools=tool_list,
                        system_message=SystemMessage(content=instructions),
                    ),
                    handler,
                ),
                context=context,
            )
        return result.result[0]

    def finished():
        return [payload for event, payload in events if event == "model_call_finished"]

    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    try:
        model = build()
        original_headers = dict(model.default_headers)
        messages = [HumanMessage(content="Calculate 21 + 21")]
        first = await invoke(model, messages)
        # Streamed reasoning is kept as the merged fields replayed on the wire, not per-delta
        # records; the estimate already counted only what is sent.
        from wavefunction.gateway_chat import collapse_reasoning

        estimate = execution_policy.request_tokens(ModelRequest(model=model, messages=[first], tools=[]))
        assert len(first.additional_kwargs["gateway_reasoning_chunks"]) > 1
        collapse_reasoning(first)
        assert "gateway_reasoning_chunks" not in first.additional_kwargs
        assert first.additional_kwargs["reasoning_content"] == "Think carefully"
        assert (
            execution_policy.request_tokens(ModelRequest(model=model, messages=[first], tools=[])) == estimate
        )
        assert first.tool_calls == [
            {"name": "calculator", "args": {"a": 21}, "id": "call_1", "type": "tool_call"}
        ]
        assert first.usage_metadata["input_token_details"] == {"cache_read": 4096}
        assert first.usage_metadata["output_token_details"] == {"reasoning": 10}
        assert first.response_metadata["cost"] == 0.012
        messages += [first, ToolMessage(content="42", tool_call_id="call_1")]
        reply = await invoke(model, messages)
        assert seen[1] == seen[2]  # Middleware retries the same body and headers, SDK retries disabled.
        assert len([event for event, _ in events if event == "model_retry"]) == 1
        assert seen[0][0]["x-openrouter-title"] == "Wavefunction"
        assert "provider" not in seen[0][1] and "max_tokens" not in seen[0][1]
        assert "extra_body" not in seen[0][1] and "prompt_cache_key" not in seen[0][1]
        assert seen[0][1]["reasoning"] == {"effort": "high"}
        assert seen[0][1]["stream_options"] == {"include_usage": True}
        session = seen[0][1]["session_id"]
        assert len(session) == 64 and session == seen[1][1]["session_id"]
        assert "chat-a" not in session and "sk-or-v1-test" not in session
        assert (
            model.extra_body == {"reasoning": {"effort": "high"}}
            and model.default_headers == original_headers
        )
        assert seen[2][1]["messages"][:2] == seen[0][1]["messages"]
        wire_reply = seen[2][1]["messages"][2]
        assert wire_reply["reasoning_content"] == "Think carefully"
        assert wire_reply["reasoning_details"] == [
            {
                "index": 0,
                "id": "r1",
                "type": "reasoning.encrypted",
                "format": "synthetic",
                "data": "opaque-state",
            }
        ]

        before, after = [item["cache_diagnostics"] for item in finished()]
        failed = next(
            payload["cache_diagnostics"] for event, payload in events if event == "model_call_failed"
        )
        assert {**failed, "upstream_provider": "Synthetic endpoint"} == after
        assert before["upstream_provider"] == after["upstream_provider"] == "Synthetic endpoint"
        assert before["system_sha256"] == after["system_sha256"]
        assert before["tools_sha256"] == after["tools_sha256"]
        assert {"messages": before["history_messages"], "sha256": before["history_sha256"]} in after[
            "history_prefixes"
        ]
        assert (
            before["system_sha256"]
            == hashlib.sha256(canonical(seen[0][1]["messages"][0]) + b"\n").hexdigest()
        )
        assert before["tools_sha256"] == hashlib.sha256(canonical(seen[0][1]["tools"])).hexdigest()
        assert (
            after["history_sha256"]
            == hashlib.sha256(
                b"".join(canonical(item) + b"\n" for item in seen[2][1]["messages"])
            ).hexdigest()
        )
        assert finished()[0]["usage"] == [first.usage_metadata]

        # Saved history and a fresh client preserve routing on the next turn; sync SDK calls
        # inherit the same per-attempt context without putting diagnostics into messages.
        restored = messages_from_dict(json.loads(json.dumps(messages_to_dict([*messages, reply]))))
        next_turn = build()
        await invoke(next_turn, [*restored, HumanMessage(content="Continue")], sync=True)
        assert seen[-1][1]["session_id"] == session
        assert seen[-1][1]["messages"][: len(seen[2][1]["messages"])] == seen[2][1]["messages"]
        assert finished()[-1]["cache_diagnostics"]["upstream_provider"] == "Synthetic endpoint"
        assert all("cache_diagnostics" not in message.response_metadata for message in restored)

        # Simultaneous chats share a client safely; a fork and another credential cannot
        # take the parent's route even when they start with identical copied history.
        concurrent_start = len(finished())
        await asyncio.gather(invoke(next_turn, restored), invoke(next_turn, restored, chat="fork-b"))
        assert seen[-2][1]["session_id"] == session
        fork_session = seen[-1][1]["session_id"]
        assert fork_session != session
        assert {item["cache_diagnostics"]["session_id"] for item in finished()[concurrent_start:]} == {
            session,
            fork_session,
        }
        secrets.set("openrouter_api_key", "sk-or-v1-other-account")
        await invoke(build(), restored, chat="fork-b")
        assert seen[-1][1]["session_id"] not in {session, fork_session}
        assert next_turn.default_headers == original_headers

        await invoke(
            next_turn,
            [HumanMessage(content=f"Synthetic note {i}") for i in range(64)],
            instructions="Changed instructions",
            tool_list=[],
        )
        bounded = finished()[-1]["cache_diagnostics"]
        assert (
            bounded["system_sha256"] != before["system_sha256"]
            and bounded["tools_sha256"] != before["tools_sha256"]
        )
        assert bounded["history_messages"] == 65 and len(bounded["history_prefixes"]) == 16
        assert len(json.dumps(bounded)) < 2500 and "Synthetic note" not in json.dumps(bounded)
        await invoke(next_turn, restored, chat=None)
        assert "session_id" not in seen[-1][1] and "session_id" not in finished()[-1]["cache_diagnostics"]

        # Only the exact OpenRouter host opts in. Existing OpenAI cache keys and Vercel's
        # automatic cache option stay on their own protocols, with no shared session header.
        for provider in ("openai", "vercel", "openai-compatible"):
            control = build(
                provider, "https://openrouter.ai.example/v1" if provider == "openai-compatible" else None
            )
            await invoke(control, restored)
            assert "session_id" not in seen[-1][1] and "cache_diagnostics" not in finished()[-1]
            if provider == "openai":
                assert seen[-1][1]["prompt_cache_key"] == "chat-a"
            if provider == "vercel":
                assert seen[-1][1]["providerOptions"] == {"gateway": {"caching": "auto"}}
        lookalike = build()
        lookalike.openai_api_base = "https://openrouter.ai.example/v1"
        await invoke(lookalike, restored)
        assert "session_id" not in seen[-1][1] and "cache_diagnostics" not in finished()[-1]
        assert gateway_diagnostics.get() is None
    finally:
        for model in models:
            model.root_client.close()
            await model.root_async_client.close()


async def test_zero_data_retention_restricts_openrouter_and_vercel_routing_from_the_next_model(
    monkeypatch,
) -> None:
    """Each gateway gets its own ZDR field, read when a task's model and tools are built: a running
    task keeps the policy it started with, and one built after the setting is off is unrestricted.
    OpenRouter web search, a separate request carrying a conversation-derived query, follows it too."""
    secrets = MemorySecretStore()
    secrets.set("openrouter_api_key", "sk-or-v1-test")
    secrets.set("vercel_api_key", "vercel-test")
    required = {"on": True}
    gateway = ModelGateway(secrets, zero_data_retention=lambda: required["on"])
    bodies = []

    async def respond(_self, request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": "vendor/model",
                "choices": [
                    {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)

    def build(provider):
        model = gateway.chat_model(ModelSpec(provider=provider, model="vendor/model"), max_retries=0)
        # Own the HTTP pool: LangChain shares a cached one that other tests close.
        model.root_async_client._client = httpx.AsyncClient(trust_env=False)
        return model

    def search():
        return _build_web_search(
            ToolContext(
                agent_id="agent",
                repository=None,
                files=None,
                secrets=secrets,
                coding_workspace=None,
                models=gateway,
                model=ModelSpec(provider="openrouter", model="vendor/model"),
            )
        )

    models, searches = [build("openrouter"), build("vercel")], [search()]
    required["on"] = False
    models += [build("openrouter"), build("vercel")]
    searches.append(search())
    try:
        for model in models:
            await model.ainvoke([HumanMessage(content="Synthetic question")])
        for tool_ in searches:
            await tool_.ainvoke({"query": "synthetic search", "max_results": 2})
        assert bodies[4]["provider"] == {"zdr": True} and bodies[4]["plugins"] == [
            {"id": "web", "max_results": 2}
        ]
        assert "provider" not in bodies[5]
        assert bodies[0]["provider"] == {"zdr": True}
        assert bodies[1]["providerOptions"] == {"gateway": {"caching": "auto", "zeroDataRetention": True}}
        assert "provider" not in bodies[2] and "providerOptions" not in bodies[2]
        assert (
            bodies[3]["providerOptions"] == {"gateway": {"caching": "auto"}} and "provider" not in bodies[3]
        )
    finally:
        for model in models:
            await model.root_async_client.close()


class SyntheticStream(httpx.AsyncByteStream):
    """Actual SDK SSE parsing, including byte fragmentation and keepalive-only silence."""

    def __init__(self, chunks, streams, *, stall=False, delay=0, stalled=None):
        self.chunks, self.stall, self.delay, self.stalled = chunks, stall, delay, stalled
        self.closed = False
        streams.append(self)

    async def __aiter__(self):
        for chunk in self.chunks:
            data = b"data: " + json.dumps(chunk).encode() + b"\n\n"
            for start in range(0, len(data), 37):
                yield data[start : start + 37]
            await asyncio.sleep(self.delay)
        if self.stall:
            if self.stalled is not None:
                self.stalled.set()
            while True:
                yield b": OPENROUTER PROCESSING\n\n"
                await asyncio.sleep(0.005)
        yield b"data: [DONE]\n\n"

    async def aclose(self):
        self.closed = True


def stream_chunk(delta=None, *, finish=None, usage=None):
    chunk = {
        "id": "resp-synthetic",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "synthetic/stream",
        "provider": "Synthetic endpoint",
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def stream_usage(prompt=100, completion=10, cost=0.001):
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_tokens_details": {"cached_tokens": 0},
        "cost": cost,
    }


def streamed_models(stack):
    models = []
    build = stack.models.chat_model

    def captured(*args, **kwargs):
        model = build(*args, **kwargs)
        # LangChain caches default HTTP pools. This fixture owns its clients so closing
        # them cannot poison a later test or a concurrent model sharing a default pool.
        model.root_client._client = httpx.Client(trust_env=False)
        model.root_async_client._client = httpx.AsyncClient(trust_env=False)
        model.streaming = True
        model.stream_chunk_timeout = 0.5
        models.append(model)
        return model

    stack.models.chat_model = captured
    return models


async def test_stream_timeout_retries_only_the_model_and_recovers_owned_usage_and_partial_output(
    stack, monkeypatch
):
    from openai import APIConnectionError, APITimeoutError

    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    assert all(
        is_retryable_provider_error(error)
        for error in (
            APIConnectionError(request=request),
            APITimeoutError(request=request),
            httpx.ConnectError("connection reset"),
            httpx.ReadTimeout("timed out"),
        )
    )
    stack.secrets.set("openrouter_api_key", "synthetic-account-a")
    models = streamed_models(stack)
    streams, seen, lookups, effects = [], [], [], []
    attempts = Counter()
    stalled = asyncio.Event()

    @tool("calculator")
    def calculate(expression: str) -> str:
        """Record a completed synthetic action without running a command."""
        effects.append(expression)
        return "42"

    monkeypatch.setitem(
        TOOL_REGISTRY, "calculator", replace(TOOL_REGISTRY["calculator"], build=lambda _: calculate)
    )
    monkeypatch.setattr(execution_policy, "retry_delay", lambda *_: 0)

    async def respond(_transport, request):
        assert request.url.host == "openrouter.ai"
        if request.method == "GET":
            lookups.append((dict(request.headers), dict(request.url.params)))
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "gen-interrupted-a",
                        "native_tokens_prompt": 1000,
                        "native_tokens_completion": 200,
                        "native_tokens_cached": 0,
                        "native_tokens_reasoning": 150,
                        "usage": 0.002,
                    }
                },
            )
        body = json.loads(request.content)
        seen.append((dict(request.headers), body))
        control = any(message.get("content") == "Control" for message in body["messages"])
        has_result = any(message.get("role") == "tool" for message in body["messages"])
        if control:
            chunks = [
                stream_chunk(
                    {"content": "Other chat answer"}, finish="stop", usage=stream_usage(10, 2, 0.004)
                )
            ]
        elif not has_result:
            chunks = [
                stream_chunk(
                    {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "completed-action",
                                "type": "function",
                                "function": {"name": "calculator", "arguments": '{"expression":"21+21"}'},
                            }
                        ]
                    },
                    finish="tool_calls",
                    usage=stream_usage(),
                )
            ]
        else:
            attempts["after_tool"] += 1
            if attempts["after_tool"] == 1:
                # Meaningful reasoning/argument chunks keep arriving for longer than the
                # gap timeout. Then only SSE comments arrive: they must not keep it alive.
                chunks = [stream_chunk({"role": "assistant", "content": "Partial findings "})]
                chunks += [
                    stream_chunk(
                        {
                            "reasoning_content": "private synthetic thought",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "incomplete-action" if index == 0 else None,
                                    "type": "function",
                                    "function": {
                                        "name": "calculator" if index == 0 else None,
                                        "arguments": '{"expression":"' if index == 0 else "unfinished",
                                    },
                                }
                            ],
                        }
                    )
                    for index in range(8)
                ]
                return httpx.Response(
                    200,
                    stream=SyntheticStream(chunks, streams, stall=True, delay=0.08, stalled=stalled),
                    headers={
                        "content-type": "text/event-stream",
                        "x-generation-id": "gen-interrupted-a",
                        "set-cookie": "synthetic-private-cookie",
                    },
                )
            chunks = [
                stream_chunk(
                    {"content": "Recovered answer"}, finish="stop", usage=stream_usage(100, 20, 0.003)
                )
            ]
        return httpx.Response(
            200, stream=SyntheticStream(chunks, streams), headers={"content-type": "text/event-stream"}
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)
    spec = AgentSpecV2(
        name="Stream test",
        description="Synthetic",
        system_prompt="Complete the task.",
        model=ModelSpec(provider="openrouter", model="synthetic/stream"),
        tools=[ToolBinding(name="calculator")],
    )
    try:
        first = await stack.runtime.start_run(stack.agent["id"], "Calculate", spec=spec)
        await asyncio.wait_for(stalled.wait(), 5)
        # Start the concurrent chat after progress is delivered so synchronous model
        # setup cannot consume a synthetic inter-chunk deadline on slower CI runners.
        # A different chat and rotated account cannot change the first run's recovery key.
        stack.secrets.set("openrouter_api_key", "synthetic-account-b")
        other = await stack.runtime.start_run(
            stack.agent["id"],
            "Control",
            spec=spec,
            conversation_id=stack.repository.current_conversation(stack.agent["id"], "other-chat")["id"],
        )
        result, control = await asyncio.gather(
            stack.runtime.wait(first["id"]), stack.runtime.wait(other["id"])
        )
        assert result["status"] == control["status"] == "completed"
        assert (
            result["output"]["text"] == "Recovered answer"
            and control["output"]["text"] == "Other chat answer"
        )
        assert effects == ["21+21"] and attempts["after_tool"] == 2
        assert result["usage"]["input_tokens"] == 1200 and result["usage"]["output_tokens"] == 230, (
            lookups,
            [
                e["payload"]
                for e in stack.repository.list_run_events(first["id"])
                if e["event_type"] == "model_call_failed"
            ],
        )
        assert result["usage"]["total_tokens"] == 1430 and result["usage"]["cost"] == 0.006
        assert result["usage"]["output_token_details"] == {"reasoning": 150}
        assert control["usage"]["total_tokens"] == 12 and control["usage"]["cost"] == 0.004
        assert len(lookups) == 1 and lookups[0][0]["authorization"] == "Bearer synthetic-account-a"
        assert lookups[0][1] == {"id": "gen-interrupted-a"}
        after_tool = [body for _, body in seen if any(m.get("role") == "tool" for m in body["messages"])]
        assert after_tool[0] == after_tool[1]
        events = stack.repository.list_run_events(first["id"])
        failed = next(event["payload"] for event in events if event["event_type"] == "model_call_failed")
        assert failed["error"] == "StreamChunkTimeoutError" and failed["usage_status"] == "recovered"
        assert failed["usage"][0]["output_token_details"] == {"reasoning": 150}
        assert failed["stream"]["generation_id"] == "gen-interrupted-a"
        assert failed["stream"]["chunks_received"] == 9 and failed["stream"]["reasoning_chars"] > 0
        assert failed["stream"]["tool_argument_chars"] > 0 and failed["stream"]["last_chunk_age_ms"] >= 450
        partial = [
            event["payload"]
            for event in events
            if event["event_type"] == "assistant_message" and event["payload"].get("partial")
        ]
        assert len(partial) == 1 and partial[0]["text"] == "Partial findings "
        assert not any(event["event_type"] == "assistant_delta" for event in events)
        assert not any(
            event["event_type"] == "model_call_failed"
            for event in stack.repository.list_run_events(other["id"])
        )
        foreign = stack.repository.add_run_event(
            other["id"], "assistant_delta", {"text": "Other chat buffer", "message_id": "foreign"}
        )
        stack.repository.preserve_assistant_start(first["id"], foreign["id"])
        assert stack.repository.list_run_events(other["id"])[-1] == foreign
        stack.repository.delete_run_events(other["id"], "assistant_delta")
        assert all(
            text not in json.dumps(events)
            for text in ("synthetic-account-a", "synthetic-private-cookie", "private synthetic thought")
        )
        assert not stack.runtime.tasks and not stack.runtime.active and not stack.runtime.conditions
    finally:
        for model in models:
            model.root_client.close()
            await model.root_async_client.close()
    assert streams and all(stream.closed for stream in streams)


async def test_keepalive_only_stream_exhausts_retries_preserving_partial_answers_and_unknown_usage(
    stack, monkeypatch
):
    from wavefunction.runtime import DELTA_FLUSH_CHARS

    stack.secrets.set("openrouter_api_key", "synthetic-account")
    models = streamed_models(stack)
    streams, attempts, lookups = [], [], []
    stalled = asyncio.Event()
    texts = [
        f"Partial attempt {index}" + ("." * DELTA_FLUSH_CHARS if index == 1 else "") for index in range(1, 5)
    ]
    monkeypatch.setattr(execution_policy, "retry_delay", lambda *_: 0)

    async def respond(_transport, request):
        if request.method == "GET":
            lookups.append(str(request.url))
            if request.url.params["id"] == "gen-stall-2":
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": "gen-foreign",
                            "native_tokens_prompt": 9999,
                            "native_tokens_completion": 9999,
                            "usage": 99,
                        }
                    },
                )
            return httpx.Response(404, json={"error": {"message": "Not available yet"}})
        attempts.append(json.loads(request.content))
        index = len(attempts)
        chunks = [stream_chunk({"content": texts[index - 1]})]
        if index == 1:
            # The connection can fail after its usage frame. That frame was already
            # counted through the SDK and must not be fetched or added a second time.
            chunks.append(stream_chunk(usage=stream_usage(5, 2, 0.0002)))
        return httpx.Response(
            200,
            stream=SyntheticStream(chunks, streams, stall=True, stalled=stalled if index == 1 else None),
            headers={"content-type": "text/event-stream", "x-generation-id": f"gen-stall-{index}"},
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond)
    spec = AgentSpecV2(
        name="Stream test",
        description="Synthetic",
        system_prompt="Complete the task.",
        model=ModelSpec(provider="openrouter", model="synthetic/stream"),
    )
    try:
        run = await stack.runtime.start_run(stack.agent["id"], "Inspect", spec=spec)
        await asyncio.wait_for(stalled.wait(), 5)
        async with asyncio.timeout(3):
            while not (
                deltas := stack.repository.list_run_events(run["id"], event_types=("assistant_delta",))
            ):
                await asyncio.sleep(0)
        original = deltas[0]
        await stack.runtime.steer(run["id"], "Change direction")
        result = await asyncio.wait_for(stack.runtime.wait(run["id"]), 8)
        assert result["status"] == "failed" and "response stream timed out" in result["error"]
        assert "Nothing happened" not in result["error"]
        assert result["usage"]["total_tokens"] == 7 and result["usage"]["cost"] == 0.0002
        assert len(attempts) == 4 and len(lookups) == 3 and all(body == attempts[0] for body in attempts)
        events = stack.repository.list_run_events(run["id"])
        failures = [e["payload"] for e in events if e["event_type"] == "model_call_failed"]
        assert len(failures) == 4 and failures[0]["usage_status"] == "reported"
        assert failures[0]["usage"][0]["input_token_details"] == {"cache_read": 0}
        assert all(f["usage_status"] == "unknown" and f["usage"] == [None] for f in failures[1:])
        assert failures[0]["stream"]["chunks_received"] == 2
        assert all(f["stream"]["chunks_received"] == 1 and f["timeout_seconds"] == 0.5 for f in failures[1:])
        assert len([e for e in events if e["event_type"] == "model_retry"]) == 3
        partials = [e["payload"]["text"] for e in events if e["event_type"] == "assistant_message"]
        assert partials == texts
        anchor = next(e for e in events if e["id"] == original["id"])
        steer = next(e for e in events if e["event_type"] == "steering_requested")
        assert (
            anchor["event_type"] == "assistant_started"
            and anchor["sequence"] == original["sequence"] < steer["sequence"]
        )
        assert anchor["payload"] == {"message_id": original["payload"]["message_id"]}
        assert all(e["payload"].get("partial") for e in events if e["event_type"] == "assistant_message")
        assert not any(
            e["event_type"] in {"assistant_delta", "tool_started", "run_completed"} for e in events
        )
        # Reopening SQLite keeps partial responses; no transient stream rows are needed.
        assert stack.repository.get_run(run["id"])["error"] == result["error"]
        assert not stack.runtime.tasks and not stack.runtime.active and not stack.runtime.conditions
    finally:
        for model in models:
            model.root_client.close()
            await model.root_async_client.close()
    assert streams and all(stream.closed for stream in streams)


@pytest.mark.asyncio
async def test_web_search_reads_the_citations_openrouter_returns(monkeypatch) -> None:
    """The plugin answers in prose and carries the sources as `url_citation` annotations."""
    import httpx  # noqa: PLC0415

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Two places are well reviewed.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {
                                        "url": "https://example.com/pizza",
                                        "title": "Best deep dish",
                                        "content": "Lou Malnati's is the classic pick.",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def patched(*args, **kwargs):
        return original(*args, **{**kwargs, "transport": transport})

    monkeypatch.setattr(httpx, "AsyncClient", patched)

    gateway = ModelGateway(MemorySecretStore())
    gateway.secrets.set("openrouter_api_key", "test-key")
    result = await gateway.web_search(
        ModelSpec(provider="openrouter", model="google/gemini-3.7-flash"), "deep dish chicago", 5
    )

    assert captured["body"]["plugins"] == [{"id": "web", "max_results": 5}]
    assert captured["auth"] == "Bearer test-key"
    # Formatted the way every other source-bearing tool result is, so citation checks still work.
    assert "SOURCE: https://example.com/pizza" in result
    assert "Lou Malnati's" in result
