"""Fault-injection regressions for the built-agent harness audit. No external accounts."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from conftest import ScriptedModel, agent_spec, script_gateway, tool_call_message
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest

from wavefunction.execution_policy import (
    RequestBudgetMiddleware,
    ResultStore,
    ToolExecutionMiddleware,
    _is_user_message,
    surface_tool_error,
)
from wavefunction.runtime import result_text
from wavefunction.spec_v2 import AgentSpecV2, ToolBinding
from wavefunction.toolkit import TOOL_REGISTRY


async def test_empty_later_turn_never_reuses_old_answer(stack):
    agent = stack.agent
    script_gateway(stack.models, [AIMessage(content="Previous answer")], [AIMessage(content="")])
    first = await stack.runtime.start_run(agent["id"], "First request", spec=agent_spec())
    assert (await stack.runtime.wait(first["id"]))["status"] == "completed"
    second = await stack.runtime.start_run(agent["id"], "Second request", spec=agent_spec())
    result = await stack.runtime.wait(second["id"])
    assert result["status"] == "failed"
    assert "Previous answer" not in result["output"]["text"]
    assert (
        result_text(
            [AIMessage(content="Previous answer"), HumanMessage(content="Now"), AIMessage(content="")]
        )
        != "Previous answer"
    )
    await stack.runtime.aclose()


def request_for(messages, tools=None, prompt="Instructions"):
    model = ScriptedModel(script=[AIMessage(content="Done")], profile={"max_input_tokens": 32768})
    return ModelRequest(model=model, messages=messages, tools=tools or [], system_prompt=prompt)


async def test_context_overflow_retries_once_with_smaller_valid_request(tmp_path):
    policy = RequestBudgetMiddleware(ResultStore(tmp_path))
    requests = []

    async def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise ValueError("context_length_exceeded")
        return "done"

    assert await policy.awrap_model_call(request_for([HumanMessage(content="hello")]), handler) == "done"
    assert len(requests) == 2


@pytest.mark.parametrize("read_only", [False, True])
async def test_an_accepted_write_with_a_lost_result_is_reported_as_uncertain(read_only):
    effects = []

    @tool("remote_action")
    def action() -> str:
        """Synthetic remote action."""
        return "done"

    action.metadata = {"readOnlyHint": read_only}
    request = ToolCallRequest(
        tool_call={"id": "same", "name": action.name, "args": {}}, tool=action, state={}, runtime=None
    )

    # A permission failure after an accepted action is still ambiguous. Only an
    # explicit pre-execution refusal can claim that nothing ran.
    unknown = []

    async def result_denied(request):
        effects.append(request.tool_call["id"])
        raise PermissionError("Action accepted, but its result is no longer readable")

    with pytest.raises(PermissionError) as rejected:
        await ToolExecutionMiddleware(on_uncertain=unknown.append).awrap_tool_call(request, result_denied)
    assert len(unknown) == (0 if read_only else 1)
    assert ("may have completed" in surface_tool_error(rejected.value, request)) is not read_only


@pytest.mark.parametrize("name", ["calculator", "run_command"])
async def test_cancel_preserves_usage_and_releases_registries(stack, monkeypatch, name):
    began = asyncio.Event()

    @tool(name)
    async def slow(expression: str) -> str:
        """Synthetic long tool."""
        began.set()
        await asyncio.sleep(10)
        return "2"

    monkeypatch.setitem(TOOL_REGISTRY, name, replace(TOOL_REGISTRY[name], build=lambda _: slow))
    message = tool_call_message(name, {"expression": "1+1"}, "slow")
    message.usage_metadata = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}
    script_gateway(stack.models, [message, AIMessage(content="Done")])
    agent = stack.agent
    spec = AgentSpecV2(
        name="Cancellation",
        description="Use tokens then wait",
        system_prompt="Run the requested tool",
        tools=[ToolBinding(name=name)],
    )
    run = await stack.runtime.start_run(agent["id"], "Start", spec=spec)
    await asyncio.wait_for(began.wait(), 3)
    cancelled = await stack.runtime.cancel(run["id"])
    assert cancelled["usage"]["total_tokens"] == 110
    # The running total travels with each finished call, so a chat can follow it live.
    finished = [
        event["payload"]
        for event in stack.repository.list_run_events(run["id"])
        if event["event_type"] == "model_call_finished"
    ]
    assert finished[0]["run_usage"]["total_tokens"] == 110
    unknown = [
        event
        for event in stack.repository.list_run_events(run["id"])
        if event["event_type"] == "action_outcome_unknown"
    ]
    assert len(unknown) == (1 if name == "run_command" else 0)
    if unknown:
        assert unknown[0]["payload"]["call_id"] == "slow"
    assert not stack.runtime.tasks and not stack.runtime.conditions and not stack.runtime.active
    await stack.runtime.aclose()


@pytest.mark.sandbox
async def test_default_send_steers_once_with_captured_context_and_explicit_queue(
    coding, tmp_path, monkeypatch
):
    from support import create, repository

    client, ws, services = coding
    folder = repository(tmp_path)
    thread = await create(client, folder=str(folder))
    base = f"/api/coding/threads/{thread['id']}"
    began, release = asyncio.Event(), asyncio.Event()
    executions = []

    @tool("calculator")
    async def slow(expression: str) -> str:
        """Wait while the user steers."""
        executions.append(expression)
        began.set()
        await release.wait()
        return "2"

    monkeypatch.setitem(
        TOOL_REGISTRY, "calculator", replace(TOOL_REGISTRY["calculator"], build=lambda _: slow)
    )
    services.skills.save("steer-guidance", "Steering fixture", "Keep the original repository.")
    script_gateway(
        services.models,
        [tool_call_message("calculator", {"expression": "1+1"}, "first"), AIMessage(content="Updated")],
        [AIMessage(content="Queued answer")],
    )
    original = await client.post(base + "/messages", json={"content": "Begin the original task"})
    assert original.status_code == 202, original.text
    run = original.json()
    try:
        await asyncio.wait_for(began.wait(), 5)
        active = services.runtime.active[run["id"]]
        captured = services.repository.get_run(run["id"])["spec"]
        file = (await client.get(base + "/file", params={"path": "a.txt"})).json()
        reference = {"path": "a.txt", "version": file["version"], "start_line": 1, "end_line": 1}
        update = await client.post(
            base + "/messages",
            json={
                "content": "Use the other file instead",
                "context": {"files": [reference], "skills": ["steer-guidance"]},
            },
        )
        assert update.status_code == 202, update.text
        assert update.json()["id"] == run["id"]
        queued = await client.post(
            base + "/messages",
            json={"content": "Run after this task", "delivery": "queue", "context": {"files": [reference]}},
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["id"] != run["id"] and queued.json()["status"] == "queued"
        assert services.repository.get_run(run["id"])["spec"] == captured
        assert services.repository.get_run(run["id"])["status"] == "running"
    finally:
        release.set()
    assert (await services.runtime.wait(run["id"]))["output"]["text"] == "Updated"
    assert (await services.runtime.wait(queued.json()["id"]))["output"]["text"] == "Queued answer"
    state = await active.agent.aget_state({"configurable": {"thread_id": run["conversation_id"]}})
    updates = [
        m
        for m in state.values["messages"]
        if isinstance(m, HumanMessage) and "Use the other file instead" in str(m.content)
    ]
    assert len(updates) == 1
    assert "1: before" in str(updates[0].content) and "steer-guidance" in str(updates[0].content)
    events = services.repository.list_run_events(run["id"])
    requested = [event for event in events if event["event_type"] == "steering_requested"]
    applied = [event for event in events if event["event_type"] == "steering_applied"]
    assert len(requested) == len(applied) == 1
    # A message sent while the task ran is found by search like any other.
    found = (await client.get("/api/coding/search", params={"query": "other file instead"})).json()
    assert thread["id"] in {match["id"] for match in found}
    # What the user wrote, not the file text attached to it for the model.
    assert "1: before" in services.repository.get_run(queued.json()["id"])["input"]["text"]
    assert not (await client.get("/api/coding/search", params={"query": "1: before"})).json()
    assert applied[0]["payload"]["event_id"] == requested[0]["id"]
    assert requested[0]["payload"]["coding_context"]["files"][0]["version"] == file["version"]
    checkpoints = ws.runs(thread["id"])[0]["checkpoints"]
    boundary = next(point for point in checkpoints if point["kind"] == "before_steering")
    assert boundary["event_id"] == requested[0]["id"] and boundary["available"]
    assert len(ws.runs(thread["id"])) == 2
    rewind = await client.post(base + "/rewind", json={"checkpoint_id": boundary["id"]})
    assert rewind.status_code == 200, rewind.text
    restored = rewind.json()
    assert restored["draft"]["content"] == "Use the other file instead"
    assert restored["draft"]["context"]["files"][0]["version"] == file["version"]
    assert restored["draft"]["context"]["skills"] == ["steer-guidance"]
    saver = await services.runtime.checkpointer()
    saved = await saver.aget_tuple({"configurable": {"thread_id": restored["thread"]["conversation_id"]}})
    messages = saved.checkpoint["channel_values"]["messages"]
    assert [m.content for m in messages if _is_user_message(m)] == ["Begin the original task"]
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "first" for m in messages)
    assert len(ws.runs(restored["archived_thread_id"])) == 2
    assert not any(
        event["event_type"] == "steering_applied" for run in ws.runs(thread["id"]) for event in run["events"]
    )
    script_gateway(services.models, [AIMessage(content="Resumed from the steering boundary")])
    resumed = await ws.send(thread["id"], "Continue from the restored boundary")
    assert (await services.runtime.wait(resumed["id"]))["output"][
        "text"
    ] == "Resumed from the steering boundary"
    assert executions == ["1+1"]


async def test_quitting_says_so_for_a_running_and_a_queued_task(stack, monkeypatch):
    """Both settle in their own handlers, which wrote no reason, so each read "Run cancelled."
    as if the user had stopped it."""
    from wavefunction.repository import INTERRUPTED

    began, release = asyncio.Event(), asyncio.Event()

    @tool("calculator")
    async def slow(expression: str) -> str:
        """Wait until released."""
        began.set()
        await release.wait()
        return "2"

    monkeypatch.setitem(
        TOOL_REGISTRY, "calculator", replace(TOOL_REGISTRY["calculator"], build=lambda _: slow)
    )
    agent = stack.agent
    script_gateway(
        stack.models,
        [tool_call_message("calculator", {"expression": "1+1"}, "first"), AIMessage(content="First")],
        [AIMessage(content="Second")],
    )
    spec = agent_spec(ToolBinding(name="calculator"))
    running = await stack.runtime.start_run(agent["id"], "First", spec=spec)
    await asyncio.wait_for(began.wait(), 3)
    queued = await stack.runtime.start_run(agent["id"], "Second", spec=spec, queue_if_busy=True)
    for run in (queued, running):
        await stack.runtime.cancel(run["id"], reason=INTERRUPTED)
    assert [
        (stack.repository.get_run(run["id"])["status"], stack.repository.get_run(run["id"])["error"])
        for run in (running, queued)
    ] == [("cancelled", INTERRUPTED)] * 2


async def test_cancelled_queued_work_does_not_execute_or_cancel_predecessor(stack, monkeypatch):
    began, release = asyncio.Event(), asyncio.Event()

    @tool("calculator")
    async def slow(expression: str) -> str:
        """Wait until released."""
        began.set()
        await release.wait()
        return "2"

    monkeypatch.setitem(
        TOOL_REGISTRY, "calculator", replace(TOOL_REGISTRY["calculator"], build=lambda _: slow)
    )
    agent = stack.agent
    script_gateway(
        stack.models,
        [tool_call_message("calculator", {"expression": "1+1"}, "first"), AIMessage(content="First")],
        [AIMessage(content="Should not execute")],
    )
    spec = agent_spec(ToolBinding(name="calculator"))
    first = await stack.runtime.start_run(agent["id"], "First", spec=spec)
    await asyncio.wait_for(began.wait(), 3)
    second = await stack.runtime.start_run(agent["id"], "Second", spec=spec, queue_if_busy=True)
    assert (await stack.runtime.cancel(second["id"]))["status"] == "cancelled"
    assert stack.repository.get_run(first["id"])["status"] == "running"
    release.set()
    assert (await stack.runtime.wait(first["id"]))["status"] == "completed"
    assert not any(
        e["event_type"] == "model_call_started" for e in stack.repository.list_run_events(second["id"])
    )
    await stack.runtime.aclose()


async def test_queue_waits_for_terminal_checkpoint_cleanup(stack, monkeypatch):
    finished, release = asyncio.Event(), asyncio.Event()
    finish = stack.runtime._finish

    async def slow_cleanup(run_id, active, emit):
        await finish(run_id, active, emit)
        if not finished.is_set():
            finished.set()
            await release.wait()

    monkeypatch.setattr(stack.runtime, "_finish", slow_cleanup)
    agent = stack.agent
    script_gateway(stack.models, [AIMessage(content="First")], [AIMessage(content="Second")])
    first = await stack.runtime.start_run(agent["id"], "First", spec=agent_spec())
    await asyncio.wait_for(finished.wait(), 3)
    second = await stack.runtime.start_run(agent["id"], "Second", spec=agent_spec(), queue_if_busy=True)
    assert second["input"]["wait_for"] == [first["id"]]
    await asyncio.sleep(0.05)
    assert stack.repository.get_run(second["id"])["status"] == "queued"
    release.set()
    assert (await stack.runtime.wait(second["id"]))["output"]["text"] == "Second"
    assert not stack.runtime.tasks and not stack.runtime.conditions
    await stack.runtime.aclose()


async def test_send_at_finalization_is_applied_or_starts_one_followup(coding, tmp_path, monkeypatch):
    from support import create, repository

    from wavefunction.steering import SteeringMiddleware

    client, ws, services = coding
    thread = await create(client, folder=str(repository(tmp_path)))
    endpoint = f"/api/coding/threads/{thread['id']}/messages"
    closing, release = asyncio.Event(), asyncio.Event()
    finalize = SteeringMiddleware.aafter_agent

    async def paused_finalize(self, state, runtime):
        if not closing.is_set():
            closing.set()
            await release.wait()
        return await finalize(self, state, runtime)

    paused_finalize.__dict__.update(finalize.__dict__)
    monkeypatch.setattr(SteeringMiddleware, "aafter_agent", paused_finalize)
    script_gateway(
        services.models,
        [AIMessage(content="Original"), AIMessage(content="Updated")],
        [AIMessage(content="Follow-up")],
    )
    first = await client.post(endpoint, json={"content": "Start"})
    assert first.status_code == 202, first.text
    run = first.json()
    try:
        await asyncio.wait_for(closing.wait(), 5)
        update = await client.post(endpoint, json={"content": "Change the answer"})
        assert update.status_code == 202 and update.json()["id"] == run["id"], update.text
    finally:
        release.set()
    finished = await services.runtime.wait(run["id"])
    assert finished["output"]["text"] == "Updated"
    later = await client.post(endpoint, json={"content": "Arrived after completion"})
    assert later.status_code == 202 and later.json()["id"] != run["id"], later.text
    assert (await services.runtime.wait(later.json()["id"]))["output"]["text"] == "Follow-up"
    events = services.repository.list_run_events(run["id"])
    assert len([event for event in events if event["event_type"] == "steering_requested"]) == 1
    assert len([event for event in events if event["event_type"] == "steering_applied"]) == 1
    assert len(ws.runs(thread["id"])) == 2
