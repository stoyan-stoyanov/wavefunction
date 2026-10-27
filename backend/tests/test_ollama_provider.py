from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama

from wavefunction import main
from wavefunction.config import Settings
from wavefunction.models import ModelGateway
from wavefunction.ollama_provider import discover_models
from wavefunction.secrets import MemorySecretStore
from wavefunction.spec_common import ModelSpec


async def test_discovery_uses_native_api_and_keeps_capabilities(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "agent:8b"}, {"name": "embed:latest"}]})
        name = json.loads(request.content)["model"]
        return httpx.Response(
            200,
            json={
                "capabilities": ["completion", "tools"] if name == "agent:8b" else ["embedding"],
                "model_info": {"llama.context_length": 65536},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://localhost:11434")
    monkeypatch.setattr("wavefunction.ollama_provider.httpx.AsyncClient", lambda **kwargs: client)
    models = await discover_models("http://localhost:11434")
    assert models[0]["id"] == "agent:8b"
    assert "tools" in models[0]["capabilities"]
    assert "tools" not in models[1]["capabilities"]
    assert models[0]["context_length"] == 65536
    assert len(requests) == 3


def test_connect_persists_only_success_and_drives_default(tmp_path, monkeypatch):
    isolated = main.Services(Settings(data_dir=tmp_path, api_token="", secret_backend="memory"))
    monkeypatch.setattr(main, "services", isolated)
    models = [{"id": "agent:8b", "name": "agent:8b", "capabilities": ["tools"], "context_length": 8192}]
    discovery = AsyncMock(return_value=models)
    monkeypatch.setattr(main, "discover_models", discovery)
    with TestClient(main.app) as client:
        result = client.put(
            "/api/connections/ollama", json={"base_url": "http://localhost:11434/", "num_ctx": 16384}
        )
        assert result.status_code == 200
        assert client.get("/api/connections").json()["ollama"]["models"] == models
        enabled = client.put("/api/connections/enabled-models", json={"models": ["ollama::agent:8b"]}).json()
        assert enabled["model"]["provider"] == "ollama"
        model = isolated.models.chat_model(ModelSpec(provider="ollama", model="agent:8b"))
        assert isinstance(model, ChatOllama)
        assert model.base_url == "http://localhost:11434"
        assert model.num_ctx == 8192  # Never exceed the model's reported capacity.
        discovery.side_effect = httpx.ConnectError("offline")
        failed = client.put("/api/connections/ollama", json={"base_url": "http://other:11434"})
        assert failed.status_code == 502
        assert isolated.repository.get_setting("ollama")["base_url"] == "http://localhost:11434"
    restored = main.Services(Settings(data_dir=tmp_path, api_token="", secret_backend="memory"))
    assert (
        restored.models.chat_model(ModelSpec(provider="ollama", model="agent:8b")).base_url
        == "http://localhost:11434"
    )


async def test_native_adapter_streams_and_executes_tools(monkeypatch):
    requests = []

    def handler(request):
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["model"] == "agent:8b"
        assert "authorization" not in request.headers
        if payload.get("tools") and not any(m["role"] == "tool" for m in payload["messages"]):
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": "double", "arguments": {"value": 3}}}],
            }
        else:
            message = {"role": "assistant", "content": "The result is 6."}
        body = {
            "model": "agent:8b",
            "message": message,
            "done": True,
            "prompt_eval_count": 12,
            "eval_count": 6,
        }
        if payload.get("stream"):
            return httpx.Response(
                200, text=json.dumps(body) + "\n", headers={"content-type": "application/x-ndjson"}
            )
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)

    def native_model(**kwargs):
        kwargs["client_kwargs"]["transport"] = transport
        return ChatOllama(**kwargs)

    monkeypatch.setattr("langchain_ollama.ChatOllama", native_model)
    gateway = ModelGateway(MemorySecretStore(), ollama_config=lambda: {"base_url": "http://localhost:11434"})
    spec = ModelSpec(provider="ollama", model="agent:8b", max_tokens=2048)
    model = gateway.chat_model(spec)
    chunks = [chunk async for chunk in model.astream("Hello")]
    assert "".join(chunk.content for chunk in chunks) == "The result is 6."

    called = []

    @tool
    def double(value: int) -> int:
        """Double a number."""
        called.append(value)
        return value * 2

    agent = create_agent(model, tools=[double])
    result = await agent.ainvoke({"messages": [("user", "Double 3")]})
    assert called == [3]
    assert result["messages"][-1].content == "The result is 6."
    assert requests[-1]["options"]["num_predict"] == 2048
    assert requests[-1]["options"]["num_ctx"] == 32768
