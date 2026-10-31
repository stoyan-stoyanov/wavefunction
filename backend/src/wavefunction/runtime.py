"""Execute one task at a time per conversation, with durable SQLite checkpoints.

Token deltas are streamed to the client but not persisted individually; only discrete events
(tool calls, completed assistant text, approvals, terminal states) become rows, so replay stays
cheap and the run_events table does not explode.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import Counter
from contextlib import suppress
from contextvars import Context
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command, Interrupt

from .agent_factory import build_agent
from .checkpoint_messages import MessageSaver
from .computer import find_sentinel
from .connector_setup import connector_status
from .execution_policy import COMPACT_KEY, LIMIT_MARK, _is_user_message
from .folder_setup import REPOSITORY, picked_folder, record_folder, setup_status
from .local_coding import REPOSITORY_FOLDERS_SETTING
from .models import NESTED_USAGE, ModelGateway, describe_provider_error, message_usage
from .models import merge_usage as _merge_usage
from .records import Run, RunEvent
from .repository import Repository
from .sandbox import GRANTS_SETTING, load_grants
from .setup_rows import setup_row_status
from .spec_v2 import CONNECTOR_TOOL, AgentSpecV2, load_spec
from .toolkit import ToolContext

if TYPE_CHECKING:
    from .coding_workspace import CodingWorkspace

logger = logging.getLogger(__name__)

INTERRUPTED_CALL = (
    "Execution was interrupted before a result was recorded. The action may have partially "
    "completed; verify its state before retrying."
)
NOT_RUN_CALL = "Not run: the task was stopped before this call started, so it had no effect."


async def repair_interrupted_tool_calls(agent, config) -> None:
    """Close abandoned calls before a new turn; never replay their side effects.

    Recovery happens at the next normal input, after the previous task has stopped. This also
    covers a process crash and leaves approval resumes untouched. Keep successful results: the
    state read here already carries those of parallel calls that finished in the stopped step.

    A call held by an approval question never ran — Stop was pressed on the question — and is
    closed as not run. Any other abandoned call was cut off somewhere in its execution, and may
    have partly happened.
    """
    snapshot = await agent.aget_state(config)
    messages = list((snapshot.values or {}).get("messages", []))
    answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
    if not any(
        call["id"] not in answered for m in messages if isinstance(m, AIMessage) for call in m.tool_calls
    ):
        return
    held = await _held_for_approval(agent, config)
    last = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
    repaired = []
    for message in messages:
        repaired.append(message)
        if isinstance(message, AIMessage):
            repaired.extend(
                ToolMessage(
                    content=NOT_RUN_CALL if message is last and call["id"] in held else INTERRUPTED_CALL,
                    tool_call_id=call["id"],
                    name=call["name"],
                    status="error",
                )
                for call in message.tool_calls
                if call["id"] not in answered
            )
    await agent.aupdate_state(
        config, {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *repaired]}, as_node="__start__"
    )


async def _held_for_approval(agent, config) -> set[str]:
    """The IDs of the calls an approval card or question in the thread's last checkpoint holds.

    Read from the checkpoint's pending writes rather than from this graph's view of the state:
    the next request may be built without the approval gate, and then the pause it left is
    invisible to that graph.
    """
    saver = getattr(agent, "checkpointer", None)
    saved = await saver.aget_tuple(config) if saver is not None else None
    writes = [
        (task, channel, value if isinstance(value, list) else [value])
        for task, channel, value in (saved.pending_writes or [] if saved else [])
    ]
    # A pause the user answered stays among the writes beside its answer when the resumed call is
    # then stopped while running: that call was let through, so it is not held. LangGraph's own
    # name for the answers' channel.
    answered = {}
    for task, channel, values in writes:
        if channel == "__resume__":
            answered[task] = answered.get(task, 0) + len(values)
    held = set()
    for task, _channel, values in writes:
        pauses = [item for item in values if isinstance(item, Interrupt) and isinstance(item.value, dict)]
        for item in pauses[answered.get(task, 0) :]:
            requests = [
                item.value,
                *(
                    request
                    for request in item.value.get("action_requests") or []
                    if isinstance(request, dict)
                ),
            ]
            held.update(request["call_id"] for request in requests if request.get("call_id"))
    return held


def result_text(messages: list[Any]) -> str:
    """A run's answer: the last assistant text of its request."""
    text = ""
    for message in reversed(messages):
        # The user's message: not an image or environment notice inside the turn.
        if (
            _is_user_message(message)
            if isinstance(message, HumanMessage)
            else getattr(message, "type", "") == "human"
        ):
            break
        if getattr(message, "tool_calls", None):
            continue
        if isinstance(message, AIMessage):
            text = assistant_text(message)
            break
    if not text.strip():
        # Said while making its last tool call, then ended on an empty message.
        from .execution_policy import closing_text

        text = closing_text(messages)

    # An empty answer is never something to record silently. The usual cause is an output
    # budget spent entirely on reasoning tokens, which leaves finish_reason "length" on a
    # message with no content — the agent looks like it ignored you.
    if not text.strip():
        text = _describe_empty_answer(messages)

    return text


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
# Paused on the user: a question or approval, a connector to set up, or the desktop handed over.
WAITING_STATUSES = {"waiting_approval", "waiting_connector", "waiting_computer"}
# The events that open those pauses, which a helper's main chat shows as the helper's card.
PAUSE_EVENTS = (
    "run_questions",
    "run_waiting_for_approval",
    "run_connector_setup_required",
    "run_computer_takeover",
)
# LangGraph counts super-steps and raises once a graph runs past `recursion_limit`; left unset
# it is 25. A task that keeps doing useful work is allowed to keep doing it: what ends one that
# has stopped producing anything is the loop guard, the idle timeout below, and Stop. The graph
# limit is set explicitly, far past anything a run could reach, so the framework default
# cannot quietly come back.
STEP_LIMIT = 1_000_000_000
# How long a run may go with nothing happening — no token, no tool call, no tool result — before
# it is given up on. Silence is the only thing that distinguishes a stuck run from a long one:
# a fifteen-minute ceiling on total time stopped long, productive tasks. The clock stops while a
# model call or a tool is in flight: each has a limit of its own (the provider's request timeout,
# the tool's deadline), and a model that thinks for six minutes before its first token is not
# stuck.
IDLE_TIMEOUT = 300
# How long `wait` waits for a run to settle. Not a limit on the run itself.
WAIT_CEILING = 6 * 3600

# How much assistant text accumulates before a streaming event is written. Big enough that a
# reply becomes a handful of rows, small enough that the UI reads as live typing.
DELTA_FLUSH_CHARS = 48


def in_flight_id(payload: dict[str, Any]) -> str:
    """Which call an in-flight event is about. A summary carries no id; there is one at a time."""
    return str(payload.get("call_id") or "summarization")


def stream_finished(status: str) -> bool:
    """Whether the event stream of a run in this status is over.

    Over when the work has ended,
    or when it waits on an approval, which the client answers and then follows again. Anything
    else is followed to its end: a sign-in finished in Settings resumes the work by itself, and
    handing the desktop back resumes it on the stream already open.
    """
    return status in TERMINAL_STATUSES or status == "waiting_approval"


def _delta_text(message: Any) -> str:
    """The streamable text in one message chunk, "" when it carries none.

    Only the model node's assistant prose streams; tool chunks and tool-call argument deltas
    have their own events and would render as noise.
    """
    if getattr(message, "type", "") not in ("ai", "AIMessageChunk"):
        return ""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def assistant_text(message: Any) -> str:
    """What one assistant message says, whatever shape the provider put it in.

    The Responses API — which a ChatGPT sign-in always uses — puts assistant text in a list of
    content blocks, not a string. Every place that read `.content` and checked `isinstance(str)`
    therefore saw nothing on those models, and one of them then walked further back and recorded
    a *tool result* as the agent's answer: an agent that replied "21 + 21 = 42." had its run
    saved as "42".
    """
    if not isinstance(message, AIMessage):
        return ""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


@dataclass
class ActiveRun:
    agent: Any
    project_id: str
    thread_id: str
    started_at: float
    # How many requests the current approval pause is holding: one decision answers each, in order.
    pending_decisions: int = 0
    accepting_steering: bool = True
    usage: dict[str, int] = field(default_factory=dict)
    # Whose package the run's screenshots are saved under — the agent, even when the run is
    # owned by a project. The screenshot event carries it so the UI knows where to fetch from.
    agent_id: str = ""
    # The task that started this helper, so a parent that stops can take its helpers with it.
    parent_run_id: str | None = None
    reference_files: list[dict[str, Any]] = field(default_factory=list)
    # The run's own idle clock: stopped while a model call or tool is in flight.
    clock: asyncio.Timeout | None = None
    # The conversation's messages from before this request, so a resume keeps what the run
    # itself said before its pause.
    previous_message_ids: set[str] = field(default_factory=set)
    executing_tools: set[str] = field(default_factory=set)
    model_calls: set[str] = field(default_factory=set)
    retry_until: float = 0
    # Compact now: the run summarises the conversation and ends, without asking the model anything.
    compact: bool = False


class BusyConversationError(ValueError):
    """The conversation already has a run in progress and the caller did not ask to queue."""


