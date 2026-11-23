"""Plain helpers for the coding tests: a chat made through the API and a synthetic repository.

The `coding` fixture that serves the API is in conftest.py."""

import asyncio
import subprocess
import sys
from pathlib import Path

# What a sandboxed command is told when the sandbox refuses it. On Linux, a path outside what the
# command may read is not there at all, and one it may only read is a read-only file system.
REFUSALS = ("Operation not permitted", "Permission denied") + (
    ("No such file or directory", "Read-only file system") if sys.platform == "linux" else ()
)


def refused(text: str) -> bool:
    return any(words in text for words in REFUSALS)


def host_pid(pid: int, ancestor: int) -> int:
    """The host's number for a process a sandboxed shell reported as `pid` (`$!`). On Linux each
    command has a PID namespace of its own, whose numbers mean other processes outside it."""
    if sys.platform != "linux":
        return pid
    parents, inner = {}, {}
    for status in Path("/proc").glob("[0-9]*/status"):
        try:
            fields = dict(line.split(":\t", 1) for line in status.read_text().splitlines() if ":\t" in line)
        except OSError:
            continue
        number = int(status.parent.name)
        parents[number], inner[number] = int(fields["PPid"]), int(fields["NSpid"].split()[-1])
    for number, inside in inner.items():
        up = number
        while inside == pid and up in parents and up > 1:
            if (up := parents[up]) == ancestor:
                return number
    raise LookupError(f"No process {pid} under {ancestor}")


async def create(client, **data):
    response = await client.post("/api/coding/threads", json=data)
    assert response.status_code == 201, response.text
    return response.json()


async def reached(event, runtime, run_id):
    """Until a run gets to `event`, or ends without it, however long this machine takes.

    Getting there can take real worktrees, checkpoints and sandboxed commands, whose time doubles
    from one CI run of the same code to the next, so no fixed limit fits. An assertion that fails
    in a scripted model ends its run: this reports that run, where a wait for the event would time out.
    """
    arrived = asyncio.ensure_future(event.wait())
    ended = asyncio.ensure_future(runtime.wait(run_id))
    await asyncio.wait({arrived, ended}, return_when=asyncio.FIRST_COMPLETED)
    arrived.cancel()
    ended.cancel()
    assert event.is_set(), runtime.repository.get_run(run_id)


def git(folder, *args):
    return (
        subprocess.check_output(["git", "-C", str(folder), *args], stderr=subprocess.STDOUT).decode().strip()
    )


def repository(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    git(folder, "init", "-b", "main")
    git(folder, "config", "user.name", "Synthetic Tester")
    git(folder, "config", "user.email", "synthetic@example.invalid")
    (folder / "a.txt").write_text("before\n")
    (folder / "other.txt").write_text("other before\n")
    git(folder, "add", ".")
    git(folder, "commit", "-m", "Initial synthetic commit")
    return folder
