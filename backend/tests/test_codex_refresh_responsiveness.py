import asyncio
import threading
import time

import httpx
import pytest

from wavefunction.codex import (
    CODEX_BASE_URL,
    AsyncCodexTransport,
    CodexAuth,
    CodexAuthError,
    CodexAuthStore,
    CodexCredentials,
)
from wavefunction.models import ModelGateway
from wavefunction.secrets import MemorySecretStore
from wavefunction.spec_common import ModelSpec


async def test_model_build_does_not_refresh_and_async_requests_share_nonblocking_refresh(
    tmp_path, monkeypatch
):
    auth = CodexAuth(CodexAuthStore(tmp_path / "credentials.json"))
    auth._remember(CodexCredentials("expired", "refresh", 0, "old-account"))
    entered, release = threading.Event(), threading.Event()
    calls = []

    def refresh(credentials):
        calls.append(credentials)
        entered.set()
        assert release.wait(5)
        return CodexCredentials("fresh", "rotated", time.time() + 3600, "new-account")

    monkeypatch.setattr("wavefunction.codex.refresh_credentials", refresh)
    gateway = ModelGateway(MemorySecretStore(), codex=auth)
    model = gateway.chat_model(ModelSpec(provider="codex", model="gpt-5"))
    assert not entered.is_set()
    received = []

    async def handle(self, request):
        received.append(dict(request.headers))
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", handle)
    transport = AsyncCodexTransport(auth=auth)
    requests = [
        asyncio.create_task(
            transport.handle_async_request(
                httpx.Request(
                    "POST",
                    CODEX_BASE_URL + "/responses",
                    headers={"x-openai-internal-codex-residency": "old-region"},
                )
            )
        )
        for _ in range(2)
    ]
    try:
        # A heartbeat on this same event loop must run while the network refresh is pending.
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.005)
            await asyncio.sleep(0.02)
            assert not any(task.done() for task in requests)
            release.set()
            await asyncio.gather(*requests)
        assert len(calls) == 1
        assert all(headers["authorization"] == "Bearer fresh" for headers in received)
        assert all(headers["chatgpt-account-id"] == "new-account" for headers in received)
        assert all("x-openai-internal-codex-residency" not in headers for headers in received)
    finally:
        release.set()
        await transport.aclose()
        model.http_client.close()
        await model.http_async_client.aclose()


async def test_signout_during_refresh_does_not_restore_credentials(tmp_path, monkeypatch):
    auth = CodexAuth(CodexAuthStore(tmp_path / "credentials.json"))
    auth._remember(CodexCredentials("expired", "refresh", 0, "account"))
    entered, release = threading.Event(), threading.Event()

    def refresh(credentials):
        entered.set()
        assert release.wait(5)
        return CodexCredentials("fresh", "rotated", time.time() + 3600, "account")

    monkeypatch.setattr("wavefunction.codex.refresh_credentials", refresh)
    pending = asyncio.create_task(asyncio.to_thread(auth.request_credentials))
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.005)
            auth.logout()
            release.set()
            with pytest.raises(CodexAuthError, match="sign-in changed"):
                await pending
        assert not auth.connected()
        assert not (tmp_path / "credentials.json").exists()
    finally:
        release.set()
