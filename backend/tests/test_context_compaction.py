"""Regressions for the subscription-audit context loss, using synthetic conversations."""

from unittest.mock import AsyncMock, Mock

import pytest
from conftest import ScriptedModel, tool_call_message
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.message import add_messages
from pydantic import PrivateAttr

from wavefunction.execution_policy import (
    RequestBudgetMiddleware,
    ResultStore,
    RetryingSummarizationMiddleware,
)
from wavefunction.steering import PARALLEL_HINT, SteeringAfterCompaction, SteeringMiddleware

GOAL = "Find subscriptions in Gmail and Outlook and list the charges."
STEERING = "Include annual subscriptions too; do not send or move email."


def tool_history(rounds=6, size=18000, batch=0):
    messages = [HumanMessage(content=GOAL, id="request")]
    for i in range(rounds):
        messages.extend(
            [
                tool_call_message(
                    "read_mail", {"mailbox": "All Mail", "message_id": str(i)}, f"read-{batch}-{i}"
                ),
                ToolMessage(
                    content=f"Receipt {i}: Example costs 10 USD monthly. " + "evidence " * (size // 9),
                    tool_call_id=f"read-{batch}-{i}",
                    id=f"result-{batch}-{i}",
                ),
            ]
        )
    return messages


@pytest.mark.parametrize(
    "response",
    [
        AIMessage(content=""),
        AIMessage(content="Partial", response_metadata={"finish_reason": "length"}),
        RuntimeError("provider failed"),
    ],
)
async def test_failed_summary_preserves_the_checkpoint(tmp_path, response):
    model = ScriptedModel(script=[AIMessage(content="Should not run")], profile={"max_input_tokens": 32768})
    summary = RetryingSummarizationMiddleware(model, store=ResultStore(tmp_path))
    summary._summary_model = type(
        "SummaryModel",
        (),
        {
            "ainvoke": AsyncMock(
                side_effect=response if isinstance(response, Exception) else None, return_value=response
            )
        },
    )()
    graph = create_agent(model, middleware=[summary], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "synthetic-failure"}}
    messages = tool_history()
    with pytest.raises((ValueError, RuntimeError)):
        await graph.ainvoke({"messages": messages}, config)
    saved = (await graph.aget_state(config)).values["messages"]
    assert [m.content for m in saved] == [m.content for m in messages]
    assert not any(m.additional_kwargs.get("lc_source") == "summarization" for m in saved)
    # A provider's "could not summarise" sentence is refused like an empty one, which keeps the
    # checkpoint the same way.
    with pytest.raises(ValueError, match="empty or incomplete"):
        summary._finish_summary(AIMessage(content="Previous conversation was too long to summarize."), None)


class RecordingModel(ScriptedModel):
    _seen: list = PrivateAttr(default_factory=list)

    async def _agenerate(self, messages, **kwargs):
        self._seen.append(messages)
        return await super()._agenerate(messages, **kwargs)


