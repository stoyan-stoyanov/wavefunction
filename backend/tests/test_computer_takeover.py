"""Real graph/API handoffs with a synthetic desktop; no real credentials or Docker."""

import asyncio
import json
import time
from functools import partial

import httpx
import pytest
from conftest import ScriptedModel, tool_call_message
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from wavefunction.computer import DesktopManager, DesktopSession, DockerStatus
from wavefunction.spec_v2 import AgentSpecV2, ToolBinding


class FakeDesktop(DesktopManager):
    def __init__(self):
        super().__init__(docker_path="docker")
        self.actions = []
        self.private = False
        self._status = DockerStatus(docker_available=True, image_ready=True)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle), base_url="http://desktop")

    def handle(self, request):
        if request.url.path == "/screenshot":
            return httpx.Response(200, content=b"private-frame" if self.private else b"public-frame")
        if request.method == "POST":
            self.actions.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True})

    async def session(self, conversation_id, copy=None):
        if conversation_id not in self._sessions:
            self._sessions[conversation_id] = DesktopSession(
                conversation_id, "fake", 0, "fake", self.client, copy=copy, home=copy and f"home-{copy}"
            )
        return self._sessions[conversation_id]

    async def refresh_status(self):
        return self._status

    async def _drop(self, conversation_id):
        self._sessions.pop(conversation_id, None)


async def until(stack, run_id, status):
    async with asyncio.timeout(8):
        while True:
            row = stack.repository.get_run(run_id)
            if row["status"] == status:
                # A finished status precedes the task's cleanup; the chat's next task waits for both.
                return await stack.runtime.wait(run_id) if status == "completed" else row
            assert row["status"] not in {"failed", "cancelled"}, row
            await asyncio.sleep(0.01)