class RuntimeManager:
    def __init__(
        self,
        repository: Repository,
        models: ModelGateway,
        files: Any,
        *,
        coding_workspace: CodingWorkspace,
        checkpoint_path: Any = None,
        connectors: Any = None,
        skills: Any = None,
        computer: Any = None,
    ):
        self.repository = repository
        self.connectors = connectors
        self.skills = skills
        self.computer = computer
        self.coding_workspace = coding_workspace
        self.models = models
        self.files = files
        self.checkpoint_path = checkpoint_path or (repository.agent_root / "checkpoints.sqlite")
        self.active: dict[str, ActiveRun] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.conditions: dict[str, asyncio.Condition] = {}
        self._checkpointer: MessageSaver | None = None
        self._checkpointer_lock = asyncio.Lock()
        # One answer at a time per run. Per run, so a resume waiting on its own run's stream
        # does not hold up every other run's approval.
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._waiting_connector_ids: set[str] = set()
        self._recovered_connector_waits = False
        self._resuming_connectors: set[str] = set()

    async def checkpointer(self) -> MessageSaver:
        """One shared saver for the process. Durable, so interrupts outlive the session."""
        async with self._checkpointer_lock:
            if self._checkpointer is None:
                self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
                connection = await aiosqlite.connect(str(self.checkpoint_path))
                saver = MessageSaver(connection)
                await saver.setup()
                self._checkpointer = saver
            return self._checkpointer

    async def aclose(self) -> None:
        if self._checkpointer is not None:
            with suppress(Exception):
                await self._checkpointer.conn.close()
            self._checkpointer = None

    async def start_run(
        self,
        agent_id: str,
        input_text: str,
        *,
        spec: AgentSpecV2,
        kind: str = "user",
        conversation_id: str | None = None,
        queue_if_busy: bool = False,
        desktop_copy: str | None = None,
        reference_files: list[dict[str, Any]] | None = None,
        message_files: list[dict[str, Any]] | None = None,
        display_text: str | None = None,
        coding_context: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
        local_read_only: bool = False,
        folder_grants: list[dict[str, Any]] | None = None,
        routine_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start one recorded run with the conversation's exclusive checkpoint writer.

        `spec` is captured with the run: skills are pinned to their current versions here, and
        a queued, steered or resumed task keeps this configuration.
        """
        if self.skills is not None:
            spec = self.skills.pin_spec(spec)
        # The thread is the conversation, not the run. Runs inside one share a checkpoint, which
        # is what lets the model see what it said last time; a run used to be its own thread, so
        # every message started from nothing.
        conversation = (
            {"id": conversation_id} if conversation_id else self.repository.current_conversation(agent_id)
        )
        # Paused runs still own their checkpoint after restart. A settled status can also
        # precede final cleanup, even after the run has left `active`; include those tasks.
        predecessors = [
            run["id"]
            for run in self.repository.pending_conversation_runs(
                agent_id,
                conversation["id"],
                settling=tuple(run_id for run_id, task in self.tasks.items() if not task.done()),
            )
        ]
        if predecessors and not queue_if_busy:
            raise BusyConversationError(
                "this conversation already has a run in progress — wait for it, answer its "
                "approval, or stop it first"
            )

        run = self.repository.create_run(
            agent_id,
            {
                "text": input_text,
                "wait_for": predecessors,
                **({"desktop_copy": desktop_copy} if desktop_copy else {}),
                **({"reference_files": reference_files} if reference_files is not None else {}),
                # The files this message sent; `reference_files` also carries earlier ones forward.
                **({"initial_reference_files": message_files} if message_files is not None else {}),
                **({"display_text": display_text} if display_text is not None else {}),
                **({"coding_context": coding_context} if coding_context is not None else {}),
                **({"local_read_only": True} if local_read_only else {}),
                **({"folder_grants": folder_grants} if folder_grants is not None else {}),
                # A routine's report, which the chat shows as the routine's rather than the user's.
                **({"routine_report": routine_report} if routine_report else {}),
            },
            conversation["id"],
            spec=spec.model_dump(mode="json"),
            kind=kind,
            parent_run_id=parent_run_id,
        )
        condition = asyncio.Condition()
        self.conditions[run["id"]] = condition

        try:
            agent = build_agent(
                spec,
                self.models,
                ToolContext(
                    agent_id,
                    self.repository,
                    self.files,
                    self.models.secrets,
                    run_id=run["id"],
                    parent_run_id=parent_run_id,
                    local_read_only=local_read_only,
                    folder_grants=folder_grants,
                    connectors=self.connectors,
                    skills=self.skills,
                    computer=self.computer,
                    conversation_id=conversation["id"],
                    desktop_copy=desktop_copy or agent_id,
                    reference_files=reference_files,
                    steering=lambda: self._steering_messages(run["id"]),
                    close_steering=lambda: self._close_steering(run["id"]),
                    apply_steering=lambda item, state: self._apply_steering(run["id"], item, state),
                    helper_updates=(lambda seen: self._helper_updates(run["id"], seen))
                    if not parent_run_id
                    else None,
                    record_action_unknown=lambda payload: self.repository.add_run_event(
                        run["id"], "action_outcome_unknown", payload
                    ),
                    choose_folder=kind == "user" and not parent_run_id,
                    coding_workspace=self.coding_workspace,
                ),
                checkpointer=await self.checkpointer(),
            )
        except (Exception, asyncio.CancelledError) as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            self.repository.update_run(
                run["id"],
                status="cancelled" if cancelled else "failed",
                error=str(error) or type(error).__name__,
            )
            self.repository.add_run_event(
                run["id"],
                "run_cancelled" if cancelled else "run_failed",
                {"message": str(error) or type(error).__name__},
            )
            self.conditions.pop(run["id"], None)
            raise
        self.active[run["id"]] = ActiveRun(
            agent=agent,
            project_id=agent_id,
            thread_id=conversation["id"],
            started_at=time.monotonic(),
            agent_id=agent_id,
            parent_run_id=parent_run_id,
            reference_files=reference_files if reference_files is not None else [],
            compact=kind == "compact",
        )
        emit = self._emitter(run["id"], condition)
        # A helper run owns its stream. Inheriting LangGraph's tool context also
        # inherits the parent's callbacks, even with callbacks=[] in our config.
        self.tasks[run["id"]] = asyncio.create_task(
            self._execute_queued(run["id"], predecessors, input_text, emit), context=Context()
        )
        if predecessors:
            await self._note(run["id"], "run_queued", {"waiting_for": predecessors})
        return run

    def connector_setup(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_run(run_id)
        rows = []
        if run["status"] == "waiting_connector":
            pending = run["input"].get("connector_setup") or {}
            rows = self._setup_rows(pending.get("connectors", []))
            rows += self._captured_connector_rows(run, {row["id"] for row in rows})
        return {"status": run["status"], "project_id": run["project_id"], "connectors": rows}

    def _captured_connector_rows(self, run: Run, listed: set[str]) -> list[dict[str, Any]]:
        """The task's other connectors whose tools are unavailable now. Continuing rebuilds the
        agent with every tool it captured, so each of them has to be ready, not only the one it
        paused for: the card asks for those too rather than the resume failing."""
        if not run.get("spec"):
            return []
        connected = self.connectors.tools()
        servers = {
            binding.name.split("__", 1)[0]
            for binding in load_spec(run["spec"]).enabled_tools()  # pyright: ignore[reportArgumentType]
            if CONNECTOR_TOOL.match(binding.name) and binding.name not in connected
        }
        return [connector_status(self.connectors, server) for server in sorted(servers - listed)]

    def _setup_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Current folder, connector and computer setup state for a paused tool."""
        grants = load_grants(self.repository.get_setting(GRANTS_SETTING, []))
        return [
            setup_status(row, grants) or setup_row_status(self.connectors, row, self.computer) for row in rows
        ]

    def choose_folder(self, run_id: str, path: str, setup_id: str | None = None) -> None:
        """The folder the user picked on a folder card: the one an agent asked where to work
        with, or one a task needs, such as a folder to save a file to.

        Where it works is kept for this chat on this Mac, so its later runs start there without
        asking. Kept before the card is marked chosen, so the run resumed from it is built in
        that folder.
        """
        run = self.repository.get_run(run_id)
        pending = run["input"].get("connector_setup") or {}
        row = record_folder(pending, path, setup_id) if run["status"] == "waiting_connector" else None
        if row is None:
            raise ValueError("This task is no longer waiting for a folder.")
        # The card allows the folder before it says which one it was. Where that was refused,
        # as for a folder holding the app's own data, nothing is kept.
        if self._setup_rows([row])[0]["state"] != "ready":
            raise ValueError(
                f"{row['path']} is not allowed, so the agent cannot use it. Choose another folder."
            )
        if row["id"] == REPOSITORY:
            stored = self.repository.get_setting(REPOSITORY_FOLDERS_SETTING, {})
            self.repository.set_setting(
                REPOSITORY_FOLDERS_SETTING,
                {
                    **(stored if isinstance(stored, dict) else {}),
                    run["input"].get("desktop_copy") or run["project_id"]: row["path"],
                },
            )
        self.repository.save_run_connector_setup(run_id, pending)

    def _github_row(self, run: Run) -> tuple[dict[str, Any], dict[str, Any]] | None:
        pending = run["input"].get("connector_setup") or {}
        row = next((row for row in pending.get("connectors", []) if row.get("local_kind") == "github"), None)
        return (pending, row) if run["status"] == "waiting_connector" and row is not None else None

    async def github_signed_in(self, run_id: str) -> str:
        """A GitHub card saw gh signed in: once gh says so too, every task waiting on that sign-in
        carries on, as with a connector's — it is this Mac's, not one chat's."""
        from .github_cli import signed_in_account

        if self._github_row(self.repository.get_run(run_id)) is None:
            raise ValueError("This task is no longer waiting for GitHub.")
        account = await signed_in_account()
        if not account:
            raise ValueError("GitHub is not signed in yet.")
        for waiting in dict.fromkeys(
            [run_id, *await asyncio.to_thread(self.repository.waiting_connector_runs)]
        ):
            with contextlib.suppress(KeyError):
                if found := self._github_row(self.repository.get_run(waiting)):
                    pending, row = found
                    row["account"] = account
                    self.repository.save_run_connector_setup(waiting, pending)
                    self._waiting_connector_ids.add(waiting)
        return account

    async def resume_connected(self) -> None:
        """Continue the same saved request after sign-in, including after a restart."""
        if not self._recovered_connector_waits:
            self._waiting_connector_ids.update(
                await asyncio.to_thread(self.repository.waiting_connector_runs)
            )
            self._recovered_connector_waits = True
        for run_id in tuple(self._waiting_connector_ids):
            if run_id in self._resuming_connectors:
                continue
            try:
                run = self.repository.get_run(run_id)
            except KeyError:
                self._waiting_connector_ids.discard(run_id)
                continue
            try:
                if run["status"] != "waiting_connector":
                    self._waiting_connector_ids.discard(run_id)
                    continue
                task = self.tasks.get(run_id)
                if task is not None and not task.done():
                    continue  # The graph must finish writing its interrupt checkpoint first.
                if (owner := self._helper_owner(run)) and self.repository.get_run(owner)[
                    "status"
                ] in TERMINAL_STATUSES:
                    await self.cancel(run_id)
                    continue
                rows = self.connector_setup(run_id)["connectors"]
                if not rows or any(row["state"] != "ready" for row in rows):
                    continue
                self._resuming_connectors.add(run_id)
                # Claim before rebuilding, so another poll cannot launch this request twice.
                try:
                    self.repository.update_run(run_id, status="queued", expected_status="waiting_connector")
                except ValueError:
                    continue
                await self._rehydrate(run_id, run, connector_setup=True)
                if self.repository.get_run(run_id)["status"] != "queued":
                    self.active.pop(run_id, None)
                    self.conditions.pop(run_id, None)
                    continue
                self._waiting_connector_ids.discard(run_id)
                emit = self._emitter(run_id, self.conditions[run_id])
                await emit("run_queued", {"waiting_for": run["input"].get("wait_for", [])})
                pending = run["input"]["connector_setup"]
                self.repository.save_run_connector_setup(run_id, None)
                self.tasks[run_id] = asyncio.create_task(
                    self._execute(
                        run_id,
                        Command(
                            resume={
                                key: {"folder": picked_folder(pending)} for key in pending["interrupt_ids"]
                            }
                        ),
                        emit,
                    ),
                    context=Context(),
                )
            except Exception as error:
                logger.exception("Could not resume project connector setup")
                self._waiting_connector_ids.discard(run_id)
                self.active.pop(run_id, None)
                if self.repository.get_run(run_id)["status"] not in TERMINAL_STATUSES:
                    self.repository.update_run(run_id, status="failed", error=describe_provider_error(error))
                    await self._note(run_id, "run_failed", {"message": describe_provider_error(error)})
            finally:
                self._resuming_connectors.discard(run_id)

    def _steering_messages(self, run_id):
        return [
            {**event["payload"], "id": event["id"]}
            for event in self.repository.list_run_events(run_id, event_types=("steering_requested",))
        ]

    def _helper_updates(self, run_id: str, seen: dict) -> dict:
        """Compact direct-child changes at the parent's model boundary, with durable cursors.

        Running progress stays in explicit tool reads. Only newly settled results or pauses
        are announced, after their checkpoint writer has released ownership. No code snapshot
        or diff is generated here; get_agent/wait_agents supply the saved integration evidence.
        """
        from .coding_agents import helper_cursor
        from .execution_policy import excerpt_text

        with self.coding_workspace.db() as db:
            children = {
                row["conversation_id"]: dict(row)
                for row in db.execute(
                    "SELECT id, title, conversation_id FROM coding_threads WHERE parent_run_id = ?", (run_id,)
                )
            }
        updates, pending, cursors, user_waits = [], [], {}, []
        pending_count = 0
        for run in self.repository.helper_runs(self.coding_workspace.agent_id, list(children)):
            child = children.get(run["conversation_id"])
            if child is None:
                continue
            child_id = child["id"]
            if child_id in seen:
                cursors[child_id] = seen[child_id]
            status = run["status"]
            released = run["id"] not in self.tasks
            brief = {"agent_id": child_id, "title": child["title"], "run_id": run["id"], "status": status}
            # A helper paused for the user's approval, setup or takeover cannot be resolved by
            # its parent, so it does not hold the parent's turn open.
            if released and self.needs_user(run):  # pyright: ignore[reportArgumentType]
                user_waits.append(run["id"])
            elif status not in TERMINAL_STATUSES or not released:
                pending_count += 1
                if len(pending) < 8:
                    pending.append(brief)
            if not released or status not in TERMINAL_STATUSES | WAITING_STATUSES or len(updates) >= 8:
                continue
            events = self.repository.list_run_events(run["id"], limit=1, tail=True)
            cursor = helper_cursor(run, events, released)
            if seen.get(child_id) == cursor:
                continue
            notice = {**brief, "cursor": cursor}
            if status in TERMINAL_STATUSES:
                saved = self.repository.get_run(run["id"])
                notice["summary"] = excerpt_text(
                    str((saved.get("output") or {}).get("text") or saved.get("error") or ""), 1200
                )
            else:
                # Questions and authorization/setup waits remain different events. The parent
                # receives a bounded hint and can inspect before deciding how to continue.
                notice["request"] = excerpt_text(
                    json.dumps(
                        [{"event_type": e["event_type"], "payload": e["payload"]} for e in events],
                        ensure_ascii=False,
                    ),
                    1200,
                )
            updates.append(notice)
            cursors[child_id] = cursor
        return {
            "updates": updates,
            "cursors": cursors,
            "pending": pending,
            "pending_count": pending_count,
            "wait_for_user": (lambda: self._await_user_helpers(run_id, user_waits)) if user_waits else None,
        }

    async def _await_user_helpers(self, run_id: str, helper_run_ids: list[str]) -> None:
        """Hold a parent's finished turn while its other helpers wait on the user.

        Their result only reaches this parent, which must stay open to integrate it. Waiting here
        costs no model calls and, like a running tool, pauses the idle limit. It ends when one of
        them changes or the user writes; Stop cancels it with the run.
        """
        active = self.active.get(run_id)
        marker = f"user-helpers:{run_id}"
        if active is not None:
            active.executing_tools.add(marker)
            if active.clock is not None:
                active.clock.reschedule(None)
        try:
            statuses = {helper: self.repository.get_run(helper)["status"] for helper in helper_run_ids}
            while not self.pending_steering(run_id) and all(
                self.repository.get_run(helper)["status"] == status for helper, status in statuses.items()
            ):
                # Run events wake this at once; the slow backstop only covers silent status changes.
                waiters = [
                    asyncio.create_task(self.changed(identifier, 30))
                    for identifier in (run_id, *helper_run_ids)
                ]
                try:
                    await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for waiter in waiters:
                        waiter.cancel()
                    await asyncio.gather(*waiters, return_exceptions=True)
        finally:
            if active is not None:
                active.executing_tools.discard(marker)
                self._touch_clock(active)

    def pending_steering(self, run_id: str) -> bool:
        events = self.repository.list_run_events(
            run_id, event_types=("steering_requested", "steering_applied")
        )
        applied = {
            event["payload"]["event_id"] for event in events if event["event_type"] == "steering_applied"
        }
        return any(
            event["event_type"] == "steering_requested" and event["id"] not in applied for event in events
        )

    async def steer(
        self,
        run_id: str,
        text: str,
        *,
        display_text: str | None = None,
        coding_context: dict[str, Any] | None = None,
        reference_files: list[dict[str, Any]] | None = None,
    ):
        run = self.repository.get_run(run_id)
        active = self.active.get(run_id)
        # A compaction answers nothing: a message steered into it was summarised away unanswered.
        # Refused, it is queued as the task after it.
        if run["status"] != "running" or active is None or not active.accepting_steering or active.compact:
            raise ValueError(
                "This task is finishing or is no longer running. Queue a follow-up for this agent instead."
            )
        if not text.strip():
            raise ValueError("Provide a message to steer this task.")
        # No await between accepting the message and recording it: finalization either
        # observes this update or closes steering first and the caller queues a follow-up.
        if reference_files:
            self.repository.capture_run_files(run_id, reference_files)
        await self._note(
            run_id,
            "steering_requested",
            {
                "text": text,
                "display_text": display_text if display_text is not None else text,
                "coding_context": coding_context or {},
                "reference_files": reference_files or [],
            },
        )
        return self.repository.get_run(run_id)

    async def _apply_steering(self, run_id: str, item: dict, state: dict) -> None:
        await self._capture_checkpoint(run_id, "before_steering", event_id=item["id"], state=state)
        if active := self.active.get(run_id):
            known = {row["id"] for row in active.reference_files}
            active.reference_files.extend(
                row for row in item.get("reference_files", []) if row["id"] not in known
            )
        # A process can stop between this event and the graph's next durable checkpoint.
        # Replaying that boundary applies the stable HumanMessage ID without a duplicate event.
        if not any(
            event["event_type"] == "steering_applied" and event["payload"]["event_id"] == item["id"]
            for event in self.repository.list_run_events(run_id, event_types=("steering_applied",))
        ):
            await self._note(
                run_id, "steering_applied", {"event_id": item["id"], "message_id": f"steering-{item['id']}"}
            )

    async def _capture_checkpoint(self, run_id: str, kind: str, **kwargs) -> None:
        try:
            await self.coding_workspace.checkpoints.capture(run_id, kind=kind, **kwargs)
        except Exception:
            # Code history is optional for ordinary execution; a failed capture
            # must not discard an otherwise valid answer or user instruction.
            logger.exception("Could not capture %s checkpoint for %s", kind, run_id)

    async def _prune_checkpoints(self, conversation_id: str) -> None:
        try:
            await self.coding_workspace.checkpoints.prune_models(conversation_id)
        except Exception:
            # Pruning only reclaims space; the finished run's answer stands either way.
            logger.exception("Could not prune checkpoints for %s", conversation_id)

    def _close_steering(self, run_id: str) -> None:
        if active := self.active.get(run_id):
            active.accepting_steering = False

    async def _execute_queued(self, run_id, predecessors, input_text, emit):
        try:
            for predecessor in predecessors:
                await self._settled(predecessor, TERMINAL_STATUSES)
            if run_id not in self.active:
                return
            # Once the task before it has settled: a follow-up queued behind a task that then
            # failed or was stopped takes over that task's helpers too.
            run = self.repository.get_run(run_id)
            if (
                run["kind"] == "user"
                and not run.get("parent_run_id")
                and (copy := run["input"].get("desktop_copy"))
            ):
                await self._adopt_helpers(run["project_id"], run["conversation_id"], run_id, copy)  # pyright: ignore[reportArgumentType]
            self.active[run_id].started_at = time.monotonic()
            # A compaction adds nothing to the conversation; its text is only what the chat shows.
            message = [] if self.active[run_id].compact else [{"role": "user", "content": input_text}]
            await self._execute(run_id, {"messages": message}, emit)
        except asyncio.CancelledError:
            if active := self.active.get(run_id):
                self._settle(run_id, active, "cancelled")
                await emit("run_cancelled", {"run_id": run_id})
            raise
        except Exception as error:
            self.repository.update_run(run_id, status="failed", error=str(error))
            self.active.pop(run_id, None)
            await emit("run_failed", {"message": str(error)})
        finally:
            self.tasks.pop(run_id, None)
            if run_id not in self.active:
                self.conditions.pop(run_id, None)

    async def changed(self, run_id: str, timeout: float = 1.0) -> None:
        """Until something is written on this run, or `timeout` passes.

        Wakes on the run's own events. The timeout is a backstop for the few status changes
        that come without one, so a reader checks once a second rather than polling SQLite.
        """
        condition = self.conditions.get(run_id)
        if condition is None:
            # A waiter queued before completion can start after execution already
            # released its condition. Do not recreate a settled run's state.
            if run_id not in self.tasks and self.repository.get_run(run_id)["status"] in TERMINAL_STATUSES:
                return
            condition = self.conditions.setdefault(run_id, asyncio.Condition())
        with suppress(TimeoutError):
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout)

    async def _settled(self, run_id: str, statuses: set[str]) -> Run:
        """Until the run is in one of `statuses` and its execution has let go of the thread.

        The persisted status can precede the checkpoint flush and stream cleanup, so a caller
        must not resume or rehydrate until that execution has actually yielded ownership.
        """
        while True:
            run = self.repository.get_run(run_id)
            task = self.tasks.get(run_id)
            if task is not None and not task.done():
                await asyncio.wait({task})
            elif run["status"] in statuses:
                return run
            else:
                await self.changed(run_id)

    async def _note(self, run_id: str | None, event_type: str, payload: dict[str, Any]) -> None:
        """Write one event onto a run from outside its own stream, waking its followers."""
        if not run_id:
            return
        with suppress(KeyError):
            self.repository.add_run_event(run_id, event_type, payload, None)
        condition = self.conditions.get(run_id)
        if condition is not None:
            async with condition:
                condition.notify_all()

    def _emitter(self, run_id: str, condition: asyncio.Condition):
        async def emit(
            event_type: str, payload: dict[str, Any], node_id: str | None = None
        ) -> dict[str, Any]:
            event = self.repository.add_run_event(run_id, event_type, payload, node_id)
            async with condition:
                condition.notify_all()
            return event

        return emit

    async def _execute(
        self,
        run_id: str,
        command: dict[str, Any] | Command,
        emit,
        *,
        rejected_commands: dict[str, str] | None = None,
    ) -> None:
        active = self.active[run_id]
        config = {
            "configurable": {
                "thread_id": active.thread_id,
                "wavefunction_rejected_commands": rejected_commands or {},
                **({COMPACT_KEY: True} if active.compact else {}),
            },
            "recursion_limit": STEP_LIMIT,
        }
        self.repository.update_run(run_id, status="running")
        await emit("run_started", {"run_id": run_id})
        if active.parent_run_id and isinstance(command, Command):
            # Its pause was decided, in its own chat or in its main chat's copy of the card.
            await self._mirror_helper(run_id, "helper_resolved")

        open_calls: dict[str, str] = {}
        seen_messages: set[str] = set()
        # Assistant tokens stream to the UI as they arrive, in buffered pieces rather than one
        # event per token — every event is a SQLite row, and a row per token would swamp the
        # database for text the finished run stores anyway. The delta rows are deleted once the
        # run settles; until then they are what a reconnecting client replays.
        delta_buffer = ""
        delta_message_id = None
        partial_after = 0
        partial_pending = False

        async def flush_deltas(force: bool = False) -> None:
            nonlocal delta_buffer
            if delta_buffer and (force or len(delta_buffer) >= DELTA_FLUSH_CHARS):
                await emit("assistant_delta", {"text": delta_buffer, "message_id": delta_message_id})
                delta_buffer = ""

        async def preserve_partial() -> None:
            nonlocal partial_pending
            if not partial_pending:
                return
            await flush_deltas(force=True)
            # Read only this attempt's deltas, once on interruption. Finished tool results
            # and earlier answers stay in their checkpoints; incomplete calls never enter it.
            deltas = self.repository.list_run_events(run_id, partial_after, event_types=("assistant_delta",))
            pieces: dict[str | None, list[RunEvent]] = {}
            for event in deltas:
                payload = event["payload"]
                pieces.setdefault(payload.get("message_id"), []).append(event)
            for message_id, events in pieces.items():
                text = "".join(event["payload"].get("text", "") for event in events)
                if text.strip():
                    # The completion stays append-only for stream cursors, while its
                    # original position survives compaction and mid-reply steering.
                    self.repository.preserve_assistant_start(run_id, events[0]["id"])
                    await emit("assistant_message", {"text": text, "message_id": message_id, "partial": True})
            partial_pending = False
            self.repository.delete_run_events(run_id, "assistant_delta")

        try:
            interrupted = False
            setup_interrupts = []
            computer_interrupts = []
            question_interrupts = []
            approval_requests = []
            if isinstance(command, dict):
                await repair_interrupted_tool_calls(active.agent, config)
            previous = await active.agent.aget_state(config)
            seen_messages.update(
                message.id
                for message in (previous.values or {}).get("messages", [])
                if getattr(message, "id", None)
            )
            # Summarization can replay earlier turns. Their limits belong to their own runs. Set
            # at the request, not at each resume: what the run said before a pause is its own.
            if isinstance(command, dict):
                active.previous_message_ids = set(seen_messages)
                await self._capture_checkpoint(run_id, "before_user")
            async with asyncio.timeout(IDLE_TIMEOUT) as clock:
                active.clock = clock
                async for mode, chunk in active.agent.astream(
                    command, config=config, stream_mode=["updates", "messages", "custom"], durability="sync"
                ):
                    # Something happened: the run is alive, so the clock starts over.
                    self._touch_clock(active)
                    if mode == "custom":
                        if isinstance(chunk, dict) and chunk.get("type") == NESTED_USAGE:
                            # A model call inside a tool, such as a web search, spends too.
                            active.usage = _merge_usage(active.usage, chunk.get("payload", {}).get("usage"))
                            continue
                        if isinstance(chunk, dict) and chunk.get("type"):
                            event, payload = chunk["type"], chunk.get("payload", {})
                            if event == "execution_tool_started":
                                active.executing_tools.add(payload["call_id"])
                                clock.reschedule(None)
                            elif event == "execution_tool_finished":
                                active.executing_tools.discard(payload["call_id"])
                                self._touch_clock(active)
                            elif event == "model_retry":
                                active.retry_until = (
                                    asyncio.get_running_loop().time() + payload["delay_seconds"]
                                )
                                self._touch_clock(active)
                            elif event in ("model_call_started", "summarization_started"):
                                active.retry_until = 0
                                active.model_calls.add(in_flight_id(payload))
                                clock.reschedule(None)
                            elif event in (
                                "model_call_finished",
                                "model_call_failed",
                                "summarization_finished",
                                "summarization_failed",
                            ):
                                active.model_calls.discard(in_flight_id(payload))
                                self._touch_clock(active)
                            if recovered := payload.get("recovered_usage"):
                                active.usage = _merge_usage(active.usage, recovered)
                                self.repository.update_run(
                                    run_id,
                                    status="running",
                                    usage=active.usage,
                                    duration_ms=int((time.monotonic() - active.started_at) * 1000),
                                )
                            if event in (
                                "model_call_finished",
                                "model_call_failed",
                                "summarization_finished",
                                "summarization_failed",
                            ):
                                # The run's total so far, which the call's streamed reply (or the
                                # usage a failed call recovered) has already added to: a chat
                                # following the run keeps its token count current.
                                payload = {**payload, "run_usage": active.usage}
                            recorded = await emit(event, payload)
                            if event == "model_call_started":
                                partial_after = recorded["sequence"]
                            elif event == "model_call_failed":
                                await preserve_partial()
                        continue
                    if mode == "messages":
                        message, metadata = chunk
                        # Internal model calls still cost tokens, even when their prose is hidden.
                        usage = message_usage(message)
                        if usage:
                            active.usage = _merge_usage(active.usage, usage)
                            self.repository.update_run(
                                run_id,
                                status="running",
                                usage=active.usage,
                                duration_ms=int((time.monotonic() - active.started_at) * 1000),
                            )
                        # Message streams include model calls inside tools/middleware as well.
                        # Only this graph's model node speaks in this run's answer bubble.
                        namespace = metadata.get("langgraph_checkpoint_ns", "")
                        if metadata.get("langgraph_node") != "model" or "|" in namespace:
                            continue
                        message_id = getattr(message, "id", None)
                        if message_id != delta_message_id:
                            await flush_deltas(force=True)
                            delta_message_id = message_id
                        piece = _delta_text(message)
                        if piece:
                            partial_pending = True
                            delta_buffer += piece
                            await flush_deltas()
                        continue

                    await flush_deltas(force=True)
                    for node_name, update in (chunk or {}).items():
                        if node_name == "__interrupt__":
                            await flush_deltas(force=True)
                            computer_interrupts.extend(
                                item
                                for item in update
                                if isinstance(item.value, dict)
                                and item.value.get("kind") == "computer_takeover"
                            )
                            setup_interrupts.extend(
                                item
                                for item in update
                                if isinstance(item.value, dict)
                                and item.value.get("kind") == "connector_setup"
                            )
                            question_interrupts.extend(
                                item
                                for item in update
                                if isinstance(item.value, dict)
                                and item.value.get("kind") == "coding_question"
                            )
                            approval_requests.extend(
                                request
                                for request in interrupt_requests(update)
                                if request.get("kind") not in {"connector_setup", "coding_question"}
                            )
                            interrupted = True
                            break
                        if not isinstance(update, dict):
                            continue
                        for message in novel_messages(update, seen_messages):
                            if reason := limit_message(message):
                                await emit("limit_reached", {"message": reason})
                            for event in _message_events(message, open_calls, active.agent_id):
                                await emit(event["type"], event["payload"], event.get("node_id"))
                                if event["type"] == "assistant_message":
                                    partial_pending = False
                    # Let the graph stream finish after its interrupt. Breaking the async
                    # iterator here leaves checkpoint writes in its unfinished generator,
                    # even after this task returns and an approval is allowed to resume.

            await flush_deltas(force=True)
            if interrupted:
                # The paused text is in its finished message now; deltas left behind would be
                # replayed after the resume as a second, stale "writing" line.
                self.repository.delete_run_events(run_id, "assistant_delta")
                if computer_interrupts:
                    await self.computer.control(active.thread_id).claim()
                    self.repository.update_run(run_id, status="waiting_computer")
                    await emit("run_computer_takeover", {})
                    return
                if question_interrupts:
                    self.repository.update_run(run_id, status="waiting_approval")
                    await emit(
                        "run_questions",
                        {
                            "groups": [
                                {"id": item.id, "questions": item.value["questions"]}
                                for item in question_interrupts
                            ]
                        },
                    )
                    return
                if setup_interrupts:
                    pending = {
                        "connectors": list(
                            {
                                row["id"]: row
                                for item in setup_interrupts
                                for row in item.value["connectors"]
                            }.values()
                        ),
                        "interrupt_ids": [item.id for item in setup_interrupts],
                    }
                    self.repository.save_run_connector_setup(run_id, pending)
                    self._waiting_connector_ids.add(run_id)
                    status = "waiting_connector"
                else:
                    active.pending_decisions = max(1, len(approval_requests))
                    status = "waiting_approval"
                self.repository.update_run(
                    run_id,
                    status=status,
                    duration_ms=int((time.monotonic() - active.started_at) * 1000),
                    usage=active.usage,
                )
                if setup_interrupts:
                    await emit("run_connector_setup_required", pending)
                else:
                    await emit(
                        "run_waiting_for_approval",
                        {"requests": approval_requests, "count": active.pending_decisions},
                    )
                return

            await self._finish(run_id, active, emit)
            self.repository.delete_run_events(run_id, "assistant_delta")
        except asyncio.CancelledError:
            await preserve_partial()
            self._settle(run_id, active, "cancelled")
            await emit("run_cancelled", {"run_id": run_id})
            self.repository.delete_run_events(run_id, "assistant_delta")
            await self._stop_helpers(run_id)
            raise
        except Exception as error:  # noqa: BLE001 - recorded on the run and shown to the user
            await preserve_partial()
            duration_ms = int((time.monotonic() - active.started_at) * 1000)
            stopped = await self._stop_helpers(run_id)
            # SDK stream timeouts also inherit TimeoutError. Only an expired app clock is
            # app inactivity; a provider timeout keeps its own description and diagnostics.
            if isinstance(error, TimeoutError) and active.clock is not None and active.clock.expired():
                message = (
                    f"Nothing happened for {IDLE_TIMEOUT // 60} minutes — no reply, no tool call — so "
                    "the run was stopped. The model may be stuck in a loop that produces nothing, or "
                    "the provider never answered. Try again; if it keeps happening, pick another model."
                    + (f" {stopped} helper run(s) it was waiting on were stopped with it." if stopped else "")
                )
            else:
                logger.exception("Run %s failed", run_id)
                message = describe_provider_error(error)
            message += _account_suffix(self.repository, run_id, duration_ms)
            self.repository.update_run(
                run_id,
                status="failed",
                error=message,
                duration_ms=duration_ms,
                # The tokens already spent: a run that fails after real model calls still cost
                # money, and recording {} would say it cost nothing.
                usage=active.usage,
            )
            await emit("run_failed", {"message": message})
            self.repository.delete_run_events(run_id, "assistant_delta")
            self.active.pop(run_id, None)
        finally:
            settled = self.repository.get_run(run_id)["status"]
            if active.parent_run_id and settled in WAITING_STATUSES:
                await self._mirror_helper(run_id, "helper_waiting")
            if settled in TERMINAL_STATUSES:
                await self._release_finished(run_id)
            # A request can arrive after the graph's final checkpoint. Do not leave
            # an unclaimed handoff holding the desktop after its last task ends.
            if self.computer is not None and self.repository.get_run(run_id)["status"] in TERMINAL_STATUSES:
                control = self.computer.control(active.thread_id)
                if control.state == "pausing" and not any(
                    peer["status"] not in TERMINAL_STATUSES
                    for peer in self.repository.list_conversation_runs(active.project_id, active.thread_id)
                ):
                    control.state = "agent"
            if self.repository.get_run(run_id)["status"] in TERMINAL_STATUSES:
                self.coding_workspace.cleanup.touch(
                    (self.repository.get_run(run_id).get("spec") or {}).get("working_directory")
                )
                if self.repository.get_run(run_id)["status"] != "completed":
                    await self._capture_checkpoint(run_id, "settled")
                # Before the task lets go: a queued follow-up or a fork waits, so no new
                # checkpoint or message point can appear mid-prune.
                await self._prune_checkpoints(active.thread_id)
            # This task has relinquished the checkpoint writer. Subscribers retain their
            # own condition reference and can read terminal events from SQLite.
            self.tasks.pop(run_id, None)
            if run_id not in self.active:
                self.conditions.pop(run_id, None)
                self._run_locks.pop(run_id, None)

    def _run_lock(self, run_id: str) -> asyncio.Lock:
        return self._run_locks.setdefault(run_id, asyncio.Lock())

    async def _mirror_helper(self, run_id: str, event_type: str) -> None:
        """Copy a helper's pause onto its main task, whose chat shows the card until it is resolved."""
        try:
            await self._copy_helper_pause(run_id, event_type)
        except Exception:
            # Settling the run (releasing its task) must go on; the card copy is a convenience.
            logger.exception("Could not copy %s's pause to its main task", run_id)

    async def _copy_helper_pause(self, run_id: str, event_type: str) -> None:
        run = self.repository.get_run(run_id)
        try:
            thread = self.coding_workspace.thread(run["input"].get("desktop_copy") or "")
        except KeyError:
            return
        if not thread.get("parent_run_id"):
            return
        payload = {
            "agent_id": thread["id"],
            "title": thread["title"],
            "run_id": run_id,
            "status": run["status"],
        }
        pauses = self.repository.list_run_events(run_id, limit=1, tail=True, event_types=PAUSE_EVENTS)
        # A helper's question is its main agent's to answer, which asks the user only if it can't:
        # neither the question nor its answer is copied to the main chat.
        if pauses and pauses[-1]["event_type"] == "run_questions":
            return
        if event_type == "helper_waiting":
            if not pauses:
                return
            payload["pause"] = {key: pauses[-1][key] for key in ("event_type", "sequence", "payload")}
        await self._note(thread["parent_run_id"], event_type, payload)

    async def _release_finished(self, run_id: str) -> None:
        """What a finished task leaves behind: a helper's desktop, or a main task's helper terminals."""
        try:
            await self._release_resources(run_id)
        except Exception:
            # A desktop or terminal that won't close must not keep the run from settling.
            logger.exception("Could not release what %s left behind", run_id)

    async def _release_resources(self, run_id: str) -> None:
        run = self.repository.get_run(run_id)
        conversation = run.get("conversation_id") or run_id
        if run.get("parent_run_id"):
            # Done, it leaves the sidebar; a follow-up from its main task brings it back.
            if self.coding_workspace is not None and not self.repository.pending_conversation_runs(
                run["project_id"], conversation
            ):
                self.coding_workspace.archive_helper(run["input"].get("desktop_copy") or "")
            if (
                self.computer is not None
                and self.computer.running(conversation, run["input"].get("desktop_copy"))
                and not any(
                    peer["id"] != run_id and peer["status"] not in TERMINAL_STATUSES
                    for peer in self.repository.list_conversation_runs(run["project_id"], conversation)
                )
            ):
                await self.computer.close_session(conversation)
            return
        await self.coding_workspace.close_helper_terminals(run_id)

    async def _finish(self, run_id: str, active: ActiveRun, emit) -> None:  # noqa: C901
        config = {"configurable": {"thread_id": active.thread_id}}
        snapshot = await active.agent.aget_state(config)
        values = dict(snapshot.values or {})
        # A loop stop left its notice as the last assistant message.
        final = next((m for m in reversed(values.get("messages", [])) if isinstance(m, AIMessage)), None)
        ended = limit_message(final) if final is not None else ""
        # This request's messages, less the notice the harness wrote to end it: a loop stop is
        # a notice about the run, not something the agent said.
        messages = [
            m
            for m in values.get("messages", [])
            if getattr(m, "id", None) not in active.previous_message_ids
            and not (isinstance(m, AIMessage) and limit_message(m))
        ]
        text = result_text(messages)
        duration_ms = int((time.monotonic() - active.started_at) * 1000)
        from .execution_policy import completion_error

        reason = completion_error(messages)
        if active.compact:
            # Nothing was asked, so there is no answer to check: only whether a summary was made.
            text, ended, reason = (
                ("Context compacted." if messages else "There was nothing to compact."),
                "",
                "",
            )
        if ended or reason:
            await self._stop_helpers(run_id)
            if ended:
                # Stopped as stuck before there was an answer: said as that, not as something
                # the model could not do.
                reason = ended + _account_suffix(self.repository, run_id, duration_ms)
                self.repository.update_run(
                    run_id, status="failed", error=reason, duration_ms=duration_ms, usage=active.usage
                )
                await emit("run_failed", {"message": reason})
                self.active.pop(run_id, None)
                return
            self.repository.update_run(
                run_id,
                status="failed",
                output={"text": text},
                error=reason,
                duration_ms=duration_ms,
                usage=active.usage,
            )
            await emit("run_failed", {"message": reason, "text": text, "partial": True})
            self.active.pop(run_id, None)
            return

        output = {"text": text}
        await self._capture_checkpoint(run_id, "after_assistant")
        self.repository.update_run(
            run_id, status="completed", output=output, duration_ms=duration_ms, usage=active.usage
        )
        await emit("run_completed", {**output, "duration_ms": duration_ms, "usage": active.usage})
        self.active.pop(run_id, None)

    def _touch_clock(self, active: ActiveRun) -> None:
        """Something happened on this run: it has the full idle allowance again.

        Not while a model call or a tool is in flight: the clock stays stopped until
        the last of them is back, and each has a limit of its own.
        """
        clock = active.clock
        if clock is None or clock.expired() or active.executing_tools or active.model_calls:
            return
        clock.reschedule(max(asyncio.get_running_loop().time(), active.retry_until) + IDLE_TIMEOUT)

    async def _stop_helpers(self, run_id: str) -> int:
        """Cancel every helper run this task started that is still going.

        A helper's result only ever reaches its parent, so once the parent has ended the work
        would be spent with no reader.
        """
        children = {
            child_id for child_id, child in list(self.active.items()) if child.parent_run_id == run_id
        }
        with self.coding_workspace.db() as db:
            conversations = [
                row[0]
                for row in db.execute(
                    "SELECT conversation_id FROM coding_threads WHERE parent_run_id = ?", (run_id,)
                )
            ]
        for conversation in conversations:
            children.update(
                run["id"]
                for run in self.repository.list_conversation_runs(
                    self.coding_workspace.agent_id, conversation
                )
                if run["status"] not in TERMINAL_STATUSES or run["id"] in self.tasks
            )
        for child_id in children:
            await self.cancel(child_id)
        return len(children)

    def _settle(self, run_id: str, active: ActiveRun, status: str) -> None:
        duration_ms = int((time.monotonic() - active.started_at) * 1000)
        self.repository.update_run(run_id, status=status, duration_ms=duration_ms, usage=active.usage)
        self.active.pop(run_id, None)

    async def _rehydrate(self, run_id: str, run: Run, *, connector_setup: bool = False) -> ActiveRun:
        """Rebuild a paused run from its checkpoint after the app restarted.

        The checkpointer stores the graph's state under the run id as thread id, so the pause
        itself survives a restart. What does not survive is the in-memory `ActiveRun`, and
        without it every approval was rejected — which made the durable checkpointer pointless.
        """
        spec = load_spec(run["spec"])  # pyright: ignore[reportArgumentType]
        # The stored project_id is the agent package owner, not the coding project group.
        agent_id = run["project_id"]
        reference_files = run["input"].get("reference_files")
        agent = build_agent(
            spec,
            self.models,
            ToolContext(
                agent_id,
                self.repository,
                self.files,
                self.models.secrets,
                run_id=run_id,
                parent_run_id=run.get("parent_run_id"),
                local_read_only=run["input"].get("local_read_only", False),
                folder_grants=run["input"].get("folder_grants"),
                connectors=self.connectors,
                skills=self.skills,
                computer=self.computer,
                conversation_id=run.get("conversation_id") or run_id,
                desktop_copy=run["input"].get("desktop_copy") or run["project_id"],
                reference_files=reference_files,
                steering=lambda: self._steering_messages(run_id),
                close_steering=lambda: self._close_steering(run_id),
                apply_steering=lambda item, state: self._apply_steering(run_id, item, state),
                helper_updates=(lambda seen: self._helper_updates(run_id, seen))
                if not run.get("parent_run_id")
                else None,
                record_action_unknown=lambda payload: self.repository.add_run_event(
                    run_id, "action_outcome_unknown", payload
                ),
                choose_folder=run.get("kind") == "user" and not run.get("parent_run_id"),
                coding_workspace=self.coding_workspace,
            ),
            checkpointer=await self.checkpointer(),
        )
        thread_id = run.get("conversation_id") or run_id
        pending = []
        if not connector_setup:
            state = await agent.aget_state({"configurable": {"thread_id": thread_id}})
            pending = interrupt_requests(getattr(state, "interrupts", ()) or ())
        # Rebuild the clock from the run's own start time, not from the duration saved at the
        # pause. A run in the same process keeps its monotonic clock through the wait, so
        # measuring only active time here would make the two paths disagree about how long the
        # same run took.
        elapsed = _seconds_since(run.get("started_at"))
        active = ActiveRun(
            agent=agent,
            project_id=run["project_id"],
            thread_id=thread_id,
            started_at=time.monotonic() - elapsed,
            pending_decisions=max(1, len(pending)) if not connector_setup else 0,
            usage=dict(run.get("usage") or {}),
            agent_id=agent_id,
            parent_run_id=run.get("parent_run_id"),
            reference_files=reference_files if reference_files is not None else [],
        )
        self.active[run_id] = active
        self.conditions.setdefault(run_id, asyncio.Condition())
        return active

    def _helper_owner(self, run: Run) -> str | None:
        """The main task a helper run now answers to: a later task in its chat can take it over."""
        if not run.get("parent_run_id"):
            return None
        try:
            return (
                self.coding_workspace.thread(run["input"].get("desktop_copy") or "").get("parent_run_id")
                or run["parent_run_id"]  # pyright: ignore[reportTypedDictNotRequiredAccess, reportGeneralTypeIssues]
            )
        except KeyError:
            return run["parent_run_id"]  # pyright: ignore[reportTypedDictNotRequiredAccess, reportGeneralTypeIssues]

    async def _adopt_helpers(self, agent_id: str, conversation_id: str, run_id: str, thread_id: str) -> None:
        """A task started after one that failed or was stopped takes over that task's helpers.

        Asked to continue, it is told their state at its first step and can restart them with
        send_message. Helpers still waiting on the user show their card in this task's chat.
        """
        with self.coding_workspace.db() as db:
            if (
                db.execute(
                    "SELECT 1 FROM coding_threads WHERE parent_thread_id = ? LIMIT 1", (thread_id,)
                ).fetchone()
                is None
            ):
                return  # Most chats never had a helper: no history is read for them.
        previous = self.repository.previous_user_run(agent_id, conversation_id, run_id)
        if previous is None or previous["status"] not in {"failed", "cancelled"}:
            return
        with self.coding_workspace.db() as db:
            adopted = [
                row["conversation_id"]
                for row in db.execute(
                    "SELECT conversation_id FROM coding_threads WHERE parent_thread_id = ? AND parent_run_id = ?",
                    (thread_id, previous["id"]),
                )
            ]
            db.execute(
                "UPDATE coding_threads SET parent_run_id = ? WHERE parent_thread_id = ? AND parent_run_id = ?",
                (run_id, thread_id, previous["id"]),
            )
        for helper in self.repository.helper_runs(agent_id, adopted):
            if helper["status"] in WAITING_STATUSES:
                await self._mirror_helper(helper["id"], "helper_waiting")

    async def _require_live_parent(self, run: Run) -> None:
        parent_id = self._helper_owner(run)
        if not parent_id:
            return
        try:
            ended = self.repository.get_run(parent_id)["status"] in TERMINAL_STATUSES
        except KeyError:
            ended = True
        if ended:
            await self.cancel(run["id"])
            raise ValueError("This helper's parent task has ended. Start a new helper from the current task.")

    async def resume_computer(self, run_id: str) -> Run:
        async with self._run_lock(run_id):
            run = self.repository.get_run(run_id)
            if run["status"] != "waiting_computer":
                raise ValueError("The agent is not ready to resume from computer control.")
            await self._require_live_parent(run)
            task = self.tasks.get(run_id)
            if task is not None and not task.done():
                await asyncio.wait({task})
            active = self.active.get(run_id) or await self._rehydrate(run_id, run)
            state = await active.agent.aget_state({"configurable": {"thread_id": active.thread_id}})
            answer = {
                item.id: {"computer_takeover": "resumed"}
                for item in state.interrupts
                if isinstance(item.value, dict) and item.value.get("kind") == "computer_takeover"
            }
            if not answer:
                raise ValueError("The computer pause is no longer available.")
            # Claimed only if still paused: Stop can land while this waited on the stream.
            self.repository.update_run(run_id, status="queued", expected_status="waiting_computer")
            self.tasks[run_id] = asyncio.create_task(
                self._execute(run_id, Command(resume=answer), self._emitter(run_id, self.conditions[run_id])),
                context=Context(),
            )
            return self.repository.get_run(run_id)

    async def answer_questions(
        self, run_id: str, answers: dict[str, str], sequence: int, *, dismiss: bool = False
    ):
        """Answer a paused task's open questions, or close them unanswered (`dismiss`)."""
        async with self._run_lock(run_id):
            return await self._answer_questions(run_id, answers, sequence, "dismissed" if dismiss else None)

    async def _answer_questions(
        self,
        run_id: str,
        answers: dict[str, str],
        sequence: int,
        closed: str | None,
        *,
        source: str | None = None,
    ) -> Run:
        """With this run's lock held. `closed` is "dismissed" or "replied" when no form answers come."""
        run = self.repository.get_run(run_id)
        pause = self._latest_pause(run_id)
        if (
            run["status"] != "waiting_approval"
            or not pause
            or pause["event_type"] != "run_questions"
            or pause["sequence"] != sequence
        ):
            raise ValueError("These questions changed or were already answered. Reload this conversation.")
        await self._require_live_parent(run)
        previous = self.tasks.get(run_id)
        if previous and not previous.done():
            await asyncio.wait({previous})
        active = self.active.get(run_id) or await self._rehydrate(run_id, run, connector_setup=True)
        state = await active.agent.aget_state({"configurable": {"thread_id": active.thread_id}})
        pending = [
            i
            for i in state.interrupts
            if isinstance(i.value, dict) and i.value.get("kind") == "coding_question"
        ]
        if closed:
            if not pending or answers:
                raise ValueError(
                    "These questions changed or were already answered. Reload this conversation."
                )
            origin = {"source": source} if source else {}
            answer = {item.id: {closed: True, **origin} for item in pending}
            payload: dict[str, Any] = {"sequence": sequence, closed: True, **origin}
        else:
            expected = {f"{item.id}:{q['id']}" for item in pending for q in item.value["questions"]}
            if (
                not pending
                or set(answers) != expected
                or any(not a.strip() or len(a) > 4000 for a in answers.values())
            ):
                raise ValueError("Answer each question with up to 4,000 characters.")
            answer = {
                item.id: {
                    "answers": {
                        q["id"]: answers[f"{item.id}:{q['id']}"].strip() for q in item.value["questions"]
                    }
                }
                for item in pending
            }
            payload = {"sequence": sequence, "answers": answers}
        emit = self._emitter(run_id, self.conditions[run_id])
        await emit("questions_answered", payload)
        self.repository.update_run(run_id, status="queued", expected_status="waiting_approval")
        self.tasks[run_id] = asyncio.create_task(
            self._execute(run_id, Command(resume=answer), emit), context=Context()
        )
        return self.repository.get_run(run_id)

    def _latest_pause(self, run_id: str) -> RunEvent | None:
        events = self.repository.list_run_events(
            run_id, limit=1, tail=True, event_types=("run_questions", "run_waiting_for_approval")
        )
        return events[-1] if events else None

    def _asks_questions(self, run: Run) -> bool:
        pause = self._latest_pause(run["id"])
        return run["status"] == "waiting_approval" and bool(pause) and pause["event_type"] == "run_questions"

    def needs_user(self, run: Run) -> bool:
        """Paused for an approval, setup or takeover: only the user can resume it, not a parent."""
        return run["status"] in WAITING_STATUSES and not self._asks_questions(run)

    async def reply(
        self,
        run_id: str,
        text: str,
        *,
        display_text: str | None = None,
        coding_context: dict[str, Any] | None = None,
        reference_files: list[dict[str, Any]] | None = None,
        parent_run_id: str | None = None,
    ) -> Run:
        """A chat message for a task waiting on the user, rather than a follow-up queued behind it.

        It reaches the agent as steering at the task's next model call. Open questions take it as
        their answer and the task carries on; any other pause keeps it until the user decides.
        """
        if not text.strip():
            raise ValueError("Provide a message for this task.")
        async with self._run_lock(run_id):
            run = self.repository.get_run(run_id)
            if run["status"] not in WAITING_STATUSES:
                raise ValueError("This task is no longer waiting for you. Queue a follow-up instead.")
            pause: Any = self._latest_pause(run_id)
            questions = (
                run["status"] == "waiting_approval" and pause and pause["event_type"] == "run_questions"
            )
            if parent_run_id:
                if (
                    self._helper_owner(run) != parent_run_id
                    or self.repository.get_run(parent_run_id)["status"] != "running"
                ):
                    raise ValueError("Only this helper's running parent can answer its clarification.")
                if not questions:
                    raise ValueError(
                        "This helper needs a user decision or setup. Parent messages cannot approve it."
                    )
                text = "Parent agent clarification (not a user approval):\n\n" + text
            if reference_files:
                self.repository.capture_run_files(run_id, reference_files)
            await self._note(
                run_id,
                "steering_requested",
                {
                    "text": text,
                    "display_text": display_text if display_text is not None else text,
                    "coding_context": coding_context or {},
                    "reference_files": reference_files or [],
                    **({"source": "parent"} if parent_run_id else {}),
                    # A reply to a pause (an answer, "I signed in") is not a new request to delegate.
                    # A message written while an approval card waits can be one, so it keeps the hint.
                    "pause_reply": bool(questions) or run["status"] != "waiting_approval",
                    **({"reply_to": pause["sequence"]} if questions else {}),
                },
            )
            if questions:
                return await self._answer_questions(
                    run_id, {}, pause["sequence"], "replied", source="parent" if parent_run_id else None
                )
            return self.repository.get_run(run_id)

    async def resume(
        self, run_id: str, decision: str, feedback: str = "", *, expected_approval: int | None = None
    ) -> Run:
        # Decisions share this run's lock. A stale card must not approve the next request merely
        # because the same run has paused for approval again.
        async with self._run_lock(run_id):
            if expected_approval is not None:
                requests = [
                    event
                    for event in self.repository.list_run_events(run_id)
                    if event["event_type"] == "run_waiting_for_approval"
                ]
                if not requests or requests[-1]["sequence"] != expected_approval:
                    raise ValueError("This approval has changed. Review the current action.")
            return await self._resume(run_id, decision, feedback)

    async def _resume(self, run_id: str, decision: str, feedback: str = "") -> Run:
        """Answer what a paused run asked, and let it carry on."""
        run = self.repository.get_run(run_id)
        if run["status"] != "waiting_approval":
            raise ValueError("run is not waiting for an approval")
        await self._require_live_parent(run)
        latest_pause = self._latest_pause(run_id)
        if latest_pause and latest_pause["event_type"] == "run_questions":
            raise ValueError("Answer this task's questions before continuing.")
        previous_task = self.tasks.get(run_id)
        if previous_task is not None and not previous_task.done():
            # Pause events reach clients before the stream/checkpointer has necessarily closed.
            # Do not let an immediate approval race that final checkpoint write.
            await asyncio.wait({previous_task})
            run = self.repository.get_run(run_id)
            if run["status"] != "waiting_approval":
                raise ValueError("run is not waiting for an approval")
        active = self.active.get(run_id) or await self._rehydrate(run_id, run)
        state = await active.agent.aget_state({"configurable": {"thread_id": active.thread_id}})
        condition = self.conditions[run_id]
        emit = self._emitter(run_id, condition)

        await emit("approval_decided", {"decision": decision, "feedback": feedback})
        # One decision per gated tool call, in the order the middleware requested them.
        entry: dict[str, Any] = (
            {"type": "approve"}
            if decision == "approve"
            else {"type": "reject", **({"message": feedback} if feedback else {})}
        )
        decisions = [entry for _ in range(max(1, active.pending_decisions))]
        rejected_commands = {}
        if latest_pause:
            requests = latest_pause["payload"].get("requests", [])
            # A decision names what its card showed: the folder, or the command.
            commands = ("outside_sandbox", "irreversible")
            if any(request.get("kind") in ("folder_access", *commands) for request in requests):
                decisions = [
                    {
                        **entry,
                        **(
                            {"folder": request["folder"], "writable": request["writable"]}
                            if request.get("kind") == "folder_access"
                            else {}
                        ),
                        **(
                            {
                                "command": request["command"],
                                "cwd": request.get("cwd"),
                                "danger": request.get("danger"),
                            }
                            if request.get("kind") in commands
                            else {}
                        ),
                    }
                    for request in requests
                ]
            if decision == "reject":
                requested = {
                    request["command"]: request for request in requests if request.get("kind") in commands
                }
                messages = (state.values or {}).get("messages", [])
                answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
                last = next(
                    (message for message in reversed(messages) if isinstance(message, AIMessage)), None
                )
                for call in last.tool_calls if last else []:
                    if (
                        call["name"] == "run_command"
                        and call["id"] not in answered
                        and (request := requested.get(call["args"].get("command")))
                    ):
                        reason = (
                            "this command, which can't be undone"
                            if request["kind"] == "irreversible"
                            else "this command outside the sandbox"
                        )
                        rejected_commands[call["id"]] = (
                            f"The user did not allow {reason}. Do not retry it or work around it; "
                            "continue without it or ask how they want to proceed."
                        )
        active.pending_decisions = 0
        # Parallel tool calls have separate interrupts. Each must receive the displayed
        # batch decision under its own ID, as with question and connector resumes.
        answer = {item.id: {"decisions": decisions} for item in state.interrupts}

        # Claimed only if still paused: Stop can land while this waited on the stream above.
        self.repository.update_run(run_id, status="queued", expected_status="waiting_approval")
        self.tasks[run_id] = asyncio.create_task(
            self._execute(run_id, Command(resume=answer), emit, rejected_commands=rejected_commands),
            context=Context(),
        )
        return self.repository.get_run(run_id)

    async def cancel(self, run_id: str, reason: str | None = None) -> Run:
        """Stop a run, saying why where its answer would be: stopped by the user unless told."""
        self._waiting_connector_ids.discard(run_id)
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        run = self.repository.get_run(run_id)
        await self._stop_helpers(run_id)
        # Cancellation before the task's first instruction cannot reach its try/finally.
        if run["status"] not in TERMINAL_STATUSES:
            if run["status"] in WAITING_STATUSES:
                # A paused task's finalizer has already run without pruning. Prune before the
                # status lets a queued follow-up start writing checkpoints of its own.
                await self._prune_checkpoints(run.get("conversation_id") or run_id)
            active = self.active.pop(run_id, None)
            self.repository.update_run(
                run_id,
                status="cancelled",
                error=reason or "Run cancelled.",
                usage=active.usage if active else run.get("usage"),
            )
            await self._capture_checkpoint(run_id, "settled")
            await self._note(run_id, "run_cancelled", {"run_id": run_id})
            if run["status"] in WAITING_STATUSES and run.get("parent_run_id"):
                await self._mirror_helper(run_id, "helper_resolved")
            await self._release_finished(run_id)
        elif reason and run["status"] == "cancelled" and not run.get("error"):
            # Stopped mid-step or while queued, the task settled itself without a word on why.
            self.repository.update_run(run_id, status="cancelled", error=reason, expected_status="cancelled")
        self.tasks.pop(run_id, None)
        self.conditions.pop(run_id, None)
        self._run_locks.pop(run_id, None)
        self.coding_workspace.cleanup.touch((run.get("spec") or {}).get("working_directory"))
        key = run.get("conversation_id") or run_id
        if self.computer is not None and self.computer.control(key).state != "agent":
            peers = self.repository.list_conversation_runs(run["project_id"], key)
            if not any(peer["id"] != run_id and peer["status"] not in TERMINAL_STATUSES for peer in peers):
                await self.computer.close_session(key)
        return self.repository.get_run(run_id)

    async def wait(self, run_id: str, timeout: float = WAIT_CEILING) -> Run:
        """Until the run settles, or pauses for an approval.

        The run's own idle limit is what ends a stuck one; `timeout` is how long this caller is
        prepared to wait for it.
        """
        try:
            async with asyncio.timeout(timeout):
                return await self._settled(run_id, TERMINAL_STATUSES | {"waiting_approval"})
        except TimeoutError:
            raise TimeoutError("run did not finish before the timeout") from None


