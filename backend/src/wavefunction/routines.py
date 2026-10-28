"""Routines: a project chat that is sent its instruction on a schedule, while the app is open.

One routine is one chat. When it falls due the scheduler sends the instruction to that chat as
a message, so the chat keeps what earlier runs did, and compaction keeps its history bounded.
A routine can report each finished run to another chat, which starts a turn there. A time
missed while the app was closed runs once when it opens, not once per time missed. A routine
whose chat is busy stays due and runs once the chat is free. Archiving the chat pauses it, and
so does removing its project, which hides the chat; deleting the chat deletes it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Literal

from langchain_core.tools import tool
from pydantic import ValidationError

from .execution_policy import ToolCallRejected, excerpt_text
from .repository import now_iso
from .routine_timing import Interval, Once, describe, from_words, next_due, parse

logger = logging.getLogger(__name__)

# How often the scheduler looks for work. A minute is the shortest routine, so a ten-second look
# fires one within ten seconds of falling due and never before.
TICK_SECONDS = 10
SETTLED = {"completed", "failed", "cancelled"}
NOT_IN_A_PROJECT = (
    "Routines belong to a project, and this chat is not in one. Say so; do not pretend one was set."
)


class Routines:
    def __init__(self, ws):
        self.ws = ws
        with ws.db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS coding_routines (
                    thread_id TEXT PRIMARY KEY REFERENCES coding_threads(id) ON DELETE CASCADE,
                    instruction TEXT NOT NULL, timing TEXT NOT NULL,
                    report_to TEXT REFERENCES coding_threads(id) ON DELETE SET NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                    last_run_at TEXT, last_run_id TEXT, reported_run_id TEXT, last_error TEXT
                )""")

    @staticmethod
    def present(row) -> dict:
        timing = parse(json.loads(row["timing"]))
        due = (
            next_due(timing, created_at=row["created_at"], last_run_at=row["last_run_at"])
            if row["enabled"]
            else None
        )
        return {
            "instruction": row["instruction"],
            "timing": timing.model_dump(),
            "summary": describe(timing),
            "enabled": bool(row["enabled"]),
            "report_to": row["report_to"],
            "last_run_at": row["last_run_at"],
            "next_run_at": due.isoformat() if due else None,
            "last_error": row["last_error"],
            "finished": isinstance(timing, Once) and bool(row["last_run_at"]),
        }

    def all(self, db=None) -> dict[str, dict]:
        """Every routine by chat; `db` reads them in a workspace connection the caller holds."""
        if db is None:
            with self.ws.db() as db:
                return self.all(db)
        return {row["thread_id"]: self.present(row) for row in db.execute("SELECT * FROM coding_routines")}

    def get(self, thread_id: str) -> dict:
        with self.ws.db() as db:
            row = db.execute("SELECT * FROM coding_routines WHERE thread_id = ?", (thread_id,)).fetchone()
        if row is None:
            raise KeyError("This chat is not a routine.")
        return self.present(row)

    def _report_target(self, thread: dict, report_to: str | None) -> str | None:
        if report_to is None:
            return None
        target = self.ws.thread(report_to)
        if target["id"] == thread["id"] or target["project_id"] != thread["project_id"]:
            raise ValueError("A routine reports to another chat in its project.")
        return target["id"]

    def create(self, source: dict, *, title: str, instruction: str, timing, report_to: str | None) -> dict:
        """A new chat in the source chat's project, with its model and access, that runs on `timing`."""
        from .coding_workspace import ThreadInput
        from .spec_common import ModelSpec

        if not source["project_id"]:
            raise ValueError(NOT_IN_A_PROJECT)
        thread = self.ws.create_thread(
            ThreadInput(
                title=title,
                project_id=source["project_id"],
                model=ModelSpec.model_validate(source["model"]),
                mode=source["mode"],
            )
        )
        with self.ws.db() as db:
            db.execute(
                "INSERT INTO coding_routines (thread_id, instruction, timing, report_to, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    thread["id"],
                    instruction,
                    json.dumps(timing.model_dump()),
                    self._report_target(thread, report_to),
                    now_iso(),
                ),
            )
        return thread

    def update(self, thread_id: str, changes: dict) -> dict:
        """Change what a routine says, when it runs, whether it runs, or where it reports."""
        current = self.get(thread_id)
        thread = self.ws.thread(thread_id)
        instruction = changes.get("instruction", current["instruction"])
        if not str(instruction).strip():
            raise ValueError("Say what the routine should do.")
        timing = parse(changes["timing"]) if "timing" in changes else parse(current["timing"])
        report_to = (
            self._report_target(thread, changes["report_to"])
            if "report_to" in changes
            else current["report_to"]
        )
        enabled = changes.get("enabled", current["enabled"])
        # A new time starts counting again from now, so it is not due at once for a time it never
        # had; a once that has run can be set again. An interval counts from the last run, so a
        # new one keeps it and does not run again at once.
        restart = timing.model_dump() != current["timing"] and not isinstance(timing, Interval)
        with self.ws.db() as db:
            db.execute(
                """UPDATE coding_routines SET instruction = ?, timing = ?, report_to = ?, enabled = ?,
                          created_at = CASE WHEN ? THEN ? ELSE created_at END,
                          last_run_at = CASE WHEN ? THEN NULL ELSE last_run_at END WHERE thread_id = ?""",
                (
                    instruction.strip(),
                    json.dumps(timing.model_dump()),
                    report_to,
                    int(bool(enabled)),
                    restart,
                    now_iso(),
                    restart,
                    thread_id,
                ),
            )
        return self.get(thread_id)

    def stop(self, thread_id: str) -> None:
        """The chat stays as an ordinary chat; only its schedule goes."""
        self.get(thread_id)
        with self.ws.db() as db:
            db.execute("DELETE FROM coding_routines WHERE thread_id = ?", (thread_id,))

    async def tick(self, now: datetime | None = None) -> list[str]:
        """Report finished runs, then send every due routine its instruction. Returns started run ids."""
        now = now or datetime.now(UTC)
        repository, started = self.ws.services.repository, []
        with self.ws.db() as db:
            # Oldest attempts first, so a frequent routine cannot starve another.
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM coding_routines ORDER BY COALESCE(last_run_at, ''), created_at"
                )
            ]
        for row in rows:
            try:
                thread = self.ws.thread(row["thread_id"])
                await self._report(row, thread)
                if not row["enabled"] or thread["archived"] or not thread["project_id"]:
                    continue
                timing = parse(json.loads(row["timing"]))
                due = next_due(timing, created_at=row["created_at"], last_run_at=row["last_run_at"])
                if due is None or now < due:
                    continue
                if repository.pending_conversation_runs(self.ws.agent_id, thread["conversation_id"]):
                    continue
                run, error = None, None
                try:
                    run = await self.ws.send(thread["id"], row["instruction"], delivery="queue")
                except Exception as failure:  # noqa: BLE001 - shown in the routine's settings
                    error = str(failure) or type(failure).__name__
                    logger.warning("Routine %s could not start: %s", thread["id"], error)
                # A failed start uses up a repeating routine's slot; a one-off stays due until it runs.
                consumed = run is not None or not isinstance(timing, Once)
                with self.ws.db() as db:
                    db.execute(
                        "UPDATE coding_routines SET last_run_at = CASE WHEN ? THEN ? ELSE last_run_at END, "
                        "last_run_id = COALESCE(?, last_run_id), last_error = ? WHERE thread_id = ?",
                        (consumed, now.isoformat(), run and run["id"], error, thread["id"]),
                    )
                if run:
                    started.append(run["id"])
            except Exception:  # noqa: BLE001 - one broken routine must not stop the others
                logger.exception("Routine %s could not be processed", row["thread_id"])
        return started

    async def _report(self, row: dict, thread: dict) -> None:
        """Send a finished run's answer to the chat the routine reports to, once."""
        run_id = row["last_run_id"]
        if not run_id or run_id == row["reported_run_id"]:
            return
        try:
            run = self.ws.services.repository.get_run(run_id)
        except KeyError:
            run = None
        if run is not None and (run["status"] not in SETTLED or run_id in self.ws.services.runtime.tasks):
            return
        with self.ws.db() as db:
            db.execute(
                "UPDATE coding_routines SET reported_run_id = ? WHERE thread_id = ?", (run_id, thread["id"])
            )
        try:
            target = self.ws.thread(row["report_to"]) if row["report_to"] else None
        except KeyError:
            target = None
        if run is None or target is None or target["archived"]:
            return
        said = (run.get("output") or {}).get("text") or ""
        if run["status"] != "completed" and run.get("error"):
            # What it managed, and why it stopped.
            said = f"{said}\n\n{run['error']}".strip()
        outcome = {"completed": "finished", "failed": "failed", "cancelled": "was stopped"}[run["status"]]
        said = excerpt_text(said or "It ended without an answer.", 20_000)
        try:
            # The model reads which routine and how it ended; the chat shows the answer under a
            # card that says so.
            await self.ws.send(
                target["id"],
                f"Routine “{thread['title']}” {outcome}:\n\n{said}",
                delivery="queue",
                display_text=said,
                routine_report={"thread_id": thread["id"], "title": thread["title"], "status": run["status"]},
            )
        except Exception as failure:  # noqa: BLE001 - shown in the routine's settings, not retried every check
            with self.ws.db() as db:
                db.execute(
                    "UPDATE coding_routines SET last_error = ? WHERE thread_id = ?",
                    (f"The report to “{target['title']}” could not be sent: {failure}", thread["id"]),
                )
            logger.warning("Routine %s could not report: %s", thread["id"], failure)

    async def run(self) -> None:
        await self.ws.services.workspace_ready.wait()
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the loop outlives a bad tick
                logger.exception("Routines could not run")
            await asyncio.sleep(TICK_SECONDS)