@pytest.fixture
async def desktop(stack, monkeypatch):
    from wavefunction import toolkit

    manager = FakeDesktop()
    monkeypatch.setattr(stack.runtime, "computer", manager)
    monkeypatch.setattr(stack.services, "computer", manager)
    monkeypatch.setattr(toolkit, "_acted", partial(toolkit._acted, settle=0))
    try:
        yield manager
    finally:
        tasks = list(stack.runtime.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await stack.runtime.aclose()
        await manager.client.aclose()


async def test_agent_private_login_resumes_same_run_without_secret_in_history(stack, desktop, monkeypatch):
    from wavefunction.main import app

    seen = []

    class RecordingModel(ScriptedModel):
        async def _agenerate(self, messages, **kwargs):
            seen.append(str(messages))
            return ChatResult(generations=[ChatGeneration(message=self._next())])

    model = RecordingModel(
        script=[
            tool_call_message("computer_request_takeover", {}, "login"),
            AIMessage(content="Signed in and continuing."),
        ]
    )
    monkeypatch.setattr(stack.models, "chat_model", lambda *a, **k: model)
    agent = stack.agent
    spec = AgentSpecV2(
        name="Browser",
        description="Browser test",
        system_prompt="Use the browser",
        tools=[ToolBinding(name="computer_screenshot"), ToolBinding(name="computer_request_takeover")],
    )
    run = await stack.runtime.start_run(agent["id"], "Sign in privately", spec=spec)
    await until(stack, run["id"], "waiting_computer")
    base = f"/api/computer/control/run/{run['id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        state = (await client.get(base)).json()
        assert state["state"] == "user"
        desktop.private = True
        secret = "synthetic-password-never-in-traces"
        response = await client.post(
            base + "/action",
            json={"generation": state["generation"], "action": "type", "payload": {"text": secret}},
        )
        assert response.status_code == 200
        frame = await client.get(base + f"/screen?generation={state['generation']}")
        assert frame.content == b"private-frame"
        assert frame.headers["cache-control"] == "no-store"
        assert secret not in str(stack.repository.list_run_events(run["id"]))
        assert not any(
            b"private-frame" in p.read_bytes()
            for p in stack.repository.package_path(agent["id"]).rglob("*.png")
        )
        desktop.private = False
        assert (
            await client.post(base + "/resume", json={"generation": state["generation"]})
        ).status_code == 200
        await until(stack, run["id"], "completed")
        assert (
            await client.post(
                base + "/action",
                json={"generation": state["generation"], "action": "type", "payload": {"text": "stale"}},
            )
        ).status_code == 409
    assert secret not in str(seen)
    # The hand-back's own result shows the screen; the next request carries it once.
    assert seen[-1].count("Screenshot from") == 1, seen[-1]
    active = stack.runtime.active.get(run["id"])
    assert active is None
    assert secret not in str(stack.repository.list_run_events(run["id"]))


async def test_desktop_lifetime_idle_reaping_cancel_and_finished_task_takeover(stack, desktop, monkeypatch):
    model = ScriptedModel(script=[tool_call_message("computer_request_takeover", {}, "login")])
    monkeypatch.setattr(stack.models, "chat_model", lambda *a, **k: model)
    agent = stack.agent
    spec = AgentSpecV2(
        name="Browser",
        description="Test",
        system_prompt="Browse",
        tools=[ToolBinding(name="computer_screenshot"), ToolBinding(name="computer_request_takeover")],
    )
    run = await stack.runtime.start_run(agent["id"], "Sign in", spec=spec)
    await until(stack, run["id"], "waiting_computer")
    key = stack.repository.get_run(run["id"])["conversation_id"] or run["id"]
    desktop._sessions[key].last_used = time.monotonic() - desktop._idle_ttl - 1
    await desktop.session("another-conversation")
    await desktop.reap_stale()
    assert key in desktop._sessions
    await stack.runtime.cancel(run["id"])
    assert stack.repository.get_run(run["id"])["status"] == "cancelled"
    assert desktop.control(key).state == "agent"
    assert key not in desktop._sessions
    assert "another-conversation" in desktop._sessions
    with pytest.raises(ValueError, match="not ready"):
        await stack.runtime.resume_computer(run["id"])

    # A finished task's desktop belongs to the chat and stays usable: it can be taken over,
    # viewed and handed back, and is refused only once it has closed.
    from wavefunction.main import app

    model = ScriptedModel(
        script=[tool_call_message("computer_screenshot", {}, "look"), AIMessage(content="Opened it.")]
    )
    monkeypatch.setattr(stack.models, "chat_model", lambda *a, **k: model)
    looked = await stack.runtime.start_run(agent["id"], "Open the site", spec=spec)
    await until(stack, looked["id"], "completed")
    key = stack.repository.get_run(looked["id"])["conversation_id"] or looked["id"]
    assert key in desktop._sessions
    base = f"/api/computer/control/run/{looked['id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        state = (await client.post(base + "/take")).json()
        assert state["state"] == "user" and state["available"]
        screen = await client.get(base + f"/screen?generation={state['generation']}")
        assert screen.status_code == 200 and screen.content == b"public-frame"
        returned = (await client.post(base + "/resume", json={"generation": state["generation"]})).json()
        assert returned["state"] == "agent"
        # A container that died leaves its entry behind. Handing a dead desktop back returns
        # control to the agent, and taking one over is refused, so no later task pauses for it.
        state = (await client.post(base + "/take")).json()
        monkeypatch.setattr(desktop, "_alive", lambda session: asyncio.sleep(0, False))
        dead = await client.post(base + "/resume", json={"generation": state["generation"]})
        assert (
            dead.status_code == 409 and desktop.control(key).state == "agent" and key not in desktop._sessions
        )
        await desktop.session(key)
        refused = await client.post(base + "/take")
        assert refused.status_code == 409 and desktop.control(key).state == "agent"
        await desktop._drop(key)
        closed = await client.post(base + "/take")
        assert closed.status_code == 409 and "no longer running" in closed.json()["detail"]

    # Taking over moved the chat's control on. A later task that does not use the desktop
    # neither reopens the closed one nor fails because it cannot start.
    from wavefunction.computer import DesktopUnavailable

    async def unavailable(*args, **kwargs):
        raise DesktopUnavailable("Docker is installed but its engine is not running.")

    monkeypatch.setattr(desktop, "session", unavailable)
    model = ScriptedModel(script=[AIMessage(content="Nothing on screen was needed.")])
    monkeypatch.setattr(stack.models, "chat_model", lambda *a, **k: model)
    later = await stack.runtime.start_run(agent["id"], "Summarize", spec=spec, conversation_id=key)
    await until(stack, later["id"], "completed")
    assert key not in desktop._sessions
    # With no current frame, an action based on an old screenshot is still refused.
    model = ScriptedModel(
        script=[
            tool_call_message("computer_click", {"x": 10, "y": 10}, "blind"),
            AIMessage(content="I will look first."),
        ]
    )
    monkeypatch.setattr(stack.models, "chat_model", lambda *a, **k: model)
    blind = await stack.runtime.start_run(
        agent["id"],
        "Click it",
        spec=AgentSpecV2(
            name="Browser",
            description="Test",
            system_prompt="Browse",
            tools=[ToolBinding(name="computer_screenshot"), ToolBinding(name="computer_click")],
        ),
        conversation_id=key,
    )
    await until(stack, blind["id"], "completed")
    assert "queued action was not executed" in str(stack.repository.list_run_events(blind["id"]))
    assert key not in desktop._sessions and not desktop.actions

    # Once shown the screen, the model is not sent it again: not after the screenshot it asked
    # for, nor at every later task (an image each, keeping the desktop from ever closing). An
    # action planned on a desktop that has since closed is refused, not run on a fresh one.
    from langchain_core.messages import HumanMessage

    monkeypatch.delattr(desktop, "session")
    clicking = AgentSpecV2(
        name="Browser",
        description="Test",
        system_prompt="Browse",
        tools=[ToolBinding(name="computer_screenshot"), ToolBinding(name="computer_click")],
    )
    requests = []

    async def task(*script):
        model = ScriptedModel(script=list(script), seen=requests)
        monkeypatch.setattr(stack.models, "chat_model", lambda *a, **k: model)
        run = await stack.runtime.start_run(agent["id"], "Go on", spec=clicking, conversation_id=key)
        await until(stack, run["id"], "completed")
        return run

    await task(tool_call_message("computer_screenshot", {}, "fresh"), AIMessage(content="Seen it."))
    await task(AIMessage(content="Nothing on screen was needed."))
    assert not [
        m
        for request in requests
        for m in request
        if isinstance(m, HumanMessage)
        and m.additional_kwargs.get("lc_source") == "computer_screenshot"
        and m.content[0]["text"].startswith("Screenshot from the tool call above.")
    ]
    for closed in ("died", "dropped"):
        if closed == "died":  # Still listed, as after Docker restarted, but no longer answering.
            monkeypatch.setattr(desktop, "_alive", lambda session: asyncio.sleep(0, False))
        else:
            await desktop._drop(key)
        stale = await task(
            tool_call_message("computer_click", {"x": 10, "y": 10}, "stale"),
            AIMessage(content="I will look."),
        )
        assert (
            "closed and would open fresh" in str(stack.repository.list_run_events(stale["id"]))
            and not desktop.actions
        )

    # After the user's turn, every later task is shown the screen as the user left it, not only the
    # one that resumed. With no desktop open, also after the app restarted, a click is refused.
    import base64

    from wavefunction.main import app

    monkeypatch.delattr(desktop, "_alive")
    reopened = await task(
        tool_call_message("computer_screenshot", {}, "reopen"), AIMessage(content="Open again.")
    )
    base = f"/api/computer/control/run/{reopened['id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        state = (await client.post(base + "/take")).json()
        desktop.private = True  # What the user leaves on screen.
        assert (
            await client.post(base + "/resume", json={"generation": state["generation"]})
        ).status_code == 200
    await task(AIMessage(content="Signed in."))
    await task(AIMessage(content="Still signed in."))
    assert b"private-frame" in [
        base64.b64decode(part["base64"])
        for m in requests[-1]
        if isinstance(m, HumanMessage)
        for part in m.content
        if isinstance(part, dict) and part.get("type") == "image"
    ]
    desktop._controls.clear()
    await desktop._drop(key)
    restarted = await task(
        tool_call_message("computer_click", {"x": 10, "y": 10}, "restarted"),
        AIMessage(content="I will look."),
    )
    assert (
        "closed and would open fresh" in str(stack.repository.list_run_events(restarted["id"]))
        and not desktop.actions
    )

    # A question answered while the user holds the desktop is not asked again.
    await task(tool_call_message("computer_screenshot", {}, "open"), AIMessage(content="Open."))
    model = ScriptedModel(
        script=[
            tool_call_message(
                "ask_user", {"questions": [{"id": "account", "title": "Which account?"}]}, "ask"
            ),
            AIMessage(content="Thanks."),
        ]
    )
    monkeypatch.setattr(stack.models, "chat_model", lambda *a, **k: model)
    asking = await stack.runtime.start_run(agent["id"], "Sign in", spec=clicking, conversation_id=key)
    await until(stack, asking["id"], "waiting_approval")
    base = f"/api/computer/control/run/{asking['id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        state = (await client.post(base + "/take")).json()
        asked = [
            e for e in stack.repository.list_run_events(asking["id"]) if e["event_type"] == "run_questions"
        ]
        await stack.runtime.answer_questions(
            asking["id"], {f"{asked[0]['payload']['groups'][0]['id']}:account": "work"}, asked[0]["sequence"]
        )
        await until(stack, asking["id"], "waiting_computer")
        assert (
            await client.post(base + "/resume", json={"generation": state["generation"]})
        ).status_code == 200
    await until(stack, asking["id"], "completed")
    assert (
        len([e for e in stack.repository.list_run_events(asking["id"]) if e["event_type"] == "run_questions"])
        == 1
    )


async def test_manual_takeover_drains_action_and_drops_queued_click(stack, desktop, monkeypatch):
    from wavefunction.main import app

    entered, finish = asyncio.Event(), asyncio.Event()
    captured = False

    async def delayed(request):
        nonlocal captured
        if request.url.path == "/screenshot" and not captured:
            captured = True
            entered.set()
            await finish.wait()
        return desktop.handle(request)

    await desktop.client.aclose()
    desktop.client = httpx.AsyncClient(transport=httpx.MockTransport(delayed), base_url="http://desktop")
    model = ScriptedModel(
        script=[
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "look", "name": "computer_screenshot", "args": {}},
                    {"id": "stale", "name": "computer_click", "args": {"x": 100, "y": 200}},
                ],
            ),
            AIMessage(content="I checked the updated screen."),
        ]
    )
    monkeypatch.setattr(stack.models, "chat_model", lambda *a, **k: model)
    agent = stack.agent
    spec = AgentSpecV2(
        name="Browser",
        description="Test",
        system_prompt="Browse",
        tools=[ToolBinding(name="computer_screenshot"), ToolBinding(name="computer_click")],
    )
    run = await stack.runtime.start_run(agent["id"], "Use the browser", spec=spec)
    base = f"/api/computer/control/run/{run['id']}"
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            state = (await client.post(base + "/take")).json()
            assert state["state"] == "pausing"
            assert (await client.get(base + f"/screen?generation={state['generation']}")).status_code == 409
            finish.set()
            await until(stack, run["id"], "waiting_computer")
            assert not desktop.actions
            private = await client.post(
                base + "/action",
                json={"generation": state["generation"] - 1, "action": "type", "payload": {"text": "stale"}},
            )
            assert private.status_code == 409
            assert (
                await client.post(base + "/resume", json={"generation": state["generation"]})
            ).status_code == 200
            await until(stack, run["id"], "completed")
            assert not desktop.actions
        results = str(stack.repository.list_run_events(run["id"]))
        assert "queued action was not executed" in results
    finally:
        finish.set()
        if stack.repository.get_run(run["id"])["status"] != "completed":
            await stack.runtime.cancel(run["id"])


async def test_private_routes_require_auth_and_do_not_echo_invalid_secrets(stack, desktop, monkeypatch):
    from wavefunction.main import app, services

    monkeypatch.setattr(services.settings, "development_auth_bypass", False)
    monkeypatch.setattr(services.settings, "desktop_token", "test-owner")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        base = "/api/computer/control/run/unknown"
        assert (await client.get(base)).status_code == 401
        secret = "invalid-secret-must-not-be-echoed"
        response = await client.post(
            base + "/action", headers={"Authorization": "Bearer test-owner"}, json={"action": secret}
        )
        assert response.status_code == 400
        assert secret not in response.text
    # The live stream needs the token too (as a subprotocol), and control of that desktop.
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    # Without `with`, so the app's startup (which looks for Docker) does not run.
    sync = TestClient(app)
    for protocols in (["binary"], ["binary", "bearer.test-owner"]):
        with (
            pytest.raises(WebSocketDisconnect) as closed,
            sync.websocket_connect(base + "/stream?generation=1", subprotocols=protocols) as socket,
        ):
            socket.receive_bytes()
        assert closed.value.code == 1008
