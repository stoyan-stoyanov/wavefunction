"""Plain helpers for the coding tests: a chat made through the API and a synthetic repository.

The `coding` fixture that serves the API is in conftest.py."""

import asyncio
import subprocess


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
