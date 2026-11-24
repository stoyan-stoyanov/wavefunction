"""What differs between the computers the engine runs on: macOS, and Linux built from source.

Feature code asks this module, never the platform itself, and `scripts/check-platform.mjs` fails a
change that does. So something each computer does its own way is one function here, and a feature
only the macOS app has is one entry in `_MACOS_ONLY`, which Linux support then removes. The
sandbox's own enforcement is the exception: `sandbox.py` and `sandbox_linux.py` check for
themselves.

Standard library only, and cheap to import: `fileops` imports it inside the sandbox on every file
tool call, so asyncio is imported only where a coroutine needs it.
"""

from __future__ import annotations

import fcntl
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

Feature = Literal["integration_install"]
# What only the macOS app has for now, and what other builds say instead.
_MACOS_ONLY: dict[Feature, str] = {
    "integration_install": "In-app installation currently supports Intel and Apple silicon Macs."
}


def unavailable(feature: Feature) -> str | None:
    """Why this computer lacks `feature`, or None where it has it."""
    return None if sys.platform == "darwin" else _MACOS_ONLY[feature]


def machine() -> str:
    """The processor as the system names it: arm64 or x86_64 on a Mac."""
    return platform.machine()


def description() -> str:
    """The system and processor, as the model is told them: "Darwin arm64"."""
    return f"{platform.system()} {platform.machine()}"


# Words that name this computer and its parts in what the engine says, as `words` in the
# interface's lib/platform.ts. The macOS ones are what the app has always said.
def computer() -> str:
    """The user's computer: "Mac", or "computer" on Linux."""
    return "Mac" if sys.platform == "darwin" else "computer"


def this_computer() -> str:
    """The words "this Mac", or "this computer" on Linux."""
    return f"this {computer()}"


def file_manager() -> str:
    """Where files are browsed: "Finder", or "the file manager" on Linux."""
    return "Finder" if sys.platform == "darwin" else "the file manager"


def system() -> str:
    """The system's name: "macOS" or "Linux"."""
    return "macOS" if sys.platform == "darwin" else "Linux"


def opener() -> str:
    """The command that opens a file, page or app as a double-click would: `open`, or `xdg-open`."""
    return "open" if sys.platform == "darwin" else "xdg-open"


def worded(tool: BaseTool) -> BaseTool:
    """`tool`, with `{this_computer}`, `{computer}` and `{system}` in its description, which a
    docstring can't compute, filled in."""
    tool.description = tool.description.format(
        this_computer=this_computer(), computer=computer(), system=system()
    )
    return tool


def docker_manager() -> Literal["app", "systemd"]:
    """What starts and restarts Docker for computer use: on macOS the Docker Desktop app, opened
    as the Dock opens it (`docker_macos`), and on Linux systemd, which runs Docker Desktop and
    Docker Engine as services (`docker_linux`)."""
    return "app" if sys.platform == "darwin" else "systemd"


def has_proc() -> bool:
    """Whether what a process has open is read from /proc, as on Linux. macOS has no /proc, and
    asks lsof."""
    return sys.platform != "darwin"


def sandboxed_gh() -> bool:
    """Whether gh reaches GitHub from inside the sandbox, through the app's Unix socket. Seatbelt
    lets it connect; the Linux sandbox refuses every Unix socket, so there gh runs outside it."""
    return sys.platform == "darwin"


def data_dir() -> Path:
    """The engine's data folder when nothing names one. The desktop app always names one."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Harness"
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Harness"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "harness"


def copy_with_metadata(source: str, target: str) -> None:
    """Copy a file with its permissions and times and, on macOS, its ACLs and extended attributes,
    which `cp -p` keeps there."""
    if sys.platform == "darwin":
        subprocess.run(["/bin/cp", "-p", source, target], check=True)
    else:
        shutil.copy2(source, target)


def path_of(descriptor: int) -> Path:
    """Where an open file or folder really is, whatever links or letter case led to it."""
    if sys.platform == "darwin":
        found = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
        return Path(os.fsdecode(found.split(b"\0", 1)[0]))
    return Path(os.readlink(f"/proc/self/fd/{descriptor}"))


async def show(target: Path, *, reveal: bool) -> None:
    """Open a file in its app or, with `reveal`, show it in the file manager: selected in Finder,
    and on Linux by opening its folder."""
    import asyncio

    if sys.platform == "linux":
        await _xdg_open(target.parent if reveal else target)
        return
    # By absolute path: an `open` placed earlier on PATH must never be what runs.
    command = ["/usr/bin/open", "-R", str(target)] if reveal else ["/usr/bin/open", "--", str(target)]
    process = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, error = await process.communicate()
    if process.returncode:
        raise ValueError(error.decode(errors="replace").strip() or "The file could not be opened.")


async def _xdg_open(target: Path) -> None:
    """Hand a file or folder to the desktop's handler. `xdg-open` returns once a handler has it,
    or, where none takes it over, runs as long as the app it started, so it is not waited for
    beyond a few seconds."""
    import asyncio
    import contextlib

    process = await asyncio.create_subprocess_exec(
        "/usr/bin/xdg-open",
        str(target),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(5):
            await process.communicate()
            if process.returncode:
                raise ValueError("The file could not be opened. Is xdg-open installed?")
