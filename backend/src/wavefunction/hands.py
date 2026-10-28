"""Local file and command tools, enforced by the native execution boundary.

Coding calls stay inside project/folder grants and task scratch, with network access by
default. Grants are read for every call so revocation affects already running tasks.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from .execution_policy import ToolCallRejected, emit_command_output
from .irreversible import command_danger
from .sandbox import Grant, Sandbox, run_outside

LOCAL_TOOL_NAMES = frozenset(
    {"read_file", "write_file", "edit_file", "run_command", "find_files", "search_files"}
)

# How the agent's commands run, by agent id: "sandboxed" (offline) or "network" (the
# default). A device setting like the folder grants: read on every call, so a change bites a
# run already under way.
COMMAND_ACCESS_SETTING = "agent_command_access"
COMMAND_ACCESS = ("sandboxed", "network")


def command_access(repository: Any, agent_id: str) -> str:
    return saved_access(repository.get_setting(COMMAND_ACCESS_SETTING, {}), agent_id)


def saved_access(stored: Any, agent_id: str) -> str:
    """The agent's access in the saved setting, for a caller that has already read it."""
    access = stored.get(agent_id) if isinstance(stored, dict) else None
    return access if access in COMMAND_ACCESS else "network"


class FileEdit(BaseModel):
    old_text: str = Field(min_length=1, description="Exact original text as shown by read_file.")
    new_text: str
    replace_all: bool = Field(default=False, description="Replace all matches; otherwise require one.")


# Defaults keep exploratory reads bounded; explicit reads can use the larger limits.
DEFAULT_READ_LINES = 200
DEFAULT_READ_CHARS = 6_000
READ_LINES = 2000
READ_CHARS = 40_000
# Said after a refusal inside the sandbox, which cannot tell the model which rule it met.
READ_DENIED = (
    "The sandbox reads only the project, folders the user granted and toolchain support; "
    "private folders such as ~/.ssh, Keychains and the app's own data stay unreadable. "
    "Use request_folder_access for another folder."
)
WRITE_DENIED = (
    "In the sandbox files change only in folders allowed for writing; ask the user to "
    "allow this one in Settings › Access."
)


