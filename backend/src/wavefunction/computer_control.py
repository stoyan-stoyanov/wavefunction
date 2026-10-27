"""Exclusive desktop ownership and checkpointed, private user takeover.

Private frames and input never enter the agent event/checkpoint path. Only the
handoff itself does. All desktop tool calls hold the same lock as user input.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt

from .computer import DESKTOP_FILE_TOOLS


@dataclass
class DesktopControl:
    state: str = "agent"
    generation: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # The conversation's, not one task's. `seen`: the takeover generation whose screen is in the
    # chat's history (a screenshot result after it). `look`: that generation and the frame shown
    # to a task at its start instead, which later tasks are shown again rather than a new capture.
    # `screen`: the desktop the model last saw.
    seen: int = 0
    look: tuple[int, str] | None = None
    screen: str | None = None

    def request(self) -> None:
        if self.state == "agent":
            self.generation += 1
            self.state = "pausing"

    async def claim(self) -> None:
        self.request()
        async with self.lock:
            self.state = "user"

    def checkpoint(self) -> None:
        if self.state != "agent":
            interrupt({"kind": "computer_takeover"})


class ComputerControlMiddleware(AgentMiddleware):
    def __init__(self, context: Any):
        self.context = context
        self.control = context.computer.control(context.conversation_id or context.agent_id)
        self.key = context.conversation_id or context.agent_id
        # The generation this task was shown at its start; a rebuilt graph starts unshown, so it
        # does not replay a pre-takeover click from its checkpoint.
        self.shown = -1

    async def awrap_model_call(self, request, handler):
        self.control.checkpoint()
        generation = self.control.generation
        if generation not in (self.control.seen, self.shown):
            from .computer import ComputerUseMiddleware, find_sentinel, for_model, screenshots_root
            from .toolkit import _look

            async with self.control.lock:
                self.control.checkpoint()
                # Only a desktop that is still open has anything to show. Never start one, or fail
                # the model call, for a task that may not use the desktop at all. The frame shown
                # after the user's turn is shown again while that same desktop is open.
                session = self.context.computer.running(self.key, self.context.desktop_copy)
                frame = (
                    self.control.look[1]
                    if (
                        session is not None
                        and session.container_id == self.control.screen
                        and self.control.look
                        and self.control.look[0] == generation
                        and await self.context.computer._alive(session)
                    )
                    else None
                )
                if session is not None and frame is None:
                    try:
                        frame = find_sentinel(await _look(self.context, session))
                        session.touch()
                        self.control.look, self.control.screen = (generation, frame), session.container_id
                    except Exception:  # noqa: BLE001 - a desktop that closed meanwhile shows nothing
                        frame = None
                image = ComputerUseMiddleware(
                    screenshots_root(self.context.files, self.context.agent_id)
                )._load(frame)
                if image is not None:
                    request = request.override(messages=[*request.messages, for_model(image, request)])
                    self.shown = generation
                # Without a frame the model is still behind the user's turn: its next action is
                # refused until it takes a screenshot.
        response = await handler(request)
        # A manual handoff requested during inference must pause before another tool runs.
        self.control.checkpoint()
        return response

    async def awrap_tool_call(self, request, handler):
        # Only tools that act on the screen wait for the user's turn: another tool's own pause (a
        # question, an approval or folder card) must not have its answer taken by this one when it
        # runs again. A desktop file copy acts on a path, not on what the screen showed.
        name = request.tool_call["name"]
        if not name.startswith("computer_"):
            return await handler(request)
        waits = name not in DESKTOP_FILE_TOOLS
        if waits:
            self.control.checkpoint()
        async with self.control.lock:
            if waits:
                self.control.checkpoint()
            # A file copy acts on a path, not on what the screen showed, so it is not stale after
            # the user's turn; and it must run again when it resumes from a folder card.
            acts_on_screen = name not in {
                "computer_screenshot",
                "computer_request_takeover",
                *DESKTOP_FILE_TOOLS,
            }
            if acts_on_screen and self.control.generation not in (self.control.seen, self.shown):
                return ToolMessage(
                    content="The user changed the desktop. This queued action was not executed. Take a fresh screenshot before deciding what to do next.",
                    tool_call_id=request.tool_call["id"],
                )
            running = self.context.computer.running(self.key, self.context.desktop_copy)
            if acts_on_screen and (
                running is None
                or self.control.screen is not None
                and (
                    running.container_id != self.control.screen
                    or not await self.context.computer._alive(running)
                )
            ):
                # Not open (closed when idle, after Docker or the app restarted, or never yet), or
                # not the one the model saw: what the model planned against is not on the screen.
                return ToolMessage(
                    content="The desktop is closed and would open fresh. This action was not executed. Take a screenshot before deciding what to do next.",
                    tool_call_id=request.tool_call["id"],
                )
            generation = self.control.generation
            result = await handler(request)
            if acts_on_screen:
                self.control.look = None  # The frame from after the user's turn is no longer the screen.
            from .computer import find_sentinel

            if find_sentinel(str(getattr(result, "content", ""))):
                # Its result shows the screen in the chat's history (for a takeover, as the user
                # handed it back): later model calls and tasks need no other picture of it.
                if name == "computer_request_takeover" or self.control.generation == generation:
                    self.control.seen = self.control.generation
                if shown := self.context.computer.running(self.key, self.context.desktop_copy):
                    self.control.screen = shown.container_id
            return result
