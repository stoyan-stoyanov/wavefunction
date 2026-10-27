"""What the user approves for the coding agent's local execution: folders outside the project,
and commands that run outside the sandbox."""

from pathlib import Path
from typing import Literal

from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel

from . import responses as api
from .execution_policy import ToolCallRejected, executing_call_id
from .hands import COMMAND_ACCESS_SETTING
from .sandbox import GRANTS_SETTING, Grant

# A chat in this mode runs what the agent asks to run outside the sandbox without asking each
# time, as in Claude Code's bypass-permissions mode or Codex's full access. Commands still start
# in the sandbox.
FULL_ACCESS = "full"
# The mode a new chat starts in: the one last chosen in the composer or in Settings.
DEFAULT_MODE_SETTING = "default_chat_mode"


class AccessInput(BaseModel):
    access: Literal["sandboxed", "network"]


class DefaultModeInput(BaseModel):
    mode: Literal["auto", "plan", "full"]


def register_access(router, workspace):
    @router.put("/access", response_model=api.AccessSetting)
    def access(payload: AccessInput):
        ws = workspace()
        repo = ws.services.repository
        repo.set_setting(
            COMMAND_ACCESS_SETTING,
            {**repo.get_setting(COMMAND_ACCESS_SETTING, {}), ws.agent_id: payload.access},
        )
        return {"access": payload.access}

    @router.put("/default-mode", response_model=api.DefaultMode)
    def default_mode(payload: DefaultModeInput):
        workspace().services.repository.set_setting(DEFAULT_MODE_SETTING, payload.mode)
        return {"mode": payload.mode}


def full_access(workspace, thread_id: str | None) -> bool:
    """Whether the chat has full access. A helper follows its main chat, as that setting changes."""
    try:
        thread = workspace.thread(thread_id) if thread_id else None
        if thread and thread.get("parent_thread_id"):
            thread = workspace.thread(thread["parent_thread_id"])
        return bool(thread) and thread["mode"] == FULL_ACCESS
    except KeyError:
        return False


def approve_command(
    workspace,
    thread_id: str | None,
    command: str,
    reason: str,
    cwd: Path,
    *,
    outside: bool,
    danger: str | None,
) -> None:
    """Return once the user has allowed `command`. One that can't be undone asks on its own card
    in every mode; one outside the sandbox asks on its card unless the chat has full access."""
    if danger:
        request = {
            "kind": "irreversible",
            "danger": danger,
            "outside_sandbox": outside,
            "description": f"{danger} {command}",
        }
        refusal = (
            "The user did not allow this command, which can't be undone. Do not retry it or work around it;"
        )
    elif full_access(workspace, thread_id):
        return
    else:
        request = {
            "kind": "outside_sandbox",
            "description": f"Run outside the sandbox, on this Mac: {command}",
        }
        refusal = "The user did not allow this command outside the sandbox. Do not retry it;"
    while True:
        result = interrupt(
            {
                "action_requests": [
                    {
                        "name": "run_command",
                        "command": command,
                        "cwd": str(cwd),
                        "reason": reason,
                        "call_id": executing_call_id(),
                        **request,
                    }
                ]
            }
        )
        approved = [
            decision
            for decision in result.get("decisions", [])
            if decision.get("type") == "approve"
            and decision.get("command") == command
            and decision.get("cwd") == str(cwd)
        ]
        if not approved:
            raise PermissionError(f"{refusal} continue without it or ask how they want to proceed.")
        if not danger or any(decision.get("danger") == danger for decision in approved):
            return
        # Recovery data can change while the card is open. An approval without the
        # current irreversible warning cannot authorize it; show a fresh card.


def request_access(
    workspace,
    folder: Path,
    writable: bool = False,
    *,
    ask: bool = False,
    exists: bool = True,
    purpose: str = "",
) -> Path:
    """Return the folder once the user allows it. `ask` shows the card even for a folder already
    granted: a helper's captured access is narrower than the saved grants. `exists=False` asks for
    a folder about to be created, and `purpose` says on the card what it is for."""
    folder = Path(workspace.folder(str(folder), exists=exists))
    grants = [Grant.parse(row) for row in workspace.services.repository.get_setting(GRANTS_SETTING, [])]
    if not ask and any(
        folder.is_relative_to(grant.path) and (not writable or grant.writable) for grant in grants
    ):
        return folder
    access = "read and write" if writable else "read-only"
    result = interrupt(
        {
            "action_requests": [
                {
                    "name": "request_folder_access",
                    "kind": "folder_access",
                    "call_id": executing_call_id(),
                    "folder": str(folder),
                    "writable": writable,
                    "description": f"{purpose}Allow {access} access to {folder}? This folder permission is saved until revoked in Settings → Access.",
                }
            ]
        }
    )
    # Bind the answer to the path shown in the persisted approval, including on replay
    # after a symlink change or restart. A generic tool-level 'always' is insufficient.
    if not any(
        decision.get("type") == "approve"
        and decision.get("folder") == str(folder)
        and decision.get("writable") == writable
        for decision in result.get("decisions", [])
    ):
        raise ToolCallRejected(f"Access to {folder} was not approved.")
    workspace.allow_folder(str(folder), writable=writable)
    return folder


def ensure_path_access(workspace, scratch: Path, path: str, writable: bool):
    if Path(path).expanduser().resolve().is_relative_to(scratch.resolve()):
        return
    request_access(workspace, nearest_folder(path), writable)


def nearest_folder(path: str) -> Path:
    target = Path(path).expanduser().resolve()
    folder = target if target.is_dir() else target.parent
    # New nested files can request their nearest existing folder.
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    return folder


def helper_folder_access(context, folder: Path, writable: bool) -> None:
    """A helper works in the folders it inherited; the user can allow another on its card.

    Inside an inherited folder its access stays as captured, so an isolated helper cannot gain
    write access to the checkout it was isolated from, by asking for it or for a folder above it.
    """
    from .execution_policy import ToolCallRejected

    folder = Path(folder).expanduser().resolve()
    if any(
        folder.is_relative_to(grant.path) and (not writable or grant.writable)
        for grant in context.local_grants()
    ):
        return
    # What it started with, not folders the user allowed on its cards, which a later card can widen.
    inherited = [Grant.parse(row).path for row in context.folder_grants if not row.get("approved")]
    if any(folder.is_relative_to(path) or path.is_relative_to(folder) for path in inherited):
        raise ToolCallRejected(
            "This helper can write only where it was granted in the folders it inherited. "
            "Ask the parent agent to handle this path."
        )
    folder = request_access(context.coding_workspace, folder, writable, ask=True)
    row = {"path": str(folder), "writable": writable, "approved": True}
    context.folder_grants.append(row)
    context.repository.add_run_folder_grant(context.run_id, row)


def build_request_folder_access(workspace, working_directory, *, can_write, helper=None):
    @tool
    def request_folder_access(path: str, writable: bool = False) -> str:
        """Ask for access to a folder outside the project. Call this when a sandboxed
        command needs another folder; shell commands cannot escape the folder boundary.
        Request read-only access unless the task needs to change files there. The grant
        is saved until the user revokes it. This does not change the task's repository."""
        if writable and not can_write:
            raise ToolCallRejected("Plan mode can request read-only folder access.")
        folder = Path(path).expanduser()
        if not folder.is_absolute():
            if not working_directory:
                raise ValueError("Provide an absolute folder path.")
            folder = Path(working_directory) / folder
        if helper is None:
            request_access(workspace, folder, writable)
        else:
            helper_folder_access(helper, folder, writable)
        return f"Folder access granted: {folder.resolve()}"

    return request_folder_access
