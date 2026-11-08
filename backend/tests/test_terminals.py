"""Terminals: the user's PTY, the agent's terminals and their ownership, and cleanup on close."""

import asyncio

import httpx
import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage, ToolMessage
from support import create


@pytest.mark.sandbox
async def test_interactive_terminal_input_resize_reconnect_and_isolation(coding, tmp_path):
    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    first, second = (await create(client, folder=str(folder)), await create(client, folder=str(folder)))
    base = f"/api/coding/threads/{first['id']}/terminals"
    response = await client.post(base, json={"title": "Shell", "cols": 91, "rows": 31})
    assert response.status_code == 201, response.text
    terminal = response.json()
    session = await ws.terminals.get(first["id"], terminal["id"])
    pid = session.process.pid
    url = base + "/" + terminal["id"]
    try:
        assert (
            await client.post(url + "/input", json={"text": "printf 'PTY-ready\\n'; stty size\n"})
        ).status_code == 204
        cursor, text = 0, ""
        for _ in range(20):
            output = await client.get(url + "/output", params={"cursor": cursor, "wait": 0.3})
            assert output.status_code == 200, output.text
            body = output.json()
            cursor, text = body["cursor"], text + body["text"]
            if "31 91" in text:
                break
        assert "PTY-ready" in text and "31 91" in text, text
        assert (await client.get(url + "/output", params={"cursor": 0})).json()["text"]
        assert len((await client.get(base)).json()) == 1
        assert (
            await client.get(f"/api/coding/threads/{second['id']}/terminals/{terminal['id']}/output")
        ).status_code == 404
        assert (await client.put(url + "/size", json={"cols": 103, "rows": 41})).status_code == 204
        await client.post(
            url + "/input", json={"text": "stty size; read answer; printf 'ANSWER=%s\\n' \"$answer\"\n"}
        )
        await client.post(url + "/input", json={"text": "interactive-value\n"})
        for _ in range(20):
            body = (await client.get(url + "/output", params={"cursor": cursor, "wait": 0.3})).json()
            cursor, text = body["cursor"], text + body["text"]
            if "ANSWER=interactive-value" in text:
                break
        assert "41 103" in text and "ANSWER=interactive-value" in text, text
        # A grant added or re-saved elsewhere never reaches this running sandbox, so it keeps
        # running; revoking write access to its own folder stops it.
        from wavefunction.sandbox import GRANTS_SETTING

        grants = services.repository.get_setting(GRANTS_SETTING, [])
        (tmp_path / "other").mkdir()
        ws.allow_folder(str((tmp_path / "other").resolve()), writable=False)
        for grant in grants:
            ws.allow_folder(grant["path"], writable=grant["writable"])
        assert (await client.post(url + "/input", json={"text": "\n"})).status_code == 204
        assert session.status == "running"
        services.repository.set_setting(GRANTS_SETTING, [{**g, "writable": False} for g in grants])
        assert (await client.post(url + "/input", json={"text": "\n"})).status_code == 422
        assert session.status == "closed"
        services.repository.set_setting(GRANTS_SETTING, grants)
        await client.patch(f"/api/coding/threads/{first['id']}", json={"mode": "plan"})
        assert (await client.post(url + "/input", json={"text": "touch forbidden\n"})).status_code == 422
        assert session.process.returncode is not None
        assert not (folder / "forbidden").exists()
        assert (await client.post(base, json={})).status_code == 422
    finally:
        await ws.terminals.forget(first["id"])
    assert session.process.returncode is not None, pid


