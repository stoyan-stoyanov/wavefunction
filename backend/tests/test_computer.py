"""Computer use without Docker: every piece is tested against fakes.

CI has no Docker daemon, so nothing here starts a container. The action server is imported
straight from its file and exercised over real HTTP with `subprocess.run` faked; the manager
gets an injected command runner. What cannot be tested this way — an actual desktop — is what
the manual end-to-end pass is for.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from wavefunction import computer as computer_module
from wavefunction.computer import (
    COMPUTER_TOOL_NAMES,
    CommandResult,
    DesktopManager,
    DesktopSession,
    DesktopUnavailable,
    find_sentinel,
    image_tag,
)
from wavefunction.toolkit import ToolContext, build_tools

SERVER_PATH = Path(computer_module.__file__).parent / "sandbox_image" / "server.py"


# ---- the action server ----------------------------------------------------------------------


@pytest.fixture
def server(monkeypatch):
    """The real server.py, loaded from its file, listening on a random port, xdotool faked."""
    from wavefunction import toolkit

    # The fake desktop has nothing to redraw. Override the helper's bound default,
    # rather than the constant it captured when the function was defined.
    monkeypatch.setattr(toolkit, "_acted", partial(toolkit._acted, settle=0))
    monkeypatch.setenv("ACTION_TOKEN", "secret-token")
    spec = importlib.util.spec_from_file_location("desktop_server_under_test", SERVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    calls: list[list[str]] = []
    inputs = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        inputs.append(_kwargs.get("input"))
        if args[0] == "scrot":
            Path(args[-1]).write_bytes(b"\x89PNG fake image bytes")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield SimpleNamespace(base=base, calls=calls, inputs=inputs, module=module)
    finally:
        httpd.shutdown()


def test_server_refuses_a_missing_or_wrong_token(server) -> None:
    assert httpx.get(f"{server.base}/health").status_code == 401
    wrong = httpx.get(f"{server.base}/health", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert not server.calls


AUTH = {"Authorization": "Bearer secret-token"}


def test_server_rejects_keys_outside_the_whitelist(server) -> None:
    refused = httpx.post(f"{server.base}/key", headers=AUTH, json={"keys": "Return; rm -rf /"})
    assert refused.status_code == 400
    assert "unsupported key" in refused.json()["error"]
    assert not any(call[0:2] == ["xdotool", "key"] for call in server.calls)

    accepted = httpx.post(f"{server.base}/key", headers=AUTH, json={"keys": "ctrl+a Return"})
    assert accepted.status_code == 200


def test_server_types_via_stdin_without_exposing_text_in_argv(server) -> None:
    httpx.post(f"{server.base}/type", headers=AUTH, json={"text": "--delay 0 $(reboot)"})
    typed = next(call for call in server.calls if call[1] == "type")
    assert typed[-2:] == ["--file", "-"]
    assert b"--delay 0 $(reboot)" in server.inputs
    assert "--delay 0 $(reboot)" not in str(server.calls)


# ---- the manager ----------------------------------------------------------------------------


def fake_runner(responses: dict[str, CommandResult]):
    """Answer docker commands from a table keyed by subcommand ('info', 'image', ...)."""
    seen: list[tuple[str, ...]] = []

    async def run(*args: str, timeout: float = 0) -> CommandResult:  # noqa: ARG001
        seen.append(args)
        return responses.get(args[1], CommandResult(0, "", ""))

    run.seen = seen  # type: ignore[attr-defined]
    return run


async def test_docker_recovery_waits_for_readiness_and_prevents_overlapping_operations(monkeypatch, tmp_path):
    action = "restart"
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def run(*args, **kwargs):
        calls.append(args[1:])
        if args[1:3] == ("desktop", "stop"):
            entered.set()
            await finish.wait()
        if args[1:3] == ("image", "ls"):
            return CommandResult(0, image_tag().rpartition(":")[2], "")
        return CommandResult(0, "27.4.0", "")

    manager = DesktopManager(docker_path="docker", run_command=run)
    await manager.refresh_status()
    task = asyncio.create_task(manager.manage_docker(action))
    try:
        await entered.wait()
        assert (await manager.refresh_status()).docker_action == action
        with pytest.raises(DesktopUnavailable, match="current Docker operation"):
            await manager.manage_docker(action)
        assert manager.start_build() is False
    finally:
        finish.set()
        status = await task
    assert status.docker_action is None
    assert status.docker_problem is None
    assert status.image_ready
    assert ("desktop", "stop", "--timeout", "60") in calls
    assert ("-b", "com.docker.docker") in calls  # Opened through Launch Services.

    # A status check that was already running when a build finished must not bring back the
    # "building" it saw when it started.
    listing, release = asyncio.Event(), asyncio.Event()

    async def slow_list(*args, **kwargs):
        if args[1:3] == ("image", "ls"):
            listing.set()
            await release.wait()
        return await run(*args, **kwargs)

    manager._run = slow_list
    manager._status.building = True
    probe = asyncio.create_task(manager.refresh_status())
    await listing.wait()
    manager._status.building = False  # The build finishes meanwhile.
    release.set()
    assert not (await probe).building and not manager.status_cached().building

    # A finished build reads as building until the status has seen its image, never as
    # "not built", which would stop the Settings page polling.
    import contextlib

    from wavefunction import computer

    @contextlib.contextmanager
    def staging(prefix=""):
        yield str(tmp_path)

    monkeypatch.setattr(computer.tempfile, "TemporaryDirectory", staging)
    monkeypatch.setattr(computer, "image_context_dir", lambda: tmp_path)
    monkeypatch.setattr(manager, "docker", lambda: "docker")
    release = asyncio.Event()
    manager._run = slow_list
    listing.clear()
    manager._stream_build = lambda docker, context: asyncio.sleep(0)
    assert manager.start_build()
    await listing.wait()
    assert manager.status_cached().building
    release.set()
    while manager.status_cached().building:
        await asyncio.sleep(0.01)
    assert manager.status_cached().image_ready


async def test_failed_docker_recovery_reports_the_error_or_reopens_a_hung_docker_desktop(tmp_path):
    # A hung Docker Desktop keeps its window but has no engine, and its own stop cannot end it.
    root = tmp_path.resolve()
    cli = root / "Docker.app/Contents/Resources/bin/docker"
    cli.parent.mkdir(parents=True)
    cli.touch()
    calls, state = [], {"quit": False, "elsewhere": False}

    async def run(*args, **kwargs):
        calls.append(args)
        if args[0] == "pkill":
            state["quit"] = True
            return CommandResult(0, "", "")
        if args[0] == "pgrep":
            return CommandResult(1, "", "")  # None left after the polite quit.
        if args[0] == "/usr/bin/open":
            return CommandResult(0, "", "")
        if args[1:3] == ("desktop", "stop"):
            return CommandResult(1, "", "Docker Desktop did not respond")
        if args[1:3] == ("--context", "desktop-linux"):
            return CommandResult(0, "27.4.0" if state["quit"] or state["elsewhere"] else "", "")
        if args[1] == "info":
            return CommandResult(0, "27.4.0", "") if state["quit"] else CommandResult(0, "", "Cannot connect")
        return CommandResult(0, "Desktop CLI", "")

    # A docker that is not inside Docker Desktop: the failure is shown, and nothing is quit.
    manager = DesktopManager(docker_path="docker", run_command=run)
    await manager.refresh_status()
    with pytest.raises(DesktopUnavailable, match="Docker Desktop did not respond"):
        await manager.manage_docker("restart")
    assert manager.status_cached().docker_action is None
    assert manager.status_cached().docker_problem
    assert not any(call[0] in {"pkill", "pgrep", "/usr/bin/open"} for call in calls)

    # Docker Desktop's own docker: start, or the restart the user confirmed, quits the hung
    # app's processes and opens it again through Launch Services, never as this app's child,
    # which macOS keeps out of Docker's own settings.
    quit = (
        "pkill",
        "-TERM",
        "-U",
        str(os.getuid()),
        "-f",
        "^" + str(root).replace(".", "\\.") + "/Docker\\.app/Contents/MacOS/",
    )
    reopen = ("/usr/bin/open", "-a", str(root / "Docker.app"))
    manager = DesktopManager(docker_path=str(cli), run_command=run)
    for action in ("start", "restart"):
        state["quit"] = False
        calls.clear()
        await manager.refresh_status()
        status = await manager.manage_docker(action)
        assert status.docker_problem is None and status.docker_action is None
        assert calls.index(quit) < calls.index(reopen)
        assert not any(
            call[:2] == ("pkill", "-KILL") or call[1:3] in {("desktop", "start"), ("desktop", "restart")}
            for call in calls
        )

    # The active context is another daemon that is down, while Docker Desktop runs fine: it
    # and its containers are left alone, even when its own stop fails.
    state.update(quit=False, elsewhere=True)
    calls.clear()
    await manager.refresh_status()
    with pytest.raises(DesktopUnavailable, match="engine is not running"):
        await manager.manage_docker("start")
    with pytest.raises(DesktopUnavailable, match="Docker Desktop did not respond"):
        await manager.manage_docker("restart")
    assert not any(call[0] == "pkill" for call in calls)


class FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


async def test_shutdown_drops_own_sessions_and_preserves_unknown_owners() -> None:
    runner = fake_runner({"ps": CommandResult(0, "orphan1\n", "")})
    manager = DesktopManager(docker_path="/usr/local/bin/docker", run_command=runner)
    manager._sessions = {"one": DesktopSession("one", "ccc", 3, "t", FakeClient())}  # type: ignore[arg-type]

    await manager.shutdown()

    assert not manager._sessions
    flattened = [call for call in runner.seen if "rm" in call]
    assert any("ccc" in call for call in flattened)
    assert not any("orphan1" in call for call in flattened)


# ---- status copy, prompts, registry ---------------------------------------------------------


class FakeDesktop:
    """A manager whose one session talks to the fake action server over real HTTP."""

    def __init__(self, base: str):
        self.client = httpx.AsyncClient(base_url=base, headers={"Authorization": "Bearer secret-token"})

    async def session(self, conversation_id: str, copy: str | None = None):
        return SimpleNamespace(conversation_id=conversation_id, client=self.client)


async def test_every_action_returns_the_screen_afterwards(server, tmp_path) -> None:
    """A click used to answer "Clicked at (10, 20)." and leave the looking to a separate
    screenshot call — one model turn to act, another to see, and a timeline whose last
    picture was as stale as the model's last decision to look. The action now carries the
    screen as it looks afterwards."""
    tools = desktop_tools(tmp_path, FakeDesktop(server.base))

    said = await tools["computer_click"].coroutine(x=10, y=20)
    assert said.startswith("Clicked at (10, 20) with the left button.")
    relative = find_sentinel(said)
    assert relative is not None
    stored = tmp_path / "agent-1" / "outputs" / "screenshots" / relative
    assert stored.read_bytes().startswith(b"\x89PNG")
    # The click landed before the picture was taken.
    assert ["xdotool", "click", "1"] in server.calls
    assert server.calls.index(["xdotool", "click", "1"]) < len(server.calls) - 1

    for name, kwargs in (
        ("computer_type", {"text": "hello"}),
        ("computer_key", {"keys": "Return"}),
        ("computer_scroll", {}),
        ("computer_move", {"x": 1, "y": 2}),
        ("computer_drag", {"x1": 1, "y1": 2, "x2": 3, "y2": 4}),
        ("computer_double_click", {"x": 5, "y": 6}),
    ):
        assert find_sentinel(await tools[name].coroutine(**kwargs)), name


# ---- screenshots ---------------------------------------------------------------------------


class FakeFiles:
    def __init__(self, root: Path):
        self.root = root

    def project_dir(self, agent_id: str) -> Path:
        return self.root / agent_id


def desktop_tools(tmp_path: Path, desktop: Any) -> dict[str, Any]:
    """The desktop tools as a task gets them. They never touch the chats, the repository or keys."""
    context = ToolContext("agent-1", None, FakeFiles(tmp_path), None, computer=desktop, coding_workspace=None)  # type: ignore[arg-type]
    return {tool.name: tool for tool in build_tools(list(COMPUTER_TOOL_NAMES), context)}


async def test_a_busy_screen_is_stored_at_the_size_clicks_are_read_in(tmp_path) -> None:
    """Fine detail (a map, a photo grid) makes a PNG over the size limit. It used to be halved to
    640x400 while the tool still said 1280x800, so clicks read off it landed at half the
    intended coordinates."""
    import io
    import random

    from PIL import Image

    noise = Image.frombytes("RGB", (1280, 800), random.Random(7).randbytes(1280 * 800 * 3))
    captured = io.BytesIO()
    noise.save(captured, format="PNG")
    assert len(captured.getvalue()) > computer_module.MAX_SCREENSHOT_BYTES
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=captured.getvalue())),
        base_url="http://desktop",
    )

    class Desktop:
        async def session(self, conversation_id: str, copy: str | None = None):
            return SimpleNamespace(conversation_id=conversation_id, client=client)

    tools = desktop_tools(tmp_path, Desktop())
    async with client:
        said = await tools["computer_screenshot"].coroutine()

    assert said.startswith("Screenshot captured (1280x800).")
    stored = (tmp_path / "agent-1" / "outputs" / "screenshots" / find_sentinel(said)).read_bytes()
    assert len(stored) <= computer_module.MAX_SCREENSHOT_BYTES
    assert Image.open(io.BytesIO(stored)).size == (1280, 800)


# ---- the API --------------------------------------------------------------------------------


async def test_a_restarted_app_removes_only_its_own_orphaned_desktops(monkeypatch, tmp_path):
    # A crashed process of this installation left "dead" running. The sweep of the next one must
    # remove it, and only it: never the installed app's beside a checkout, one whose process still
    # lives, a desktop of unknown owner, or anything else — such as an agent's kept home.
    import json

    mine, installed = (
        computer_module.installation_id(tmp_path / "data"),
        computer_module.installation_id(tmp_path / "app"),
    )
    owners = {
        "live": {"wavefunction.installation": mine, "wavefunction.owner-pid": "123"},
        "dead": {"wavefunction.installation": mine, "wavefunction.owner-pid": "456"},
        "installed-app": {"wavefunction.installation": installed, "wavefunction.owner-pid": "456"},
        "legacy": {"wavefunction.owner-host": "0123456789ab", "wavefunction.owner-pid": "456"},
    }
    removed, asked = [], []

    async def run(*args, **kwargs):
        asked.append(args[1:3])
        if args[1] == "ps":
            return CommandResult(0, "\n".join(owners), "")
        if args[1] == "inspect":
            return CommandResult(0, json.dumps([{"Config": {"Labels": owners[args[2]]}}]), "")
        if args[1] == "rm":
            removed.extend(args[3:])
        return CommandResult(0, "", "")

    def kill(pid, signal):
        assert signal == 0
        if pid == 456:
            raise ProcessLookupError()

    monkeypatch.setattr(computer_module.os, "kill", kill)
    await DesktopManager(docker_path="docker", run_command=run, data_dir=tmp_path / "data").sweep_orphans()
    assert removed == ["dead"]
    assert not any(command[0] == "volume" for command in asked)
