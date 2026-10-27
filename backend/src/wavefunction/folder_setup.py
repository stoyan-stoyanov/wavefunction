"""Folder setup cards shared by coding and computer tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt

from .sandbox import Grant

FOLDER, REPOSITORY = "folder-access", "repository-folder"
KINDS = {FOLDER: "folder", REPOSITORY: "folder"}


def _row(setup_id: str, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": setup_id,
        "title": title,
        "catalog_id": None,
        "local_kind": KINDS[setup_id],
        "auth": "none",
        "state": "setup",
        "error": "",
        "tool_count": 0,
        **extra,
    }


def _covered(path: str, writable: bool, grants: list[Grant]) -> bool:
    target = Path(path)
    return any(
        (grant.path == target or grant.path in target.parents) and (grant.writable or not writable)
        for grant in grants
    )


def setup_status(row: dict[str, Any], grants: list[Grant]) -> dict[str, Any] | None:
    """Read a paused folder card against the user's current grants."""
    if row.get("id") not in KINDS or row.get("local_kind") != "folder":
        return None
    known = {tuple(item) for item in row.get("known", [])}
    ready = (
        _covered(row["path"], bool(row.get("writable")), grants)
        if row.get("path")
        else row["id"] == FOLDER
        and any(
            (str(grant.path), grant.writable) not in known and (grant.writable or not row.get("writable"))
            for grant in grants
        )
    )
    return {**row, "state": "ready" if ready else "setup"}


def record_folder(pending: dict[str, Any], path: str, setup_id: str | None = None) -> dict[str, Any] | None:
    """Record the folder the user picked on a folder card, and return that card's row; None when
    no folder card is waiting.

    The folder picked answers the card, whichever folder it named. A card that named one waited
    for that very folder, so a task whose user picked another, or the same one spelled another
    way, as ~/downloads for ~/Downloads, waited for ever. A card that asked for
    any folder waited for a grant it had not seen when it opened, so picking a folder that already
    had the access left it waiting too. And an agent's card is how the agent learns where it works.
    """
    row = next(
        (
            row
            for row in pending.get("connectors", [])
            if row.get("local_kind") == "folder" and setup_id in (None, row.get("id"))
        ),
        None,
    )
    if row is None:
        return None
    chosen = Path(path).expanduser()
    if not chosen.is_absolute():
        raise ValueError("Choose the folder by its full path.")
    row["path"] = str(chosen.resolve())
    return row


def picked_folder(pending: dict[str, Any]) -> str | None:
    """The folder a paused folder-access tool goes on in: the one picked on its card, or the
    one it named when that was allowed some other way, such as in Settings › Access."""
    return next(
        (row["path"] for row in pending.get("connectors", []) if row.get("id") == FOLDER and row.get("path")),
        None,
    )


def build_choose_repository_folder(working_directory: str | None) -> BaseTool:
    """Ask for the run's working folder. The run is built again once the folder is chosen, so
    `working_directory` is the chosen folder by the time the tool is answered."""

    @tool("choose_repository_folder")
    def choose_repository_folder() -> str:
        """Ask the user which folder on this Mac to work in, with a card in this conversation, and wait.

        Call it when the task needs a repository or folder and none is chosen, or when the user
        wants you to work in another one. They pick it in the macOS folder picker, which lets
        you change files there, and it stays your folder in later conversations here.
        """
        interrupt(
            {
                "kind": "connector_setup",
                "connectors": [_row(REPOSITORY, "Repository folder", path="", writable=True)],
            }
        )
        return json.dumps(
            {"folder": working_directory, "next": "Relative file paths and commands use this folder now."}
        )

    choose_repository_folder.metadata = {"readOnlyHint": True}
    return choose_repository_folder
