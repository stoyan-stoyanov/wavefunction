"""Expire clean managed checkouts while retaining their conversations and branches."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from .hands import file_operation
from .repository import now_iso

logger = logging.getLogger(__name__)
SETTING = "worktree_retention_days"
# A helper's checkout is there to inspect its result for a while, not to be continued: it goes a
# day after its last use, whatever the retention set for chats.
HELPER_RETENTION = timedelta(hours=24)


class CleanupSettings(BaseModel):
    retention_days: int | None = Field(default=14, ge=1, le=3650, strict=True)


class WorktreeCleanup:
    def __init__(self, ws):
        self.ws = ws
        self.settings_lock = asyncio.Lock()
        with ws.db() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(coding_worktrees)")}
            for name, definition in {
                "cleanup_state": "TEXT NOT NULL DEFAULT 'active'",
                "last_used_at": "TEXT NOT NULL DEFAULT ''",
                "retained_head": "TEXT",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE coding_worktrees ADD COLUMN {name} {definition}")
            # Give existing installations a full retention period after upgrading.
            db.execute("UPDATE coding_worktrees SET last_used_at = ? WHERE last_used_at = ''", (now_iso(),))

    def settings(self):
        return CleanupSettings(retention_days=self.ws.services.repository.get_setting(SETTING, 14))

    def touch(self, folder):
        if folder:
            with self.ws.db() as db:
                db.execute(
                    "UPDATE coding_worktrees SET last_used_at = ? WHERE folder = ?", (now_iso(), str(folder))
                )

    def owned(self, thread_id):
        thread = self.ws.thread(thread_id)
        with self.ws.db() as db:
            # A later repository choice changes where this chat runs, not the
            # identity of its retained checkout. Helpers without their own
            # checkout still resolve the managed folder they share.
            row = db.execute("SELECT * FROM coding_worktrees WHERE thread_id = ?", (thread_id,)).fetchone()
            if row is None:
                folder = self.ws.effective_folder(thread)
                row = db.execute("SELECT * FROM coding_worktrees WHERE folder = ?", (folder,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def paths(owned):
        source, folder = Path(owned["source"]), Path(owned["folder"])
        expected = source.parent / ".harness-worktrees" / f"{source.name}-{owned['thread_id']}"
        if folder != expected or source.resolve() != source or folder.resolve() != folder:
            raise ValueError("The managed worktree location changed; automatic cleanup is unavailable.")
        return source, folder

    def rewind_archives(self, thread_id, folder):
        """The hidden "Before rewind" chats a chat's rewinds in this folder set aside. Repeated
        rewinds move older journals onto the newer archive; this follows that lineage."""
        with self.ws.db() as db:
            return {
                row[0]
                for row in db.execute(
                    """
                WITH RECURSIVE rewind_archives(id) AS (
                    SELECT ? UNION
                    SELECT json_extract(r.history, '$.archive') FROM coding_recovery r
                    JOIN rewind_archives a ON r.thread_id = a.id
                    WHERE r.state = 'rewound' AND r.folder = ? AND r.history IS NOT NULL
                ) SELECT id FROM rewind_archives
            """,
                    (thread_id, str(folder)),
                )
            }

    def protected(self, owned):
        ws, folder = self.ws, owned["folder"]
        repo, runtime = ws.services.repository, ws.services.runtime
        # Only current ownership metadata is needed, not every chat's run history.
        # Keep this query fresh on the final check so new pins/folder choices apply.
        with ws.db() as db:
            threads = [
                dict(row)
                for row in db.execute(
                    """
                SELECT t.id, t.parent_run_id, t.pinned, t.archived,
                       COALESCE(NULLIF(chosen.value, ''), NULLIF(t.folder, ''), p.folder) AS folder
                FROM coding_threads t
                LEFT JOIN coding_projects p ON p.id = t.project_id
                LEFT JOIN json_each(?) chosen ON chosen.key = t.id
                WHERE t.id = ? OR COALESCE(NULLIF(chosen.value, ''), NULLIF(t.folder, ''), p.folder) = ?
                """,
                    (json.dumps(repo.get_setting("repository_folders", {})), owned["thread_id"], folder),
                )
            ]
        owner = next(t for t in threads if t["id"] == owned["thread_id"])
        peers = [t for t in threads if t["folder"] == folder]
        if owner["pinned"] or any(t["pinned"] for t in peers):
            return True
        # A settled read-only helper retains history in its parent's checkout, and a rewind keeps
        # the set-aside history there; those records alone do not keep the directory alive forever.
        archives = self.rewind_archives(owner["id"], folder)
        if any(
            t["id"] != owner["id"]
            and not t.get("parent_run_id")
            and not (t["archived"] and t["id"] in archives)
            for t in peers
        ):
            return True
        runs = {run["id"]: run for run in repo.active_runs(ws.agent_id)}
        runs.update((key, repo.get_run(key)) for key, task in runtime.tasks.items() if not task.done())
        if owner.get("parent_run_id") in runs:
            return True
        if any((run.get("spec") or {}).get("working_directory") == folder for run in runs.values()):
            return True
        if any(
            not session.closed and str(session.cwd) == folder for session in ws.terminals.sessions.values()
        ):
            return True
        with ws.db() as db:
            return (
                db.execute(
                    "SELECT 1 FROM coding_recovery WHERE folder = ? AND state = 'rewinding'", (folder,)
                ).fetchone()
                is not None
            )

    async def git(self, owned, action, **values):
        source, folder = self.paths(owned)
        # Git status can run repository-configured filters. Keep those inside the
        # checkout's boundary, including when Git's removal rechecks cleanliness.
        if action == "worktree_restore":
            runner = self.ws.runner()
        elif action == "worktree_delete" and not folder.exists():
            # A retry after removal must not require the vanished checkout's
            # .git pointer just to remove its remaining registration.
            runner = self.ws.runner(source)
        else:
            runner = self.ws.runner(folder)
        return await file_operation(
            runner,
            {"op": "git", "root": str(source), "action": action, "destination": str(folder), **values},
            timeout=120,
        )

    async def sweep(self):
        with self.ws.db() as db:
            interrupted = [
                dict(row)
                for row in db.execute("SELECT * FROM coding_worktrees WHERE cleanup_state = 'deleting'")
            ]
        for owned in interrupted:
            try:
                async with (
                    self.ws.checkpoints.locks.setdefault(owned["thread_id"], asyncio.Lock()),
                    self.ws.reviews.locks.setdefault(owned["folder"], asyncio.Lock()),
                ):
                    latest = self.ws.delivery.worktree(owned["thread_id"])
                    if latest and latest["cleanup_state"] == "deleting":
                        await self.recover_deletion(latest)
            except (KeyError, ValueError, OSError, RuntimeError):
                logger.info(
                    "Interrupted worktree cleanup still needs recovery: %s", owned["thread_id"], exc_info=True
                )
        days = self.settings().retention_days
        now = datetime.now(UTC)
        chats = (now - timedelta(days=days)).isoformat() if days is not None else ""
        helpers = (now - HELPER_RETENTION).isoformat()
        with self.ws.db() as db:
            candidates = [
                dict(row)
                for row in db.execute(
                    "SELECT w.*, t.parent_thread_id FROM coding_worktrees w JOIN coding_threads t ON t.id = w.thread_id "
                    "WHERE w.cleanup_state = 'active' AND w.last_used_at < "
                    "CASE WHEN t.parent_thread_id IS NULL THEN ? ELSE ? END",
                    (chats, helpers),
                )
            ]
        for candidate in candidates:
            cutoff = helpers if candidate["parent_thread_id"] else chats
            try:
                async with (
                    self.ws.checkpoints.locks.setdefault(candidate["thread_id"], asyncio.Lock()),
                    self.ws.reviews.locks.setdefault(candidate["folder"], asyncio.Lock()),
                ):
                    owned = self.ws.delivery.worktree(candidate["thread_id"])
                    if (
                        not owned
                        or owned["cleanup_state"] != "active"
                        or owned["last_used_at"] >= cutoff
                        or self.protected(owned)
                    ):
                        continue
                    # A helper's checkout may hold build output: it goes if the rest is exactly its saved final state.
                    final = self.helper_snapshot(candidate) if candidate["parent_thread_id"] else {}
                    saved = await self.git(owned, "worktree_cleanup_check", **final)
                    # A settings save waits for any removal already committed to,
                    # then takes effect before another candidate can be removed.
                    async with self.settings_lock:
                        latest = self.ws.delivery.worktree(owned["thread_id"])
                        if (
                            self.settings().retention_days != days
                            or latest["last_used_at"] != owned["last_used_at"]
                            or self.protected(owned)
                        ):
                            continue
                        # The intent survives a process interruption after Git removed the
                        # directory but before SQLite recorded completion.
                        with self.ws.db() as db:
                            db.execute(
                                "UPDATE coding_worktrees SET cleanup_state = 'deleting', retained_head = ?, branch = ? WHERE thread_id = ?",
                                (saved["head"], saved["branch"], owned["thread_id"]),
                            )
                        await self.git(
                            owned,
                            "worktree_cleanup_remove",
                            head=saved["head"],
                            branch=saved["branch"],
                            **final,
                        )
                        self.deleted(owned)
            except (KeyError, ValueError, OSError, RuntimeError):
                logger.info(
                    "Retained managed worktree %s; cleanup checks did not pass",
                    candidate["thread_id"],
                    exc_info=True,
                )

    def helper_code(self, thread_id):
        """The helper's last saved code: its result, uncommitted edits included."""
        with self.ws.db() as db:
            row = db.execute(
                "SELECT code FROM coding_checkpoints WHERE thread_id = ? AND error IS NULL "
                "AND kind IN ('after_assistant', 'settled') ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return json.loads(row["code"]) if row else None

    def helper_snapshot(self, owned):
        """The helper's last saved code, which its checkout must still match to be removed."""
        code = self.helper_code(owned["thread_id"])
        if code is None:
            raise ValueError("This helper has no saved final state; its checkout is retained.")
        return {"tree": code["tree"], "owner": owned["thread_id"]}

    async def restore_helper_result(self, thread_id, folder):
        """A helper's checkout comes back as it was removed, its result on top of its branch, so
        its chat, a fork of it and a later cleanup all see that result."""
        code = self.helper_code(thread_id)
        if code is None:
            return
        checkpoints = self.ws.checkpoints
        populated = await checkpoints.git(
            folder, action="checkpoint_capture", owner=thread_id, point=f"{thread_id}-restored"
        )
        try:
            plan = await checkpoints.git(
                folder,
                action="checkpoint_plan",
                before=populated["tree"],
                after=code["tree"],
                before_modes=populated.get("modes"),
                after_modes=code.get("modes"),
                before_tracked=populated.get("tracked", False),
                after_tracked=code.get("tracked", False),
            )
        finally:
            await checkpoints.git(folder, action="checkpoint_forget", refs=[populated["ref"]])
        await self.ws.recovery.apply(
            str(folder), checkpoints.files(plan["files"]), code_history=True, resume=True
        )

    def deleted(self, owned):
        # Leave the recoverable deleting intent until the generated write grant
        # is revoked, so an interruption or failed settings write retries it.
        self.ws.delivery.ungrant(Path(owned["folder"]))
        with self.ws.db() as db:
            db.execute(
                "UPDATE coding_worktrees SET cleanup_state = 'deleted' WHERE thread_id = ?",
                (owned["thread_id"],),
            )

    async def recover_deletion(self, owned):
        """Directory existence alone cannot prove an interrupted removal rolled back."""
        _, folder = self.paths(owned)
        if not folder.exists():
            self.deleted(owned)
            return
        try:
            helper = self.ws.thread(owned["thread_id"]).get("parent_thread_id")
            saved = await self.git(
                owned, "worktree_cleanup_check", **(self.helper_snapshot(owned) if helper else {})
            )
            if saved["head"] != owned["retained_head"] or saved["branch"] != owned["branch"]:
                raise ValueError("The saved branch changed.")
        except (ValueError, OSError, RuntimeError) as error:
            raise ValueError(
                "Cleanup was interrupted and this checkout is incomplete or changed. "
                "Its remaining files were kept. Repair its Git registration and restore "
                "the saved branch's clean checkout before continuing this chat."
            ) from error
        with self.ws.db() as db:
            db.execute(
                "UPDATE coding_worktrees SET cleanup_state = 'active' WHERE thread_id = ?",
                (owned["thread_id"],),
            )

    async def restore(self, thread_id):
        """Called under the chat lock before sending or forking, never just for reading."""
        owned = self.owned(thread_id)
        if not owned or owned["cleanup_state"] == "active":
            return
        if owned["thread_id"] != thread_id:
            async with self.ws.checkpoints.locks.setdefault(owned["thread_id"], asyncio.Lock()):
                await self.restore(owned["thread_id"])
            return
        async with self.ws.reviews.locks.setdefault(owned["folder"], asyncio.Lock()):
            source, folder = self.paths(owned)
            if owned["cleanup_state"] == "deleting":
                await self.recover_deletion(owned)
                if folder.exists():
                    return
            if folder.exists() and owned["cleanup_state"] != "restoring":
                raise ValueError(
                    "The deleted worktree's path is occupied. Its files were preserved; move them before continuing this chat."
                )
            if not any(g.writable and source.is_relative_to(g.path) for g in self.ws.services.grants()):
                raise ValueError(
                    "Allow the original repository in Settings → Access before restoring this worktree."
                )
            with self.ws.db() as db:
                db.execute(
                    "UPDATE coding_worktrees SET cleanup_state = 'restoring' WHERE thread_id = ?",
                    (thread_id,),
                )
            try:
                self.ws.allow_folder(str(folder))
                if not folder.exists():
                    await self.git(
                        owned, "worktree_restore", head=owned["retained_head"], branch=owned["branch"]
                    )
                # Populating a checkout stays inside the owning worktree's permissions.
                await file_operation(
                    self.ws.runner(folder),
                    {
                        "op": "git",
                        "root": str(folder),
                        "action": "worktree_restore_checkout",
                        "head": owned["retained_head"],
                        "branch": owned["branch"],
                    },
                    timeout=120,
                )
                if self.ws.thread(thread_id).get("parent_thread_id"):
                    await self.restore_helper_result(thread_id, folder)
            except BaseException:
                if not (folder / ".git").is_file():
                    self.ws.delivery.ungrant(folder)
                raise
            with self.ws.db() as db:
                db.execute(
                    "UPDATE coding_worktrees SET cleanup_state = 'active', last_used_at = ? WHERE thread_id = ?",
                    (now_iso(), thread_id),
                )
                db.execute("UPDATE coding_threads SET updated_at = ? WHERE id = ?", (now_iso(), thread_id))

    async def run(self):
        await asyncio.sleep(60)
        while True:
            try:
                await self.sweep()
            except Exception:
                logger.exception("Managed worktree cleanup could not complete")
            await asyncio.sleep(60 * 60)
