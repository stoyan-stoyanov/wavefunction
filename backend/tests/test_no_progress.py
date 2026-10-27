"""A run that cannot make progress ends plainly instead of repeating itself.

The trace this came from: an agent on a model whose window the app under-estimated read the
same two skills fifty-nine times in sixteen minutes. Nothing it read could be kept, nothing
told it how much would fit, and the loop detector only counted three-in-a-row from a history
that compaction kept rewriting.
"""

from __future__ import annotations

from conftest import ScriptedModel, agent_spec, script_gateway, tool_call_message
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from pydantic import PrivateAttr

from wavefunction.execution_policy import (
    LIMIT_MARK,
    NO_PROGRESS,
    PLAN_REMINDER,
    PLAN_REMINDER_EVERY,
    STOP_REPEATS,
    PlanReminderMiddleware,
    ProgressMiddleware,
)


class RecordingModel(ScriptedModel):
    _seen: list = PrivateAttr(default_factory=list)

    def _generate(self, messages, **kwargs):
        self._seen.append(list(messages))
        return super()._generate(messages, **kwargs)

    async def _agenerate(self, messages, **kwargs):
        self._seen.append(list(messages))
        return await super()._agenerate(messages, **kwargs)


# ---- room to work in -----------------------------------------------------------------------


# ---- pointers a model can follow -----------------------------------------------------------


# ---- a run that only repeats itself --------------------------------------------------------


def looping_agent(script, *, polling: bool | str = False):
    """`polling="immediate"`: polling tools whose results say they returned without waiting."""
    calls: dict[str, int] = {"read_skill": 0, "read_output": 0}
    immediate = polling == "immediate"

    @tool(response_format="content_and_artifact")
    def read_skill(name: str) -> tuple[str, dict | None]:
        """Read a skill."""
        calls["read_skill"] += 1
        return f"The {name} skill, unchanged.", {"polled": False} if immediate else None

    @tool(response_format="content_and_artifact")
    def read_output(output_id: str) -> tuple[str, dict | None]:
        """Read a stored output."""
        calls["read_output"] += 1
        return "The stored output, unchanged.", {"polled": False} if immediate else None

    if polling:
        read_skill.metadata = {"polling": True}
        read_output.metadata = {"polling": True}
    model = RecordingModel(script=script)
    return (
        create_agent(model=model, tools=[read_skill, read_output], middleware=[ProgressMiddleware()]),
        model,
        calls,
    )


def alternating(rounds: int) -> list[AIMessage]:
    """The loop from the trace: never the same call twice in a row, never anything new."""
    script = []
    for number in range(rounds):
        name, args = (
            ("read_skill", {"name": "email"}) if number % 2 == 0 else ("read_output", {"output_id": "abc"})
        )
        script.append(tool_call_message(name, args, f"c{number}"))
    return [*script, AIMessage(content="Finished.")]


async def test_alternating_repeats_get_feedback_and_then_end_the_run(tmp_path) -> None:
    # A polling tool that answered without waiting is not a poll: its repeats count too.
    for polling in (False, "immediate"):
        agent, model, calls = looping_agent(alternating(40), polling=polling)
        result = await agent.ainvoke({"messages": [HumanMessage(content="Set up email")]})
        final = result["messages"][-1]
        assert final.content == NO_PROGRESS and final.additional_kwargs[LIMIT_MARK] == "no_progress"
        # Ended at the tenth identical result, not at the end of the script.
        assert calls["read_skill"] == STOP_REPEATS and calls["read_output"] == STOP_REPEATS - 1
        # And told to change course long before that, although no call was ever repeated in a row.
        nudged = [
            index
            for index, seen in enumerate(model._seen)
            if any(m.additional_kwargs.get("lc_source") == "loop_feedback" for m in seen)
        ]
        assert nudged and nudged[0] <= 6
    # A correction the user sent during the last repeat is answered instead of ending the run.
    looped = model._seen[-1]
    counts = ProgressMiddleware()._count({"messages": looped, "repeated_actions": {}})["repeated_actions"]
    state = {"messages": looped, "repeated_actions": {key: STOP_REPEATS - 1 for key in counts}}
    assert ProgressMiddleware()._count(state)["jump_to"] == "end"
    correction = HumanMessage(content="Stop reading skills.")
    notice = HumanMessage(content="A helper finished.", additional_kwargs={"lc_source": "helper_update"})
    for arrived in ([correction], [correction, notice]):
        assert "jump_to" not in ProgressMiddleware()._count({**state, "messages": [*looped, *arrived]})
    # A computer action names each capture anew. The same move on the same screen is a repeat;
    # a changed screen is progress.
    from types import SimpleNamespace

    from wavefunction.computer import save_screenshot
    from wavefunction.execution_policy import _signature

    files = SimpleNamespace(project_dir=lambda _agent: tmp_path)
    same = [save_screenshot(files, "agent", "conversation", b"same screen") for _ in range(2)]
    changed = save_screenshot(files, "agent", "conversation", b"another screen")
    assert same[0] != same[1]
    move = {"name": "computer_move", "args": {"x": 300, "y": 244}}

    def moved(sentinel):
        return _signature(
            move, ToolMessage(content=f"Moved the mouse. The screen afterwards: {sentinel}", tool_call_id="m")
        )

    assert moved(same[0]) == moved(same[1]) != moved(changed)


