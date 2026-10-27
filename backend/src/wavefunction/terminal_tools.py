"""Agent controls for terminals owned by this coding conversation."""

import json
from contextlib import suppress
from pathlib import Path

from langchain_core.tools import tool

from .irreversible import command_danger


def workspace_for(context):
    ws = context.coding_workspace
    if not context.run_id:
        raise ValueError("Managed terminals belong to a running task.")
    thread = ws.thread(context.desktop_copy)
    run = context.repository.get_run(context.run_id)
    if run["conversation_id"] != thread["conversation_id"]:
        raise PermissionError("The terminal belongs to another conversation")
    return ws, thread["id"]


async def reachable(context, terminal_id, *, writable=False):
    """A terminal of this chat or, for a main task, of one of its helpers."""
    ws, thread_id = workspace_for(context)
    session = ws.terminals.sessions.get(terminal_id)
    if (
        session is not None
        and not context.parent_run_id
        and session.thread_id in ws.helper_threads(context.run_id)
    ):
        thread_id = session.thread_id
    return ws, thread_id, await ws.terminals.get(thread_id, terminal_id, writable=writable)


def build_terminal_tool(context, name):
    @tool
    async def start_terminal(command: str, title: str = "Agent terminal") -> str:
        """Start an interactive command or a background server in a managed PTY.

        Returns a terminal id; use read_terminal for output and write_terminal for input
        (including '\\u0003' for Ctrl+C). The user can inspect it in Terminal. It stays
        running across replies until closed or the app quits. Use list_terminals to
        find existing sessions. Close temporary test servers when verification is
        complete; keep servers the user wants to use running.
        """
        if not command.strip() or len(command) > 20000 or not 1 <= len(title) <= 80:
            raise ValueError("Provide a command (up to 20000 characters) and a short title")
        ws, thread_id = workspace_for(context)
        ws.terminal_signature(thread_id)
        if context.local_read_only:
            raise PermissionError("Terminals are disabled for read-only helpers")
        cwd = (
            Path(context.working_directory).expanduser().resolve()
            if context.working_directory
            else ws.root(thread_id)
        )
        if cwd != ws.root(thread_id):
            raise ValueError("The repository changed since this task started. Start a new task.")
        runner = ws.runner(cwd)
        if context.folder_grants is not None:
            runner.grants = context.local_grants()
            runner.scratch = context.local_scratch
        before = await ws.reviews.snapshot(runner, cwd)
        # A terminal can't stop for the user's approval, so what can't be undone goes through run_command.
        if danger := command_danger(
            command, cwd=cwd, project=cwd, scratch=runner.scratch, outside=False, captured=before["files"]
        ):
            raise PermissionError(f"{danger} Run it with run_command so the user can approve it.")
        if runner.network:
            runner.env = await ws.services.github_proxy.environment()
        session = await ws.terminals.create(
            thread_id, runner, cwd, command=command, title=title, owner="agent", run_id=context.run_id
        )

        async def record():
            after = await ws.reviews.snapshot(ws.runner(cwd), cwd, before=before)
            with suppress(KeyError):
                ws.reviews.record(thread_id, context.run_id, cwd, before, after, "terminal")

        session.on_exit = record
        return json.dumps(await session.output(wait=0.2))

    @tool
    async def list_terminals() -> str:
        """List this conversation's terminal sessions, and your helpers', with ids, owners and exit status."""
        ws, thread_id = workspace_for(context)
        sessions = await ws.terminals.list(thread_id)
        if not context.parent_run_id:
            for helper, title in ws.helper_threads(context.run_id).items():
                sessions.extend({**row, "helper": title} for row in await ws.terminals.list(helper))
        return json.dumps(sessions)

    @tool
    async def read_terminal(terminal_id: str, cursor: int = 0, wait_seconds: float = 1) -> str:
        """Read terminal output after a cursor. Reuse the returned cursor; wait up to 20s."""
        if not 0 <= wait_seconds <= 20:
            raise ValueError("wait_seconds must be between 0 and 20")
        _, _, session = await reachable(context, terminal_id)
        return json.dumps(await session.output(cursor, wait_seconds))

    @tool
    async def write_terminal(terminal_id: str, text: str) -> str:
        """Send input to this chat's agent terminal, including earlier runs. Newline submits a line."""
        _, _, session = await reachable(context, terminal_id, writable=True)
        if session.owner != "agent":
            raise PermissionError("Only the user can type into a user terminal")
        if not 1 <= len(text) <= 65536:
            raise ValueError("Input must be 1–65536 characters")
        await session.write(text)
        return "Input sent. Read the terminal to inspect the result."

    @tool
    async def close_terminal(terminal_id: str) -> str:
        """Stop this chat's or a helper's agent terminal and its children, including earlier runs. Keeps user terminals."""
        ws, thread_id, session = await reachable(context, terminal_id)
        if session.owner != "agent":
            raise PermissionError("Only the user can close a user terminal")
        await ws.terminals.remove(thread_id, terminal_id)
        return "Terminal and its child processes stopped."

    return next(
        t
        for t in (start_terminal, list_terminals, read_terminal, write_terminal, close_terminal)
        if t.name == name
    )
