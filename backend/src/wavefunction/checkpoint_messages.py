"""Model checkpoints that keep each message once.

LangGraph saves the whole state at every step, and a chat keeps one state per message point, so
the file grew with the square of a chat's length and each message rewrote the whole conversation
dozens of times. Here a checkpoint names its messages by id, in ranges, and each message is stored
once per conversation. Everything that reads a checkpoint goes through this saver, so it sees the
same state as before. Rows saved in full stay as they are and read as they always did.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any, cast

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

SHARED = "__shared_messages__"


def ranges(ids: list[int]) -> list[list[int]]:
    spans: list[list[int]] = []
    for item in ids:
        if spans and sum(spans[-1]) == item:
            spans[-1][1] += 1
        else:
            spans.append([item, 1])
    return spans


class MessageSaver(AsyncSqliteSaver):
    async def setup(self) -> None:
        if self.is_setup:
            return
        await super().setup()
        async with self.lock, self.conn.cursor() as cursor:
            await cursor.execute(
                """CREATE TABLE IF NOT EXISTS checkpoint_messages (
                    id INTEGER PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    hash BLOB NOT NULL,
                    type TEXT NOT NULL,
                    message BLOB NOT NULL,
                    UNIQUE (thread_id, hash))"""
            )
            await self.conn.commit()

    async def share(self, thread_id: str, checkpoint: dict[str, Any]) -> dict[str, Any]:
        """The checkpoint with its messages stored apart. The caller commits them with the row
        that names them; a crash between the two leaves unnamed messages, never a missing one."""
        messages = checkpoint["channel_values"].get("messages")
        if not isinstance(messages, list) or not messages:
            return checkpoint
        await self.setup()
        rows = [self.serde.dumps_typed(message) for message in messages]
        hashes = [
            hashlib.blake2b(kind.encode() + b"\0" + data, digest_size=16).digest() for kind, data in rows
        ]
        async with self.lock, self.conn.cursor() as cursor:
            await cursor.execute("SELECT hash, id FROM checkpoint_messages WHERE thread_id = ?", (thread_id,))
            known = {bytes(row[0]): row[1] for row in await cursor.fetchall()}
            for digest, (kind, data) in zip(hashes, rows, strict=True):
                if digest not in known:
                    await cursor.execute(
                        "INSERT INTO checkpoint_messages (thread_id, hash, type, message) VALUES (?, ?, ?, ?)",
                        (thread_id, digest, kind, data),
                    )
                    known[digest] = cursor.lastrowid
        values = {**checkpoint["channel_values"], "messages": {SHARED: ranges([known[d] for d in hashes])}}
        return {**checkpoint, "channel_values": values}

    async def restored(self, saved: Any) -> Any:
        """A saved tuple as it was given to `aput`."""
        values = saved.checkpoint["channel_values"]
        shared = values.get("messages")
        if not isinstance(shared, dict) or SHARED not in shared:
            return saved
        thread_id = saved.config["configurable"]["thread_id"]
        messages = []
        async with self.lock, self.conn.cursor() as cursor:
            for first, count in shared[SHARED]:
                await cursor.execute(
                    "SELECT type, message FROM checkpoint_messages WHERE thread_id = ? AND id >= ? AND id < ? ORDER BY id",
                    (thread_id, first, first + count),
                )
                found = list(await cursor.fetchall())
                if len(found) != count:
                    raise ValueError("A saved conversation is missing some of its messages.")
                messages += [self.serde.loads_typed((row[0], row[1])) for row in found]
        checkpoint = {**saved.checkpoint, "channel_values": {**values, "messages": messages}}
        return saved._replace(checkpoint=checkpoint)

    async def aget_tuple(self, config: Any) -> Any:
        saved = await super().aget_tuple(config)
        return saved and await self.restored(saved)

    async def alist(
        self, config: Any, *, filter: Any = None, before: Any = None, limit: Any = None
    ) -> AsyncIterator[Any]:
        # Collected first: the parent holds the lock while it yields.
        found = [saved async for saved in super().alist(config, filter=filter, before=before, limit=limit)]
        for saved in found:
            yield await self.restored(saved)

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        shared = await self.share(str(config["configurable"]["thread_id"]), checkpoint)
        return await super().aput(config, cast(Any, shared), metadata, new_versions)

    async def adelete_thread(self, thread_id: str) -> None:
        await super().adelete_thread(thread_id)
        async with self.lock, self.conn.cursor() as cursor:
            await cursor.execute("DELETE FROM checkpoint_messages WHERE thread_id = ?", (str(thread_id),))
            await self.conn.commit()