def build_routine_tools(context):
    ws = context.coding_workspace
    source_id = context.desktop_copy

    def source() -> dict:
        thread = ws.thread(source_id)
        if not thread["project_id"]:
            raise ToolCallRejected(NOT_IN_A_PROJECT)
        return thread

    def project_routines(project_id: str) -> list[dict]:
        with ws.db() as db:
            rows = db.execute(
                """SELECT t.id, t.title, r.* FROM coding_routines r JOIN coding_threads t ON t.id = r.thread_id
                                 WHERE t.project_id = ? ORDER BY r.created_at""",
                (project_id,),
            ).fetchall()
        return [
            {
                "routine_id": row["id"],
                "title": row["title"],
                **ws.routines.present(row),
                "reports_to_this_chat": row["report_to"] == source_id,
            }
            for row in rows
        ]

    @tool
    async def create_routine(
        title: str,
        instruction: str,
        repeat: Literal["once", "every", "daily", "weekdays", "weekly", "monthly"],
        time: str = "",
        date: str = "",
        days: list[str] | None = None,
        day_of_month: int | None = None,
        every_minutes: int | None = None,
        report_back: bool = False,
    ) -> str:
        """Create a routine: a new chat in this project that is sent `instruction` on a schedule.

        Use it when the user asks for something done regularly or later ("draft replies to my
        email every hour", "check the logs every 15 minutes until the fix holds"). The routine
        is its own chat, so each run sees what earlier runs did; write `instruction` as the full
        request that chat should act on each time. It uses this chat's model and access, works
        in the project's folder, and keeps running after this chat ends. Set report_back=true
        only when this chat needs each result: then every finished run is sent here as a new
        message, and you can cancel_routine once it has done its job. Routines run while
        Wavefunction is open on this Mac; times are this Mac's clock, 24-hour.

        Args:
            title: A short name for the routine's chat.
            instruction: What the routine is asked each time, complete enough to act on alone.
            repeat: "once" (needs date and time), "every" (needs every_minutes), "daily" and
                "weekdays" (need time), "weekly" (needs days and time), "monthly" (needs
                day_of_month and time).
            time: Time of day as "HH:MM".
            date: For "once", the day as "YYYY-MM-DD".
            days: For "weekly", full English day names, for example ["Monday", "Thursday"].
            day_of_month: For "monthly", 1 to 31. 31 means the last day of every month.
            every_minutes: For "every", minutes between runs, at least 1. The first run is right away.
            report_back: Send each finished run's answer to this chat.
        """
        origin = source()
        if not instruction.strip() or len(instruction) > 30000 or not 1 <= len(title.strip()) <= 160:
            raise ToolCallRejected("Give an instruction up to 30000 characters and a short title.")
        try:
            timing = from_words(
                repeat,
                time=time.strip(),
                date=date.strip(),
                days=days,
                day_of_month=day_of_month,
                every_minutes=every_minutes,
            )
        except (ValidationError, ValueError) as error:
            reason = error.errors()[0]["msg"] if isinstance(error, ValidationError) else str(error)
            return f"Nothing was set. {reason}"
        due = next_due(timing, created_at=now_iso(), last_run_at=None) if isinstance(timing, Once) else None
        if due is not None and due <= datetime.now(UTC):
            return "Nothing was set: that time has already passed. Check today's date and try again."
        thread = ws.routines.create(
            origin,
            title=title.strip(),
            instruction=instruction.strip(),
            timing=timing,
            report_to=origin["id"] if report_back else None,
        )
        routine = ws.routines.get(thread["id"])
        return json.dumps(
            {
                "routine_id": thread["id"],
                "title": thread["title"],
                "summary": routine["summary"],
                "next_run_at": routine["next_run_at"],
                "reports_to_this_chat": report_back,
                "note": "It is listed first in its project in the sidebar; its chat's menu has its settings.",
            }
        )

    @tool
    def list_routines() -> str:
        """List this project's routines: id, title, instruction, when they run, and whether they report here."""
        return json.dumps(project_routines(source()["project_id"]))

    @tool
    def cancel_routine(routine_id: str) -> str:
        """Stop one of this project's routines, by the id list_routines gives. Its chat and history stay."""
        origin = source()
        if not any(row["routine_id"] == routine_id.strip() for row in project_routines(origin["project_id"])):
            return "No routine in this project has that id. Call list_routines for the ids."
        ws.routines.stop(routine_id.strip())
        return f"Stopped routine {routine_id.strip()}. Its chat stays as an ordinary chat."

    list_routines.metadata = {"readOnlyHint": True}
    return [create_routine, list_routines, cancel_routine]