def run_account(repository: Repository, run_id: str, duration_ms: int | None = None) -> str:
    """What a run did, in a sentence or two, from its trace and its helpers' traces.

    Appended to failure messages, so a task that failed after a long time says what it and its
    helpers got done. Written by the runtime, not the model, so it costs nothing and cannot be
    wrong about what happened.
    """
    try:
        run = repository.get_run(run_id)
        events = repository.list_run_events(run_id)
    except KeyError:
        return ""
    total = duration_ms if duration_ms is not None else run.get("duration_ms")
    sentences = [f"Took {_span(total)}."] if total else []

    # Calls that succeeded: a call that failed is not something it did.
    own = Counter(
        str(event["payload"].get("tool") or "") for event in events if event["event_type"] == "tool_completed"
    )
    for event in events:
        if event["event_type"] != "delegation_started":
            continue
        payload = event["payload"]
        name = payload.get("agent_name") or "a helper"
        try:
            child = repository.get_run(str(payload.get("run_id")))
            child_events = repository.list_run_events(child["id"])
        except KeyError:
            continue
        did = _did(
            Counter(
                str(e["payload"].get("tool") or "")
                for e in child_events
                if e["event_type"] == "tool_completed"
            )
        )
        took = _span(child.get("duration_ms"))
        detail = ", ".join(part for part in (took, did) if part)
        status = child["status"]
        if status == "completed":
            sentences.append(f"Asked {name} ({detail}).")
        elif status == "cancelled":
            sentences.append(f"Asked {name}, stopped with this run ({detail}).")
        elif status == "failed":
            reason = (child.get("error") or "no reason recorded").split(" — ")[0][:120]
            sentences.append(f"Asked {name}, which failed: {reason}.")
        else:
            sentences.append(f"Asked {name}, still running ({did or 'just started'}).")
    if own_did := _did(own):
        sentences.append(f"On its own it {own_did}.")
    return " ".join(sentences)


