"""The agent's hands on the user's projects: what the sidebar's own menus do, asked for in chat.

Every change here is one the user can take back. A removed project leaves its folder, chats and
history, and a project made again for that folder takes its chats back; an archived chat is
restored from its project's archive. Only a new folder asks first, on the same card as any other
folder the agent would write to.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from langchain_core.tools import tool

from .coding_access import request_access
from .execution_policy import ToolCallRejected

# Clear, but not so sure of itself that it hides the projects it could have meant.
UNKNOWN = "No project is called {name!r}. Call list_projects for their names and ids."
AMBIGUOUS = "More than one project is called {name!r}. Use one of these ids instead: {ids}."
# Chats an archive result names; the rest are counted, so a big cleanup stays a small result.
LISTED = 20


def build_project_tools(context):
    from .coding_workspace import ProjectInput

    ws = context.coding_workspace
    current = context.desktop_copy

    def projects() -> list[dict]:
        with ws.db() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM coding_projects ORDER BY position, created_at")
            ]

    def find(project: str) -> dict:
        wanted = project.strip()
        known = projects()
        if match := next((item for item in known if item["id"] == wanted), None):
            return match
        named = [item for item in known if item["name"].casefold() == wanted.casefold()]
        if len(named) == 1:
            return named[0]
        raise ToolCallRejected(
            AMBIGUOUS.format(name=wanted, ids=", ".join(item["id"] for item in named))
            if named
            else UNKNOWN.format(name=wanted)
        )

    @tool
    def list_projects() -> str:
        """List the user's projects: id, name, folder, and how many chats each has open and archived."""
        with ws.db() as db:
            counts = {
                (row["project_id"], row["archived"]): row["count"]
                for row in db.execute(
                    """SELECT project_id, archived, COUNT(*) AS count FROM coding_threads
                   WHERE parent_thread_id IS NULL GROUP BY project_id, archived"""
                )
            }
            here = db.execute("SELECT project_id FROM coding_threads WHERE id = ?", (current,)).fetchone()
        return json.dumps(
            [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "folder": item["folder"],
                    "chats": counts.get((item["id"], 0), 0),
                    "archived_chats": counts.get((item["id"], 1), 0),
                    "this_chat_is_here": bool(here and here["project_id"] == item["id"]),
                }
                for item in projects()
            ]
        )

    @tool
    def create_project(name: str, folder: str) -> str:
        """Add a project: a folder on this Mac that the user's chats work in, listed in the sidebar.

        The folder is created if it does not exist. To start a project from a repository, create
        the project first, then clone into its folder with run_command, workdir set to the folder:
        `git clone <url> .` (the folder must still be empty). Use the folder the user named; if
        they named none, ask where it should go.

        Args:
            name: The project's name in the sidebar.
            folder: Absolute path of the folder, or one starting with ~.
        """
        if not name.strip() or len(name.strip()) > 100:
            raise ToolCallRejected("Give the project a name of up to 100 characters.")
        path = Path(folder.strip()).expanduser()
        if not path.is_absolute():
            raise ToolCallRejected("Give the folder as an absolute path, or one starting with ~.")
        try:
            path = Path(ws.folder(str(path), exists=False))
        except ValueError as error:
            raise ToolCallRejected(str(error)) from error
        if existing := next((item for item in projects() if item["folder"] == str(path)), None):
            return json.dumps(
                {
                    "project_id": existing["id"],
                    "name": existing["name"],
                    "folder": existing["folder"],
                    "note": "A project for this folder already exists; nothing was added.",
                }
            )
        new = not path.exists()
        request_access(
            ws,
            path,
            writable=True,
            exists=False,
            purpose=f"Create the project “{name.strip()}” in {'a new folder' if new else 'this folder'}. ",
        )
        path.mkdir(parents=True, exist_ok=True)
        project = ws.create_project(ProjectInput(name=name.strip(), folder=str(path)))
        return json.dumps(
            {
                "project_id": project["id"],
                "name": project["name"],
                "folder": project["folder"],
                "created_folder": new,
                "note": "It is listed in the sidebar.",
            }
        )

    @tool
    async def archive_chats(project: str, older_than_days: float) -> str:
        """Archive a project's chats that have had no activity for at least `older_than_days` days.

        Archived chats leave the sidebar with their history intact; the project's menu, Show
        archived, lists them, and each one's menu restores it. Pinned chats, chats with a task
        running or waiting, routines and this chat stay where they are.

        Args:
            project: The project's name or id.
            older_than_days: Days since a chat last had a message, more than 0.
        """
        if not 0 < older_than_days < 100_000:
            raise ToolCallRejected("Give older_than_days as a number of days greater than 0.")
        target = find(project)
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        routines = ws.routines.all()
        with ws.db() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """SELECT id, title, updated_at, pinned, conversation_id FROM coding_threads
                   WHERE project_id = ? AND archived = 0 AND parent_thread_id IS NULL""",
                    (target["id"],),
                )
            ]
        archived, kept = [], {"pinned": 0, "busy": 0, "routines": 0}
        for row in rows:
            if row["id"] == current or datetime.fromisoformat(row["updated_at"]) >= cutoff:
                continue
            reason = (
                "pinned"
                if row["pinned"]
                else "routines"
                if row["id"] in routines and routines[row["id"]]["enabled"]
                else "busy"
                if ws.services.repository.pending_conversation_runs(ws.agent_id, row["conversation_id"])
                else None
            )
            if reason:
                kept[reason] += 1
                continue
            async with ws.checkpoints.locks.setdefault(row["id"], asyncio.Lock()):
                # Decided again under the chat's lock, which a message or an edit holds: either
                # one moves `updated_at`, so a chat that changed while this waited stays.
                if ws.services.repository.pending_conversation_runs(ws.agent_id, row["conversation_id"]):
                    kept["busy"] += 1
                    continue
                with ws.db() as db:
                    changed = db.execute(
                        """UPDATE coding_threads SET archived = 1 WHERE id = ? AND archived = 0 AND pinned = 0
                           AND project_id = ? AND updated_at = ?""",
                        (row["id"], target["id"], row["updated_at"]),
                    ).rowcount
            if changed:
                archived.append({"id": row["id"], "title": row["title"], "last_activity": row["updated_at"]})
        return json.dumps(
            {
                "project": target["name"],
                "archived_count": len(archived),
                "archived": archived[:LISTED],
                **({"not_listed": len(archived) - LISTED} if len(archived) > LISTED else {}),
                "left_in_place": {key: count for key, count in kept.items() if count},
                "note": "Restore any of them from the project's menu, Show archived.",
            }
        )

    @tool
    def remove_project(project: str) -> str:
        """Remove a project from the sidebar. Nothing is deleted: its folder and files, its chats and
        their history all stay, and the chats keep working in that folder outside any project.
        Adding a project for the same folder again brings its chats back.

        Args:
            project: The project's name or id.
        """
        target = find(project)
        ws.remove_project(target["id"])
        return json.dumps(
            {
                "removed": target["name"],
                "folder": target["folder"],
                "note": "Its folder and chats are untouched.",
            }
        )

    list_projects.metadata = {"readOnlyHint": True}
    return [list_projects, create_project, archive_chats, remove_project]
