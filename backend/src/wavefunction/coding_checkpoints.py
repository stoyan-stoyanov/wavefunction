"""Message boundaries shared by forks, worktrees, and recoverable code rewind."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import nullcontext, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.base import empty_checkpoint

from .coding_delivery import WorktreeInput
from .hands import file_operation
from .repository import now_iso
from .spec_common import ModelSpec

ACTIVE = {"queued", "running", "waiting_approval", "waiting_connector", "waiting_computer"}

# LangGraph saves the whole agent state at every step and never removes one. Resuming reads a
# conversation's latest checkpoint and a message point reads the one it recorded; nothing reads
# the rest, since the agent's channels are plain reducers, not DeltaChannel, which rebuilds state
# from its ancestors' writes.
RECORDED_MODELS = (
    "SELECT json_extract(model_config, '$.configurable.checkpoint_id') FROM coding_checkpoints "
    "WHERE json_extract(model_config, '$.configurable.thread_id') = ?"
)
PRUNE_MODELS = [
    f"""DELETE FROM {table} WHERE thread_id = :conversation
    AND checkpoint_id NOT IN (SELECT value FROM json_each(:recorded) WHERE value IS NOT NULL)
    AND checkpoint_id < (SELECT MAX(checkpoint_id) FROM checkpoints AS latest
                         WHERE latest.thread_id = :conversation AND latest.checkpoint_ns = {table}.checkpoint_ns)"""
    for table in ("writes", "checkpoints")
]


def resolved(folder):
    """A recorded folder as it resolves now: one recorded before it moved behind a symlink still
    names the same checkout as the resolved root it is compared with."""
    return folder and str(Path(folder).resolve())


class Checkpoints:
    def __init__(self, ws):
        self.ws = ws
        self.locks = {}
        with ws.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS coding_checkpoints (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES coding_threads(id) ON DELETE CASCADE,
                conversation_id TEXT NOT NULL, run_id TEXT NOT NULL, kind TEXT NOT NULL,
                event_id TEXT, event_sequence INTEGER NOT NULL, folder TEXT, code TEXT,
                model_config TEXT, model_type TEXT, model BLOB, created_at TEXT NOT NULL, error TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS coding_checkpoints_run ON coding_checkpoints(run_id)")

    def points(self, thread_id, run_id):
        with self.ws.db() as db:
            return [
                {
                    "id": row["id"],
                    "run_id": run_id,
                    "kind": row["kind"],
                    "event_id": row["event_id"],
                    "available": row["error"] is None,
                    "unavailable_reason": row["error"],
                }
                for row in db.execute(
                    "SELECT id, kind, event_id, error FROM coding_checkpoints WHERE thread_id = ? AND run_id = ? AND kind != 'settled' ORDER BY created_at",
                    (thread_id, run_id),
                )
            ]

    def point(self, thread_id, point_id):
        thread = self.ws.thread(thread_id)
        with self.ws.db() as db:
            row = db.execute(
                "SELECT * FROM coding_checkpoints WHERE id = ? AND thread_id = ? AND conversation_id = ?",
                (point_id, thread_id, thread["conversation_id"]),
            ).fetchone()
        if row is None:
            raise KeyError("This message checkpoint does not belong to the current conversation.")
        value = dict(row)
        if value["error"]:
            raise ValueError(value["error"])
        value["code"] = json.loads(value["code"])
        return value

    async def git(self, folder, **request):
        return await file_operation(
            self.ws.runner(folder), {"op": "git", "root": str(folder), **request}, timeout=120
        )

    async def capture(self, run_id, kind, event_id=None, state=None):
        repo = self.ws.services.repository
        run = repo.get_run(run_id)
        thread_id = run["input"].get("desktop_copy")
        if not thread_id:
            return
        thread = self.ws.thread(thread_id)
        if thread["conversation_id"] != run["conversation_id"]:
            return
        with self.ws.db() as db:
            if db.execute(
                "SELECT 1 FROM coding_checkpoints WHERE run_id = ? AND kind = ? AND event_id IS ?",
                (run_id, kind, event_id),
            ).fetchone():
                return
        point_id, code, error = str(uuid4()), None, None
        folder = resolved(
            (run.get("spec") or {}).get("working_directory") or self.ws.effective_folder(thread)
        )
        saver = await self.ws.services.runtime.checkpointer()
        model = await saver.aget_tuple({"configurable": {"thread_id": run["conversation_id"]}})
        model_type, model_bytes = None, None
        if state is not None:
            checkpoint = copy.deepcopy(model.checkpoint) if model else empty_checkpoint()
            checkpoint["channel_values"].update(state)
            model_type, model_bytes = saver.serde.dumps_typed(checkpoint)
        try:
            if not folder:
                raise ValueError("Choose a Git repository to record code history.")
            async with self.ws.reviews.locks.setdefault(str(folder), asyncio.Lock()):
                code = await self.git(folder, action="checkpoint_capture", owner=thread_id, point=point_id)
        except (ValueError, OSError, RuntimeError) as failure:
            error = str(failure)
        events = repo.list_run_events(run_id, limit=1, tail=True)
        with self.ws.db() as db:
            db.execute(
                "INSERT INTO coding_checkpoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    point_id,
                    thread_id,
                    thread["conversation_id"],
                    run_id,
                    kind,
                    event_id,
                    max((event["sequence"] for event in events), default=0),
                    folder,
                    json.dumps(code),
                    json.dumps(model.config) if model else None,
                    model_type,
                    model_bytes,
                    now_iso(),
                    error,
                ),
            )
            db.execute("UPDATE coding_threads SET updated_at = ? WHERE id = ?", (now_iso(), thread_id))
        return point_id

    async def copy_model(self, source, target_conversation, point=None):
        saver = await self.ws.services.runtime.checkpointer()
        config = (
            json.loads(point["model_config"])
            if point and point["model_config"]
            else {"configurable": {"thread_id": source["conversation_id"]}}
        )
        saved = None if point and not point["model_config"] else await saver.aget_tuple(config)
        if point and point.get("model"):
            checkpoint = saver.serde.loads_typed((point["model_type"], point["model"]))
        elif saved:
            checkpoint = copy.deepcopy(saved.checkpoint)
        else:
            return None
        checkpoint["id"] = empty_checkpoint()["id"]
        checkpoint["ts"] = now_iso()
        return await saver.aput(
            {"configurable": {"thread_id": target_conversation, "checkpoint_ns": ""}},
            checkpoint,
            {**(saved.metadata if saved else {}), "source": "fork"},
            checkpoint["channel_versions"],
        )

    async def prune_models(self, conversation_id):
        """Drop the conversation's agent states that neither resuming nor a message point reads."""
        with self.ws.db() as db:
            recorded = json.dumps([row[0] for row in db.execute(RECORDED_MODELS, (conversation_id,))])
        saver = await self.ws.services.runtime.checkpointer()
        async with saver.lock, saver.conn.cursor() as cursor:
            for statement in PRUNE_MODELS:
                await cursor.execute(statement, {"conversation": conversation_id, "recorded": recorded})
            await saver.conn.commit()

    def settled(self, thread_id):
        if any(
            run["status"] in ACTIVE
            or (task := self.ws.services.runtime.tasks.get(run["id"]))
            and not task.done()
            for run in self.ws.runs(thread_id)
        ):
            raise ValueError("Finish or stop this task before forking its conversation.")

    async def new_worktree(self, thread_id, base="HEAD"):
        return await self.ws.delivery.create_worktree(
            thread_id, WorktreeInput(branch=f"codex/chat-{thread_id[:8]}", base=base)
        )

    async def branch(self, source_thread_id, target_thread_id, checkpoint_id=None, live=False):
        folder = self.ws.root(target_thread_id) if live else self.ws.root(source_thread_id)
        if not live:
            self.settled(source_thread_id)
        if checkpoint_id:
            point = self.point(source_thread_id, checkpoint_id)
            if resolved(point["folder"]) != str(folder):
                raise ValueError("Return to this message's original repository before forking its code.")
            code = point["code"]
        else:
            async with self.ws.reviews.locks.setdefault(str(folder), asyncio.Lock()):
                self.ws.delivery.mutable(
                    target_thread_id if live else source_thread_id, folder, changes_checkout=False
                )
                code = await self.git(
                    folder,
                    action="checkpoint_capture",
                    owner=source_thread_id,
                    point=f"{target_thread_id}-base",
                )
        try:
            worktree = await self.ws.delivery.create_worktree(
                target_thread_id,
                WorktreeInput(branch=f"codex/chat-{target_thread_id[:8]}", base=code["head"]),
            )
        except BaseException:
            if not checkpoint_id and not self.ws.delivery.worktree(target_thread_id):
                await self.git(folder, action="checkpoint_forget", refs=[code["ref"]])
            raise
        with self.ws.db() as db:
            db.execute(
                "UPDATE coding_worktrees SET base_commit = ?, base_tree = ?, base_ref = ? WHERE thread_id = ?",
                (code["commit"], code["tree"], code["ref"], target_thread_id),
            )
        # The checkout wrote its files through the repository's filters (line endings, LFS), so
        # plan from the bytes it wrote rather than HEAD's raw blobs.
        populated = await self.git(
            worktree["folder"],
            action="checkpoint_capture",
            owner=target_thread_id,
            point=f"{target_thread_id}-populated",
            matching={"tree": code["tree"], "modes": code.get("modes")},
        )
        branched = {"folder": worktree["folder"], "base_commit": code["commit"], "base_tree": code["tree"]}
        # Usually the checkout is already exact: nothing uncommitted came with it.
        if populated.get("matches"):
            return branched
        try:
            plan = await self.git(
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
            await self.git(worktree["folder"], action="checkpoint_forget", refs=[populated["ref"]])
        # Sparse checkout can already omit files that the raw snapshot omits.
        # Accept exact target bytes/absence, while still rejecting divergent edits.
        await self.ws.recovery.apply(
            worktree["folder"], self.files(plan["files"]), code_history=True, resume=True
        )
        return branched

    async def child_diff(self, thread_id, run_id):
        worktree = self.ws.delivery.worktree(thread_id)
        if not worktree or not worktree.get("base_commit"):
            return {"files": [], "diff": ""}
        # Report what this helper finished, never later user edits in its retained checkout.
        # Completion/cancellation already captured these bytes; polling must not snapshot again.
        with self.ws.db() as db:
            point = db.execute(
                "SELECT code, error FROM coding_checkpoints WHERE thread_id = ? AND run_id = ? "
                "AND kind IN ('after_assistant', 'settled') ORDER BY created_at DESC LIMIT 1",
                (thread_id, run_id),
            ).fetchone()
        if not point or point["error"]:
            raise ValueError(point["error"] if point else "This helper has no settled code snapshot.")
        code = json.loads(point["code"])
        return {
            "base_commit": worktree["base_commit"],
            "base_tree": worktree["base_tree"],
            "result_commit": code["commit"],
            "result_ref": code["ref"],
            **await self.git(
                worktree["source"] if worktree.get("cleanup_state") == "deleted" else worktree["folder"],
                action="checkpoint_diff",
                base=worktree["base_commit"],
                tip=code["commit"],
            ),
        }

    @staticmethod
    def files(plan):
        return [
            {
                "path": row["path"],
                "expected": row["before_version"],
                "target": row["after_version"],
                "expected_mode": row["before_mode"],
                "mode": row["after_mode"],
                "data": row["after_data"],
                "redo_data": row["before_data"],
                "redo_mode": row["before_mode"],
            }
            for row in plan
        ]

    def draft(self, run, point):
        if point["kind"] == "before_user":
            value = run["input"]
            files = value.get("initial_reference_files", value.get("reference_files", []))
        elif point["kind"] == "before_steering":
            value = next(
                event["payload"]
                for event in self.ws.services.repository.list_run_events(run["id"])
                if event["id"] == point["event_id"]
            )
            files = value.get("reference_files", [])
        else:
            return None
        return {
            "content": value.get("display_text", value.get("text", "")),
            "files": files,
            "context": value.get("coding_context") or {"files": [], "skills": [], "command": None},
        }

    def restore_draft_files(self, thread_id, draft):
        """The draft's attachments become those of the message being edited, as its text does."""
        with self.ws.db() as db:
            db.execute("DELETE FROM coding_attachments WHERE thread_id = ?", (thread_id,))
            for file in (draft or {}).get("files", []):
                db.execute(
                    "INSERT INTO coding_attachments VALUES (?, ?, ?)", (str(uuid4()), thread_id, file["id"])
                )

    async def clone_history(self, source, target, point: Any = None, *, copy_draft=True):
        repo = self.ws.services.repository
        run_ids, event_ids = {}, {}
        draft = None
        for run in self.ws.runs(source["id"]):
            boundary = point and run["id"] == point["run_id"]
            if boundary:
                draft = self.draft(run, point)
                if point["kind"] == "before_user":
                    break
            partial = boundary and point["kind"] == "before_steering"
            values = {**run["input"], "desktop_copy": target["id"], "forked_from": run["id"]}
            if partial:
                # Files that only steering after the boundary sent belong to the abandoned future.
                def sent(events):
                    return {
                        f["id"]
                        for e in events
                        if e["event_type"] == "steering_requested"
                        for f in e["payload"].get("reference_files", [])
                    }

                # From the edited steering message itself: the checkpoint's sequence can include later ones.
                cut = next(
                    (e["sequence"] for e in run["events"] if e["id"] == point["event_id"]),
                    point["event_sequence"],
                )
                after = [e for e in run["events"] if e["sequence"] >= cut]
                dropped = (
                    sent(after)
                    - sent([e for e in run["events"] if e not in after])
                    - {f["id"] for f in values.get("initial_reference_files", [])}
                )
                values["reference_files"] = [
                    f for f in values.get("reference_files", []) if f["id"] not in dropped
                ]
            clone = repo.create_run(
                self.ws.agent_id, values, target["conversation_id"], spec=None, kind="fork"
            )
            run_ids[run["id"]] = clone["id"]
            repo.update_run(
                clone["id"],
                status="completed" if partial else run["status"],
                output=None if partial else run["output"],
                error=None if partial else run.get("error"),
                duration_ms=run.get("duration_ms"),
                usage=run.get("usage"),
            )
            repo.keep_run_times(
                clone["id"], run["started_at"], point["created_at"] if partial else run.get("completed_at")
            )
            applied = {
                event["payload"].get("event_id")
                for event in run["events"]
                if event["event_type"] == "steering_applied"
                and (not partial or event["sequence"] <= point["event_sequence"])
            }
            for event in run["events"]:
                if partial and (
                    event["sequence"] > point["event_sequence"] or event["id"] == point["event_id"]
                ):
                    continue
                if partial and event["event_type"] == "steering_requested" and event["id"] not in applied:
                    continue
                payload = dict(event["payload"])
                if payload.get("event_id") in event_ids:
                    payload["event_id"] = event_ids[payload["event_id"]]
                copied = repo.add_run_event(
                    clone["id"],
                    event["event_type"],
                    payload,
                    event.get("node_id"),
                    created_at=event.get("created_at"),
                )
                event_ids[event["id"]] = copied["id"]
            if boundary:
                break
        # Review rows coalesce edits across a whole run. A steering fork must not
        # inherit its later patches; its exact earlier bytes are in the code snapshot.
        review_ids = {
            old: new
            for old, new in run_ids.items()
            if not (point and point["kind"] == "before_steering" and old == point["run_id"])
        }
        self.ws.reviews.clone(source["id"], target["id"], review_ids)
        with self.ws.db() as db:
            points = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM coding_checkpoints WHERE thread_id = ? ORDER BY created_at",
                    (source["id"],),
                )
                if row["run_id"] in run_ids
                and (not point or row["created_at"] <= point["created_at"])
                and (not row["event_id"] or row["event_id"] in event_ids)
            ]
        copied = {row["id"] for row in points}
        # Messages from the source's checkout move with it to the target's (a worktree fork's own
        # checkout); one recorded in another checkout keeps naming it, so restoring it there
        # is still refused rather than writing that checkout's files into this one.
        moved_from, moved_to = (
            self.ws.effective_folder(source),
            self.ws.effective_folder(self.ws.thread(target["id"])),
        )
        for row in points:
            model_config = await self.copy_model(source, target["conversation_id"], row)
            row.update(
                id=str(uuid4()),
                thread_id=target["id"],
                conversation_id=target["conversation_id"],
                run_id=run_ids[row["run_id"]],
                event_id=event_ids.get(row["event_id"]),
                folder=moved_to if resolved(row["folder"]) == moved_from else row["folder"],
                model_config=json.dumps(model_config) if model_config else None,
                model_type=None,
                model=None,
            )
            with self.ws.db() as db:
                db.execute(
                    "INSERT INTO coding_checkpoints ("
                    + ",".join(row)
                    + ") VALUES ("
                    + ",".join("?" for _ in row)
                    + ")",
                    tuple(row.values()),
                )
        model_config = await self.copy_model(source, target["conversation_id"], point)
        if point and run_ids and point["id"] not in copied:
            # The history now ends at this boundary, but its own checkpoint belongs to a message
            # left out of it. Its code is what is on disk, so the next rewind or fork starts there.
            with self.ws.db() as db:
                db.execute(
                    "INSERT INTO coding_checkpoints (id, thread_id, conversation_id, run_id, kind, event_id, event_sequence, folder, code, model_config, created_at) "
                    "VALUES (?, ?, ?, ?, 'settled', NULL, ?, ?, ?, ?, ?)",
                    (
                        str(uuid4()),
                        target["id"],
                        target["conversation_id"],
                        list(run_ids.values())[-1],
                        point["event_sequence"],
                        self.ws.effective_folder(self.ws.thread(target["id"])),
                        json.dumps(point["code"]),
                        json.dumps(model_config) if model_config else None,
                        point["created_at"],
                    ),
                )
        with self.ws.db() as db:
            for file in self.ws.attachments(source["id"]) if copy_draft else []:
                db.execute(
                    "INSERT INTO coding_attachments VALUES (?, ?, ?)",
                    (str(uuid4()), target["id"], file["id"]),
                )
        return draft

    async def fork(self, thread_id, checkpoint_id=None, worktree=True):
        from .coding_workspace import ThreadInput

        async with self.locks.setdefault(thread_id, asyncio.Lock()):
            await self.recover(thread_id)
            self.settled(thread_id)
            source = self.ws.thread(thread_id)
            if source["archived"] and worktree and checkpoint_id is None:
                with self.ws.db() as db:
                    saved = db.execute(
                        "SELECT id FROM coding_checkpoints WHERE thread_id = ? ORDER BY created_at DESC LIMIT 1",
                        (thread_id,),
                    ).fetchone()
                if not saved:
                    raise ValueError(
                        "This archived conversation has no saved code. Fork its history without a worktree instead."
                    )
                checkpoint_id = saved["id"]
            point = self.point(thread_id, checkpoint_id) if checkpoint_id else None
            if point and not worktree:
                raise ValueError("Fork this message in a worktree so its code matches its history.")
            if point and resolved(point["folder"]) != self.ws.effective_folder(source):
                raise ValueError("Return to this message's original repository before forking its code.")
            if worktree:
                await self.ws.cleanup.restore(thread_id)
                source = self.ws.thread(thread_id)
            folder = self.ws.effective_folder(source)
            async with self.ws.reviews.locks.setdefault(folder, asyncio.Lock()) if folder else nullcontext():
                source = self.ws.thread(thread_id)
                if self.ws.effective_folder(source) != folder:
                    raise ValueError("The repository changed while forking. Try again.")
                shared_deleted_checkout = not worktree and source["worktree_status"] == "deleted"
                target = self.ws.create_thread(
                    ThreadInput(
                        title=("Fork: " + source["title"])[:160],
                        project_id=source["project_id"],
                        folder=None if shared_deleted_checkout else folder,
                        model=ModelSpec.model_validate(source["model"]),
                        mode=source["mode"],
                        instructions=source["instructions"],
                    )
                )
                if shared_deleted_checkout:
                    # Keep the saved association without validating or granting a
                    # missing checkout. Continuing the fork restores its owner first.
                    with self.ws.db() as db:
                        db.execute(
                            "UPDATE coding_threads SET folder = ? WHERE id = ?", (folder, target["id"])
                        )
                    target = self.ws.thread(target["id"])
            try:
                if worktree:
                    await self.branch(thread_id, target["id"], checkpoint_id)
                # A message fork starts from that message's draft, not the source's current one.
                draft = await self.clone_history(source, target, point, copy_draft=point is None)
            except BaseException:
                if not self.ws.delivery.worktree(target["id"]):
                    await self.discard(target["id"])
                raise
            if point is not None:
                self.restore_draft_files(target["id"], draft)
            return {**self.ws.thread(target["id"]), "draft": draft}

    async def discard(self, thread_id):
        """Remove only a failed, task-created continuation, never its source history."""
        thread = self.ws.thread(thread_id)
        saver = await self.ws.services.runtime.checkpointer()
        await saver.adelete_thread(thread["conversation_id"])
        repo = self.ws.services.repository
        with repo._history(self.ws.agent_id) as db:
            db.execute(
                "DELETE FROM run_events WHERE run_id IN (SELECT id FROM runs WHERE conversation_id = ?)",
                (thread["conversation_id"],),
            )
            db.execute("DELETE FROM runs WHERE conversation_id = ?", (thread["conversation_id"],))
            db.execute("DELETE FROM conversations WHERE id = ?", (thread["conversation_id"],))
        with self.ws.db() as db:
            db.execute("DELETE FROM coding_threads WHERE id = ?", (thread_id,))

    async def rewind(self, thread_id, checkpoint_id):
        from .coding_workspace import ThreadInput

        async with self.locks.setdefault(thread_id, asyncio.Lock()):
            await self.recover(thread_id)
            point = self.point(thread_id, checkpoint_id)
            source = self.ws.thread(thread_id)
            if source.get("parent_thread_id"):
                raise ValueError("Fork this helper to restore or continue its history independently.")
            await self.ws.cleanup.restore(thread_id)
            root = self.ws.root(thread_id)
            if resolved(point["folder"]) != str(root):
                raise ValueError("Return to this message's repository before restoring its code.")
            for run in self.ws.runs(thread_id):
                if (
                    run["status"] in ACTIVE
                    or (task := self.ws.services.runtime.tasks.get(run["id"]))
                    and not task.done()
                ):
                    await self.ws.services.runtime.cancel(run["id"])
                else:
                    await self.ws.services.runtime._stop_helpers(run["id"])
            async with self.ws.reviews.locks.setdefault(str(root), asyncio.Lock()):
                self.ws.delivery.mutable(thread_id, root, restore=True)
                with self.ws.db() as db:
                    latest = next(
                        (
                            row
                            for row in db.execute(
                                "SELECT folder, code, error FROM coding_checkpoints WHERE thread_id = ? ORDER BY created_at DESC",
                                (thread_id,),
                            )
                            if resolved(row["folder"]) == str(root)
                        ),
                        None,
                    )
                if not latest or latest["error"]:
                    raise ValueError("There is no complete code checkpoint for this continuation.")
                end = json.loads(latest["code"])
                plan = await self.git(
                    root,
                    action="checkpoint_plan",
                    before=end["tree"],
                    after=point["code"]["tree"],
                    before_modes=end.get("modes"),
                    after_modes=point["code"].get("modes"),
                    before_ignored=end.get("ignored"),
                    after_ignored=point["code"].get("ignored"),
                    before_tracked=end.get("tracked", False),
                    after_tracked=point["code"].get("tracked", False),
                )
                files = self.files(plan["files"])
                staged = set((await self.git(root, action="checkpoint_staging"))["paths"])
                if staged.intersection(row["path"] for row in files):
                    raise ValueError(
                        "Unstage the affected files before restoring this message. Your staged changes are preserved."
                    )
                # Validate before creating the archive; failed conflicts leave no extra chat.
                await file_operation(
                    self.ws.runner(root),
                    {
                        "op": "restore_files",
                        "root": str(root),
                        "files": files,
                        "validate_only": True,
                        "max_bytes": 16 * 1024 * 1024,
                    },
                )
                target = self.ws.create_thread(
                    ThreadInput(
                        title=("Before rewind: " + source["title"])[:160],
                        project_id=source["project_id"],
                        folder=str(root),
                        model=ModelSpec.model_validate(source["model"]),
                        mode=source["mode"],
                        instructions=source["instructions"],
                    )
                )
                with self.ws.db() as db:
                    db.execute("UPDATE coding_threads SET archived = 1 WHERE id = ?", (target["id"],))
                try:
                    draft = await self.clone_history(source, target, point)
                except BaseException:
                    await self.discard(target["id"])
                    raise
                if self.ws.root(thread_id) != root:
                    await self.discard(target["id"])
                    raise ValueError("The conversation folder changed. Retry in its original repository.")
                action_id, stamp = str(uuid4()), now_iso()
                history = {
                    "source": source["conversation_id"],
                    "target": target["conversation_id"],
                    "archive": target["id"],
                    "draft_files": [file["id"] for file in (draft or {}).get("files", [])],
                }
                with self.ws.db() as db:
                    db.execute(
                        "INSERT INTO coding_recovery (id, thread_id, folder, change_ids, files, state, created_at, updated_at, history) VALUES (?, ?, ?, '[]', ?, 'rewinding', ?, ?, ?)",
                        (
                            action_id,
                            thread_id,
                            str(root),
                            json.dumps(files),
                            stamp,
                            stamp,
                            json.dumps(history),
                        ),
                    )
                await self.ws.terminals.forget(thread_id)
                # The set-aside conversation's desktop would keep the chat's home, so the chat's
                # next desktop would open blank. The home itself is kept.
                await self.ws.services.computer.close_session(source["conversation_id"])
                await self.ws.recovery.apply(root, files, code_history=True)
                self.finish_rewind(thread_id, action_id, history)
                return {
                    "thread": self.ws.thread(thread_id),
                    "draft": draft,
                    "archived_thread_id": target["id"],
                }

    def finish_rewind(self, thread_id, action_id, history):
        archive, source, target = history["archive"], history["source"], history["target"]
        with self.ws.db() as db:
            current = db.execute(
                "SELECT conversation_id FROM coding_threads WHERE id = ?", (thread_id,)
            ).fetchone()["conversation_id"]
            if current == source:
                db.execute(
                    "UPDATE coding_threads SET conversation_id = ? WHERE id = ?", (str(uuid4()), archive)
                )
                db.execute(
                    "UPDATE coding_threads SET conversation_id = ?, updated_at = ? WHERE id = ?",
                    (target, now_iso(), thread_id),
                )
                db.execute("UPDATE coding_threads SET conversation_id = ? WHERE id = ?", (source, archive))
                for table in (
                    "coding_checkpoints",
                    "coding_review_runs",
                    "coding_changes",
                    "coding_attachments",
                ):
                    db.execute(
                        f"UPDATE {table} SET thread_id = CASE thread_id WHEN ? THEN ? ELSE ? END WHERE thread_id IN (?, ?)",
                        (thread_id, archive, thread_id, thread_id, archive),
                    )
                db.execute(
                    "UPDATE coding_threads SET parent_thread_id = ? WHERE parent_thread_id = ?",
                    (archive, thread_id),
                )
                db.execute(
                    "UPDATE coding_recovery SET thread_id = ? WHERE thread_id = ? AND id != ?",
                    (archive, thread_id, action_id),
                )
            elif current != target:
                raise ValueError("This recovery no longer owns the conversation.")
        repo = self.ws.services.repository
        with repo._history(self.ws.agent_id) as db:
            for conversation, owner in ((target, thread_id), (source, archive)):
                db.execute(
                    "UPDATE runs SET input_json = json_set(input_json, '$.desktop_copy', ?) WHERE conversation_id = ?",
                    (owner, conversation),
                )
                db.execute("UPDATE conversations SET project_id = ? WHERE id = ?", (owner, conversation))
        with self.ws.db() as db:
            # The draft takes the edited message's files in the same commit that completes the
            # journal, so recovery after a crash still restores them.
            db.execute("DELETE FROM coding_attachments WHERE thread_id = ?", (thread_id,))
            for file_id in history.get("draft_files", []):
                db.execute(
                    "INSERT INTO coding_attachments VALUES (?, ?, ?)", (str(uuid4()), thread_id, file_id)
                )
            db.execute(
                "UPDATE coding_recovery SET state = 'rewound', updated_at = ? WHERE id = ?",
                (now_iso(), action_id),
            )

    async def recover(self, thread_id):
        with self.ws.db() as db:
            pending = db.execute(
                "SELECT * FROM coding_recovery WHERE thread_id = ? AND state = 'rewinding' AND history IS NOT NULL",
                (thread_id,),
            ).fetchall()
        for row in pending:
            async with self.ws.reviews.locks.setdefault(row["folder"], asyncio.Lock()):
                with self.ws.db() as db:
                    if (
                        db.execute("SELECT state FROM coding_recovery WHERE id = ?", (row["id"],)).fetchone()[
                            "state"
                        ]
                        != "rewinding"
                    ):
                        continue
                root = self.ws.root(thread_id)
                if str(root) != row["folder"]:
                    raise ValueError("Return to the original folder to finish the interrupted rewind.")
                self.ws.delivery.mutable(thread_id, root, restore=True)
                await file_operation(
                    self.ws.runner(row["folder"]),
                    {
                        "op": "restore_files",
                        "root": row["folder"],
                        "files": json.loads(row["files"]),
                        "resume": True,
                        "max_bytes": 16 * 1024 * 1024,
                    },
                )
                self.finish_rewind(thread_id, row["id"], json.loads(row["history"]))

    async def forget(self, thread_id):
        with self.ws.db() as db:
            points = db.execute(
                "SELECT folder, code FROM coding_checkpoints WHERE thread_id = ? AND code != 'null'",
                (thread_id,),
            ).fetchall()
            retained = {
                json.loads(row["code"])["ref"]
                for row in db.execute(
                    "SELECT code FROM coding_checkpoints WHERE thread_id != ? AND code != 'null'",
                    (thread_id,),
                )
            }
            worktrees = db.execute(
                "SELECT thread_id, source, folder, base_ref, cleanup_state FROM coding_worktrees"
            ).fetchall()
        retained.update(
            row["base_ref"] for row in worktrees if row["thread_id"] != thread_id and row["base_ref"]
        )
        # Expiration leaves Git objects in the source repository after its checkout
        # disappears. Remove this chat's refs there before its ownership row cascades.
        sources = {row["folder"]: row["source"] for row in worktrees if row["cleanup_state"] == "deleted"}
        refs = {
            json.loads(point["code"])["ref"]: sources.get(point["folder"], point["folder"])
            for point in points
        }
        refs.update(
            {
                row["base_ref"]: row["source"]
                for row in worktrees
                if row["thread_id"] == thread_id and row["base_ref"]
            }
        )
        for ref, folder in refs.items():
            if ref not in retained:
                with suppress(ValueError, OSError):
                    await self.git(folder, action="checkpoint_forget", refs=[ref])
