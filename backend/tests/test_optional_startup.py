import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from conftest import ScriptedModel

from wavefunction import main
from wavefunction.config import Settings
from wavefunction.connectors import McpServer


@pytest.mark.asyncio
async def test_startup_health_does_not_wait_for_optional_services_and_shutdown_cancels_them(
    tmp_path, monkeypatch
):
    services = main.Services(Settings(data_dir=tmp_path, development_auth_bypass=True))
    monkeypatch.setattr(main, "services", services)
    started, cancelled = set(), set()
    discovery_started = asyncio.Event()

    async def hang(name):
        started.add(name)
        if started == {"connectors", "computer"}:
            discovery_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.add(name)

    # The import behavior has a separate fresh-process regression. This workflow keeps
    # discovery pending without performing network, Docker, or worktree cleanup operations.
    monkeypatch.setattr(services, "_warm_optional_code", lambda: None)
    monkeypatch.setattr(services.coding_workspace.cleanup, "run", AsyncMock())
    monkeypatch.setattr(services.connectors, "refresh_all", lambda: hang("connectors"))
    monkeypatch.setattr(services.computer, "refresh_status", lambda: hang("computer"))
    monkeypatch.setattr(services.computer, "shutdown", AsyncMock())
    monkeypatch.setattr(services.model_registry, "refresh", AsyncMock())
    generating = asyncio.Event()

    class WorkingModel(ScriptedModel):
        async def _agenerate(self, messages, **kwargs):
            generating.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(services.models, "chat_model", lambda *args, **kwargs: WorkingModel())
    async with asyncio.timeout(2):
        async with main.lifespan(main.app):
            await asyncio.sleep(0)
            assert started == set()
            assert services.optional_warmup is None
            services.model_registry.refresh.assert_not_awaited()  # pyright: ignore[reportAttributeAccessIssue]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                assert (await client.get("/health")).status_code == 200
                assert not started and services.optional_warmup is None
                assert (await client.get("/api/coding/workspace")).status_code == 200
                await discovery_started.wait()
                assert started == {"connectors", "computer"}
                assert services.optional_warmup is not None and services.optional_warmup.done()
                assert (await client.get("/health")).status_code == 200
                thread = (await client.post("/api/coding/threads", json={})).json()
                response = await client.post(
                    f"/api/coding/threads/{thread['id']}/messages", json={"content": "Keep working"}
                )
                assert response.status_code == 202
                run_id = response.json()["id"]
                await generating.wait()
                assert (await client.get("/api/activity")).json() == {"active": True}
        assert cancelled == started
    assert services.repository.get_run(run_id)["status"] == "cancelled"
    assert all(task.done() for task in services.runtime.tasks.values())
    assert services.runtime._checkpointer is None


@pytest.mark.asyncio
async def test_connector_discovery_is_concurrent_and_cancellation_does_not_become_failure(stack, monkeypatch):
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from wavefunction.connectors import ConnectorRegistry

    registry = ConnectorRegistry(stack.repository, stack.secrets)
    registry.save(
        [
            McpServer(name="slow", transport="http", url="https://slow.example/mcp"),
            McpServer(name="fast", transport="http", url="https://fast.example/mcp"),
        ]
    )
    fast_done = asyncio.Event()

    async def get_tools(self, *, server_name):
        if server_name == "slow":
            await asyncio.Event().wait()
        fast_done.set()
        return []

    monkeypatch.setattr(MultiServerMCPClient, "get_tools", get_tools)
    pending = asyncio.create_task(registry.refresh_all())
    try:
        await asyncio.wait_for(fast_done.wait(), timeout=1)
        await asyncio.sleep(0)
        assert "slow" in registry.checking
        assert registry.cached("fast") is not None
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert registry.checking == set()
    assert registry.cached("slow") is None


