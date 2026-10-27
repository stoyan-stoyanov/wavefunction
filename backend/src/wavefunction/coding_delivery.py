"""Git state for the Changes tab, and the worktrees chats own."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, Field

from . import responses as api
from .hands import file_operation
from .repository import now_iso


class WorktreeInput(BaseModel):
    branch: str = Field(min_length=1, max_length=200)
    base: str = Field(default="HEAD", min_length=1, max_length=4096)


class Delivery:
    def __init__(self, ws):
        self.ws = ws
        with ws.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS coding_worktrees (
                    thread_id TEXT PRIMARY KEY REFERENCES coding_threads(id) ON DELETE CASCADE,
                    source TEXT NOT NULL, folder TEXT NOT NULL UNIQUE, branch TEXT NOT NULL,
                    original_folder TEXT, created_at TEXT NOT NULL,
                    base_commit TEXT, base_tree TEXT, base_ref TEXT
                );
            """)

    def worktree(self, thread_id):
        self.ws.thread(thread_id)
        with self.ws.db() as db:
            row = db.execute("SELECT * FROM coding_worktrees WHERE thread_id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None

    def mutable(self, thread_id, root, *, changes_checkout=True, restore=False):
        thread = self.ws.thread(thread_id)
        # Plan mode withholds the agent's changes. Starting a chat in a worktree changes no files,
        # and restoring a message's code is the user's own edit, so neither is refused for it.
        if thread["archived"] or thread["mode"] == "plan" and changes_checkout and not restore:
            raise ValueError(
                "Restore this conversation and switch out of Plan mode to change files or publish."
            )
        if self.ws.root(thread_id) != root:
            raise ValueError("Conversation folder changed. Refresh before retrying.")
        # Another conversation can own an executing task in this shared checkout.
        repo = self.ws.services.repository
        runs = {run["id"]: run for run in repo.active_runs(self.ws.agent_id)}
        runs.update(
            (run_id, repo.get_run(run_id))
            for run_id, task in self.ws.services.runtime.tasks.items()
            if not task.done()
        )
        if not changes_checkout:
            # Adding a worktree or reading a snapshot leaves this checkout's files, index and
            # HEAD as they are, so another chat's task here is no reason to refuse it. This
            # chat's own task would keep working in the folder it is being moved away from.
            if any(run["input"].get("desktop_copy") == thread_id for run in runs.values()):
                raise ValueError("Finish or stop this chat's task before moving it to a worktree.")
            return
        for run in runs.values():
            if run["input"].get("local_read_only"):
                continue
            if (run.get("spec") or {}).get("working_directory") == str(root):
                raise ValueError(
                    "Finish or stop active tasks in this checkout before changing Git state. A worktree keeps work independent."
                )

    async def execute(self, thread_id, **request):
        root = self.ws.root(thread_id)
        # Registration is a product-owned operation on the shared Git directory;
        # checkout filters and all subsequent operations use the checkout boundary.
        runner = self.ws.runner() if request.get("action") == "worktree_add" else self.ws.runner(root)
        return await file_operation(runner, {"op": "git", "root": str(root), **request}, timeout=120)

    async def create_worktree(self, thread_id, payload):
        source = self.ws.root(thread_id)
        async with self.ws.reviews.locks.setdefault(str(source), asyncio.Lock()):
            self.mutable(thread_id, source, changes_checkout=False)
            if self.worktree(thread_id):
                raise ValueError("This conversation already has a managed worktree.")
            # Descendants share the original repository's Git metadata. Do not make
            # their cleanup depend on an intermediate parent checkout still existing.
            origin = source
            with self.ws.db() as db:
                while owner := db.execute(
                    "SELECT source FROM coding_worktrees WHERE folder = ?", (str(origin),)
                ).fetchone():
                    previous = Path(owner["source"])
                    if previous == origin:
                        break
                    origin = previous
            # Exact destination grant only. No parent's broad write access and no user-selected
            # removal path. Ownership is persisted before Git starts for failed-start recovery.
            destination = origin.parent / ".harness-worktrees" / f"{origin.name}-{thread_id}"
            if destination.exists() or destination.parent.is_symlink():
                raise ValueError("The managed worktree destination already exists or is linked.")
            destination.parent.mkdir(exist_ok=True)
            destination.mkdir()
            self.ws.allow_folder(str(destination))
            thread = self.ws.thread(thread_id)
            with self.ws.db() as db:
                db.execute(
                    "INSERT INTO coding_worktrees (thread_id, source, folder, branch, original_folder, created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        thread_id,
                        str(origin),
                        str(destination),
                        payload.branch,
                        thread["folder"],
                        now_iso(),
                        now_iso(),
                    ),
                )
            try:
                # Git creates the shared administration before any checkout filters
                # run. Populate files separately inside the new checkout boundary.
                await self.execute(
                    thread_id,
                    action="worktree_add",
                    branch=payload.branch,
                    base=payload.base,
                    destination=str(destination),
                )
                # A checkout hook can fail after populating files. Keep the chat
                # pointed at its owned worktree so those files remain usable.
                with self.ws.db() as db:
                    db.execute(
                        "UPDATE coding_threads SET folder = ?, updated_at = ? WHERE id = ?",
                        (str(destination), now_iso(), thread_id),
                    )
                self.clear_chosen(thread_id)
                await self.ws.terminals.forget(thread_id)
                await file_operation(
                    self.ws.runner(destination),
                    {"op": "git", "root": str(destination), "action": "worktree_checkout"},
                    timeout=120,
                )
            except BaseException:
                if destination.exists() and not any(destination.iterdir()):
                    destination.rmdir()
                if not destination.exists():
                    with self.ws.db() as db:
                        db.execute("DELETE FROM coding_worktrees WHERE thread_id = ?", (thread_id,))
                    self.ungrant(destination)
                raise
            return self.worktree(thread_id)

    def clear_chosen(self, thread_id):
        repo = self.ws.services.repository
        chosen = dict(repo.get_setting("repository_folders", {}))
        chosen.pop(thread_id, None)
        repo.set_setting("repository_folders", chosen)

    def ungrant(self, destination):
        from .sandbox import GRANTS_SETTING

        repo = self.ws.services.repository
        repo.set_setting(
            GRANTS_SETTING, [g for g in repo.get_setting(GRANTS_SETTING, []) if g["path"] != str(destination)]
        )

    async def delete_for_chat(self, thread_id):
        """Remove the confirmed checkout after its chat and helper tasks have stopped."""
        # Called only for a chat that owns a worktree.
        owned = cast(dict[str, Any], self.worktree(thread_id))
        source, folder = self.ws.cleanup.paths(owned)

        def uses_checkout(path):
            return bool(path) and Path(path).is_relative_to(folder)

        async with self.ws.reviews.locks.setdefault(str(folder), asyncio.Lock()):
            peers = [
                t
                for t in self.ws.snapshot()["threads"]
                if t["id"] != thread_id and uses_checkout(t["folder"])
            ]
            # Manually archived independent chats still own their files.
            rewind_ids = self.ws.cleanup.rewind_archives(thread_id, folder)
            archives = {
                t["id"]
                for t in peers
                if t["id"] in rewind_ids
                and t["archived"]
                and not t["worktree"]
                and t["folder"] == str(folder)
            }
            for peer in peers:
                if (
                    peer["worktree"]
                    or peer["folder"] != str(folder)
                    or not (peer["id"] in archives or peer.get("parent_thread_id") in {thread_id, *archives})
                ):
                    raise ValueError(
                        f"Another chat uses this worktree: {peer['title']}. Choose a different repository folder for that chat before deleting this one."
                    )
            repo, runtime = self.ws.services.repository, self.ws.services.runtime
            runs = {run["id"]: run for run in repo.active_runs(self.ws.agent_id)}
            runs.update((key, repo.get_run(key)) for key, task in runtime.tasks.items() if not task.done())
            if any(uses_checkout((run.get("spec") or {}).get("working_directory")) for run in runs.values()):
                raise ValueError("A task is still using this worktree. Stop it before deleting this chat.")
            for peer in peers:
                if peer.get("parent_thread_id") == thread_id:
                    await self.ws.terminals.forget(peer["id"])
            if any(
                not session.closed and uses_checkout(session.cwd)
                for session in self.ws.terminals.sessions.values()
            ):
                raise ValueError(
                    "Another chat has an open terminal in this worktree. Close it before deleting this chat."
                )
            if owned["cleanup_state"] == "deleted" and folder.exists():
                raise ValueError("The deleted worktree's path is now occupied. Its new files were preserved.")
            # With its source repository moved or deleted there is no Git registration left to
            # remove; the chat still goes, and the checkout's files stay where they are.
            if owned["cleanup_state"] != "deleted" and (source / ".git").exists():
                await self.ws.cleanup.git(owned, "worktree_delete")
            self.ws.cleanup.deleted(owned)
            # Archived rewind histories and read-only helpers survive. Keep their
            # saved code refs usable from the repository owning the shared objects.
            with self.ws.db() as db:
                for peer in peers:
                    db.execute("UPDATE coding_threads SET folder = ? WHERE id = ?", (str(source), peer["id"]))
                    db.execute(
                        "UPDATE coding_checkpoints SET folder = ? WHERE thread_id = ? AND folder = ?",
                        (str(source), peer["id"], str(folder)),
                    )
            for peer in peers:
                self.clear_chosen(peer["id"])


def register_delivery(router, workspace):
    @router.get("/threads/{thread_id}/git/state", response_model=api.GitState)
    async def state(thread_id: str):
        return await workspace().delivery.execute(thread_id, action="state")

    @router.get("/threads/{thread_id}/git/branches", response_model=api.GitBranches)
    async def branches(thread_id: str):
        return await workspace().delivery.execute(thread_id, action="branches")

    @router.get("/threads/{thread_id}/git/diff", response_model=api.GitDiff)
    async def diff(thread_id: str, path: str, staged: bool = False, combined: bool = False):
        return await workspace().delivery.execute(
            thread_id, action="diff", path=path, staged=staged, combined=combined
        )

    @router.get("/threads/{thread_id}/worktree")
    def worktree(thread_id: str):
        return workspace().delivery.worktree(thread_id)
