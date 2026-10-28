"""Owned helper conversations, using the same runtime and event stream as their parent."""

from __future__ import annotations

import asyncio
import json

from langchain_core.tools import tool

from .execution_policy import ToolCallRejected, excerpt_text
from .repository import now_iso
from .spec_common import ModelSpec
from .spec_v2 import ToolPolicy, load_spec

SETTLED = {"completed", "failed", "cancelled"}
PAUSED = {"waiting_approval", "waiting_connector", "waiting_computer"}
READ_TOOLS = {
    "calculator",
    "read_file",
    "find_files",
    "search_files",
    "read_documents",
    "list_documents",
    "search_documents",
    "web_search",
    "fetch_page",
}


def helper_cursor(run, events, released):
    """One helper state, as both tool results and parent notices name it."""
    return f"{run['id']}:{run['status']}:{events[-1]['sequence'] if events else 0}:{released}"


def build_agent_tools(context):
    workspace = context.coding_workspace
    repository = context.repository
    runtime = workspace.services.runtime
    parent_id = context.run_id
    parent_thread = context.desktop_copy

    def parent_running():
        parent = repository.get_run(parent_id)
        if context.parent_run_id or parent["status"] != "running":
            raise ToolCallRejected("Only the running parent task can manage its helpers.")
        if workspace.thread(parent_thread)["conversation_id"] != context.conversation_id:
            raise ToolCallRejected("This task no longer owns the conversation.")
        return parent

    def owned(agent_id):
        parent_running()
        try:
            child = workspace.thread(agent_id)
        except KeyError:
            raise ToolCallRejected("Helper not found for this task.") from None
        if child.get("parent_thread_id") != parent_thread or child.get("parent_run_id") != parent_id:
            raise ToolCallRejected("This helper belongs to another task.")
        return child

    def latest(child):
        runs = repository.conversation_page(workspace.agent_id, child["conversation_id"], limit=1)["runs"]
        return runs[-1] if runs else None

    def cursor(run, events):
        return helper_cursor(run, events, run["id"] not in runtime.tasks)

    async def snapshot(child, after=None):
        run = latest(child)
        if run and run["status"] in SETTLED and run["id"] in runtime.tasks:
            # Finished but still letting go of its task: reported now, it would read as
            # completed without the diff that follows. The release is brief.
            run = await runtime._settled(run["id"], SETTLED)
        result = {
            "agent_id": child["id"],
            "thread_id": child["id"],
            "title": child["title"],
            "folder": workspace.effective_folder(child),
            "run_id": run["id"] if run else None,
            "status": run["status"] if run else "failed",
            "error": run.get("error") if run else "Helper did not start.",
        }
        if run:
            events = repository.list_run_events(run["id"], limit=8, tail=True)
            result["cursor"] = cursor(run, events)
            if after == result["cursor"]:
                return {**result, "unchanged": True}
            result["output"] = run["output"]
            if run["status"] not in SETTLED:
                result["events"] = [
                    {
                        "sequence": event["sequence"],
                        "event_type": event["event_type"],
                        "summary": excerpt_text(json.dumps(event["payload"], ensure_ascii=False), 1000),
                    }
                    for event in events
                ]
            # Only an isolated helper has a separate result to review; a shared one's edits are in place.
            if (
                run["status"] in SETTLED
                and run["id"] not in runtime.tasks
                and not run["input"].get("local_read_only")
                and workspace.delivery.worktree(child["id"])
            ):
                try:
                    result.update(await workspace.checkpoints.child_diff(child["id"], run["id"]))
                except (OSError, ValueError, RuntimeError) as error:
                    result["diff_error"] = str(error)
        return result

    async def start(child, spec, text, *, read_only, grants, references):
        async with workspace.checkpoints.locks.setdefault(child["id"], asyncio.Lock()):
            child = owned(child["id"])
            # A follow-up to a helper whose checkout cleanup removed after inactivity would run in
            # a folder that no longer exists. Recreated as sending to a chat does; nothing for an
            # active checkout.
            await workspace.cleanup.restore(child["id"])
            return await runtime.start_run(
                workspace.agent_id,
                text,
                spec=spec,
                conversation_id=child["conversation_id"],
                kind="subagent",
                parent_run_id=parent_id,
                desktop_copy=child["id"],
                reference_files=list(references or []),
                local_read_only=read_only,
                folder_grants=grants,
            )

    # The models a helper can run on instead of this task's: the ones the user enabled.
    enabled = [
        key
        for key in repository.get_setting("enabled_models", []) or []
        if isinstance(key, str) and "::" in key
    ]
    saved = {
        f"{spec.get('provider')}::{spec.get('model')}": spec
        for spec in repository.get_setting("coding_models", []) or []
        if isinstance(spec, dict)
    }

    def chosen_model(key, current):
        if key not in enabled:
            raise ToolCallRejected(
                f"Choose one of the user's enabled models: {', '.join(enabled) or 'none'}."
            )
        provider, _, name = key.partition("::")
        spec = saved.get(key) or {"provider": provider, "model": name}
        if provider == "openai-compatible" and not spec.get("base_url"):
            raise ToolCallRejected(f"{key} has no server address saved; use another model.")
        return ModelSpec.model_validate(
            {**current.model_dump(), "base_url": None, "reasoning_effort": None, **spec}
        )

    @tool
    async def spawn_agent(
        task: str,
        title: str = "Helper",
        read_only: bool = False,
        isolated: bool = False,
        model: str | None = None,
    ) -> str:
        """Start a helper for a focused independent task; returns its id immediately.

        There is no fixed limit on active direct helpers.
        Helpers inherit this task's model, tools, skills, attachments and permitted access.
        By default a helper works in your checkout alongside you and other helpers: its edits
        appear there directly, so give each helper files no one else edits. Set isolated=true
        for work that could interfere, such as broad refactors or changes to shared files: the
        helper gets a separate Git worktree, and you review and integrate its result. Read-only
        helpers only inspect. A helper runs on your model unless model names another of the
        user's enabled models (provider::model). Helpers cannot delegate. Use get_agent or wait_agents for results,
        send_message to redirect, and stop_agent to cancel. Collect their results and validate
        the combined work, or stop obsolete work, before your final answer.
        """
        from .coding_workspace import ThreadInput

        if not task.strip() or len(task) > 30000 or not 1 <= len(title.strip()) <= 160:
            raise ToolCallRejected("Provide a task up to 30000 characters and a short title.")
        async with runtime._run_lock(parent_id):
            parent = parent_running()
            spec = load_spec(parent["spec"])
            if model:
                spec.model = chosen_model(model.strip(), spec.model)
            read_only = (
                read_only
                or context.local_read_only
                or not any(binding.name == "write_file" for binding in spec.enabled_tools())
            )
            if read_only:
                connected = context.connectors.tools() if context.connectors else {}
                for binding in spec.tools:
                    safe = binding.name in READ_TOOLS or (
                        (getattr(connected.get(binding.name), "metadata", None) or {}).get("readOnlyHint")
                        is True
                    )
                    if not safe:
                        binding.policy = ToolPolicy.DENY
            source = workspace.thread(parent_thread)
            child = workspace.create_thread(
                ThreadInput(
                    title=title.strip(),
                    project_id=source["project_id"],
                    folder=context.working_directory,
                    model=spec.model,
                    mode="plan" if read_only else source["mode"],
                    instructions=source["instructions"],
                ),
                parent_thread_id=parent_thread,
                parent_run_id=parent_id,
            )
            # A shared helper writes where its parent may; others write only in their own worktree.
            shared = not read_only and not isolated
            grants = [
                {"path": str(grant.path), "writable": shared and grant.writable}
                for grant in context.local_grants()
            ]
            try:
                if not read_only and isolated:
                    branch = await workspace.checkpoints.branch(parent_thread, child["id"], live=True)
                    spec.working_directory = branch["folder"]
                    grants.append({"path": branch["folder"], "writable": True})
                parent_running()
                run = await start(
                    child,
                    spec,
                    task.strip(),
                    read_only=read_only,
                    grants=grants,
                    references=context.reference_files,
                )
                await runtime._note(
                    parent_id,
                    "delegation_started",
                    {
                        "agent_id": child["id"],
                        "thread_id": child["id"],
                        "run_id": run["id"],
                        "agent_name": child["title"],
                        "read_only": read_only,
                        "isolated": not read_only and isolated,
                        # The start of its instruction, for the card the main chat shows.
                        "task": task.strip()[:280],
                    },
                )
                return json.dumps(await snapshot(workspace.thread(child["id"])))
            except (Exception, asyncio.CancelledError):
                # The worktree service owns partial creation cleanup. Retain any useful
                # checkout and its identity; an unstarted helper need not clutter the sidebar.
                if latest(child) is None:
                    with workspace.db() as db:
                        db.execute(
                            "UPDATE coding_threads SET archived = 1, updated_at = ? WHERE id = ?",
                            (now_iso(), child["id"]),
                        )
                raise

    @tool
    async def get_agent(agent_id: str, after: str | None = None) -> str:
        """Read helper status, bounded progress and answer, plus an isolated helper's saved diff.

        Pass its last cursor as after to omit unchanged results. Omit after to reread them.
        For an isolated helper, the diff and result_commit/result_ref describe its saved
        completion, excluding later checkout edits. Review base_commit..result_commit and
        integrate only the assigned work.
        """
        return json.dumps(await snapshot(owned(agent_id), after))

    @tool(response_format="content_and_artifact")
    async def wait_agents(
        agent_ids: list[str], timeout_seconds: float = 30, after: dict[str, str] | None = None
    ) -> tuple[str, dict]:
        """Wait up to an hour for a helper to finish or need input; prefer long waits (minutes).

        Pass after={agent_id: last_cursor} from prior results to wait for new results without
        repeating answers or diffs already received. Omit a cursor to reread that helper.
        Returns compact status for unchanged helpers. Also wakes when the parent receives
        steering, so you can act on the user's new instructions before waiting again.
        Returns at once with reason needs_answer when the rest are paused and one asks you a question,
        needs_user when they only wait on the user, settled when all finished and you have their results.
        """
        if not agent_ids or len(set(agent_ids)) != len(agent_ids):
            raise ToolCallRejected("Select distinct helpers owned by this task.")
        if not 0 <= timeout_seconds <= 3600:
            raise ToolCallRejected("timeout_seconds must be between 0 and 3600.")
        children = [owned(agent_id) for agent_id in agent_ids]
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        reason, waited = "timeout", False
        while True:
            if runtime.pending_steering(parent_id):
                reason = "steering"
                break
            runs = [latest(child) for child in children]
            if any(
                run is None
                or (
                    run["status"] in SETTLED | PAUSED
                    and run["id"] not in runtime.tasks
                    and cursor(run, repository.list_run_events(run["id"], limit=1, tail=True))
                    != (after or {}).get(child["id"])
                )
                for child, run in zip(children, runs, strict=True)
            ):
                reason = "ready"
                break
            unsettled = [
                run for run in runs if run and (run["status"] not in SETTLED or run["id"] in runtime.tasks)
            ]
            if not unsettled:
                # Every helper has finished and its result was seen: nothing can change.
                reason = "settled"
                break
            if all(run["id"] not in runtime.tasks and run["status"] in PAUSED for run in unsettled):
                # Only this task answering a question it received, or the user, can change these.
                reason = "needs_user" if all(map(runtime.needs_user, unsettled)) else "needs_answer"
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            # Run events wake this at once. Only a settled helper releasing its task has no
            # event, so check quickly during that brief window and slowly otherwise.
            releasing = any(run["status"] in SETTLED and run["id"] in runtime.tasks for run in unsettled)
            waiters = [
                asyncio.create_task(runtime.changed(run_id, min(remaining, 1 if releasing else 30)))
                for run_id in [parent_id, *(run["id"] for run in unsettled)]
            ]
            waited = True
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for waiter in waiters:
                    waiter.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
        result = json.dumps(
            {
                "reason": reason,
                "agents": [await snapshot(child, (after or {}).get(child["id"])) for child in children],
            }
        )
        # Only a call that actually waited is a poll. An immediate answer repeated unchanged
        # counts toward the repeated-action limit like any other tool call.
        return result, {"polled": waited and reason in {"timeout", "steering"}}

    @tool
    async def send_message(agent_id: str, message: str) -> str:
        """Steer a running helper, answer its ordinary clarification, or send a follow-up.

        Follow-ups retain its captured context. You may clarify assigned work; permission
        approvals, connector setup, and computer takeover still require the user.
        """
        if not message.strip() or len(message) > 30000:
            raise ToolCallRejected("Provide a message up to 30000 characters.")
        async with runtime._run_lock(parent_id):
            child = owned(agent_id)
            run = latest(child)
            if run is None:
                raise ToolCallRejected("This helper has no task to continue.")
            if run["status"] == "running":
                try:
                    await runtime.steer(run["id"], message.strip())
                    return json.dumps(await snapshot(child))
                except ValueError:
                    # A question or completion can win the race with steering. Re-read so
                    # a newly paused helper is answered instead of waiting for it to finish.
                    run = repository.get_run(run["id"])
            if run["status"] == "waiting_approval":
                try:
                    await runtime.reply(run["id"], message.strip(), parent_run_id=parent_id)
                except ValueError as error:
                    raise ToolCallRejected(str(error)) from None
                return json.dumps(await snapshot(child))
            if run["status"] in PAUSED or run["status"] == "queued":
                raise ToolCallRejected(
                    "This helper is waiting. Inspect it and resolve its pending request first."
                )
            if run["status"] not in SETTLED or run["id"] in runtime.tasks:
                await runtime._settled(run["id"], SETTLED)
                parent_running()
            followup = await start(
                child,
                load_spec(repository.get_run(run["id"])["spec"]),
                message.strip(),
                read_only=run["input"].get("local_read_only", False),
                grants=run["input"].get("folder_grants"),
                references=run["input"].get("reference_files"),
            )
            # A finished helper was archived; working again, it is back in the sidebar.
            workspace.archive_helper(child["id"], False)
            # Recorded on the main task, whose account of its work includes each helper run.
            await runtime._note(
                parent_id,
                "delegation_started",
                {
                    "agent_id": child["id"],
                    "thread_id": child["id"],
                    "run_id": followup["id"],
                    "agent_name": child["title"],
                    "followup": True,
                },
            )
            return json.dumps(await snapshot(child))

    @tool
    async def stop_agent(agent_id: str) -> str:
        """Stop this task's helper and its one-shot commands; retain its code for review."""
        child = owned(agent_id)
        for run in repository.list_conversation_runs(workspace.agent_id, child["conversation_id"]):
            if run["status"] not in SETTLED or run["id"] in runtime.tasks:
                await runtime.cancel(run["id"])
        return json.dumps(await snapshot(child))

    if enabled:
        spawn_agent.description += " Enabled models: " + ", ".join(enabled) + "."
    get_agent.metadata = {"readOnlyHint": True}
    # A full wait must end on its own, before the generic one-hour tool limit.
    wait_agents.metadata = {"readOnlyHint": True, "polling": True, "timeout_seconds": 3660}
    return [spawn_agent, get_agent, wait_agents, send_message, stop_agent]