@pytest.mark.asyncio
async def test_cancelled_computer_probe_reaps_its_process(monkeypatch):
    import sys

    from wavefunction.computer import _run_subprocess

    original = asyncio.create_subprocess_exec
    created = asyncio.Event()
    processes = []

    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        created.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    pending = asyncio.create_task(_run_subprocess(sys.executable, "-c", "import time; time.sleep(60)"))
    await asyncio.wait_for(created.wait(), timeout=2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert processes[0].returncode is not None


async def test_startup_serves_workspace_before_optional_warmup_and_restores_home_inventory(tmp_path):
    """A fresh process catches eager SDK imports hidden by pytest's other provider tests."""
    import os
    import subprocess
    import sys
    import textwrap

    probe = textwrap.dedent("""\
        import asyncio, json, sys, tempfile, threading
        from pathlib import Path
        tempfile.tempdir = str(Path.cwd())
        sys.path.insert(0, sys.argv[1])
        def guard(event, args):
            if event in {'subprocess.Popen', 'os.system', 'socket.connect', 'socket.connect_ex',
                         'socket.bind', 'socket.sendto', 'os.remove', 'os.rmdir', 'shutil.rmtree'}:
                raise AssertionError('Unexpected startup effect: ' + event)
        sys.addaudithook(guard)
        from wavefunction import main
        from wavefunction.spec_common import ModelSpec
        from wavefunction.coding_workspace import ThreadInput
        from wavefunction.connectors import McpServer
        optional = ('openai', 'langchain_openai', 'langchain_ollama', 'mcp', 'pypdf')
        assert not any(module in sys.modules for module in optional)
        original = main.services
        # Warm-up loads the default model's SDK only, and connector code only with a connector.
        original.repository.set_setting('enabled_models', ['openai::synthetic-model', 'ollama::synthetic:8b'])
        original.repository.set_setting('default_model', {'provider': 'openai', 'model': 'synthetic-model'})
        original.connectors.save([McpServer(name='synthetic', command='synthetic')])
        thread = original.coding_workspace.create_thread(ThreadInput())
        main.services = services = main.Services(original.settings)
        assert {thread['id'], services.coding_workspace.agent_id} <= set(main.agent_copies())
        assert services.coding_workspace.agent_id == original.coding_workspace.agent_id
        assert services.optional_warmup is None
        sent, entered, release = threading.Event(), threading.Event(), threading.Event()
        imports = []
        importer = main.import_module
        def warm_import(module):
            assert sent.is_set(), 'warm-up began before workspace body was sent'
            imports.append(module)
            if len(imports) == 1:
                entered.set()
                assert release.wait(5), 'test did not release warm-up'
            if module == 'wavefunction.connector_oauth':
                raise ImportError('synthetic optional warm-up failure')
            return importer(module)
        main.import_module = warm_import
        async def observed_app(scope, receive, send):
            async def observe(message):
                await send(message)
                if message['type'] == 'http.response.body' and not message.get('more_body'):
                    sent.set()
            await main.app(scope, receive, observe)
        async def run():
            import httpx
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observed_app), base_url='http://test') as client:
                try:
                    snapshot = await asyncio.wait_for(client.get('/api/coding/workspace'), 2)
                    assert snapshot.status_code == 200
                    assert any(row['id'] == thread['id'] for row in snapshot.json()['threads'])
                    assert await asyncio.to_thread(entered.wait, 2)
                    task = services.optional_warmup
                    assert task is not None and not task.done()
                    history = await asyncio.wait_for(client.get('/api/coding/threads/' + thread['id']), 2)
                    assert history.status_code == 200
                    assert (await asyncio.wait_for(client.get('/api/coding/workspace'), 2)).status_code == 200
                    assert services.optional_warmup is task and len(imports) == 1
                    # A cancelled discovery waiter must not cancel the owned import worker.
                    waiter = asyncio.create_task(services.finish_optional_warmup())
                    await asyncio.sleep(0)
                    waiter.cancel()
                    await asyncio.gather(waiter, return_exceptions=True)
                    assert not task.cancelled()
                finally:
                    release.set()
                    await services.finish_optional_warmup()
            assert len(imports) == len(set(imports)) == 3
            assert all(module in sys.modules for module in ('openai', 'langchain_openai', 'mcp'))
            # Other providers stay unloaded, and PDFs load their reader on first use.
            assert not any(module in sys.modules for module in ('langchain_anthropic', 'langchain_google_genai', 'pypdf'))
            # A provider left unwarmed still works on first use.
            assert 'langchain_ollama' not in sys.modules
            model = services.models.chat_model(ModelSpec(provider='ollama', model='synthetic:8b'))
            assert model.model == 'synthetic:8b'
            model._client._client.close()
            await model._async_client._client.aclose()
            await services.runtime.aclose()
            await original.runtime.aclose()
        asyncio.run(run())
        print(json.dumps({'ready': True, 'worker_joined': main.services.optional_warmup.done()}))
    """)
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", probe, str(Path(__file__).resolve().parents[1] / "src")],
        cwd=tmp_path,
        env={
            **os.environ,
            "WAVEFUNCTION_DATA_DIR": str(tmp_path / "data"),
            "WAVEFUNCTION_DEVELOPMENT_AUTH_BYPASS": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ready": True, "worker_joined": True}