async def file_operation(runner: Sandbox, request: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
    """Run one `fileops` operation inside the runner's boundary and return its result.

    The request goes in on standard input, never on the command line, where a large file does
    not fit. A refusal comes back as the error the model reads: PermissionError where the
    boundary said no, ValueError where the operation declined and so changed nothing. Only a
    helper that stopped without answering leaves the outcome unknown. The helper starts
    nothing that outlives it, so the scan for a command's detached descendants is skipped.
    """
    mode = ["--python", "-I"] if getattr(sys, "frozen", False) else ["-I"]
    result = await runner.run(
        shlex.join([sys.executable, *mode, "-m", "wavefunction.fileops"]),
        stdin=json.dumps(request).encode(),
        scan_detached=False,
        timeout=timeout,
    )
    try:
        # A long answer outgrows the preview; the spooled stream holds all of it.
        answer = json.loads(result.stdout_file.read_bytes() if result.stdout_file else result.stdout)
    except ValueError:
        answer = None
    finally:
        for spool in (result.stdout_file, result.stderr_file):
            if spool is not None:
                spool.unlink(missing_ok=True)
    if not isinstance(answer, dict):
        detail = (result.stderr.strip().splitlines() or ["it stopped without an answer"])[-1]
        raise RuntimeError(f"The file operation did not finish: {detail}")
    if answer["ok"]:
        return answer
    raise (PermissionError if answer["error"] == "denied" else ValueError)(answer["message"])


def existing_folder(folder: Path) -> Path:
    """A command's working folder, refused before the command starts when it is not there."""
    if not folder.is_dir():
        raise ValueError(f"{folder} is not an existing folder, so the command did not run.")
    return folder


RUN_COMMAND = (
    "Run /bin/sh -c on macOS; returns exit code/output. No stdin: use noninteractive flags. "
    "Background children stop on return; start/use servers in one call. Long output returns "
    "a tail and file path; read_file with offsets retrieves the rest. A command stopped at its "
    "timeout returns what it printed so far: do not pipe through tail or head, which hides it. "
    "Read and search files with read_file, find_files and search_files, not cat, sed, head or "
    "grep: their output is bounded, and write_file replaces only files read with read_file. "
)
RUN_COMMAND_ARGS = (
    "workdir defaults to the repository; overrides may be absolute or repository-relative. "
    "Use request_folder_access for other folders. Other sandbox restrictions: request "
    "outside_sandbox=true with a one-sentence reason."
)


def build_hands(
    scratch: Path,
    grants_now: Callable[[], list[Grant]],
    access_now: Callable[[], str],
    *,
    read_only: bool | str = False,
    working_directory: str | None = None,
    reads: dict[str, str] | None = None,
    record_changes: Callable | None = None,
    changed_by: Callable[[str], str | None] | None = None,
    private_roots: list[Path] | None = None,
    ensure_access: Callable[[str, bool], None],
    credentials: Callable[[], Awaitable[dict[str, str]]] | None = None,
    approve_command: Callable[..., None] | None = None,
) -> list[BaseTool]:
    """The local tools, reading their permissions live.

    `access_now` says how commands run: "sandboxed" or "network" (the sandbox plus the
    network). Runners are constructed per call from it and the current grants rather than
    captured at build: a turn already running must see a revocation made in Settings. Relative
    paths need a captured working directory. `read_only` refuses writes and commands.
    `ensure_access` raises PermissionError for a path the task may not use, asking for it
    first where someone can answer. `credentials` gives a command that may use the network the
    variables that send `git` and `gh` through the app's GitHub proxy, with a placeholder for
    the sign-in, so they work as in the user's terminal without the token. `approve_command`
    returns once the user has allowed a command that runs outside the sandbox or can't be undone,
    pausing the task for them until then; without it, as for a helper, such commands are refused.

    `reads` holds, by path, the version of each file these tools have read or saved in this
    run: write_file replaces an existing file only while it is still that version. A built
    agent's tools share their run's; without one it lasts as long as these tools.
    """
    reads = {} if reads is None else reads

    def refuse(message: str) -> None:
        if read_only:
            raise ToolCallRejected(read_only if isinstance(read_only, str) else message)

    def check_access(path: str, writable: bool) -> None:
        try:
            ensure_access(path, writable)
        except PermissionError as error:
            raise ToolCallRejected(str(error)) from error

    def sandboxed(network: bool = False, env: dict[str, str] | None = None) -> Sandbox:
        return Sandbox(
            grants_now(),
            scratch=scratch,
            network=network,
            private_roots=private_roots,
            restrict_reads=True,
            env=env,
        )

    async def operate(request: dict[str, Any], denied: str) -> dict[str, Any]:
        if request.get("path"):
            check_access(request["path"], request["op"] in {"write", "edit"})
        runner = sandboxed()
        try:
            if record_changes and request["op"] in {"write", "edit"}:
                async with record_changes(runner, paths=[request["path"]], source="file"):
                    return await file_operation(runner, request)
            return await file_operation(runner, request)
        except PermissionError as error:
            raise ToolCallRejected(f"{error}. {denied}") from None
        except ValueError as error:
            # Only a refusal because the file changed since this task read it is someone else's doing.
            if (
                changed_by
                and request["op"] in {"write", "edit"}
                and "has changed since it was last read" in str(error)
                and (who := changed_by(request["path"]))
            ):
                raise ValueError(
                    f"{error} {who} changed this file while you worked: read it again and keep their change."
                ) from None
            raise

    cwd = Path(working_directory).expanduser() if working_directory else None

    @asynccontextmanager
    async def command_changes(runner):
        if record_changes:
            async with record_changes(runner, source="command") as before:
                yield before
        else:
            yield None

    def resolved_path(path: str) -> str:
        expanded = Path(path).expanduser()
        if expanded.is_absolute():
            return str(expanded)
        if cwd is None:
            # Its scratch space is hidden and home would be a guess: nowhere a relative path
            # could be meant.
            raise ValueError(
                f"{path!r} is a relative path, and this agent has no repository folder. "
                "Use an absolute path, or ask the user for the folder with "
                "choose_repository_folder when you have that tool."
            )
        return str(cwd / expanded)

    @tool("read_file", response_format="content_and_artifact")
    async def read_file(
        path: str,
        offset: int = 0,
        limit: int = DEFAULT_READ_CHARS,
        start_line: int | None = None,
        line_count: int = DEFAULT_READ_LINES,
    ) -> tuple[str, dict]:
        """Read text/images by absolute or repository-relative path.

        Search large/generated files first. Use start_line (1-based) and line_count, or character
        offset; do not combine them. Maxima: 40000 characters/2000 lines. Expand ranges as needed;
        follow continuation offsets, including within long lines. Binary files are described.
        Images show the first frame up to 2048 pixels to vision models; pagination is ignored.
        """
        if offset < 0 or not 1 <= limit <= READ_CHARS:
            raise ValueError(f"offset must be nonnegative and limit between 1 and {READ_CHARS}")
        if start_line is not None and (start_line < 1 or offset or not 1 <= line_count <= READ_LINES):
            raise ValueError(
                f"start_line must be positive, line_count 1–{READ_LINES}; do not combine start_line with offset"
            )
        path = resolved_path(path)
        # Until this read succeeds, the run has not seen the file: a stale version must not let
        # write_file replace, or recreate, what it can no longer read.
        reads.pop(os.path.normpath(path), None)
        image_id = uuid.uuid4().hex + ".png"
        result = await operate(
            {
                "op": "read",
                "path": path,
                "offset": offset,
                "limit": limit,
                "start_line": start_line,
                "line_count": line_count,
                "snapshot": str(scratch / "images" / image_id),
            },
            READ_DENIED,
        )
        reads[os.path.normpath(path)] = result["version"]
        if "image" in result:
            from .local_images import prune_snapshots

            prune_snapshots(scratch / "images")
            return f"Read image {path} ({result['image']}, first frame).", {
                "coding": {"read": path},
                "local_image": image_id,
            }
        return result["text"], {"coding": {"read": path}}

    @tool("write_file", response_format="content_and_artifact")
    async def write_file(path: str, content: str) -> tuple[str, dict]:
        """Create/atomically replace a whole file and missing folders; returns a diff.

        Preserves exact content, encoding and line endings. Replacement requires read_file in
        this run and refuses later file changes. Use edit_file for partial changes.
        Paths are absolute or repository-relative.
        """
        refuse("Local file writes are disabled for this read-only run.")
        path = resolved_path(path)
        key = os.path.normpath(path)
        result = await operate(
            {"op": "write", "path": path, "content": content, "expected": reads.get(key)}, WRITE_DENIED
        )
        reads[key] = result["version"]
        return result["text"], {"coding": {"modified": path}}

    @tool("edit_file", response_format="content_and_artifact")
    async def edit_file(
        path: str,
        edits: list[FileEdit] | None = None,
        old_text: str | None = None,
        new_text: str | None = None,
        replace_all: bool = False,
    ) -> tuple[str, dict]:
        """Apply exact replacements atomically; returns a unified diff. Read first.

        Supply edits=[{old_text,new_text,replace_all?},...] or one old_text/new_text pair.
        Match read_file text once in the ORIGINAL file unless replace_all=true. Matches cannot
        overlap; any invalid edit changes nothing. Preserves encoding/newlines.
        Paths are absolute or repository-relative. Use write_file for new files.
        """
        refuse("Local file writes are disabled for this read-only run.")
        if edits is not None and (old_text is not None or new_text is not None or replace_all):
            raise ValueError("Use edits or a single old_text/new_text pair, not both")
        if edits is None:
            if old_text is None or new_text is None:
                raise ValueError("Provide edits or both old_text and new_text")
            edits = [FileEdit(old_text=old_text, new_text=new_text, replace_all=replace_all)]
        if not edits:
            raise ValueError("edits must contain at least one replacement")
        path = resolved_path(path)
        result = await operate(
            {"op": "edit", "path": path, "edits": [edit.model_dump() for edit in edits]}, WRITE_DENIED
        )
        reads[os.path.normpath(path)] = result["version"]
        return result["text"], {"coding": {"modified": path}}

    @tool(
        "run_command",
        response_format="content_and_artifact",
        description=RUN_COMMAND
        + "Sandboxed by default; timeout_seconds defaults to 120. "
        + RUN_COMMAND_ARGS,
    )
    async def run_command(
        command: str,
        timeout_seconds: float | None = None,
        workdir: str | None = None,
        outside_sandbox: bool = False,
        reason: str = "",
    ) -> tuple[str, dict]:
        refuse("Local commands are disabled for this read-only run.")
        command_cwd = Path(resolved_path(workdir)) if workdir is not None else cwd
        if command_cwd is not None:
            check_access(str(existing_folder(command_cwd)), False)
        network = access_now() == "network"
        if timeout_seconds is not None and (not 0 < timeout_seconds < float("inf")):
            raise ValueError("timeout_seconds must be finite and positive")
        limit: dict[str, Any] = {"timeout": timeout_seconds} if timeout_seconds is not None else {}

        def stream(stdout: str, stderr: str) -> None:
            emit_command_output(stdout, stderr)

        if outside_sandbox and not reason.strip():
            raise ValueError("Give the reason the command needs to run outside the sandbox.")
        runner = sandboxed(network)
        async with command_changes(runner) as before:
            danger = command_danger(
                command,
                cwd=command_cwd or scratch,
                project=cwd,
                scratch=scratch,
                outside=outside_sandbox,
                captured=(before or {}).get("files"),
            )
            if outside_sandbox or danger:
                if approve_command is None:
                    raise ToolCallRejected(
                        f"{danger} Only the user can allow it: ask them, or the parent agent, to run it."
                        if danger
                        else "This task cannot run commands outside the sandbox: ask the user, or the parent agent, to run it."
                    )
                try:
                    approve_command(
                        command,
                        reason.strip(),
                        command_cwd or scratch,
                        outside=outside_sandbox,
                        danger=danger,
                    )
                except PermissionError as error:
                    raise ToolCallRejected(str(error)) from error
            if outside_sandbox:
                result = await run_outside(
                    command, cwd=command_cwd or scratch, scratch=scratch, on_output=stream, **limit
                )
            else:
                runner.env = await credentials() if credentials is not None and network else {}
                result = await runner.run(command, cwd=command_cwd, tail=True, on_output=stream, **limit)
        parts = [f"[exit code {result.exit_code}]"]
        if result.stdout.strip():
            parts.append(result.stdout.strip())
        if result.stderr.strip():
            parts.append(f"[stderr]\n{result.stderr.strip()}")
        if result.timed_out:
            parts.append("[the command was still running and was stopped]")
        return "\n\n".join(parts), {
            "coding": {
                "command": command,
                "cwd": str(command_cwd or scratch),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                **({"outside_sandbox": True} if outside_sandbox else {}),
            }
        }

    async def search(
        mode: str,
        query: str,
        path: str,
        glob: str | None,
        offset: int,
        limit: int,
        case_insensitive: bool = False,
        context_lines: int = 0,
    ) -> str:
        from .repository_search import ripgrep_binary

        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("offset must be nonnegative and limit between 1 and 100")
        if not 0 <= context_lines <= 10:
            raise ValueError("context_lines must be between 0 and 10")
        result = await operate(
            {
                "op": "search",
                "rg": str(ripgrep_binary()),
                "mode": mode,
                "query": query,
                "path": resolved_path(path),
                "glob": glob or "",
                "offset": offset,
                "limit": limit,
                "case_insensitive": case_insensitive,
                "context_lines": context_lines,
            },
            READ_DENIED,
        )
        return result["text"]

    @tool
    async def find_files(pattern: str = "*", path: str = ".", offset: int = 0, limit: int = 50) -> str:
        """Find files by glob within folder permissions; relative paths use the repository.

        Respects ignores; includes hidden files except .git/.env. Sorted, bounded results:
        narrow pattern/path or continue with the returned offset.
        """
        return await search("files", "", path, pattern, offset, limit)

    @tool
    async def search_files(
        query: str,
        path: str = ".",
        glob: str | None = None,
        offset: int = 0,
        limit: int = 50,
        case_insensitive: bool = False,
        context_lines: int = 0,
    ) -> str:
        """Search files by ripgrep regex within folder permissions; returns file:line:snippet.

        Respects ignores; skips binaries/.git/.env, includes other hidden files. Relative paths
        use the repository; glob narrows files. context_lines (0–10) adds surrounding lines.
        Sorted, bounded results include continuation offsets. For a specific definition,
        use context_lines=3; read further only if context is insufficient or before editing.
        Use search_documents for uploads.
        """
        return await search("content", query, path, glob, offset, limit, case_insensitive, context_lines)

    return [read_file, write_file, run_command, edit_file, find_files, search_files]


__all__ = ["COMMAND_ACCESS", "COMMAND_ACCESS_SETTING", "build_hands", "command_access", "file_operation"]
