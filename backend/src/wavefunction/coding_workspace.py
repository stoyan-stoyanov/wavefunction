"""Wavefunction product surface: one coding agent, durable independent conversations.

The existing LangGraph executor owns messages, checkpoints, tools, approvals and events.
This index owns only thread/project metadata; projects never instantiate agents.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import threading
from collections import Counter
from contextlib import AsyncExitStack, contextmanager, suppress
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from . import responses as api
from .change_review import ChangeReviews
from .coding_access import DEFAULT_MODE_SETTING, register_access
from .coding_checkpoints import Checkpoints
from .coding_cleanup import CleanupSettings, WorktreeCleanup
from .coding_context import Context, TaskContext, register_context
from .coding_delivery import Delivery, register_delivery
from .coding_questions import register_questions
from .coding_recovery import Recovery
from .computer import screenshot_folder, screenshots_root
from .document_images import preview_png
from .file_previews import register_file_previews
from .hands import COMMAND_ACCESS_SETTING, command_access, file_operation, saved_access
from .local_coding import runner_for
from .repository import now_iso
from .responses import ProjectIcon
from .sandbox import GRANTS_SETTING, PRIVATE_FOLDERS, Grant, holds_app_data, scratch_for
from .skill_discovery import LOCAL_ROOTS, MAX_SKILL_BYTES, catalog, discover, public_inventory
from .skills import FRONTMATTER, normalise_name, validate_skill, write_skill_markdown
from .spec_common import ModelSpec
from .spec_v2 import AgentSpecV2, ToolBinding, ToolPolicy, load_spec
from .terminals import TerminalManager
from .toolkit import TOOL_REGISTRY, find_folded
from .worktree_access import linked_worktree_grants

ACTIVE = {"queued", "running", "waiting_approval", "waiting_connector", "waiting_computer"}
CODING_PROMPT = """You are Wavefunction, one coding agent with independent chats. Complete the agreed
task and required checks, then stop. Follow user/repository instructions; preserve unrelated
work. Never use destructive commands like git reset --hard or git checkout -- unless the user
asks. Make focused, reviewable edit_file changes. Inspect and verify proportionally. Batch
independent tools, use relevant skills, ask only necessary questions.
Use git/gh for all GitHub work and current PR/issue/check status; local history is insufficient.
Push with git, never recreate commits via API. If gh is signed out, call configure_github
and continue. Merge only on the user's request.
Keep the user informed without running commentary: one short sentence when you start a distinct
step or a long check. Starting helpers, say what each will do; when one finishes, say in one
sentence what it did or found.
Claim success only with evidence. Clean up your test data/processes; leave pre-existing
resources alone. Start servers on 127.0.0.1, not 0.0.0.0, unless asked: other devices on
the network can reach all-interface servers. End concisely with outcome, validation and limitations.
Link a file you created for the user as a markdown link to its path, e.g. [report.pdf](out/report.pdf);
the app previews it.
"""


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    folder: str | None = None
    icon: ProjectIcon = "folder"
    icon_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")


class ProjectOrder(BaseModel):
    ids: list[str]


class ThreadInput(BaseModel):
    title: str = Field(default="New session", min_length=1, max_length=160)
    project_id: str | None = None
    folder: str | None = None
    model: ModelSpec | None = None
    mode: Literal["auto", "plan", "full"] = "auto"
    instructions: str = Field(default="", max_length=30000)
    worktree: bool = False
    worktree_base: str = Field(default="HEAD", min_length=1, max_length=4096)


class ThreadPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    project_id: str | None = None
    folder: str | None = None
    model: ModelSpec | None = None
    mode: Literal["auto", "plan", "full"] | None = None
    instructions: str | None = Field(default=None, max_length=30000)
    archived: bool | None = None
    pinned: bool | None = None


class RoutinePatch(BaseModel):
    instruction: str | None = Field(default=None, min_length=1, max_length=30000)
    timing: dict | None = None
    enabled: bool | None = None
    # A chat in the same project to report each finished run to; null for none.
    report_to: str | None = None


class ProjectSkillInput(BaseModel):
    name: str = Field(default="", max_length=200)
    description: str = Field(max_length=1024)
    body: str = Field(default="", max_length=256 * 1024)
    # Editing names the file it read and its digest, so a change made meanwhile is not lost.
    path: str | None = None
    digest: str | None = None


class MessageInput(BaseModel):
    content: str = Field(default="", max_length=200000)
    context: TaskContext = Field(default_factory=TaskContext)
    delivery: Literal["steer", "queue"] = "steer"
    worktree: bool = False
    worktree_base: str | None = Field(default=None, min_length=1, max_length=4096)


class ForkInput(BaseModel):
    checkpoint_id: str | None = None
    worktree: bool = True


class RewindInput(BaseModel):
    checkpoint_id: str


class FileWrite(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=1000000)
    version: str = Field(min_length=1)
    folder: str | None = None


class CommandInput(BaseModel):
    command: str = Field(min_length=1, max_length=20000)
    timeout: int = Field(default=30, ge=1, le=120)


class TerminalInput(BaseModel):
    title: str = Field(default="Terminal", min_length=1, max_length=80)
    cols: int = Field(default=80, ge=10, le=500)
    rows: int = Field(default=24, ge=2, le=200)


class TerminalWrite(BaseModel):
    text: str = Field(min_length=1, max_length=65536)


class TerminalSize(BaseModel):
    cols: int = Field(ge=10, le=500)
    rows: int = Field(ge=2, le=200)


class CodingWorkspace:
    def __init__(self, services):
        self.services = services
        self.lock = threading.RLock()
        self.pending_attachments = Counter()
        self.path = services.settings.data_dir / "harness-workspace.sqlite"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS coding_projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT, created_at TEXT NOT NULL,
                    icon TEXT NOT NULL DEFAULT 'folder', icon_color TEXT,
                    position INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS coding_threads (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL, project_id TEXT REFERENCES coding_projects(id)
                    ON DELETE SET NULL, folder TEXT, model TEXT NOT NULL,
                    mode TEXT NOT NULL, instructions TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    parent_thread_id TEXT REFERENCES coding_threads(id) ON DELETE SET NULL,
                    parent_run_id TEXT
                );
                CREATE TABLE IF NOT EXISTS coding_attachments (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES coding_threads(id)
                    ON DELETE CASCADE, file_id TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(coding_projects)")}
            # Projects saved before they could be reordered share a position, so they keep their
            # creation order until they are moved.
            if "position" not in columns:
                db.execute("ALTER TABLE coding_projects ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
            # Projects no longer have instructions: their chats take them on, ahead of their own.
            if "instructions" in columns:
                db.execute(
                    """UPDATE coding_threads SET instructions = (
                        SELECT p.instructions || CASE coding_threads.instructions WHEN '' THEN ''
                        ELSE char(10, 10) || coding_threads.instructions END
                        FROM coding_projects p WHERE p.id = coding_threads.project_id)
                    WHERE project_id IN (SELECT id FROM coding_projects WHERE instructions != '')"""
                )
                db.execute("ALTER TABLE coding_projects DROP COLUMN instructions")
        agent_id = services.repository.get_setting("harness_coding_agent")
        if agent_id:
            try:
                services.repository.get_agent(agent_id)
            except KeyError:
                agent_id = None
        if not agent_id:
            agent_id = services.repository.create_agent("Wavefunction", "Your coding agent")["id"]
            services.repository.set_setting("harness_coding_agent", agent_id)
        self.agent_id = agent_id
        self.terminals = TerminalManager(self.terminal_signature)
        self.reviews = ChangeReviews(self)
        self.delivery = Delivery(self)
        self.recovery = Recovery(self)
        self.checkpoints = Checkpoints(self)
        self.context = Context(self)
        self.cleanup = WorktreeCleanup(self)
        from .routines import Routines

        self.routines = Routines(self)

    def helper_threads(self, run_id):
        """The chats of the helpers a main task started, by id, with their titles."""
        with self.db() as db:
            return {
                row["id"]: row["title"]
                for row in db.execute(
                    "SELECT id, title FROM coding_threads WHERE parent_run_id = ?", (run_id,)
                )
            }

    async def close_helper_terminals(self, run_id):
        """When a main task ends, its helpers' agent terminals go with it; terminals the user opened stay."""
        helpers = self.helper_threads(run_id)
        for session in list(self.terminals.sessions.values()):
            if session.thread_id in helpers and session.owner == "agent":
                with suppress(KeyError):
                    await self.terminals.remove(session.thread_id, session.id)

    def terminal_signature(self, thread_id):
        thread = self.thread(thread_id)
        # An archived helper is only out of the sidebar: its terminals stay until its main task ends.
        if thread["mode"] == "plan" or (thread["archived"] and not thread.get("parent_thread_id")):
            raise ValueError("Restore the conversation and switch out of Plan mode to use a terminal.")
        # Full access changes what the agent may run outside the sandbox, not the terminal.
        grants = self.services.repository.get_setting(GRANTS_SETTING, [])
        return {
            "folder": str(self.root(thread_id)),
            "access": command_access(self.services.repository, self.agent_id),
            "grants": {g["path"]: bool(g["writable"]) for g in grants},
        }

    @contextmanager
    def db(self):
        with self.lock, sqlite3.connect(self.path, timeout=30) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    @staticmethod
    def decode(row):
        value = dict(row)
        if "model" in value:
            value["model"] = json.loads(value["model"])
            value["archived"] = bool(value["archived"])
            value["pinned"] = bool(value["pinned"])
        return value

    def folder(self, value: str | None, *, exists: bool = True) -> str | None:
        """The folder, resolved, once it may hold a repository. `exists=False` checks one about to
        be created."""
        if not value:
            return None
        expanded = Path(value).expanduser()
        if not expanded.is_absolute():
            raise ValueError("Choose an absolute repository folder.")
        folder = expanded.resolve()
        if not folder.is_dir() and (exists or folder.exists()):
            raise ValueError(
                "That folder does not exist." if exists else "That path is a file, not a folder."
            )
        if holds_app_data(folder, self.services.settings.data_dir):
            raise ValueError("Choose a repository outside Wavefunction application data.")
        private = [Path.home() / name for name in PRIVATE_FOLDERS]
        if any(folder == root or root in folder.parents for root in private):
            raise ValueError("A private credential directory cannot be a coding repository.")
        return str(folder)

    def allow_folder(self, folder: str | None, *, writable=True):
        if not folder:
            return
        repo = self.services.repository
        grants = repo.get_setting(GRANTS_SETTING, [])
        writable = writable or any(g["path"] == folder and g["writable"] for g in grants)
        grants = [g for g in grants if g["path"] != folder]
        repo.set_setting(GRANTS_SETTING, [*grants, {"path": folder, "writable": writable}])

    def project(self, project_id: str):
        with self.db() as db:
            row = db.execute("SELECT * FROM coding_projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError("Project not found")
        return dict(row)

    def create_project(self, payload: ProjectInput) -> dict:
        folder = self.folder(payload.folder)
        if not payload.name.strip():
            raise ValueError("Give the project a name.")
        record = {
            "id": str(uuid4()),
            "name": payload.name.strip(),
            "folder": folder,
            "created_at": now_iso(),
            "icon": payload.icon,
            "icon_color": payload.icon_color,
        }
        self.allow_folder(folder)
        with self.db() as db:
            record["position"] = db.execute(
                "SELECT COALESCE(MAX(position) + 1, 0) FROM coding_projects"
            ).fetchone()[0]
            db.execute(
                """INSERT INTO coding_projects
                (id, name, folder, created_at, icon, icon_color, position)
                VALUES (:id, :name, :folder, :created_at, :icon, :icon_color, :position)""",
                record,
            )
            # A project made again for a folder takes back the chats a removed one left there, which
            # follow its folder again, and those in worktrees made from it.
            if folder:
                db.execute(
                    """UPDATE coding_threads SET project_id = ?, folder = NULLIF(folder, ?) WHERE project_id IS NULL
                    AND (folder = ? OR folder IN (SELECT folder FROM coding_worktrees WHERE source = ?))""",
                    (record["id"], folder, folder, folder),
                )
                # With the slash commands the removed project left them.
                db.execute(
                    """INSERT INTO coding_workflows SELECT ?, content, updated_at FROM coding_workflows
                    WHERE owner IN (SELECT 'thread:' || id FROM coding_threads WHERE project_id = ?)
                    ORDER BY updated_at DESC LIMIT 1""",
                    ("project:" + record["id"], record["id"]),
                )
        return record

    def remove_project(self, project_id: str) -> None:
        """Take a project out of the sidebar. Its chats keep its folder, and its slash commands."""
        project = self.project(project_id)
        with self.db() as db:
            # Detaching a collection must not make its ongoing conversations lose their repo.
            for row in db.execute(
                "SELECT id FROM coding_threads WHERE project_id = ?", (project_id,)
            ).fetchall():
                db.execute(
                    "UPDATE coding_threads SET folder = COALESCE(folder, ?) WHERE id = ?",
                    (project["folder"], row["id"]),
                )
                # And its slash commands, which belonged to the project: its saved ones, or the
                # defaults it used, never commands the chat had before it joined.
                db.execute("DELETE FROM coding_workflows WHERE owner = ?", ("thread:" + row["id"],))
                db.execute(
                    "INSERT INTO coding_workflows SELECT ?, content, updated_at FROM coding_workflows WHERE owner = ?",
                    ("thread:" + row["id"], "project:" + project_id),
                )
            db.execute("DELETE FROM coding_workflows WHERE owner = ?", ("project:" + project_id,))
            db.execute("DELETE FROM coding_projects WHERE id = ?", (project_id,))

    def archive_helper(self, thread_id: str, archived: bool = True) -> None:
        """A helper leaves the sidebar once its work is done, and returns for a follow-up."""
        with self.db() as db:
            db.execute(
                "UPDATE coding_threads SET archived = ?, updated_at = ? "
                "WHERE id = ? AND parent_thread_id IS NOT NULL AND archived != ?",
                (int(archived), now_iso(), thread_id, int(archived)),
            )

    def thread(self, thread_id: str):
        with self.db() as db:
            row = db.execute("SELECT * FROM coding_threads WHERE id = ?", (thread_id,)).fetchone()
            owned = db.execute(
                "SELECT cleanup_state FROM coding_worktrees WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        if row is None:
            raise KeyError("Conversation not found")
        result = self.decode(row)
        result["worktree"] = owned is not None
        folder = self.effective_folder(result)
        with self.db() as db:
            checkout = db.execute(
                "SELECT cleanup_state FROM coding_worktrees WHERE folder = ?", (folder,)
            ).fetchone()
        result["worktree_status"] = (
            ("active" if checkout["cleanup_state"] == "active" else "deleted") if checkout else None
        )
        result["worktree_available"] = bool(folder and (Path(folder) / ".git").exists())
        return result

    def effective_folder(self, thread, *, resolve=True, chosen=None, project_folders=None):
        """The folder chosen for the chat, else its own, else its project's.

        A caller going through many chats passes the `chosen` setting and `project_folders` (each
        project's folder by id), read once, instead of reading them again for every chat.
        """
        if chosen is None:
            chosen = self.services.repository.get_setting("repository_folders", {})
        folder = chosen.get(thread["id"]) or thread["folder"]
        if not folder and thread["project_id"]:
            folder = (
                project_folders[thread["project_id"]]
                if project_folders is not None
                else self.project(thread["project_id"])["folder"]
            )
        if not folder:
            return None
        path = Path(folder).expanduser()
        return str(path.resolve() if resolve else path)

    def snapshot(self):
        # The sidebar polls this. Each database is opened once, whatever the number of chats.
        with self.db() as db:
            projects = [
                dict(r) for r in db.execute("SELECT * FROM coding_projects ORDER BY position, created_at")
            ]
            threads = [
                self.decode(r)
                for r in db.execute("SELECT * FROM coding_threads ORDER BY pinned DESC, updated_at DESC")
            ]
            worktrees = [
                dict(r) for r in db.execute("SELECT thread_id, folder, cleanup_state FROM coding_worktrees")
            ]
            owned = {r["thread_id"] for r in worktrees}
            states = {
                r["folder"]: "active" if r["cleanup_state"] == "active" else "deleted" for r in worktrees
            }
            routines = self.routines.all(db)
        repo = self.services.repository
        stored = repo.get_settings(
            {"repository_folders": {}, COMMAND_ACCESS_SETTING: {}, DEFAULT_MODE_SETTING: "auto"}
        )
        summaries = repo.conversation_summaries(
            self.agent_id, [thread["conversation_id"] for thread in threads]
        )
        project_folders = {project["id"]: project["folder"] for project in projects}
        # Chats share folders: resolve each one and look for its repository once.
        located = {}
        for thread in threads:
            thread["routine"] = routines.get(thread["id"])
            thread["worktree"] = thread["id"] in owned
            thread.update(summaries[thread["conversation_id"]])
            folder = self.effective_folder(
                thread, resolve=False, chosen=stored["repository_folders"], project_folders=project_folders
            )
            if folder not in located:
                resolved = str(Path(folder).resolve()) if folder else None
                located[folder] = resolved, bool(resolved and (Path(resolved) / ".git").exists())
            thread["configured_folder"] = thread["folder"]
            thread["folder"], thread["worktree_available"] = located[folder]
            thread["worktree_status"] = states.get(thread["folder"])
        return {
            "agent_id": self.agent_id,
            "projects": projects,
            "threads": threads,
            "access": saved_access(stored[COMMAND_ACCESS_SETTING], self.agent_id),
            "default_mode": stored[DEFAULT_MODE_SETTING],
        }

    def create_thread(self, payload: ThreadInput, *, parent_thread_id=None, parent_run_id=None):
        if payload.project_id:
            self.project(payload.project_id)
        folder = self.folder(payload.folder)
        if parent_thread_id is None:
            self.allow_folder(folder)
        thread_id, timestamp = str(uuid4()), now_iso()
        conversation = self.services.repository.current_conversation(self.agent_id, thread_id)
        model = payload.model or self.services.default_model()
        with self.db() as db:
            db.execute(
                """INSERT INTO coding_threads
                (id, conversation_id, title, project_id, folder, model, mode, instructions, created_at, updated_at, parent_thread_id, parent_run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    thread_id,
                    conversation["id"],
                    payload.title.strip() or "New session",
                    payload.project_id,
                    folder,
                    model.model_dump_json(),
                    payload.mode,
                    payload.instructions,
                    timestamp,
                    timestamp,
                    parent_thread_id,
                    parent_run_id,
                ),
            )
        return self.thread(thread_id)

    def runs(self, thread_id, records=None):
        runs = (
            records
            if records is not None
            else self.services.repository.list_conversation_runs(
                self.agent_id, self.thread(thread_id)["conversation_id"]
            )
        )
        # A main task's cost includes its helpers', which run in chats of their own.
        helpers = self.services.repository.helper_usage(self.agent_id, [run["id"] for run in runs])
        return [
            {
                **{k: v for k, v in run.items() if k != "spec"},
                "events": self.services.repository.list_run_events(run["id"]),
                "checkpoints": self.checkpoints.points(thread_id, run["id"]),
                **({"helper_usage": helpers[run["id"]]} if run["id"] in helpers else {}),
            }
            for run in runs
        ]

    def attachments(self, thread_id):
        self.thread(thread_id)
        with self.db() as db:
            ids = {
                r["file_id"]
                for r in db.execute(
                    "SELECT file_id FROM coding_attachments WHERE thread_id = ?", (thread_id,)
                )
            }
        # Oldest first, so a newly attached file is added at the end of the row.
        return sorted(
            (f for f in self.services.repository.list_project_files(self.agent_id) if f["id"] in ids),
            key=lambda f: f["created_at"],
        )

    def collect_attachments(self, file_ids):
        with self.lock:
            for file_id in set(file_ids):
                with self.db() as db:
                    selected = db.execute(
                        "SELECT 1 FROM coding_attachments WHERE file_id = ?", (file_id,)
                    ).fetchone()
                if (
                    not selected
                    and not self.pending_attachments[file_id]
                    and not self.services.repository.run_references_file(self.agent_id, file_id)
                ):
                    with suppress(KeyError):  # Older history may already have lost its upload.
                        self.services.repository.delete_project_file(self.agent_id, file_id)

    @contextmanager
    def capture_attachments(self, thread_id):
        # The removal route runs in a worker thread. Pin bytes atomically with the
        # membership read until start_run has persisted its references (or failed).
        with self.lock:
            files = self.attachments(thread_id)
            ids = [file["id"] for file in files]
            self.pending_attachments.update(ids)
        try:
            yield files
        finally:
            with self.lock:
                self.pending_attachments.subtract(ids)
                self.pending_attachments += Counter()  # Discard zero counts.
                self.collect_attachments(ids)

    def release_attachments(self, thread_id, files):
        """Attachments belong to the message that sent them; the next draft starts empty."""
        with self.lock, self.db() as db:
            db.executemany(
                "DELETE FROM coding_attachments WHERE thread_id = ? AND file_id = ?",
                [(thread_id, file["id"]) for file in files],
            )

    def sent_files(self, thread):
        """Files earlier messages sent, which stay readable for the rest of the conversation.

        Image reads in the history are reloaded from these on later model calls.
        """
        return self.services.repository.conversation_reference_files(self.agent_id, thread["conversation_id"])

    async def send(
        self,
        thread_id,
        content,
        context=None,
        *,
        mode=None,
        display_text=None,
        delivery="steer",
        worktree=False,
        worktree_base=None,
        routine_report=None,
    ):
        async with self.checkpoints.locks.setdefault(thread_id, asyncio.Lock()):
            return await self._send(
                thread_id,
                content,
                context,
                mode=mode,
                display_text=display_text,
                delivery=delivery,
                worktree=worktree,
                worktree_base=worktree_base,
                routine_report=routine_report,
            )

    async def _send(
        self,
        thread_id,
        content,
        context=None,
        *,
        mode=None,
        display_text=None,
        delivery="steer",
        worktree=False,
        worktree_base=None,
        routine_report=None,
    ):
        await self.checkpoints.recover(thread_id)
        if worktree:
            if self.runs(thread_id):
                raise ValueError("Choose a worktree before starting this chat, or fork its existing history.")
            owned = self.delivery.worktree(thread_id)
            if owned and worktree_base is not None:
                raise ValueError(
                    "This chat already has a worktree. Refresh the chat before retrying; its base branch cannot be changed."
                )
            if not owned:
                await self.checkpoints.new_worktree(thread_id, base=worktree_base or "HEAD")
        thread = self.thread(thread_id)
        context = context or TaskContext()
        if thread.get("parent_thread_id"):
            raise ValueError(
                "Ask the parent chat to continue this helper, or fork it to start an independent chat."
            )
        display_text = display_text if display_text is not None else content or f"/{context.command}"
        if mode is not None:
            thread = {**thread, "mode": mode}
        if thread["archived"]:
            raise ValueError("Restore this conversation before sending a message.")
        await self.cleanup.restore(thread_id)
        thread = self.thread(thread_id)
        if mode is not None:
            thread = {**thread, "mode": mode}
        self.cleanup.touch(self.effective_folder(thread))
        repo = self.services.repository
        workflow = self.context.workflow(thread_id)
        prompt = CODING_PROMPT + "\n\nConversation instructions:\n" + thread["instructions"]
        if thread["mode"] == "plan":
            prompt += "\nPlan mode: inspect and explain. Do not modify files or execute commands."
        plan_tools = {
            "read_file",
            "find_files",
            "search_files",
            "read_documents",
            "list_documents",
            "search_documents",
            "web_search",
            "fetch_page",
            "calculator",
        }
        folder = self.effective_folder(thread)
        # By default a message goes to the chat's current task: steering while it runs, a reply
        # while it waits on the user. Otherwise it is a follow-up queued behind it.
        pending = (
            repo.pending_conversation_runs(self.agent_id, thread["conversation_id"])
            if delivery == "steer"
            else []
        )
        active = next((run for run in pending if run["status"] == "running"), None) or next(
            (run for run in pending if run["status"].startswith("waiting")), None
        )
        if active:
            # A live task keeps its captured configuration even if the chat settings changed.
            captured_spec = repo.get_run(active["id"])["spec"]
            folder = captured_spec.get("working_directory")
        connectors = self.services.connectors
        if not active:
            # A connector that has not answered since the app started (discovery still running)
            # is asked now, or this new task would run without its tools and never say so. A
            # message to a running task keeps the tools that task started with, so it goes at once.
            unanswered = [
                server
                for server in connectors.servers()
                if server.enabled and connectors.cached(server.name) is None
            ]
            if unanswered:
                await asyncio.gather(*(connectors.refresh(server) for server in unanswered))
        names = set(TOOL_REGISTRY) | set(connectors.tools())
        self.checkout_access(folder)
        inventory = await asyncio.to_thread(
            discover, folder, self.services.skills, grants=self.services.grants()
        )
        context_inventory = (
            {"skills": [{"name": name, "active": True} for name in captured_spec["skills"]]}
            if active
            else inventory
        )
        content, captured = await self.context.expand(
            thread_id, content, context, context_inventory, folder=folder, workflow=workflow
        )
        coding_context = {
            "files": [
                {k: f[k] for k in ("path", "start_line", "end_line", "version", "folder")} for f in captured
            ],
            "skills": context.skills,
            "command": context.command,
        }
        if active:
            with self.capture_attachments(thread_id) as attachments:
                try:
                    deliver = (
                        self.services.runtime.steer
                        if active["status"] == "running"
                        else self.services.runtime.reply
                    )
                    run = await deliver(
                        active["id"],
                        content,
                        display_text=display_text,
                        coding_context=coding_context,
                        reference_files=attachments,
                    )
                    self.release_attachments(thread_id, attachments)
                    # A follow-up queued earlier runs after this message, so it can read these files too,
                    # including one that started while this message was being delivered.
                    for queued in self.services.repository.pending_conversation_runs(
                        self.agent_id, thread["conversation_id"]
                    ):
                        if attachments and queued["id"] != active["id"]:
                            self.services.repository.capture_run_files(queued["id"], attachments)
                            # A queued run's tools were built at admission; extend its live list too.
                            if live := self.services.runtime.active.get(queued["id"]):
                                known = {row["id"] for row in live.reference_files}
                                live.reference_files.extend(
                                    row for row in attachments if row["id"] not in known
                                )
                    with self.db() as db:
                        db.execute(
                            "UPDATE coding_threads SET updated_at = ? WHERE id = ?", (now_iso(), thread_id)
                        )
                    return {k: v for k, v in run.items() if k != "spec"}
                except ValueError:
                    # The final model response may have settled while context was captured.
                    # Preserve the submitted message as a queued follow-up on that same chat.
                    pass
        skills = [row for row in inventory["skills"] if row["active"]]
        sources = {
            s["name"]: self.services.skills.capture(s["_text"]) for s in skills if s["_text"] is not None
        }
        spec = AgentSpecV2(
            name="Wavefunction",
            description="Your coding agent",
            system_prompt=prompt,
            model=ModelSpec.model_validate(thread["model"]),
            working_directory=folder,
            tools=[
                ToolBinding(
                    name=n,
                    policy=ToolPolicy.DENY
                    if thread["mode"] == "plan" and n not in plan_tools
                    else ToolPolicy.ALLOW,
                )
                for n in sorted(names)
            ],
            skills=[s["name"] for s in skills],
            skill_versions={s["name"]: s["version"] for s in skills if s["source"] == "library"},
            skill_sources=sources,
            skill_paths={s["name"]: s["path"] for s in skills if s["name"] in sources},
        )
        if active:
            spec = load_spec(captured_spec)
        with self.capture_attachments(thread_id) as attachments:
            new = {file["id"] for file in attachments}
            run = await self.services.runtime.start_run(
                self.agent_id,
                content,
                spec=spec,
                conversation_id=thread["conversation_id"],
                desktop_copy=thread["id"],
                reference_files=[*(f for f in self.sent_files(thread) if f["id"] not in new), *attachments],
                message_files=attachments,
                display_text=display_text,
                coding_context=coding_context,
                queue_if_busy=True,
                routine_report=routine_report,
            )
            self.release_attachments(thread_id, attachments)
        with self.db() as db:
            db.execute(
                "UPDATE coding_threads SET title = ?, updated_at = ? WHERE id = ?",
                (
                    # The first line with words: a message can start with a blank line.
                    next((line.strip() for line in display_text.splitlines() if line.strip()), "New session")[
                        :80
                    ]
                    if thread["title"] in {"New session", "New chat", "New conversation"}
                    else thread["title"],
                    now_iso(),
                    thread_id,
                ),
            )
        return {k: v for k, v in run.items() if k != "spec"}

    async def compact(self, thread_id):
        """Summarise the chat's conversation now, as it is when it fills up, in a run of its own."""
        async with self.checkpoints.locks.setdefault(thread_id, asyncio.Lock()):
            await self.checkpoints.recover(thread_id)
            thread = self.thread(thread_id)
            if thread.get("parent_thread_id"):
                raise ValueError("A helper's context is managed by its parent chat.")
            if thread["archived"]:
                raise ValueError("Restore this conversation before compacting it.")
            repo = self.services.repository
            if repo.pending_conversation_runs(self.agent_id, thread["conversation_id"]):
                raise ValueError("Wait for the current task to finish, then compact.")
            runs = [
                repo.get_run(run["id"])
                for run in reversed(repo.list_conversation_runs(self.agent_id, thread["conversation_id"]))
            ]
            last = next((full for full in runs if full.get("spec")), None)
            update: dict[str, Any] = {"model": ModelSpec.model_validate(self.thread(thread_id)["model"])}
            if last is None:
                # A fork or rewind copies tasks without their specs; the tasks they came from keep
                # theirs. Followed back, through forks of forks, to the newest one still saved.
                last = next(filter(None, map(self._original_with_spec, runs)), None)
                update["working_directory"] = self.effective_folder(thread)
            if last is None:
                raise ValueError("There is nothing to compact yet.")
            await self.cleanup.restore(thread_id)
            # The last task's tools and skills, with the model the next message will use: the
            # summary is sized for that model's window.
            spec = load_spec(last["spec"]).model_copy(update=update)
            run = await self.services.runtime.start_run(
                self.agent_id,
                "Compact the conversation.",
                spec=spec,
                kind="compact",
                conversation_id=thread["conversation_id"],
                desktop_copy=thread["id"],
                display_text="Compact context",
            )
        return {k: v for k, v in run.items() if k != "spec"}

    def _original_with_spec(self, run):
        """The task a copied task came from, following forks of forks, if one still has its spec."""
        seen = set()
        while (origin := (run.get("input") or {}).get("forked_from")) and origin not in seen:
            seen.add(origin)
            try:
                run = self.services.repository.get_run(origin)
            except KeyError:
                return None  # The original chat was deleted.
            if run.get("spec"):
                return run
        return None

    def root(self, thread_id):
        thread = self.thread(thread_id)
        if thread.get("worktree_status") == "deleted":
            raise ValueError("This worktree was deleted after inactivity. Continue the chat to recreate it.")
        folder = self.effective_folder(thread, resolve=False)
        if not folder:
            raise ValueError("Choose a repository folder first.")
        self.checkout_access(folder)
        # A folder was given, so it resolves to one.
        return Path(cast(str, self.folder(folder)))

    def file_path(self, thread_id, path, expected_folder=None):
        root = self.root(thread_id)
        if expected_folder is not None and str(root) != expected_folder:
            raise ValueError(
                "The repository folder changed. Your draft is preserved; reopen the file in its original repository."
            )
        target = (root / path).resolve()
        if not target.is_relative_to(root) or ".git" in target.relative_to(root).parts:
            raise ValueError("The file must be inside the chosen repository, outside .git.")
        return target

    def worktree_source(self, folder):
        """The repository a managed worktree was created from, or None for any other folder."""
        with self.db() as db:
            row = db.execute(
                "SELECT source FROM coding_worktrees WHERE folder = ?", (str(folder),)
            ).fetchone()
        return row["source"] if row else None

    def checkout_access(self, folder=None):
        grants = self.services.grants()
        owner = None
        if folder is not None:
            folder = Path(folder)
            with self.db() as db:
                owner = db.execute(
                    "SELECT w.thread_id FROM coding_worktrees w JOIN coding_threads t ON t.id = w.thread_id "
                    "WHERE w.folder = ? AND t.parent_run_id IS NOT NULL",
                    (str(folder),),
                ).fetchone()
            if owner:
                # Git filters/hooks are helper-controlled code, even when the UI
                # reads status or the backend captures a checkpoint.
                grants = [Grant(g.path, g.writable and g.path.is_relative_to(folder)) for g in grants]
        return self.worktree_grants(folder, grants), owner["thread_id"] if owner else None

    def worktree_grants(self, folder, grants):
        """Add a managed worktree's shared Git metadata once its registration is verified."""
        source = self.worktree_source(folder) if folder is not None else None
        return linked_worktree_grants(Path(folder), grants, Path(source)) if source else grants

    def runner(self, folder=None):
        grants, owner = self.checkout_access(folder)
        scratch = scratch_for(self.services.settings.agent_dir) / "workbench"
        if owner:
            scratch /= owner
        runner = runner_for(command_access(self.services.repository, self.agent_id), grants, scratch)
        runner.private_roots = [self.services.settings.data_dir.resolve()]
        runner.restrict_reads = True
        return runner


# Routes close over a service getter so tests and reset use the current isolated stack.
def register_coding_workspace(app, get_services, auth):
    class CodingRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()

            async def handle(request):
                try:
                    return await handler(request)
                except KeyError as error:
                    raise HTTPException(404, str(error).strip("'")) from error
                except (ValueError, PermissionError, OSError) as error:
                    raise HTTPException(
                        409 if isinstance(error, PermissionError) else 422, str(error)
                    ) from error

            return handle

    router = APIRouter(prefix="/api/coding", dependencies=[Depends(auth)], route_class=CodingRoute)

    def workspace():
        return get_services().coding_workspace

    @router.get("/workspace", response_model=api.WorkspaceSnapshot)
    def snapshot(background_tasks: BackgroundTasks):
        services = get_services()
        snapshot = services.coding_workspace.snapshot()
        background_tasks.add_task(services.start_optional_warmup)
        return snapshot

    @router.get("/analytics", response_model=api.Analytics)
    def analytics():
        ws = workspace()
        return ws.services.repository.run_analytics(ws.agent_id)

    @router.get("/skills", response_model=api.SkillInventory)
    async def skill_inventory(thread_id: str | None = None):
        ws = workspace()
        folder = ws.effective_folder(ws.thread(thread_id)) if thread_id else None
        return public_inventory(
            await asyncio.to_thread(discover, folder, ws.services.skills, grants=ws.services.grants())
        )

    @router.get("/skill-catalog", response_model=api.SkillCatalog)
    async def skill_catalog():
        ws = workspace()
        with ws.db() as db:
            projects = [
                dict(r)
                for r in db.execute(
                    "SELECT id, name, folder FROM coding_projects ORDER BY position, created_at"
                )
            ]
        return public_inventory(
            await asyncio.to_thread(catalog, ws.services.skills, projects, grants=ws.services.grants())
        )

    def writable(path):
        """Settings writes repository files only where the folder is still granted for writing."""
        if not any(
            grant.writable and path.is_relative_to(grant.path) for grant in workspace().services.grants()
        ):
            raise ValueError("This project's folder is not writable. Allow it in Settings → Access first.")

    def project_skill(project_id, path, digest):
        """An existing repository skill file of this project, unchanged since it was read."""
        import hashlib

        folder = Path(workspace().project(project_id)["folder"] or "").resolve()
        target = Path(path or "")
        if not target.is_absolute() or target.is_symlink() or target.name != "SKILL.md":
            raise ValueError("Choose a skill file in this project.")
        resolved = target.resolve()
        # The same roots discovery reads: the folder and its parents up to the checkout root.
        boundary = next((p for p in (folder, *folder.parents) if (p / ".git").exists()), folder)
        bases = [p for p in (folder, *folder.parents) if p.is_relative_to(boundary)]
        if (
            not any(resolved.is_relative_to(base / root) for base in bases for root in LOCAL_ROOTS)
            or not resolved.is_file()
        ):
            raise ValueError("Choose a skill file in this project.")
        writable(resolved)
        if hashlib.sha256(resolved.read_bytes()).hexdigest() != digest:
            raise ValueError("This skill changed since it was opened. Reload it before saving.")
        return resolved

    def kept_frontmatter(text):
        """Frontmatter lines other than name and description, which an edit must not drop."""
        match = FRONTMATTER.match(text)
        kept, skip = [], False
        for line in match.group(1).splitlines() if match else []:
            if line[:1] not in (" ", "\t"):
                skip = line.split(":", 1)[0].strip() in {"name", "description"}
            if not skip:
                kept.append(line)
        return kept

    def skill_text(name, description, body, extra=()):
        """A valid SKILL.md small enough for discovery to read back."""
        validate_skill(name, description)
        text = write_skill_markdown(name, description, body)
        if extra:
            text = text.replace("\n---\n", "\n" + "\n".join(extra) + "\n---\n", 1)
        if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
            raise ValueError("A skill can be at most 256 KiB.")
        return text

    @router.post("/projects/{project_id}/skills", status_code=201, response_model=api.SkillFile)
    async def create_project_skill(project_id: str, payload: ProjectSkillInput):
        folder = workspace().project(project_id)["folder"]
        if not folder:
            raise ValueError("This project has no repository folder.")
        name = normalise_name(payload.name)
        text = skill_text(name, payload.description, payload.body)
        root = Path(folder).resolve()
        target = root / ".agents/skills" / name / "SKILL.md"
        # A linked folder or file would carry the write outside the repository.
        if any(
            part.is_symlink() for part in (root / ".agents", root / ".agents/skills", target.parent, target)
        ):
            raise ValueError("Skill folders in this project cannot be links.")
        if target.exists():
            raise ValueError(f"This project already has a skill called {name}.")
        writable(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return {"name": name, "path": str(target)}

    @router.put("/projects/{project_id}/skills", response_model=api.SkillFile)
    async def update_project_skill(project_id: str, payload: ProjectSkillInput):
        from .skills import Skill

        target = project_skill(project_id, payload.path, payload.digest)
        text = target.read_text(encoding="utf-8")
        name = Skill.parse(text, str(target)).name
        target.write_text(
            skill_text(name, payload.description, payload.body, kept_frontmatter(text)), encoding="utf-8"
        )
        return {"name": name, "path": str(target)}

    @router.delete("/projects/{project_id}/skills", status_code=204)
    async def delete_project_skill(project_id: str, path: str, digest: str):
        target = project_skill(project_id, path, digest)
        target.unlink()
        # Only the skill's own folder, and only when nothing else is left in it.
        with suppress(OSError):
            target.parent.rmdir()

    @router.put("/models", response_model=api.ModelChoice)
    def configure_model(payload: ModelSpec):
        ws = workspace()
        ws.services.models.validate_reasoning(payload)
        if not payload.model.strip():
            raise ValueError("Enter the provider's model ID.")
        if payload.provider == "openai-compatible":
            url = urlsplit(payload.base_url or "")
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ValueError("A compatible model needs its server's HTTP API base URL.")
        model = payload.model_dump(mode="json")
        key = f"{payload.provider}::{payload.model}"
        repo = ws.services.repository
        configured = repo.get_setting("coding_models", [])
        repo.set_setting(
            "coding_models", [m for m in configured if f"{m['provider']}::{m['model']}" != key] + [model]
        )
        repo.set_setting("default_model", model)
        enabled = repo.get_setting("enabled_models", [])
        repo.set_setting("enabled_models", list(dict.fromkeys([*enabled, key])))
        return model

    @router.post("/projects", status_code=201, response_model=api.Project)
    def create_project(payload: ProjectInput):
        return workspace().create_project(payload)

    @router.put("/projects/order", response_model=list[api.Project])
    def order_projects(payload: ProjectOrder):
        ws = workspace()
        with ws.db() as db:
            existing = {row["id"] for row in db.execute("SELECT id FROM coding_projects")}
            if len(payload.ids) != len(existing) or set(payload.ids) != existing:
                raise ValueError("List every project exactly once to reorder them.")
            db.executemany(
                "UPDATE coding_projects SET position = ? WHERE id = ?",
                [(index, project_id) for index, project_id in enumerate(payload.ids)],
            )
            return [
                dict(r) for r in db.execute("SELECT * FROM coding_projects ORDER BY position, created_at")
            ]

    @router.patch("/projects/{project_id}", response_model=api.Project)
    async def edit_project(project_id: str, payload: ProjectInput):
        ws = workspace()
        previous = ws.project(project_id)
        destination = str(Path(payload.folder).expanduser().resolve()) if payload.folder else None
        async with AsyncExitStack() as locks:
            for folder in sorted({folder for folder in (previous["folder"], destination) if folder}):
                await locks.enter_async_context(ws.reviews.locks.setdefault(folder, asyncio.Lock()))
            previous = ws.project(project_id)
            folder = ws.folder(payload.folder)
            if not payload.name.strip():
                raise ValueError("Give the project a name.")
            ws.allow_folder(folder)
            with ws.db() as db:
                db.execute(
                    "UPDATE coding_projects SET name = ?, folder = ?, icon = ?, icon_color = ? WHERE id = ?",
                    (
                        payload.name.strip(),
                        folder,
                        payload.icon if "icon" in payload.model_fields_set else previous["icon"],
                        payload.icon_color
                        if "icon_color" in payload.model_fields_set
                        else previous["icon_color"],
                        project_id,
                    ),
                )
            return ws.project(project_id)

    @router.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str):
        workspace().remove_project(project_id)

    @router.post("/threads", status_code=201, response_model=api.Thread)
    async def create_thread(payload: ThreadInput):
        ws = workspace()
        # A new chat starts in the mode last chosen; forks, helpers and routines copy their source chat.
        if "mode" not in payload.model_fields_set:
            payload.mode = ws.services.repository.get_setting(DEFAULT_MODE_SETTING, "auto")
        destination = payload.folder or (
            ws.project(payload.project_id)["folder"] if payload.project_id else None
        )
        destination = str(Path(destination).expanduser().resolve()) if destination else None
        async with AsyncExitStack() as locks:
            if destination:
                await locks.enter_async_context(ws.reviews.locks.setdefault(destination, asyncio.Lock()))
            current = payload.folder or (
                ws.project(payload.project_id)["folder"] if payload.project_id else None
            )
            current = str(Path(current).expanduser().resolve()) if current else None
            if current != destination:
                raise ValueError("Repository folder changed; try again.")
            ws.folder(destination)
            thread = ws.create_thread(payload)
        if payload.worktree:
            try:
                await ws.checkpoints.new_worktree(thread["id"], base=payload.worktree_base)
            except BaseException:
                if not ws.delivery.worktree(thread["id"]):
                    await ws.checkpoints.discard(thread["id"])
                raise
        return ws.thread(thread["id"])

    @router.get("/threads/{thread_id}", response_model=api.ThreadDetail)
    async def get_thread(thread_id: str):
        ws = workspace()
        await ws.checkpoints.recover(thread_id)
        thread = ws.thread(thread_id)
        page = ws.services.repository.conversation_page(ws.agent_id, thread["conversation_id"])
        return {
            **thread,
            "configured_folder": thread["folder"],
            "folder": ws.effective_folder(thread),
            "runs": ws.runs(thread_id, page["runs"]),
            "older_cursor": page["older_cursor"],
            "attachments": ws.attachments(thread_id),
        }

    @router.post("/threads/{thread_id}/activity", status_code=204)
    async def touch_thread(thread_id: str):
        ws = workspace()
        async with ws.checkpoints.locks.setdefault(thread_id, asyncio.Lock()):
            folder = ws.effective_folder(ws.thread(thread_id))
            if folder:
                # Read-only helpers share their owner's checkout and retention.
                # Serialize activity with cleanup even when the chat IDs differ.
                async with ws.reviews.locks.setdefault(folder, asyncio.Lock()):
                    ws.cleanup.touch(folder)

    @router.get("/worktree-cleanup", response_model=api.WorktreeCleanup)
    def cleanup_settings():
        return workspace().cleanup.settings().model_dump()

    @router.put("/worktree-cleanup", response_model=api.WorktreeCleanup)
    async def save_cleanup_settings(payload: CleanupSettings):
        ws = workspace()
        async with ws.cleanup.settings_lock:
            ws.services.repository.set_setting("worktree_retention_days", payload.retention_days)
        return payload.model_dump()

    @router.get("/threads/{thread_id}/history", response_model=api.RunPage)
    def history(thread_id: str, before: str, limit: int = Query(default=50, ge=1, le=100)):
        ws = workspace()
        page = ws.services.repository.conversation_page(
            ws.agent_id, ws.thread(thread_id)["conversation_id"], before=before, limit=limit
        )
        return {**page, "runs": ws.runs(thread_id, page["runs"])}

    @router.post("/threads/{thread_id}/fork", status_code=201, response_model=api.ForkedThread)
    async def fork_thread(thread_id: str, payload: ForkInput | None = None):
        payload = payload or ForkInput()
        return await workspace().checkpoints.fork(thread_id, payload.checkpoint_id, payload.worktree)

    @router.post("/threads/{thread_id}/rewind", response_model=api.Rewound)
    async def rewind_thread(thread_id: str, payload: RewindInput):
        return await workspace().checkpoints.rewind(thread_id, payload.checkpoint_id)

    @router.get("/search", response_model=list[api.SearchResult])
    def search(query: str):
        ws = workspace()
        needle = query.strip().casefold()[:500]
        matches = []
        # Messages sent while a task ran, as steering or replies, read in one query.
        steered: dict[str, list[str]] = {}
        with ws.services.repository._history(ws.agent_id) as db:
            for run_id, text in db.execute(
                "SELECT run_id, COALESCE(json_extract(payload_json, '$.display_text'), json_extract(payload_json, '$.text')) "
                "FROM run_events WHERE event_type = 'steering_requested' ORDER BY sequence"
            ):
                steered.setdefault(run_id, []).append(str(text or ""))
        for thread in ws.snapshot()["threads"]:
            content = thread["title"] + "\n" + thread["instructions"]
            for run in ws.services.repository.list_conversation_runs(ws.agent_id, thread["conversation_id"]):
                content += (
                    "\n"
                    # What the user wrote, not the model's input with its attached files and
                    # expanded command templates.
                    + run["input"].get("display_text", run["input"].get("text", ""))
                    + "\n"
                    + (run.get("output") or {}).get("text", "")
                )
                content += "".join("\n" + text for text in steered.get(run["id"], ()))
            at = find_folded(content, needle)
            if at >= 0:
                matches.append({**thread, "excerpt": content[max(0, at - 40) : at + 160]})
        return matches[:100]

    @router.patch("/threads/{thread_id}", response_model=api.Thread)
    async def edit_thread(thread_id: str, payload: ThreadPatch):
        ws = workspace()
        async with ws.checkpoints.locks.setdefault(thread_id, asyncio.Lock()):
            thread = ws.thread(thread_id)
            updates = payload.model_dump(exclude_unset=True)

            # Chat settings send the folder with every save. Unchanged, it is not a new choice: it
            # must not drop a folder picked on the agent's card, or re-check a removed checkout.
            def stored(value):
                return str(Path(value).expanduser().resolve()) if value else None

            if "folder" in updates and stored(updates["folder"]) == stored(thread["folder"]):
                del updates["folder"]
            target = {**thread, **updates}
            folder = ws.effective_folder(thread)
            if "folder" in updates:
                # An explicit folder update clears the separately chosen folder.
                destination = target["folder"] or (
                    ws.project(target["project_id"])["folder"] if target["project_id"] else None
                )
                destination = str(Path(destination).expanduser().resolve()) if destination else None
            else:
                destination = ws.effective_folder(target)
            # Pins and incoming shared chats protect their checkouts. Sort both
            # folder locks so two chats moving in opposite directions cannot deadlock.
            async with AsyncExitStack() as locks:
                for path in sorted({path for path in (folder, destination) if path}):
                    await locks.enter_async_context(ws.reviews.locks.setdefault(path, asyncio.Lock()))
                if "folder" in updates:
                    current = target["folder"] or (
                        ws.project(target["project_id"])["folder"] if target["project_id"] else None
                    )
                    current = str(Path(current).expanduser().resolve()) if current else None
                else:
                    current = ws.effective_folder(target)
                if current != destination:
                    raise ValueError("Repository folder changed; try again.")
                if "project_id" in updates and updates["project_id"]:
                    ws.project(updates["project_id"])
                if destination != folder and {"folder", "project_id"} & updates.keys():
                    # Cleanup may have completed while this request waited.
                    ws.folder(destination)
                if "folder" in updates:
                    owned = ws.delivery.worktree(thread_id)
                    if owned and updates["folder"] != owned["folder"]:
                        raise ValueError(
                            "Remove the managed worktree before changing this conversation's folder. Its branch will be preserved."
                        )
                    updates["folder"] = ws.folder(updates["folder"])
                    ws.allow_folder(updates["folder"])
                    chosen = ws.services.repository.get_setting("repository_folders", {})
                    if thread_id in chosen:
                        ws.services.repository.set_setting(
                            "repository_folders", {k: v for k, v in chosen.items() if k != thread_id}
                        )
                if "model" in updates:
                    if updates["model"] is None:
                        raise ValueError("Choose a model.")
                    updates["model"] = json.dumps(updates["model"])
                if any(v is None for k, v in updates.items() if k not in {"project_id", "folder"}):
                    raise ValueError("That setting cannot be empty.")
                updates["updated_at"] = now_iso()
                with ws.db() as db:
                    db.execute(
                        "UPDATE coding_threads SET "
                        + ", ".join(k + " = ?" for k in updates)
                        + " WHERE id = ?",
                        (*updates.values(), thread_id),
                    )
                return ws.thread(thread_id)

    @router.delete("/threads/{thread_id}", status_code=204)
    async def delete_thread(thread_id: str, delete_worktree: bool = False):
        ws = workspace()
        with ws.db() as db:
            helpers = [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM coding_threads WHERE parent_thread_id = ?", (thread_id,)
                )
            ]
        # Its helpers go with it: the confirmation covers their checkouts too.
        if not delete_worktree and any(
            (owned := ws.delivery.worktree(chat)) and owned["cleanup_state"] != "deleted"
            for chat in [thread_id, *helpers]
        ):
            raise ValueError(
                "Confirm deletion of this chat and its managed worktree. Uncommitted, untracked, and ignored files will be permanently deleted; its Git branch will be kept."
            )
        # The chat first: what can refuse its deletion does so before any helper is gone.
        await delete_chat(ws, thread_id)
        for helper in helpers:
            await delete_chat(ws, helper)

    async def delete_chat(ws, thread_id):
        async with ws.checkpoints.locks.setdefault(thread_id, asyncio.Lock()):
            owned = ws.delivery.worktree(thread_id)
            thread = ws.thread(thread_id)
            runs = ws.runs(thread_id)
            for run in runs:
                task = ws.services.runtime.tasks.get(run["id"])
                if run["status"] in ACTIVE or (task is not None and not task.done()):
                    await ws.services.runtime.cancel(run["id"])
                else:
                    await ws.services.runtime._stop_helpers(run["id"])
            # Settle producers before collecting terminals, including in-flight launches.
            await ws.terminals.forget(thread_id)
            if owned:
                await ws.delivery.delete_for_chat(thread_id)
            # Remove only this conversation's checkpoints and history, preserving the one shared agent.
            saver = await ws.services.runtime.checkpointer()
            await ws.checkpoints.forget(thread_id)
            await saver.adelete_thread(thread["conversation_id"])
            file_ids = {file["id"] for file in ws.attachments(thread_id)}
            file_ids.update(file["id"] for run in runs for file in run["input"].get("reference_files", []))
            # Screenshot folders this history uses: its own, and those it shares with the chat
            # it was forked or rewound from.
            shots = {screenshot_folder(thread["conversation_id"])}
            with ws.services.repository._history(ws.agent_id) as db:
                ids = [run["id"] for run in runs]
                for run_id in ids:
                    shots.update(
                        str(row[0]).split("/")[0]
                        for row in db.execute(
                            "SELECT json_extract(payload_json, '$.file') FROM run_events WHERE run_id = ? AND event_type = 'screenshot'",
                            (run_id,),
                        )
                        if row[0]
                    )
                for run_id in ids:
                    db.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
                    db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
                db.execute("DELETE FROM conversations WHERE id = ?", (thread["conversation_id"],))
            with ws.db() as db:
                db.execute("DELETE FROM coding_threads WHERE id = ?", (thread_id,))
            ws.collect_attachments(file_ids)
            chosen = ws.services.repository.get_setting("repository_folders", {})
            if thread_id in chosen:
                ws.services.repository.set_setting(
                    "repository_folders", {k: v for k, v in chosen.items() if k != thread_id}
                )
            # Its computer-use screenshots go with it, unless a fork or rewind still shows them.
            with ws.services.repository._history(ws.agent_id) as db:
                kept = {
                    folder
                    for folder in shots
                    if db.execute(
                        "SELECT 1 FROM run_events WHERE event_type = 'screenshot' AND json_extract(payload_json, '$.file') LIKE ? LIMIT 1",
                        (folder + "/%",),
                    ).fetchone()
                }
            for folder in shots - kept:
                shutil.rmtree(screenshots_root(ws.services.files, ws.agent_id) / folder, ignore_errors=True)
            await ws.services.computer.forget([thread_id])

    @router.post("/threads/{thread_id}/messages", status_code=202, response_model=api.Run)
    async def send(thread_id: str, payload: MessageInput):
        if not payload.content.strip() and not payload.context.command:
            raise ValueError("Write a message first.")
        return await workspace().send(
            thread_id,
            payload.content,
            payload.context,
            delivery=payload.delivery,
            worktree=payload.worktree,
            worktree_base=payload.worktree_base,
        )

    @router.patch("/threads/{thread_id}/routine", response_model=api.Routine)
    def update_routine(thread_id: str, payload: RoutinePatch):
        return workspace().routines.update(thread_id, payload.model_dump(exclude_unset=True))

    @router.delete("/threads/{thread_id}/routine", status_code=204)
    def stop_routine(thread_id: str):
        workspace().routines.stop(thread_id)

    @router.post("/threads/{thread_id}/compact", status_code=202, response_model=api.Run)
    async def compact(thread_id: str):
        return await workspace().compact(thread_id)

    @router.post("/threads/{thread_id}/attachments", status_code=201, response_model=api.UploadedFile)
    async def attach(thread_id: str, file: Annotated[UploadFile, File()]):
        ws = workspace()
        ws.thread(thread_id)
        record = await ws.services.files.save_upload(ws.agent_id, file)
        with ws.db() as db:
            db.execute(
                "INSERT INTO coding_attachments VALUES (?, ?, ?)", (str(uuid4()), thread_id, record["id"])
            )
        return record

    @router.get("/threads/{thread_id}/attachments/{file_id}")
    async def attachment_content(thread_id: str, file_id: str):
        """A small preview of an image in this conversation's draft or sent messages."""
        ws = workspace()
        repo = ws.services.repository
        with ws.db() as db:
            drafted = db.execute(
                "SELECT 1 FROM coding_attachments WHERE thread_id = ? AND file_id = ?", (thread_id, file_id)
            ).fetchone()
        record = (
            repo.get_project_file(ws.agent_id, file_id)
            if drafted
            else repo.conversation_file(ws.agent_id, ws.thread(thread_id)["conversation_id"], file_id)
        )
        if record is None:
            raise KeyError("Attachment not found in this conversation")
        if not record["media_type"].startswith("image/"):
            raise ValueError("Only image attachments have a preview.")
        root = ws.services.files.project_dir(ws.agent_id).resolve()
        path = (root / record["relative_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise KeyError("Attachment not found in this conversation")
        stat = path.stat()
        # Never the original: a large photo would be buffered whole by the webview.
        thumbnail = await asyncio.to_thread(preview_png, path, stat.st_mtime_ns, stat.st_size)
        return Response(thumbnail, media_type="image/png")

    @router.delete("/threads/{thread_id}/attachments/{file_id}", status_code=204)
    def remove_attachment(thread_id: str, file_id: str):
        ws = workspace()
        if file_id not in {f["id"] for f in ws.attachments(thread_id)}:
            raise KeyError("Attachment not found in this conversation")
        with ws.db() as db:
            db.execute(
                "DELETE FROM coding_attachments WHERE thread_id = ? AND file_id = ?", (thread_id, file_id)
            )
        ws.collect_attachments([file_id])

    @router.get("/threads/{thread_id}/tree", response_model=list[api.TreeEntry])
    async def tree(thread_id: str, path: str = ""):
        ws = workspace()
        root = ws.root(thread_id)
        target = ws.file_path(thread_id, path)

        result = await file_operation(
            ws.runner(root), {"op": "directory_tree", "root": str(root), "path": str(target)}
        )
        return result["entries"]

    @router.get("/threads/{thread_id}/file", response_model=api.FileText)
    async def read_file(thread_id: str, path: str, folder: str | None = None):
        ws = workspace()
        target = ws.file_path(thread_id, path, folder)
        return await file_operation(
            ws.runner(ws.root(thread_id)),
            {
                "op": "read",
                "path": str(target),
                "offset": 0,
                "limit": 1000000,
                "start_line": None,
                "line_count": 100000,
                "text_only": True,
            },
        )

    @router.put("/threads/{thread_id}/file", response_model=api.FileText)
    async def write_file(thread_id: str, payload: FileWrite):
        ws = workspace()
        folder = ws.effective_folder(ws.thread(thread_id))
        async with ws.reviews.locks.setdefault(folder, asyncio.Lock()):
            if ws.thread(thread_id)["mode"] == "plan":
                raise ValueError("Switch out of Plan mode to edit files.")
            target = ws.file_path(thread_id, payload.path, payload.folder)
            ws.cleanup.touch(folder)
            return await file_operation(
                ws.runner(ws.root(thread_id)),
                {"op": "write", "path": str(target), "content": payload.content, "expected": payload.version},
            )

    async def command(ws, thread_id, text, timeout=30):
        root = ws.root(thread_id)
        result = await ws.runner(root).run(text, cwd=root, timeout=timeout)
        try:
            return {"stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code}
        finally:
            for spool in (result.stdout_file, result.stderr_file):
                if spool:
                    spool.unlink(missing_ok=True)

    @router.post("/threads/{thread_id}/terminal")
    async def terminal(thread_id: str, payload: CommandInput):
        ws = workspace()
        folder = ws.effective_folder(ws.thread(thread_id))
        async with ws.reviews.locks.setdefault(folder, asyncio.Lock()):
            if ws.thread(thread_id)["mode"] == "plan":
                raise ValueError("Switch out of Plan mode to run commands.")
            ws.cleanup.touch(folder)
            return await command(ws, thread_id, payload.command, payload.timeout)

    @router.get("/threads/{thread_id}/terminals", response_model=list[api.TerminalSession])
    async def terminals(thread_id: str):
        ws = workspace()
        ws.thread(thread_id)
        return await ws.terminals.list(thread_id)

    @router.post("/threads/{thread_id}/terminals", status_code=201, response_model=api.TerminalSession)
    async def create_terminal(thread_id: str, payload: TerminalInput):
        ws = workspace()
        folder = ws.effective_folder(ws.thread(thread_id))
        async with ws.reviews.locks.setdefault(folder, asyncio.Lock()):
            ws.terminal_signature(thread_id)
            root = ws.root(thread_id)
            runner = ws.runner(root)
            if runner.network:
                runner.env = await ws.services.github_proxy.environment()
            ws.cleanup.touch(folder)
            session = await ws.terminals.create(thread_id, runner, root, **payload.model_dump())
            return session.metadata()

    @router.get("/threads/{thread_id}/terminals/{terminal_id}/output", response_model=api.TerminalOutput)
    async def terminal_output(thread_id: str, terminal_id: str, cursor: int = 0, wait: float = 0):
        ws = workspace()
        ws.thread(thread_id)
        if not 0 <= wait <= 20:
            raise ValueError("Terminal wait must be between 0 and 20 seconds")
        session = await ws.terminals.get(thread_id, terminal_id)
        return await session.output(cursor, wait)

    @router.post("/threads/{thread_id}/terminals/{terminal_id}/input", status_code=204)
    async def terminal_write(thread_id: str, terminal_id: str, payload: TerminalWrite):
        ws = workspace()
        session = await ws.terminals.get(thread_id, terminal_id, writable=True)
        await session.write(payload.text)
        ws.cleanup.touch(session.cwd)

    @router.put("/threads/{thread_id}/terminals/{terminal_id}/size", status_code=204)
    async def terminal_resize(thread_id: str, terminal_id: str, payload: TerminalSize):
        session = await workspace().terminals.get(thread_id, terminal_id, writable=True)
        session.resize(payload.cols, payload.rows)

    @router.patch("/threads/{thread_id}/terminals/{terminal_id}", response_model=api.TerminalSession)
    async def rename_terminal(thread_id: str, terminal_id: str, payload: TerminalInput):
        session = await workspace().terminals.get(thread_id, terminal_id)
        session.title = payload.title
        return session.metadata()

    @router.delete("/threads/{thread_id}/terminals/{terminal_id}", status_code=204)
    async def close_terminal(thread_id: str, terminal_id: str):
        ws = workspace()
        session = ws.terminals.sessions.get(terminal_id)
        if session is None or session.thread_id != thread_id:
            raise KeyError("Terminal not found in this conversation")
        async with ws.reviews.locks.setdefault(str(session.cwd), asyncio.Lock()):
            try:
                await ws.terminals.remove(thread_id, terminal_id)
            finally:
                ws.cleanup.touch(session.cwd)

    register_access(router, workspace)
    register_delivery(router, workspace)
    register_context(router, workspace)
    register_questions(router, workspace)
    register_file_previews(router, workspace)
    app.include_router(router)