def _account_suffix(repository: Repository, run_id: str, duration_ms: int) -> str:
    account = run_account(repository, run_id, duration_ms)
    return f" {account}" if account else ""


def _did(counts: Counter) -> str:
    """ "made 12 tool calls", from successful tool calls."""
    calls = sum(count for tool, count in counts.items() if tool)
    return f"made {calls} tool call{'s' if calls != 1 else ''}" if calls else ""


def _span(milliseconds: int | None) -> str:
    """ "45 s", "11 min", "1 h 12 min": long enough to read, short enough to sit in a sentence."""
    if not milliseconds:
        return ""
    seconds = max(1, round(milliseconds / 1000))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        # Seconds matter at two minutes and are noise at twenty.
        return f"{minutes} min {seconds} s" if seconds and minutes < 10 else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def novel_messages(update: dict[str, Any], seen: set[str]) -> list[Any]:
    """The messages in a node's update that have not been recorded yet.

    A node does not only append. `SummarizationMiddleware` replaces the whole history — a
    remove-all marker, its summary, and the recent messages it kept — and every kept message
    came back through here as though it had just happened, so a summarised run recorded each
    of its tool calls twice. Messages without an id are new by definition.
    """
    fresh = []
    for message in update.get("messages", []) or []:
        identifier = getattr(message, "id", None)
        if identifier:
            if identifier in seen:
                continue
            seen.add(identifier)
        fresh.append(message)
    return fresh