async def test_agent_continues_after_multiple_compactions_and_steering(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(
        "wavefunction.execution_policy.emit_policy", lambda event, **payload: events.append((event, payload))
    )
    completed_reads = []

    @tool
    def read_mail():
        """Read a synthetic receipt without external access."""
        completed_reads.append(True)
        return "Example subscription: 10 USD per month. " + "Receipt detail. " * 1100

    model = RecordingModel(
        script=[
            *[tool_call_message("read_mail", {}, f"read-{i}") for i in range(15)],
            AIMessage(content="Example: 10 USD monthly. The renewal helper is waiting for your sign-in."),
            AIMessage(content="Example: 10 USD monthly, renewal confirmed."),
        ],
        profile={"max_input_tokens": 32768},
    )
    store = ResultStore(tmp_path)
    summary = RetryingSummarizationMiddleware(model, store=store)
    during = []

    class Summarizer(RecordingModel):
        async def _agenerate(self, messages, **kwargs):
            # The model call after the first summary is the one a message sent meanwhile must reach.
            during[:] = during or [len(model._seen)]
            return await super()._agenerate(messages, **kwargs)

    summary._summary_model = Summarizer(
        script=[AIMessage(content="Example subscription costs 10 USD monthly; receipts checked.")]
    )
    helper_deliveries, user_waits = [], []

    async def wait_for_user():
        # The finished turn is held here, not by model calls, until the user's sign-in lets
        # the waiting helper finish.
        user_waits.append(len(model._seen))

    def helper_updates(seen):
        items = [
            {
                "agent_id": "receipt-helper",
                "run_id": "helper-run",
                "title": "Receipts",
                "status": "completed",
                "cursor": "result-1",
                "summary": "Inspected annual receipts.",
            }
        ]
        if user_waits:
            items.append(
                {
                    "agent_id": "renewal-helper",
                    "run_id": "renewal-run",
                    "title": "Renewal",
                    "status": "completed",
                    "cursor": "renewal-1",
                    "summary": "Renewal confirmed.",
                }
            )
        fresh = [
            item
            for item in items
            if len(completed_reads) >= 2 and seen.get(item["agent_id"]) != item["cursor"]
        ]
        helper_deliveries.extend(fresh)
        return {
            "updates": fresh,
            "pending": [],
            "pending_count": 0,
            "cursors": {item["agent_id"]: item["cursor"] for item in items}
            if len(completed_reads) >= 2
            else {},
            "wait_for_user": None if user_waits else wait_for_user,
        }

    def pending():
        items = (
            [
                {"id": "update", "text": STEERING},
                {"id": "signed-in", "text": "I signed in", "pause_reply": True},
            ]
            if len(completed_reads) >= 3
            else []
        )
        # Sent while the first summary was being written.
        return [*items, {"id": "during", "text": "Also note the renewal date."}] if during else items

    steering = SteeringMiddleware(pending, helper_updates=helper_updates)
    from wavefunction.local_coding import EnvironmentMiddleware

    # Last, as the app stacks it: the summary can replace the environment notice, which is then
    # said again before the model call.
    graph = create_agent(
        model,
        tools=[read_mail],
        middleware=[
            steering,
            summary,
            SteeringAfterCompaction(pending, helper_updates=helper_updates),
            RequestBudgetMiddleware(store),
            EnvironmentMiddleware(None, local=False),
        ],
        checkpointer=InMemorySaver(),
    )
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=GOAL)]},
        {"configurable": {"thread_id": "synthetic-audit"}, "recursion_limit": 150},
    )
    assert result["messages"][-1].content == "Example: 10 USD monthly, renewal confirmed."
    assert len(helper_deliveries) == 2 and result["helper_cursors"] == {
        "receipt-helper": "result-1",
        "renewal-helper": "renewal-1",
    }
    # One wait after the first final answer, then one model step for the helper's result.
    assert user_waits == [len(model._seen) - 1]
    assert any("Renewal confirmed." in str(m.content) for m in model._seen[-1])
    assert len(summary._summary_model._seen) >= 2
    assert any(m.content == "Also note the renewal date." + PARALLEL_HINT for m in model._seen[during[0]])
    from wavefunction.execution_policy import _is_user_message, active_request_start

    for i, messages in enumerate(model._seen):
        assert any(isinstance(m, HumanMessage) and m.content == GOAL for m in messages)
        assert any(m.additional_kwargs.get("lc_source") == "environment" for m in messages)
        assert messages[active_request_start(messages)].content == GOAL
        assert all(
            not _is_user_message(m)
            for m in messages
            if m.additional_kwargs.get("lc_source") == "helper_update"
        )
        if i >= 3:
            # A parent reads a mid-task message with the parallel-work note appended.
            assert any(m.content == STEERING + PARALLEL_HINT for m in messages)
            # A reply to a pause is not a new request to delegate.
            assert any(m.content == "I signed in" for m in messages)
    # The app's own stack has it last too.
    from types import SimpleNamespace

    from wavefunction.agent_factory import assemble_middleware

    assert isinstance(
        assemble_middleware(model, store, document_context=SimpleNamespace(computer=None))[-1],
        EnvironmentMiddleware,
    )
    stack = [type(m).__name__ for m in assemble_middleware(model, store, steering=lambda: [])]
    assert stack.index("SteeringAfterCompaction") == stack.index("RetryingSummarizationMiddleware") + 1
    assert any(event == "summarization_started" for event, _ in events)
    assert len([event for event, _ in events if event == "summarization_finished"]) == len(
        summary._summary_model._seen
    )
    assert GOAL not in str(events) and STEERING not in str(events)


def test_request_budget_preserves_original_request_steering_and_environment_during_overflow(tmp_path):
    from langchain.agents.middleware import ModelRequest

    from wavefunction.local_coding import environment_message

    messages = tool_history(20)
    messages.insert(7, HumanMessage(content=STEERING, id="steering-update"))
    model = ScriptedModel(script=[], profile={"max_input_tokens": 32768})
    budget = RequestBudgetMiddleware(ResultStore(tmp_path))
    prepared = budget.prepare(ModelRequest(model=model, tools=[], messages=messages))
    assert any(m.content == GOAL for m in prepared.messages)
    assert any(m.content == STEERING for m in prepared.messages)
    assert len(prepared.messages) < len(messages)
    # An earlier turn is cut whole, at the next user's message rather than at the environment
    # notice after its request, which left its answer without the request it answered; and
    # what the notice said goes on.
    earlier = [
        HumanMessage(content="Earlier request " + "x" * 200000, id="earlier"),
        environment_message({"local": "Network is enabled."}, first=True),
        AIMessage(content="Earlier answer"),
    ]
    prepared = budget.prepare(
        ModelRequest(model=model, tools=[], messages=[*earlier, HumanMessage(content=GOAL, id="request")])
    )
    said = [str(m.content) for m in prepared.messages]
    assert not any("Earlier request" in text or "Earlier answer" in text for text in said)
    assert any("Network is enabled." in text for text in said) and said[-1] == GOAL
    # A request too long for one call is excerpted; the environment's guidance beside it is not.
    guidance = "Repository rule. " * 3500
    prepared = budget.prepare(
        ModelRequest(
            model=model,
            tools=[],
            messages=[
                HumanMessage(content=GOAL + " " + "x" * 50000, id="request"),
                environment_message({"repository_instructions": guidance}, first=True),
            ],
        )
    )
    assert guidance in str(prepared.messages[-1].content) and len(str(prepared.messages[0].content)) < 50000


