"""Docker on Linux, started and restarted through systemd (`platforms.docker_manager`).

Docker Desktop for Linux is the user's `docker-desktop` service. Docker Engine is the user's own
`docker` service when it runs rootless, and otherwise the system's, which pkexec asks the user to
authorize. `docker_macos` does the same on macOS, where Docker Desktop is an app.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable

from .computer import CommandResult

# What the app says when Docker's engine is down, when it cannot start this Docker itself, and
# when starting it failed without saying why.
NOT_RUNNING = (
    "Docker is installed but its engine is not running. Start Docker Desktop or the Docker "
    "service and try again."
)
UNMANAGED = (
    "Start Docker Desktop or the Docker service yourself, then check again: the app starts and "
    "restarts only a Docker that systemd runs."
)
FAILED = "Start Docker and try again."
# Docker Engine runs as root, and its socket is for root and the docker group only: installing it
# doesn't add the user to that group.
NOT_ALLOWED = (
    "Docker is running, but this user can't use it. Add yourself to the docker group "
    "(`sudo usermod -aG docker $USER`), log out and back in, then check again."
)

# By absolute path: an app opened from the desktop has little PATH, and the systemctl that pkexec
# runs as root must never be one placed earlier on it. None where the system has no systemd.
SYSTEMCTL = next((path for path in ("/usr/bin/systemctl", "/bin/systemctl") if os.path.exists(path)), None)
PKEXEC = "/usr/bin/pkexec" if os.path.exists("/usr/bin/pkexec") else None
# The time pkexec gives the user to read its password prompt and answer it, and then the time
# Docker's engine has to answer once systemd started it.
CONTROL_TIMEOUT = 120
READY_TIMEOUT = 120


async def _service(run: Callable[..., Awaitable[CommandResult]]) -> tuple[list[str], str] | None:
    """The Docker service systemd knows here, as the command that controls it and its unit: Docker
    Desktop's first, then a rootless Docker Engine's, then the system's. None for neither."""
    if SYSTEMCTL is None:
        return None
    user = [SYSTEMCTL, "--user"]
    services = [(user, user, "docker-desktop.service"), (user, user, "docker.service")]
    if PKEXEC is not None:
        services.append(([SYSTEMCTL], [PKEXEC, SYSTEMCTL], "docker.service"))
    for query, control, unit in services:
        found = await run(*query, "show", "--property", "LoadState", "--value", unit, timeout=10)
        if found.exit_code == 0 and found.stdout.strip() == "loaded":
            return control, unit
    return None


def problem(info: CommandResult) -> str:
    """What the app says when Docker's engine does not answer `docker info`."""
    return NOT_ALLOWED if "permission denied" in info.stderr.lower() else NOT_RUNNING


async def manageable(run: Callable[..., Awaitable[CommandResult]], docker: str) -> bool:  # noqa: ARG001
    """Whether systemd runs a Docker here that the app can start and restart."""
    return await _service(run) is not None


async def manage(
    run: Callable[..., Awaitable[CommandResult]],
    docker: str,
    action: str,
    engine_up: Callable[[str], Awaitable[bool]],
) -> CommandResult:
    """Start or restart the Docker service, and wait for its engine: Docker Desktop's, which
    `engine_up` asks through its own context, or Docker Engine's, through the context plain
    commands use. A hung engine needs nothing more: systemd stops what does not stop itself."""
    service = await _service(run)
    if service is None:
        return CommandResult(1, "", UNMANAGED)
    control, unit = service
    done = await run(*control, action, unit, timeout=CONTROL_TIMEOUT)
    if done.exit_code != 0:
        return done

    async def engine_answers(docker: str) -> bool:
        info = await run(docker, "info", "--format", "{{.ServerVersion}}", timeout=10)
        return info.exit_code == 0 and bool(info.stdout.strip())

    ready = engine_up if unit == "docker-desktop.service" else engine_answers
    loop = asyncio.get_running_loop()
    deadline = loop.time() + READY_TIMEOUT
    while not await ready(docker):
        if loop.time() > deadline:
            return CommandResult(1, "", "Docker started, but its engine did not answer within two minutes.")
        await asyncio.sleep(2)
    return CommandResult(0, "", "")