# What LangChain's limit middleware writes into the transcript when a cap is hit. Matched by
# prefix because that text is the only signal the run gives: `ToolCallLimitMiddleware` blocks
# the call with an error ToolMessage, `ModelCallLimitMiddleware` ends the run with an AIMessage,
# and neither raises unless configured to.
def limit_message(message: Any) -> str:
    """Why the run was stopped, for the notice the loop guard injects, or "" otherwise.

    Read from the marker the guard stamps on its notice, not from the notice's wording.
    """
    reached = (getattr(message, "additional_kwargs", None) or {}).get(LIMIT_MARK)
    if reached == "no_progress" and getattr(message, "type", "") == "ai":
        return (
            "It kept repeating the same action and getting the same result, so the run was "
            "stopped as stuck. Try again, or say it differently."
        )
    return ""


def _message_events(message: Any, open_calls: dict[str, str], agent_id: str = "") -> list[dict[str, Any]]:
    """Discrete, persistable events for one completed message."""
    events: list[dict[str, Any]] = []
    for call in getattr(message, "tool_calls", None) or []:
        call_id = call.get("id") or call.get("name", "")
        open_calls[call_id] = call.get("name", "")
        events.append(
            {
                "type": "tool_started",
                "node_id": call.get("name"),
                "payload": {"id": call_id, "tool": call.get("name", ""), "args": call.get("args", {})},
            }
        )

    if getattr(message, "type", "") == "tool":
        call_id = getattr(message, "tool_call_id", "")
        content = getattr(message, "content", "")
        is_error = getattr(message, "status", "") == "error"
        name = open_calls.get(call_id, getattr(message, "name", ""))
        events.append(
            {
                "type": "tool_failed" if is_error else "tool_completed",
                "node_id": name,
                "payload": {
                    "id": call_id,
                    "tool": name,
                    "result": content if isinstance(content, str) else str(content),
                },
            }
        )
        # A screenshot result carries only a text sentinel — the PNG lives on disk. The extra
        # event hands the UI the file to fetch, so the trace can show the picture without the
        # image ever entering SQLite.
        if not is_error and isinstance(content, str):
            sentinel = find_sentinel(content)
            if sentinel:
                events.append(
                    {
                        "type": "screenshot",
                        "node_id": name,
                        "payload": {"id": call_id, "tool": name, "file": sentinel, "agent_id": agent_id},
                    }
                )

    # What the harness itself wrote into the conversation — the notice that ends a run at a cap
    # or a loop, the note that replaces a cut-off reply — is not the agent speaking. The limit is
    # recorded as `limit_reached` and the cut-off as its own policy event.
    harness = limit_message(message) or (getattr(message, "response_metadata", None) or {}).get(
        "wavefunction_truncated"
    )
    if (said := assistant_text(message)).strip() and not harness:
        events.append(
            {
                "type": "assistant_message",
                "node_id": None,
                "payload": {"text": said, "message_id": getattr(message, "id", None)},
            }
        )
    return events