def test_recent_user_constraints_and_corrections_survive_two_compactions(tmp_path):
    model = ScriptedModel(script=[], profile={"max_input_tokens": 32768})
    summary = RetryingSummarizationMiddleware(model, store=ResultStore(tmp_path))
    summary._summary_model = type(
        "SummaryModel", (), {"invoke": Mock(return_value=AIMessage(content="Continue checking receipts."))}
    )()
    instructions = [
        HumanMessage(
            content="Read mail only; do not send or move it. Report amounts in USD.", id="constraint"
        ),
        HumanMessage(
            content="Use CAD instead of USD; keep the original currency alongside it.", id="correction"
        ),
        HumanMessage(content="Now check annual subscriptions too.", id="current-request"),
    ]
    messages = [
        instructions[0],
        tool_call_message("search_available_tools", {"query": "calculator"}, "search"),
        ToolMessage(
            content="Loaded calculator",
            tool_call_id="search",
            name="search_available_tools",
            artifact={"discovered_tools": ["calculator"]},
        ),
        AIMessage(content="Understood."),
        instructions[1],
        AIMessage(content="Understood."),
        instructions[2],
        *tool_history()[1:],
    ]
    for iteration in range(2):
        update = summary.before_model({"messages": messages}, None)
        assert update is not None
        messages = add_messages(messages, update["messages"])
        assert [
            m.content
            for m in messages
            if isinstance(m, HumanMessage) and m.additional_kwargs.get("lc_source") != "summarization"
        ] == [m.content for m in instructions]
        assert len([m for m in messages if m.additional_kwargs.get("lc_source") == "summarization"]) == 1
        if not iteration:
            messages.extend(tool_history(batch=1)[1:])
    # A tool loaded by a search that was summarized away stays loaded.
    from wavefunction.tool_discovery import ToolDiscoveryMiddleware

    assert not any(isinstance(m, ToolMessage) and m.tool_call_id == "search" for m in messages)
    assert "calculator" in ToolDiscoveryMiddleware._found(messages)
    # In the order they were offered (the order found), even when an earlier tool was used
    # again later, so the first request after a compaction keeps the tool list's prefix.
    history = [
        tool_call_message("search_available_tools", {"query": "calculator"}, "s1"),
        ToolMessage(
            content="Loaded",
            tool_call_id="s1",
            name="search_available_tools",
            artifact={"discovered_tools": ["calculator"]},
        ),
        tool_call_message("search_available_tools", {"query": "skill"}, "s2"),
        ToolMessage(
            content="Loaded",
            tool_call_id="s2",
            name="search_available_tools",
            artifact={"discovered_tools": ["save_skill"]},
        ),
        tool_call_message("calculator", {"expression": "1+1"}, "c1"),
        ToolMessage(content="2", tool_call_id="c1", name="calculator"),
    ]
    handoff = summary._finish_summary(AIMessage(content="Handoff: computed 1+1."), None, history)
    compacted = RetryingSummarizationMiddleware._build_new_messages(handoff)

    def offered(history):
        return [
            name
            for name in dict.fromkeys(reversed(ToolDiscoveryMiddleware._found(history)))
            if name in ("calculator", "save_skill")
        ]

    assert offered(compacted) == offered(history) == ["calculator", "save_skill"]


def test_recent_user_retention_is_bounded_without_losing_the_active_request(tmp_path):
    summary = RetryingSummarizationMiddleware(ScriptedModel(script=[]), store=ResultStore(tmp_path))
    obsolete = HumanMessage(content="Old instruction: use USD.", id="obsolete")
    oversized_correction = HumanMessage(
        content="Replace the old instruction. " + "New requirements. " * 2000, id="correction"
    )
    current = HumanMessage(content="Now check invoices.", id="new-request")
    messages = [
        obsolete,
        AIMessage(content="Done"),
        oversized_correction,
        AIMessage(content="Done"),
        current,
        *tool_history()[1:],
    ]
    _, retained = summary._partition_messages(messages, len(messages) - 2)
    assert current in retained
    # An over-budget correction remains in the summary input/archive. Do not skip it
    # and pin an older instruction that it supersedes instead.
    assert obsolete not in retained and oversized_correction not in retained
