"""Run ownership and stream wakeup/closure at approval and completion boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from conftest import script_gateway
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.types import interrupt

from wavefunction.spec_v2 import AgentSpecV2, ToolBinding
from wavefunction.toolkit import TOOL_REGISTRY


@pytest.fixture(autouse=True)
async def _closed_checkpoints(stack):
    """Each test's checkpoint connection closes with it, not whenever it is collected later."""
    yield
    await stack.runtime.aclose()


def agent_with(stack, *tools: ToolBinding) -> tuple[dict, AgentSpecV2]:
    agent = stack.agent
    return agent, AgentSpecV2(name="Helper", description="Helps.", system_prompt="Help.", tools=list(tools))


async def test_an_answer_waits_only_on_its_own_run(stack, monkeypatch):
    approved = []

    @tool("calculator")
    def gated(expression: str) -> str:
        """Synthetic tool that asks for a command-bound approval without executing commands."""
        result = interrupt(
            {"action_requests": [{"name": "calculator", "kind": "irreversible", "command": expression}]}
        )
        if any(d.get("type") == "approve" and d.get("command") == expression for d in result["decisions"]):
            approved.append(expression)
        return "4"

    monkeypatch.setitem(
        TOOL_REGISTRY, "calculator", replace(TOOL_REGISTRY["calculator"], build=lambda _: gated)
    )
    agent, spec = agent_with(stack, ToolBinding(name="calculator"))
    script_gateway(
        stack.models,
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "calculator",
                        "args": {"expression": expression},
                        "id": expression,
                        "type": "tool_call",
                    }
                    for expression in ("2+2", "1+3")
                ],
            ),
            AIMessage(content="4."),
        ],
    )
    run = await stack.runtime.start_run(agent["id"], "Add.", spec=spec)
    assert (await stack.runtime.wait(run["id"]))["status"] == "waiting_approval"
    pause = next(
        event
        for event in reversed(stack.repository.list_run_events(run["id"]))
        if event["event_type"] == "run_waiting_for_approval"
    )
    assert {request["command"] for request in pause["payload"]["requests"]} == {"2+2", "1+3"}
    assert approved == []

    # Another run's resume is still waiting for that run's stream to let go.
    other = stack.repository.create_run(
        agent["id"],
        {"text": "x"},
        stack.repository.current_conversation(agent["id"], "other")["id"],
        spec=spec.model_dump(mode="json"),
    )
    stack.repository.update_run(other["id"], status="waiting_computer")
    stream = asyncio.create_task(asyncio.sleep(30))
    stack.runtime.tasks[other["id"]] = stream
    held = asyncio.create_task(stack.runtime.resume_computer(other["id"]))
    await asyncio.sleep(0.05)
    try:
        resumed = await asyncio.wait_for(stack.runtime.resume(run["id"], "approve"), 2)
        assert resumed["status"] in {"queued", "running"}
        assert (await stack.runtime.wait(run["id"]))["status"] == "completed"
        assert sorted(approved) == ["1+3", "2+2"]
    finally:
        held.cancel()
        stream.cancel()
        await asyncio.gather(held, stream, return_exceptions=True)
        stack.runtime.tasks.pop(other["id"], None)


async def test_a_reader_of_a_paused_run_wakes_on_its_next_event(stack):
    agent, spec = agent_with(stack)
    conversation = stack.repository.current_conversation(agent["id"])
    run = stack.repository.create_run(
        agent["id"], {"text": "x"}, conversation["id"], spec=spec.model_dump(mode="json")
    )
    stack.repository.update_run(run["id"], status="waiting_connector")
    # As after a restart: nothing in this process has touched the run yet.
    assert run["id"] not in stack.runtime.conditions

    waiting = asyncio.create_task(stack.runtime.changed(run["id"], timeout=10))
    await asyncio.sleep(0.05)
    await stack.runtime._note(run["id"], "run_queued", {"waiting_for": []})
    await asyncio.wait_for(waiting, 1)

    # A helper waiter can be scheduled just before completion, then only enter
    # changed() after finalization removed its condition. It must not recreate it.
    late = asyncio.create_task(stack.runtime.changed(run["id"], timeout=0.01))
    stack.repository.update_run(run["id"], status="completed")
    stack.runtime.conditions.pop(run["id"])
    assert run["id"] not in stack.runtime.tasks
    await asyncio.wait_for(late, 1)
    assert run["id"] not in stack.runtime.conditions


async def test_a_finished_runs_stream_closes_without_waiting(stack):
    from fastapi.testclient import TestClient

    from wavefunction import main

    agent, spec = agent_with(stack)
    script_gateway(stack.models, [AIMessage(id="answer-id", content="Done.")])
    run = await stack.runtime.start_run(agent["id"], "Go.", spec=spec)
    assert (await stack.runtime.wait(run["id"]))["status"] == "completed"
    answer = next(
        event
        for event in stack.repository.list_run_events(run["id"])
        if event["event_type"] == "assistant_message"
    )
    assert answer["payload"] == {"text": "Done.", "message_id": "answer-id"}

    with (
        TestClient(main.app) as client,
        client.stream("GET", f"/api/runs/{run['id']}/events?after=0") as response,
    ):
        kinds = [line for line in response.iter_lines() if line.startswith("event: ")]
    assert "event: run_completed" in kinds
    # Nothing is left waiting on a run that will never write again.
    assert run["id"] not in stack.runtime.conditions


def test_mailbox_setup_updates_only_the_waiting_tasks_captured_spec(stack):
    repository = stack.repository
    agent, spec = agent_with(stack)
    spec = spec.model_copy(update={"system_prompt": "Captured task instructions"})
    run = repository.create_run(
        agent["id"],
        {"text": "Read mail"},
        repository.current_conversation(agent["id"], "first")["id"],
        spec=spec.model_dump(mode="json"),
    )
    other = repository.create_run(
        agent["id"],
        {"text": "Unrelated work"},
        repository.current_conversation(agent["id"], "second")["id"],
        spec=spec.model_dump(mode="json"),
    )
    repository.update_run(run["id"], status="waiting_connector")
    repository.assign_run_connector_tools(run["id"], ["mailbox__read_mail"])
    updated = repository.get_run(run["id"])
    assert updated["spec"]["system_prompt"] == "Captured task instructions"
    assert "mailbox__read_mail" in {tool["name"] for tool in updated["spec"]["tools"]}
    assert repository.get_run(other["id"]) == other
    repository.update_run(run["id"], status="completed")
    with pytest.raises(ValueError, match="no longer waiting"):
        repository.assign_run_connector_tools(run["id"], ["another__read_mail"])


def test_run_owners_stay_bounded_and_a_forgotten_run_is_found_again(stack, monkeypatch):
    """Every run listed or created was remembered for the life of the process, deleted or not."""
    monkeypatch.setattr(type(stack.repository), "RUN_OWNERS", 2)
    agent, spec = agent_with(stack)
    conversation = stack.repository.current_conversation(agent["id"])
    runs = [
        stack.repository.create_run(
            agent["id"], {"text": str(index)}, conversation["id"], spec=spec.model_dump(mode="json")
        )
        for index in range(4)
    ]
    assert list(stack.repository._run_owners) == [runs[2]["id"], runs[3]["id"]]
    assert stack.repository.get_run(runs[0]["id"])["input"]["text"] == "0"
