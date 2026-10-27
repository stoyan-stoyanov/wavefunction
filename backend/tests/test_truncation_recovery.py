import pytest
from conftest import ScriptedModel, agent_spec
from langchain_core.messages import AIMessage

from wavefunction.agent_factory import build_agent
from wavefunction.execution_policy import EMPTY_TRUNCATION_NOTICE
from wavefunction.toolkit import ToolContext


def truncated(invalid=False):
    calls = [{"name": "calculator", "args": {"expression": "99+1"}, "id": "partial", "type": "tool_call"}]
    if invalid:
        return AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": "calculator",
                    "args": '{"expression":',
                    "id": "partial",
                    "error": "incomplete",
                    "type": "invalid_tool_call",
                }
            ],
            response_metadata={"finish_reason": "length"},
        )
    return AIMessage(content="", tool_calls=calls, response_metadata={"finish_reason": "length"})


def thought_only():
    """A reasoning model that spent its whole output budget thinking and said nothing."""
    chunks = [{"reasoning_content": "Considering the options. "} for _ in range(50)]
    return AIMessage(
        content="",
        additional_kwargs={"gateway_reasoning_chunks": chunks},
        response_metadata={"finish_reason": "length"},
    )


@pytest.mark.parametrize("invalid", [False, True])
async def test_truncated_calls_are_discarded_and_model_retries_in_actual_graph(stack, invalid):
    from wavefunction.execution_policy import TRUNCATED_REASONS, finish_reason

    shapes = [
        {"finish_reason": "length"},
        {"stop_reason": "max_tokens"},
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
    ]
    assert all(
        finish_reason(AIMessage(content="", response_metadata=shape)) in TRUNCATED_REASONS for shape in shapes
    )
    agent = stack.agent
    model = ScriptedModel(
        script=[
            truncated(invalid),
            thought_only(),
            AIMessage(content="Recovered without executing incomplete calls"),
        ]
    )
    stack.models.chat_model = lambda *a, **kw: model
    graph = build_agent(
        agent_spec(),
        stack.models,
        ToolContext(
            agent["id"],
            stack.repository,
            stack.files,
            stack.secrets,
            coding_workspace=stack.services.coding_workspace,
        ),
    )
    result = await graph.ainvoke({"messages": [{"role": "user", "content": "Calculate"}]})
    assert model._cursor == 3
    assert result["messages"][-1].content.startswith("Recovered")
    assert not any(message.type == "tool" for message in result["messages"])
    # An empty reply cut off while thinking is retried too, and its reasoning is not kept.
    assert any(message.content == EMPTY_TRUNCATION_NOTICE for message in result["messages"])
    assert not any("Considering" in str(message.additional_kwargs) for message in result["messages"])


async def test_repeated_truncation_fails_instead_of_reporting_completion(stack):
    agent = stack.agent
    model = ScriptedModel(script=[truncated()])
    stack.models.chat_model = lambda *a, **kw: model
    run = await stack.runtime.start_run(agent["id"], "Calculate", spec=agent_spec())
    await stack.runtime.wait(run["id"])
    finished = stack.repository.get_run(run["id"])
    assert finished["status"] == "failed"
    assert "three times in a row" in finished["error"]
    assert model._cursor == 3
    # The next request gets its own retries, and a cut-off reply's tokens count once.
    cut = truncated()
    cut.usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}
    model = ScriptedModel(
        script=[
            cut,
            AIMessage(
                content="Recovered in pieces",
                usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
        ]
    )
    again = await stack.runtime.start_run(
        agent["id"], "Do it in smaller pieces", spec=agent_spec(), conversation_id=finished["conversation_id"]
    )
    recovered = await stack.runtime.wait(again["id"])
    assert recovered["status"] == "completed", recovered.get("error")
    assert recovered["usage"]["total_tokens"] == 165
    await stack.runtime.aclose()