async def test_polling_and_varied_work_are_not_stopped() -> None:
    agent, _, calls = looping_agent(alternating(30), polling=True)
    result = await agent.ainvoke({"messages": [HumanMessage(content="Wait for it")]})
    assert result["messages"][-1].content == "Finished." and calls["read_skill"] == 15

    # The same tool with different arguments is work, however long it goes on.
    varied = [
        tool_call_message("read_skill", {"name": f"skill-{number}"}, f"v{number}") for number in range(30)
    ]
    agent, _, calls = looping_agent([*varied, AIMessage(content="Finished.")])
    result = await agent.ainvoke({"messages": [HumanMessage(content="Read everything")]})
    assert result["messages"][-1].content == "Finished." and calls["read_skill"] == 30


async def test_a_stuck_agent_run_fails_with_the_reason(stack) -> None:
    agent = stack.agent
    script_gateway(
        stack.models,
        [
            *[tool_call_message("calculator", {"expression": "1+1"}, f"c{n}") for n in range(30)],
            AIMessage(content="Two."),
        ],
    )
    run = await stack.runtime.start_run(agent["id"], "Add one and one", spec=agent_spec())
    finished = await stack.runtime.wait(run["id"], timeout=30)
    await stack.runtime.aclose()
    assert finished["status"] == "failed"
    assert "stopped as stuck" in finished["error"]


async def test_long_work_with_a_stale_plan_is_reminded_through_compaction_and_pauses() -> None:
    """A real build kept its plan's first step "in progress" for 100+ calls: some models write
    the plan once and never again. Every so many calls without an update, one line at the end of
    that request asks for it. The count lives in checkpointed state, so neither compaction nor a
    pause starts it again; a plan update, a new turn or a finished plan do."""
    from langchain.agents.middleware.types import ModelRequest

    reminder = PlanReminderMiddleware()
    open_plan = [{"content": "Build", "status": "in_progress"}]

    def step(state, *messages):
        state["messages"] = [*state["messages"], *messages]
        state.update(reminder._count(state))
        request = ModelRequest(model=None, messages=state["messages"], tools=[], state=state)
        return reminder._remind(request).messages

    def call(number):
        return (
            tool_call_message("read_skill", {"name": f"s{number}"}, f"w{number}"),
            ToolMessage(content="ok", tool_call_id=f"w{number}"),
        )

    planned = (
        tool_call_message("write_todos", {"todos": []}, "plan"),
        ToolMessage(content="ok", tool_call_id="plan"),
    )
    state = {"messages": [HumanMessage(content="Build it")], "todos": open_plan}
    step(state, *planned)
    sent = [step(state, *call(number)) for number in range(PLAN_REMINDER_EVERY)]
    assert [s[-1].content == PLAN_REMINDER for s in sent] == [False] * (PLAN_REMINDER_EVERY - 1) + [True]
    assert sent[-1][:-1] == state["messages"]
    # Compaction replaces the turn with its summary; the count carries on to the next reminder.
    state["messages"] = [
        HumanMessage(content="Summary", additional_kwargs={"lc_source": "summarization"}),
        *state["messages"][-2:],
    ]
    sent = [step(state, *call(number)) for number in range(PLAN_REMINDER_EVERY)]
    assert sent[-1][-1].content == PLAN_REMINDER and state["plan_quiet_calls"] == 2 * PLAN_REMINDER_EVERY
    step(state, *planned)
    assert state["plan_quiet_calls"] == 0
    step(state, *call(1), HumanMessage(content="Also this"))
    assert state["plan_quiet_calls"] == 0
    state.update(
        plan_quiet_calls=PLAN_REMINDER_EVERY - 1, todos=[{"content": "Build", "status": "completed"}]
    )
    assert step(state, *call(2))[-1].content != PLAN_REMINDER

    # The count is part of the checkpoint, which a pause for an approval or a question resumes from.
    from langchain.agents.middleware import TodoListMiddleware
    from langgraph.checkpoint.memory import InMemorySaver

    @tool
    def read_skill(name: str) -> str:
        """Synthetic read."""
        return "ok"

    script = [
        tool_call_message("write_todos", {"todos": open_plan}, "p"),
        *[tool_call_message("read_skill", {"name": f"c{n}"}, f"c{n}") for n in range(5)],
        AIMessage(content="Paused here"),
    ]
    graph = create_agent(
        model=ScriptedModel(script=script),
        tools=[read_skill],
        middleware=[TodoListMiddleware(), PlanReminderMiddleware()],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "paused"}}
    await graph.ainvoke({"messages": [HumanMessage(content="Build it")]}, config)
    assert (await graph.aget_state(config)).values["plan_quiet_calls"] == 5
