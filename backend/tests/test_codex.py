"""Sign in with ChatGPT: the token record, the request envelope, and the sign-in page.

The things worth pinning are the ones that would fail silently. A payload that keeps the
system prompt as an input item produces an agent that answers with no instructions rather than
an error, and a stale token is a 401 nobody can act on.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from wavefunction.codex import CodexAuth, CodexAuthError, CodexAuthStore, CodexCredentials, PendingLogin


def jwt_with(claims: dict) -> str:
    """A token shaped like OpenAI's, since only its middle segment is ever read."""
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def credentials(**overrides) -> CodexCredentials:
    access = overrides.pop(
        "access", jwt_with({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-1"}})
    )
    return CodexCredentials(
        access=access,
        refresh=overrides.pop("refresh", "refresh-token"),
        expires_at=overrides.pop("expires_at", time.time() + 3600),
        account_id=overrides.pop("account_id", "acct-1"),
        email=overrides.pop("email", "someone@example.com"),
    )


# ---- the account behind the token ----------------------------------------------------------


# ---- what is kept on disk ------------------------------------------------------------------


def test_the_sign_in_survives_a_restart_is_owner_only_and_reads_as_signed_out_when_corrupt(tmp_path):
    store = CodexAuthStore(tmp_path / "codex-auth.json")
    store.save(credentials())

    assert (tmp_path / "codex-auth.json").stat().st_mode & 0o777 == 0o600
    restored = store.load()
    assert restored is not None
    assert restored.account_id == "acct-1"
    assert restored.refresh == "refresh-token"

    # A half-written file must cost someone a sign-in, not the ability to open Settings.
    (tmp_path / "codex-auth.json").write_text("{not json", encoding="utf-8")
    assert store.load() is None


# ---- the token a request carries -----------------------------------------------------------


def test_a_token_close_to_expiring_is_refreshed_before_it_is_used(tmp_path, monkeypatch):
    """Refreshed early rather than on the 401: an agent turn runs for minutes, and a token
    that dies mid-turn surfaces as a failure the user can do nothing about."""
    auth = CodexAuth(CodexAuthStore(tmp_path / "codex-auth.json"))
    auth.store.save(credentials(access="stale-token", expires_at=time.time() + 30))
    refreshed = credentials(access="fresh-token", expires_at=time.time() + 3600)
    monkeypatch.setattr("wavefunction.codex.refresh_credentials", lambda current: refreshed)

    assert auth.access_token() == "fresh-token"
    # And it is kept, so the next process does not refresh again from the stale one.
    reloaded = CodexAuthStore(tmp_path / "codex-auth.json").load()
    assert reloaded is not None
    assert reloaded.access == "fresh-token"


async def test_provider_payloads_keep_stable_prefixes_and_conversation_cache_keys():
    from langchain_core.messages import SystemMessage
    from langchain_core.runnables.config import set_config_context

    from wavefunction.provider_chat import ChatCodex, ChatOpenAIDirect

    # Exercise the actual provider envelope without sending requests or using credentials.
    messages = [
        SystemMessage(content="Stable repository instructions"),
        HumanMessage(content="Implement the task"),
    ]
    for provider in (ChatCodex, ChatOpenAIDirect):
        model = provider(model="gpt-5", api_key="synthetic", use_responses_api=True)
        with set_config_context({"configurable": {"thread_id": "chat-a"}}) as context:
            first = context.run(model._get_request_payload, messages)
            steered = context.run(
                model._get_request_payload,
                [*messages, AIMessage(content="Working"), HumanMessage(content="Add a test")],
            )
            retry = context.run(model._get_request_payload, messages)
        assert retry == first
        assert first["prompt_cache_key"] == steered["prompt_cache_key"] == "chat-a"
        assert steered["input"][: len(first["input"])] == first["input"]
        if provider is ChatCodex:
            assert steered["instructions"] == first["instructions"] == messages[0].content
            assert first["store"] is False and "max_output_tokens" not in first
            assert first["extra_headers"] == {"session-id": "chat-a", "thread-id": "chat-a"}
        else:
            assert "extra_headers" not in first
        with set_config_context({"configurable": {"thread_id": "fork-b"}}) as context:
            forked = context.run(model._get_request_payload, messages)
        assert forked["prompt_cache_key"] == "fork-b"
        assert {
            key: value for key, value in forked.items() if key not in {"prompt_cache_key", "extra_headers"}
        } == {key: value for key, value in first.items() if key not in {"prompt_cache_key", "extra_headers"}}
        with set_config_context({"configurable": {"thread_id": "long-chat-" * 30}}) as context:
            long_key = context.run(model._get_request_payload, messages)["prompt_cache_key"]
            assert len(long_key) == 64
            assert context.run(model._get_request_payload, messages)["prompt_cache_key"] == long_key
        model.root_client.close()
        await model.root_async_client.close()


def test_the_model_menu_offers_only_reasoning_levels_the_api_takes():
    from wavefunction.codex import parse_models

    # "ultra" is the Codex CLI's own mode; any other level the API does not take goes too.
    levels = [{"effort": effort} for effort in ("low", "medium", "high", "xhigh", "max", "ultra", "swarm")]
    [model] = parse_models(
        {"models": [{"slug": "gpt-6.1-sol", "visibility": "list", "supported_reasoning_levels": levels}]}
    )
    assert model["reasoning_efforts"] == ["low", "medium", "high", "xhigh", "max"]


# ---- the request envelope ------------------------------------------------------------------


# ---- what someone reads when it goes wrong -------------------------------------------------


# ---- the page the browser lands on ----------------------------------------------------------


class FakeWriter:
    """Enough of a `StreamWriter` to read back what the browser would have been sent."""

    def __init__(self) -> None:
        self.sent = b""

    def write(self, data: bytes) -> None:
        self.sent += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


async def page_for(auth: CodexAuth, target: str) -> str:
    reader = asyncio.StreamReader()
    reader.feed_data(f"GET {target} HTTP/1.1\r\n".encode())
    reader.feed_eof()
    writer = FakeWriter()
    await auth._handle_request(reader, writer)
    return writer.sent.decode()


async def test_the_callback_page_agrees_with_the_sign_in_decision(tmp_path):
    """The page said "Signed in to ChatGPT" while the app rejected the sign-in behind it —
    the user was told the opposite of what happened. The page has to agree with the decision."""

    def waiting() -> CodexAuth:
        auth = CodexAuth(CodexAuthStore(tmp_path / "codex-auth.json"))
        auth._pending = PendingLogin(
            url="https://auth.openai.com/oauth/authorize",
            state="expected",
            verifier="v",
            started_at=time.time(),
        )
        auth._callback = asyncio.get_running_loop().create_future()
        return auth

    page = await page_for(waiting(), "/auth/callback?code=abc&state=forged")
    assert "400" in page.splitlines()[0]
    assert "Sign-in failed" in page
    assert "Signed in to ChatGPT" not in page

    matching = waiting()
    page = await page_for(matching, "/auth/callback?code=abc&state=expected")
    assert "Signed in to ChatGPT" in page
    assert matching._callback.result() == ("abc", "expected", "")


# ---- the catalogue --------------------------------------------------------------------------


# ---- the stream the backend actually sends ---------------------------------------------------


def cache_response(call_tool: bool, index: int) -> bytes:
    """Codex SSE, including one-shot arguments and reasoning delivered twice by the wire."""
    response = {"id": f"resp_{index}", "model": "gpt-6.1-sol", "output": [], "error": None}
    events = [{"type": "response.created", "response": response}]
    if call_tool:
        reasoning = {
            "type": "reasoning",
            "id": "rs_1",
            "summary": [],
            "encrypted_content": "synthetic-encrypted-state",
        }
        function = {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "calculator",
            "arguments": "",
        }
        events.extend(
            [
                {"type": "response.output_item.added", "output_index": 0, "item": reasoning},
                {"type": "response.output_item.done", "output_index": 0, "item": reasoning},
                {"type": "response.output_item.added", "output_index": 1, "item": function},
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": 1,
                    "item_id": "fc_1",
                    "arguments": '{"a":21}',
                },
            ]
        )
    else:
        events.extend(
            [
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "type": "message",
                        "id": f"msg_{index}",
                        "content": [],
                        "role": "assistant",
                        "phase": "final_answer",
                    },
                },
                {
                    "type": "response.output_text.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "The answer is 42.",
                },
                {
                    "type": "response.output_text.done",
                    "output_index": 0,
                    "content_index": 0,
                    "item_id": f"msg_{index}",
                },
            ]
        )
    events.append(
        {
            "type": "response.completed",
            "response": {
                **response,
                "status": "completed",
                "usage": {
                    "input_tokens": 5000,
                    "output_tokens": 20,
                    "total_tokens": 5020,
                    "input_tokens_details": {"cached_tokens": 4096, "cache_write_tokens": 256},
                    "output_tokens_details": {"reasoning_tokens": 10},
                },
            },
        }
    )
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_codex_cache_routing_and_streamed_history_survive_tool_retry_and_next_turn(
    tmp_path, monkeypatch, asynchronous
):
    from langchain_core.messages import SystemMessage, ToolMessage, messages_from_dict, messages_to_dict
    from langchain_core.runnables.config import set_config_context
    from openai import InternalServerError

    from wavefunction.codex import CODEX_BASE_URL, codex_http_clients
    from wavefunction.provider_chat import ChatCodex

    class Auth:
        account = "account-a"

        def request_credentials(self):
            return credentials(account_id=self.account)

    class Fragments(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __init__(self, body):
            self.body = body

        def __iter__(self):
            # Split inside JSON and SSE boundaries, just as real network packets do.
            return iter(self.body[i : i + 37] for i in range(0, len(self.body), 37))

        async def __aiter__(self):
            for part in self:
                yield part

    seen = []

    def respond(request):
        seen.append((dict(request.headers), json.loads(request.content)))
        index = len(seen)
        if index == 2:
            return httpx.Response(
                503,
                json={"error": {"message": "Try again"}},
                headers={"x-codex-turn-state": "bad-error-route"},
            )
        return httpx.Response(
            200,
            stream=Fragments(cache_response(index == 1, index)),
            headers={"content-type": "text/event-stream", "x-codex-turn-state": f"route-{index}"},
        )

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _self, request: respond(request))

    async def async_response(_self, request):
        return respond(request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_response)
    auth = Auth()
    models = []

    def build():
        sync, async_ = codex_http_clients(auth)
        model = ChatCodex(
            model="gpt-6.1-sol",
            api_key="synthetic",
            base_url=CODEX_BASE_URL,
            http_client=sync,
            http_async_client=async_,
            use_responses_api=True,
            streaming=True,
            store=False,
            output_version="responses/v1",
            max_retries=0,
            include=["reasoning.encrypted_content"],
        )
        models.append(model)
        return model

    async def invoke(model, messages, chat="chat-a"):
        with set_config_context({"configurable": {"thread_id": chat}}) as context:
            if asynchronous:
                return await asyncio.create_task(model.ainvoke(messages), context=context)
            return context.run(model.invoke, messages)

    try:
        model = build()
        messages = [SystemMessage(content="Stable instructions"), HumanMessage(content="Calculate 21 + 21")]
        first = await invoke(model, messages)
        assert first.tool_calls == [
            {"name": "calculator", "args": {"a": 21}, "id": "call_1", "type": "tool_call"}
        ]
        messages += [
            first,
            ToolMessage(content="42", tool_call_id="call_1"),
            HumanMessage(content="Explain briefly"),
        ]
        with pytest.raises(InternalServerError):
            await invoke(model, messages)
        reply = await invoke(model, messages)
        assert seen[1] == seen[2]  # A retry changes neither prefix nor routing state.
        assert seen[0][0]["session-id"] == seen[1][0]["session-id"] == "chat-a"
        assert "x-codex-turn-state" not in seen[0][0]
        assert seen[1][0]["x-codex-turn-state"] == "route-1"
        assert seen[1][1]["input"][: len(seen[0][1]["input"])] == seen[0][1]["input"]
        reasoning = [item for item in seen[2][1]["input"] if item.get("type") == "reasoning"]
        assert reasoning == [
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [],
                "encrypted_content": "synthetic-encrypted-state",
            }
        ]
        assert reply.usage_metadata["input_token_details"] == {"cache_read": 4096, "cache_creation": 256}

        # Persisted provider messages must replay identically, including opaque reasoning.
        messages.append(reply)
        from wavefunction.runtime import result_text

        assert result_text(messages) == "The answer is 42."
        path = tmp_path / "messages.json"
        path.write_text(json.dumps(messages_to_dict(messages)))
        restored = messages_from_dict(json.loads(path.read_text()))
        next_turn = build()
        await invoke(next_turn, [*restored, HumanMessage(content="Continue")])
        assert "x-codex-turn-state" not in seen[-1][0]  # New turn, same cache/session key.
        assert seen[-1][0]["session-id"] == "chat-a"
        assert seen[-1][1]["input"][: len(seen[2][1]["input"])] == seen[2][1]["input"]
        assert seen[-1][1]["input"][-2]["phase"] == "final_answer"
        await invoke(next_turn, restored)
        assert seen[-1][0]["x-codex-turn-state"] == "route-4"
        await invoke(next_turn, restored, chat="fork-b")
        assert seen[-1][0]["session-id"] == seen[-1][0]["thread-id"] == "fork-b"
        assert "x-codex-turn-state" not in seen[-1][0]
        auth.account = "account-b"
        await invoke(next_turn, restored, chat="fork-b")
        assert seen[-1][0]["chatgpt-account-id"] == "account-b"
        assert "x-codex-turn-state" not in seen[-1][0]
    finally:
        for model in models:
            model.root_client.close()
            await model.root_async_client.close()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_expired_auth_is_not_retried_or_masked(asynchronous):
    from openai import AsyncOpenAI, AuthenticationError, OpenAI

    from wavefunction.codex import AsyncCodexTransport, CodexTransport
    from wavefunction.models import describe_provider_error, is_retryable_provider_error

    class Expired:
        calls = 0

        def request_credentials(self):
            self.calls += 1
            raise CodexAuthError("The ChatGPT sign-in has expired. Sign in again in Settings › Models.")

    auth = Expired()
    with pytest.raises(AuthenticationError) as caught:
        if asynchronous:
            async with AsyncOpenAI(
                api_key="test", http_client=httpx.AsyncClient(transport=AsyncCodexTransport(auth=auth))
            ) as client:
                await client.responses.create(model="test", input="test")
        else:
            with OpenAI(
                api_key="test", http_client=httpx.Client(transport=CodexTransport(auth=auth))
            ) as client:
                client.responses.create(model="test", input="test")
    assert auth.calls == 1
    assert "Sign in again" in describe_provider_error(caught.value)
    assert not is_retryable_provider_error(caught.value)
