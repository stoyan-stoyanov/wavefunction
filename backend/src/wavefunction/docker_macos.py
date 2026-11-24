"""Docker Desktop on macOS, started and restarted as the app it is (`platforms.docker_manager`).

Moved out of computer.py as it was. `docker_linux` does the same with systemd.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from .computer import CommandResult

# What the app says when Docker's engine is down, when it cannot start this Docker itself, and
# when starting it failed without saying why.
NOT_RUNNING = (
    "Docker is installed but its engine is not running. Start Docker Desktop (or OrbStack) and try again."
)
UNMANAGED = "Open Docker Desktop or OrbStack to start or restart its engine, then check again."
FAILED = "Open Docker Desktop and try again."


def problem(info: CommandResult) -> str:  # noqa: ARG001
    """What the app says when Docker's engine does not answer `docker info`."""
    return NOT_RUNNING


def docker_desktop_app(docker: str) -> Path | None:
    """The Docker Desktop app a docker CLI comes from, such as /Applications/Docker.app."""
    return next(
        (parent for parent in Path(os.path.realpath(docker)).parents if parent.suffix == ".app"), None
    )


async def manageable(run: Callable[..., Awaitable[CommandResult]], docker: str) -> bool:
    """Whether this docker comes with Docker Desktop, which the app can start and restart."""
    desktop = await run(docker, "desktop", "version", timeout=10)
    return desktop.exit_code == 0 and bool(desktop.stdout.strip())


async def manage(
    run: Callable[..., Awaitable[CommandResult]],
    docker: str,
    action: str,
    engine_up: Callable[[str], Awaitable[bool]],
) -> CommandResult:
    """Start or restart Docker Desktop and wait for its engine, which `engine_up` asks."""
    app = docker_desktop_app(docker)
    result = CommandResult(0, "", "")
    if action == "restart":
        result = await run(docker, "desktop", "stop", "--timeout", "60", timeout=65)
    # A hung Docker Desktop keeps its window with no engine behind it, and its own stop
    # cannot end it. Only with its engine down does no container run that quitting it
    # could interrupt, so only then are its processes quit by force before it is opened
    # again; a stop that failed while the engine answers is reported instead.
    if app and not await engine_up(docker):
        await _force_quit(run, app)
        result = CommandResult(0, "", "")
    if result.exit_code == 0:
        result = await _open_desktop(run, docker, app, engine_up)
    return result


async def _open_desktop(
    run: Callable[..., Awaitable[CommandResult]],
    docker: str,
    app: Path | None,
    engine_up: Callable[[str], Awaitable[bool]],
) -> CommandResult:
    """Open Docker Desktop through Launch Services, as the Dock does, and wait for its engine.
    Started by `docker desktop start`, or relaunched by its restart, it runs as a child of
    this app, and macOS then keeps it out of its own group container: it cannot read its
    settings and stops with "settings-store.json: operation not permitted"."""
    target = ["-a", str(app)] if app else ["-b", "com.docker.docker"]
    opened = await run("/usr/bin/open", *target, timeout=30)
    if opened.exit_code != 0:
        return opened
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 120
    while not await engine_up(docker):
        if loop.time() > deadline:
            return CommandResult(
                1, "", "Docker Desktop opened, but its engine did not start within two minutes."
            )
        await asyncio.sleep(2)
    return CommandResult(0, "", "")


async def _force_quit(run: Callable[..., Awaitable[CommandResult]], app: Path) -> None:
    """Stop this user's processes that run from inside the Docker Desktop app, asking first
    and killing what is left. The network helper Docker installs elsewhere is not touched."""
    prefix = f"{app}/Contents/MacOS/"
    pattern = "^" + "".join(f"\\{char}" if char in ".^$*+?()[]{}|\\" else char for char in prefix)
    user = str(os.getuid())
    await run("pkill", "-TERM", "-U", user, "-f", pattern, timeout=10)
    for _ in range(20):
        if (await run("pgrep", "-U", user, "-f", pattern, timeout=10)).exit_code != 0:
            return
        await asyncio.sleep(0.5)
    await run("pkill", "-KILL", "-U", user, "-f", pattern, timeout=10)
