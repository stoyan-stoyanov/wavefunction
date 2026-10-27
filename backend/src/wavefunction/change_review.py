"""Durable records of the files each chat's own tool operations changed."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from .hands import file_operation
from .repository import now_iso

ABSENT = {"text": None, "version": "absent", "kind": "absent"}


class ChangeReviews:
    def __init__(self, workspace):
        self.ws = workspace
        self.locks = {}
        # The agent's commands and file edits in one folder, one at a time, apart from the
        # user-facing lock above.
        self.agent_locks = {}
        # Disposable, bounded acceleration only. Durable evidence and undo still use
        # the original bytes/versions in SQLite, and every capture rechecks file stats.
        self.snapshots = OrderedDict()
        with workspace.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS coding_review_runs (
                    run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES coding_threads(id) ON DELETE CASCADE,
                    folder TEXT NOT NULL, warnings TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coding_changes (
                    id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES coding_threads(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL REFERENCES coding_review_runs(run_id) ON DELETE CASCADE,
                    path TEXT NOT NULL, before_text TEXT, after_text TEXT, before_version TEXT NOT NULL,
                    after_version TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
                    reviewed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS coding_changes_owner ON coding_changes(thread_id, run_id);
                CREATE TABLE IF NOT EXISTS coding_change_contents (
                    change_id TEXT PRIMARY KEY REFERENCES coding_changes(id) ON DELETE CASCADE,
                    before_data TEXT, after_data TEXT, before_mode INTEGER, after_mode INTEGER
                );
            """)

    async def snapshot(self, runner, folder, paths=None, *, before=None):
        # Never serve bytes cached under a broader permission boundary after revocation.
        key = (
            str(Path(folder).resolve()),
            type(runner),
            tuple((str(grant.path), grant.writable) for grant in runner.grants),
            tuple(map(str, runner.private_roots)),
            runner.restrict_reads,
        )
        previous = self.snapshots.get(key, {}) if paths is None else {}
        try:
            result = await file_operation(
                runner,
                {
                    "op": "review_snapshot",
                    "root": str(folder),
                    "paths": paths,
                    "known": {path: item["stat"] for path, item in previous.items() if "stat" in item},
                },
            )
            result["files"] = {
                path: previous[path] if item.get("cached") else item for path, item in result["files"].items()
            }
            if before and result["warnings"]:
                # An unrelated omission must not hide a captured file's deletion. Recheck
                # only missing prior paths: an explicit absent entry proves the deletion,
                # while unreadable or still-existing paths remain uncertain.
                missing = before["files"].keys() - result["files"].keys()
                if missing:
                    confirmed = await self.snapshot(runner, folder, sorted(missing))
                    result["files"].update(
                        {path: item for path, item in confirmed["files"].items() if item["kind"] == "absent"}
                    )
                    result["warnings"] = list(dict.fromkeys([*result["warnings"], *confirmed["warnings"]]))
            if paths is None:
                self.snapshots[key] = result["files"]
                self.snapshots.move_to_end(key)
                while len(self.snapshots) > 4:
                    self.snapshots.popitem(last=False)
            return result
        except (ValueError, PermissionError, RuntimeError) as error:
            self.snapshots.pop(key, None)
            return {"files": {}, "warnings": [f"Change capture unavailable: {error}"]}

    def changed_by(self, thread_id, folder, path, since):
        """The other chat that last changed this file in this folder since `since`, if one did."""
        root = Path(folder).resolve()
        target = Path(path).resolve()
        if not target.is_relative_to(root):
            return None
        with self.ws.db() as db:
            row = db.execute(
                """
                SELECT t.id, t.title, t.parent_thread_id FROM coding_changes c
                JOIN coding_review_runs r ON r.run_id = c.run_id JOIN coding_threads t ON t.id = c.thread_id
                WHERE r.folder IN (?, ?) AND c.path = ? AND c.thread_id != ? AND c.updated_at >= ?
                ORDER BY c.updated_at DESC LIMIT 1""",
                (str(folder), str(root), str(target.relative_to(root)), thread_id, since),
            ).fetchone()
        return dict(row) if row else None

    def record(self, thread_id, run_id, folder, before, after, source):
        # Validate ownership again before persisting a late operation (a deleted thread must
        # never be recreated by a callback). A run's captured spec owns its folder.
        thread = self.ws.thread(thread_id)
        run = self.ws.services.repository.get_run(run_id)
        if run["conversation_id"] != thread["conversation_id"]:
            raise PermissionError("Changes belong to another conversation")
        warnings = list(dict.fromkeys([*before["warnings"], *after["warnings"]]))
        if source in {"command", "terminal"}:
            warnings.append(
                "Command changes are observations during execution. Concurrent edits in a shared folder may also appear."
            )
        stamp = now_iso()
        with self.ws.db() as db:
            existing = db.execute(
                "SELECT warnings FROM coding_review_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if existing:
                warnings = list(dict.fromkeys([*json.loads(existing["warnings"]), *warnings]))
                db.execute(
                    "UPDATE coding_review_runs SET warnings = ? WHERE run_id = ?",
                    (json.dumps(warnings), run_id),
                )
            else:
                db.execute(
                    "INSERT INTO coding_review_runs VALUES (?, ?, ?, ?, ?)",
                    (run_id, thread_id, str(folder), json.dumps(warnings), stamp),
                )
            for path in sorted(before["files"].keys() | after["files"].keys()):
                old, new = before["files"].get(path, ABSENT), after["files"].get(path, ABSENT)
                if old["version"] == new["version"] and old.get("mode") == new.get("mode"):
                    continue
                # Unreadable/omitted files cannot be interpreted as a deletion.
                if path not in after["files"] and after["warnings"]:
                    continue
                previous = db.execute(
                    "SELECT * FROM coding_changes WHERE run_id = ? AND path = ? ORDER BY updated_at DESC LIMIT 1",
                    (run_id, path),
                ).fetchone()
                kind = (
                    "deleted"
                    if new["kind"] == "absent"
                    else "added"
                    if old["kind"] == "absent"
                    else "modified"
                )
                if new["kind"] in {"binary", "large"} or old["kind"] in {"binary", "large"}:
                    kind = "binary" if "binary" in {new["kind"], old["kind"]} else "large"
                previous_content = (
                    db.execute(
                        "SELECT * FROM coding_change_contents WHERE change_id = ?", (previous["id"],)
                    ).fetchone()
                    if previous
                    else None
                )
                if previous and previous["after_version"] == old["version"] and not previous["reviewed"]:
                    initial = previous["before_version"]
                    if initial == new["version"] and (
                        not previous_content or previous_content["before_mode"] == new.get("mode")
                    ):
                        db.execute("DELETE FROM coding_changes WHERE id = ?", (previous["id"],))
                        continue
                    kind = (
                        "added" if initial == "absent" else "deleted" if new["version"] == "absent" else kind
                    )
                    db.execute(
                        "UPDATE coding_changes SET after_text = ?, after_version = ?, kind = ?, source = ?, reviewed = 0, updated_at = ? WHERE id = ?",
                        (new["text"], new["version"], kind, source, stamp, previous["id"]),
                    )
                    db.execute(
                        "UPDATE coding_change_contents SET after_data = ?, after_mode = ? WHERE change_id = ?",
                        (new.get("data"), new.get("mode"), previous["id"]),
                    )
                else:
                    if previous and previous["after_version"] != old["version"]:
                        warnings.append(
                            f"{path} changed outside the recorded operation. Separate patches preserve that intervening edit."
                        )
                    change_id = str(uuid4())
                    db.execute(
                        "INSERT INTO coding_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                        (
                            change_id,
                            thread_id,
                            run_id,
                            path,
                            old["text"],
                            new["text"],
                            old["version"],
                            new["version"],
                            kind,
                            source,
                            stamp,
                            stamp,
                        ),
                    )
                    db.execute(
                        "INSERT INTO coding_change_contents VALUES (?, ?, ?, ?, ?)",
                        (change_id, old.get("data"), new.get("data"), old.get("mode"), new.get("mode")),
                    )
            db.execute(
                "UPDATE coding_review_runs SET warnings = ? WHERE run_id = ?",
                (json.dumps(list(dict.fromkeys(warnings))), run_id),
            )

    @asynccontextmanager
    async def operation(self, thread_id, run_id, folder, runner, *, paths=None, source="file"):
        root = Path(folder).resolve()
        relative = (
            None
            if paths is None
            else [
                str(Path(p).resolve().relative_to(root))
                for p in paths
                if Path(p).resolve().is_relative_to(root)
            ]
        )
        if paths is not None and not relative:
            yield
            return
        lock = self.locks.setdefault(str(root), asyncio.Lock())
        # The agent's own changes in a folder, commands and file edits alike, go one at a time.
        async with self.agent_locks.setdefault(str(root), asyncio.Lock()):
            if source == "command":
                # A command can run for minutes; holding the folder for it would stall the
                # user's saves and terminals there. Only its snapshots take that lock, and edits
                # the user makes while it runs are recorded with it, as external edits already are.
                async with lock:
                    before = await self.snapshot(runner, root, relative)
                try:
                    yield before
                finally:
                    async with lock:
                        after = await self.snapshot(runner, root, relative, before=before)
                        with suppress(KeyError):  # This thread may have been explicitly deleted.
                            self.record(thread_id, run_id, root, before, after, source)
                return
            # User/external processes can still edit the checkout; mismatched versions remain
            # separate observations, never overwrites.
            async with lock:
                before = await self.snapshot(runner, root, relative)
                try:
                    yield before
                finally:
                    after = await self.snapshot(runner, root, relative, before=before)
                    with suppress(KeyError):  # This thread may have been explicitly deleted.
                        self.record(thread_id, run_id, root, before, after, source)

    def clone(self, source_thread, target_thread, run_ids):
        with self.ws.db() as db:
            for old_run, new_run in run_ids.items():
                review = db.execute(
                    "SELECT * FROM coding_review_runs WHERE thread_id = ? AND run_id = ?",
                    (source_thread, old_run),
                ).fetchone()
                if not review:
                    continue
                db.execute(
                    "INSERT INTO coding_review_runs VALUES (?, ?, ?, ?, ?)",
                    (new_run, target_thread, review["folder"], review["warnings"], review["created_at"]),
                )
                for row in db.execute("SELECT * FROM coding_changes WHERE run_id = ?", (old_run,)).fetchall():
                    change = {**dict(row), "id": str(uuid4()), "thread_id": target_thread, "run_id": new_run}
                    db.execute(
                        "INSERT INTO coding_changes VALUES (:id, :thread_id, :run_id, :path, :before_text, :after_text, :before_version, :after_version, :kind, :source, :reviewed, :created_at, :updated_at)",
                        change,
                    )
                    content = db.execute(
                        "SELECT * FROM coding_change_contents WHERE change_id = ?", (row["id"],)
                    ).fetchone()
                    if content:
                        db.execute(
                            "INSERT INTO coding_change_contents VALUES (?, ?, ?, ?, ?)",
                            (
                                change["id"],
                                content["before_data"],
                                content["after_data"],
                                content["before_mode"],
                                content["after_mode"],
                            ),
                        )