@pytest.mark.sandbox
async def test_agent_terminal_server_survives_replies_and_remains_owned_by_its_chat(coding, tmp_path):
    import json
    import shlex
    import sys
    from unittest.mock import AsyncMock, patch

    from conftest import ScriptedModel
    from langchain_core.outputs import ChatGeneration, ChatResult

    from wavefunction.terminal_tools import build_terminal_tool
    from wavefunction.toolkit import ToolContext

    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    # Not ThreadingHTTPServer: it looks up its own address, which can take 35 s (see conftest.py).
    (folder / "preview.py").write_text("""
import socketserver
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"preview-ready")

server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
Path("server.port").write_text(str(server.server_address[1]))
Thread(target=server.serve_forever, daemon=True).start()
for line in sys.stdin:
    Path("background.txt").write_text(line)
    print("AGENT:" + line.strip(), flush=True)
""")
    thread = await create(client, folder=str(folder))
    user = await ws.terminals.create(thread["id"], ws.runner(), folder, title="User shell")

    class TerminalModel(ScriptedModel):
        async def _agenerate(self, messages, **kwargs):
            sessions = [s for s in ws.terminals.sessions.values() if s.owner == "agent"]
            if self._cursor == 0:
                message = tool_call_message(
                    "start_terminal",
                    {"command": f"{shlex.quote(sys.executable)} -u preview.py", "title": "Preview server"},
                    "start",
                )
            elif self._cursor == 1:
                assert len(sessions) == 1
                message = tool_call_message(
                    "write_terminal", {"terminal_id": sessions[0].id, "text": "typed-value\n"}, "input"
                )
            elif self._cursor < 12 and not any(
                "AGENT:typed-value" in str(m.content) for m in messages if isinstance(m, ToolMessage)
            ):
                # As an agent would: read on from the returned cursor until the program's reply
                # has arrived. The first read can return just the echoed input, before the program
                # has printed, and rereading from the start wouldn't wait for more: a slow start
                # would then look like a stuck loop and end the run.
                reads = [
                    json.loads(str(m.content))
                    for m in messages
                    if isinstance(m, ToolMessage) and m.name == "read_terminal"
                ]
                message = tool_call_message(
                    "read_terminal",
                    {
                        "terminal_id": sessions[0].id,
                        "cursor": reads[-1]["cursor"] if reads else 0,
                        "wait_seconds": 5,
                    },
                    f"output-{self._cursor}",
                )
            else:
                message = AIMessage(content="Preview server ready")
            self._cursor += 1
            return ChatResult(generations=[ChatGeneration(message=message)])

    services.models.chat_model = lambda *_args, **_kwargs: TerminalModel()
    run = await ws.send(thread["id"], "Run an interactive background check")
    result = await services.runtime.wait(run["id"])
    assert result["status"] == "completed", result["error"]
    snapshot = (await client.get("/api/coding/workspace")).json()
    assert (
        next(t for t in snapshot["threads"] if t["id"] == thread["id"])["last_finished_run_id"] == run["id"]
    )
    sessions = [s for s in ws.terminals.sessions.values() if s.owner == "agent"]
    assert len(sessions) == 1
    agent = sessions[0]
    # The old run-status monitor also closed terminals asynchronously, after completion.
    await asyncio.sleep(0.75)
    assert not agent.closed and agent.process.returncode is None
    assert user.status == "running" and user.process.returncode is None
    assert (folder / "background.txt").read_text() == "typed-value\n"
    events = services.repository.list_run_events(run["id"])
    assert "AGENT:typed-value" in str(events)
    port = int((folder / "server.port").read_text())
    async with httpx.AsyncClient(trust_env=False) as browser:
        response = await browser.get(f"http://127.0.0.1:{port}")
    assert response.status_code == 200 and response.text == "preview-ready"

    # A later turn can interact with the same terminal instead of starting a duplicate server.
    script_gateway(
        services.models,
        [
            tool_call_message("write_terminal", {"terminal_id": agent.id, "text": "second-value\n"}, "input"),
            tool_call_message("read_terminal", {"terminal_id": agent.id, "wait_seconds": 1}, "output"),
            AIMessage(content="Preview server still ready"),
        ],
    )
    followup = await ws.send(thread["id"], "Update the existing preview")
    assert (await services.runtime.wait(followup["id"]))["status"] == "completed"
    assert "AGENT:second-value" in str(services.repository.list_run_events(followup["id"]))
    assert (folder / "background.txt").read_text() == "second-value\n"
    context = ToolContext(
        ws.agent_id,
        services.repository,
        services.files,
        services.secrets,
        run_id=followup["id"],
        conversation_id=thread["conversation_id"],
        desktop_copy=thread["id"],
        coding_workspace=ws,
    )
    listed = json.loads(await build_terminal_tool(context, "list_terminals").ainvoke({}))
    assert {s["owner"] for s in listed} == {"user", "agent"}
    with pytest.raises(PermissionError, match="Only the user"):
        await build_terminal_tool(context, "write_terminal").ainvoke(
            {"terminal_id": user.id, "text": "touch forbidden\n"}
        )
    with pytest.raises(PermissionError, match="Only the user"):
        await build_terminal_tool(context, "close_terminal").ainvoke({"terminal_id": user.id})
    assert not (folder / "forbidden").exists()
    other = await create(client, folder=str(folder))
    other_run = await ws.send(other["id"], "Separate conversation")
    await services.runtime.wait(other_run["id"])
    other_context = ToolContext(
        ws.agent_id,
        services.repository,
        services.files,
        services.secrets,
        run_id=other_run["id"],
        conversation_id=other["conversation_id"],
        desktop_copy=other["id"],
        coding_workspace=ws,
    )
    for name, arguments in (
        ("write_terminal", {"terminal_id": agent.id, "text": "other-chat\n"}),
        ("read_terminal", {"terminal_id": agent.id}),
        ("close_terminal", {"terminal_id": agent.id}),
    ):
        with pytest.raises(KeyError, match="this conversation"):
            await build_terminal_tool(other_context, name).ainvoke(arguments)
    # A terminal can't show the approval card, so it refuses what run_command would ask about.
    # Never allow this command-classification example to reach process execution.
    with patch.object(
        ws.terminals, "create", new=AsyncMock(side_effect=AssertionError("Must not execute"))
    ) as launch:
        with pytest.raises(PermissionError, match="Run it with run_command"):
            await build_terminal_tool(context, "start_terminal").ainvoke({"command": "git reset --hard"})
        launch.assert_not_awaited()

    await build_terminal_tool(context, "close_terminal").ainvoke({"terminal_id": agent.id})
    assert agent.closed and agent.process.returncode is not None
    with ws.db() as db:
        assert db.execute(
            "SELECT 1 FROM coding_changes WHERE thread_id = ? AND path = 'background.txt' AND source = 'terminal'",
            (thread["id"],),
        ).fetchone()
    async with httpx.AsyncClient(trust_env=False) as browser:
        with pytest.raises(httpx.ConnectError):
            await browser.get(f"http://127.0.0.1:{port}")
    assert user.status == "running" and user.process.returncode is None