def interrupt_requests(payload: Any) -> list[dict[str, Any]]:
    items = payload if isinstance(payload, (list, tuple)) else [payload]
    requests: list[dict[str, Any]] = []
    for item in items:
        value = getattr(item, "value", item)
        if isinstance(value, dict):
            # The HITL middleware wraps its asks in a top-level key; surface either shape.
            nested = value.get("requests") or value.get("action_requests")
            if isinstance(nested, list):
                requests.extend(entry for entry in nested if isinstance(entry, dict))
            else:
                requests.append(value)
    return requests


def _seconds_since(timestamp: str | None) -> float:
    """Wall-clock seconds since an ISO timestamp, or 0 if it cannot be read."""
    if not timestamp:
        return 0.0
    try:
        started = datetime.fromisoformat(timestamp)
    except ValueError:
        return 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - started).total_seconds())


def _describe_empty_answer(messages: list[Any]) -> str:
    """Why a run finished with nothing to say, in words the person who asked can act on.

    Recording an empty string made the agent look like it had ignored the question. The common
    cause is the output limit: a reasoning model can spend the whole allowance thinking and stop
    before it writes anything, which arrives as `finish_reason: "length"` on an empty message.
    """
    last = next((m for m in reversed(messages) if getattr(m, "type", "") == "ai"), None)
    reason = (getattr(last, "response_metadata", None) or {}).get("finish_reason", "")
    if "length" in str(reason):
        return (
            "This agent ran out of room to answer before it wrote anything — the whole reply "
            "was cut off. Ask again, and if it keeps happening ask for the answer in pieces, or "
            "pick a model that spends less of its budget thinking before it writes."
        )
    return (
        "This agent finished without saying anything. Ask it again, or open the trace above to "
        "see what it did."
    )
