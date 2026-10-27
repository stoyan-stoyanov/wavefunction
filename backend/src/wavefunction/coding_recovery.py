"""Version-checked file restores for rewinding a chat's code and restoring retained worktrees."""

from __future__ import annotations

import json

from .hands import file_operation


class Recovery:
    def __init__(self, ws):
        self.ws = ws
        with ws.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS coding_recovery (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES coding_threads(id) ON DELETE CASCADE,
                folder TEXT NOT NULL, change_ids TEXT NOT NULL, files TEXT NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                history TEXT)""")

    def list(self, thread_id):
        self.ws.thread(thread_id)
        with self.ws.db() as db:
            return [
                {
                    "id": r["id"],
                    "folder": r["folder"],
                    "changes": json.loads(r["change_ids"]),
                    "state": r["state"],
                    "created_at": r["created_at"],
                }
                for r in db.execute(
                    "SELECT * FROM coding_recovery WHERE thread_id = ? AND history IS NULL ORDER BY created_at DESC",
                    (thread_id,),
                )
            ]

    async def apply(self, root, files, *, code_history=False, resume=False):
        request = []
        for f in files:
            clean = {k: f[k] for k in ("path", "expected", "target", "data", "mode")}
            if f.get("expected_mode") is not None:
                clean["expected_mode"] = f["expected_mode"]
            request.append(clean)
        return await file_operation(
            self.ws.runner(root),
            {
                "op": "restore_files",
                "root": str(root),
                "files": request,
                "max_bytes": 16 * 1024 * 1024 if code_history else 512 * 1024,
                "resume": resume,
            },
        )