@pytest.mark.sandbox
@pytest.mark.parametrize("closure", ["close", "exit", "cancel-close"])
async def test_terminal_enforces_private_storage_and_stops_children_on_close(coding, tmp_path, closure):
    import os
    import shlex

    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    private = services.settings.data_dir / "private-test.txt"
    private.write_text("PRIVATE-SYNTHETIC-MARKER")
    thread = await create(client, folder=str(folder))
    session = await ws.terminals.create(thread["id"], ws.runner(), folder)
    try:
        await session.write(f"cat {shlex.quote(str(private))}; printf 'BOUNDARY-DONE\\n'\n")
        text, cursor = "", 0
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            output = await session.output(cursor, 0.2)
            text += output["text"]
            cursor = output["cursor"]
            if "Operation not permitted" in text or "Permission denied" in text:
                break
        assert "PRIVATE-SYNTHETIC-MARKER" not in text
        assert "Operation not permitted" in text or "Permission denied" in text, text
        await session.write("sleep 60 & echo $! >child.pid\n")
        for _ in range(30):
            if (folder / "child.pid").is_file():
                break
            await asyncio.sleep(0.05)
        child = int((folder / "child.pid").read_text())
        if closure == "exit":
            await session.write("exit\nexit\n")
            await asyncio.wait_for(session.waiter, 3)
        elif closure == "cancel-close":
            from contextlib import suppress

            closing = asyncio.create_task(ws.terminals.remove(thread["id"], session.id))
            while not session.closed:
                await asyncio.sleep(0)
            closing.cancel()
            with suppress(asyncio.CancelledError):
                await closing
            await ws.terminals.remove(thread["id"], session.id)
        else:
            await ws.terminals.remove(thread["id"], session.id)
        for _ in range(30):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail(f"Terminal child remained after close: {child}")
        assert session.process.returncode is not None
    finally:
        await session.close()
