"""Desktop notifications for a chat's task that finished or needs the user while the chat is not in view.

The desktop shell keeps one request waiting for them (`wait`), answered as soon as a task ends or
pauses, and shows each one as a macOS notification unless its chat is on screen. Helpers never
notify: their main chat does, when it finishes or while a helper waits on the user's card.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from typing import Any

from .repository import now_iso

DEFAULTS = {"desktop": True, "input": True, "sound": True, "previews": True}
FINISHED = ("run_completed", "run_failed")
NEEDS_INPUT = {
    "run_questions": "Has a question for you.",
    "run_waiting_for_approval": "Needs your approval to continue.",
    "run_connector_setup_required": "Needs you to connect a service.",
    "run_computer_takeover": "Needs you on its desktop.",
    # Written on the main task, held open until the user answers its helper's card.
    "helper_waiting": "A helper needs you.",
}
# Read per request; a burst longer than this is picked up by the next ones.
BATCH = 20


class TaskNews:
    """Wakes requests waiting for a task to end or pause. Rung by whichever thread saves a run
    event; when nobody waits that is a lock and a counter."""

    def __init__(self) -> None:
        self.rings = 0
        self._lock = threading.Lock()
        self._waiting: set[asyncio.Future] = set()
        self._closed = False

    def ring(self, event_type: str) -> None:
        if event_type in FINISHED or event_type in NEEDS_INPUT:
            self._wake()

    def close(self) -> None:
        """The server is stopping: answer every waiting request now, and any later one at once."""
        self._closed = True
        self._wake()

    def _wake(self) -> None:
        with self._lock:
            self.rings += 1
            waiting, self._waiting = self._waiting, set()
        for future in waiting:
            with suppress(RuntimeError):  # Its loop has already closed.
                future.get_loop().call_soon_threadsafe(_settle, future)

    async def wait(self, seen: int, timeout: float) -> None:
        """Until a ring after `seen`, the `rings` read before looking for news, or `timeout`."""
        future = asyncio.get_running_loop().create_future()
        with self._lock:
            if self.rings != seen or self._closed:
                return
            self._waiting.add(future)
        try:
            with suppress(TimeoutError):
                await asyncio.wait_for(future, timeout)
        finally:
            with self._lock:
                self._waiting.discard(future)


def _settle(future: asyncio.Future) -> None:
    if not future.done():
        future.set_result(None)


def settings(repository) -> dict[str, bool]:
    stored = repository.get_setting("notifications", {})
    stored = stored if isinstance(stored, dict) else {}
    return {
        key: stored[key] if isinstance(stored.get(key), bool) else value for key, value in DEFAULTS.items()
    }


def _excerpt(text: str | None, limit: int = 180) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _notice(event: dict[str, Any], thread: dict[str, Any], previews: bool) -> dict[str, str]:
    kind = event["event_type"]
    if kind == "run_completed":
        body = (_excerpt(event["text"]) or "Finished.") if previews else "A task finished."
    elif kind == "run_failed":
        body = (
            ("Stopped: " + _excerpt(event["message"], 160)) if previews else "A task stopped with an error."
        )
    else:
        body = NEEDS_INPUT[kind] if previews else "A task needs your input."
    # Without previews, nothing the chat says or is called reaches the lock screen.
    return {
        "thread_id": thread["id"],
        "title": _excerpt(thread["title"], 80) if previews else "Wavefunction",
        "body": body,
    }


async def wait_pending(
    workspace, news: TaskNews, after: str | None, visible: str | None, wait: float
) -> dict[str, Any]:
    """`pending`, but with nothing to show it waits up to `wait` seconds for a task to end or pause."""
    seen = news.rings
    result = pending(workspace, after, visible)
    if after and wait and not result["items"]:
        await news.wait(seen, wait)
        result = pending(workspace, result["cursor"], visible)
    return result


def pending(workspace, after: str | None, visible: str | None) -> dict[str, Any]:
    """Notifications since `after`, the cursor the previous call returned, at most one per chat.

    Without a cursor, it starts from now: tasks that ended before the app opened do not notify.
    The chat in view (`visible`) gets none.
    """
    repository = workspace.services.repository
    prefs = settings(repository)
    if not after:
        return {"cursor": now_iso(), "sound": prefs["sound"], "items": []}
    events = repository.task_endings(workspace.agent_id, after, FINISHED + tuple(NEEDS_INPUT), BATCH)
    cursor = events[-1]["created_at"] if events else after
    wanted = FINISHED + (tuple(NEEDS_INPUT) if prefs["input"] else ())
    events = [event for event in events if event["event_type"] in wanted]
    if not prefs["desktop"] or not events:
        return {"cursor": cursor, "sound": prefs["sound"], "items": []}
    conversations = list({event["conversation_id"] for event in events})
    with workspace.db() as db:
        threads = {
            row["conversation_id"]: dict(row)
            for row in db.execute(
                "SELECT id, title, conversation_id, parent_thread_id FROM coding_threads WHERE conversation_id IN ("
                + ",".join("?" for _ in conversations)
                + ")",
                conversations,
            )
        }
    items: dict[str, dict[str, str]] = {}
    for event in events:
        thread = threads.get(event["conversation_id"])
        if thread and not thread["parent_thread_id"] and thread["id"] != visible:
            # A later event replaces an earlier one for the same chat.
            items.pop(thread["id"], None)
            items[thread["id"]] = _notice(event, thread, prefs["previews"])
    return {"cursor": cursor, "sound": prefs["sound"], "items": list(items.values())}
