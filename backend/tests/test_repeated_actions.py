"""A repeated action is held back until the model does something else.

The traces this came from: models sent one failing edit again after every note and every fresh
read of the file, until the run was stopped as stuck, often with its work already done.
"""

from __future__ import annotations

from conftest import ScriptedModel, tool_call_message
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from pydantic import PrivateAttr

from wavefunction.execution_policy import ProgressMiddleware
from wavefunction.repeated_actions import RepeatHoldMiddleware


class RecordingModel(ScriptedModel):
    _seen: list = PrivateAttr(default_factory=list)

    async def _agenerate(self, messages, *args, **kwargs):
        self._seen.append(list(messages))
        return await super()._agenerate(messages, *args, **kwargs)


@tool
def save(name: str) -> str:
    """Save a setting."""
    return "No change."


@tool
def read_file(path: str) -> str:
    """Read a file."""
    return f"The text of {path}."


@tool
def write_other(name: str) -> str:
    """Save another way."""
    return f"Saved {name}."


EVERY = {"save", "read_file", "write_other"}


async def offered_tools(*script: AIMessage) -> tuple[list[set[str]], RecordingModel]:
    offered: list[set[str]] = []

    class Offered(AgentMiddleware):
        async def awrap_model_call(self, request, handler):
            offered.append({getattr(item, "name", "") for item in request.tools})
            return await handler(request)

    model = RecordingModel(script=[*script, AIMessage(content="Finished.")])
    agent = create_agent(
        model=model,
        tools=[save, read_file, write_other],
        middleware=[ProgressMiddleware(), RepeatHoldMiddleware(), Offered()],
    )
    await agent.ainvoke({"messages": [HumanMessage(content="Set up email")]})
    return offered, model


async def test_a_held_action_stays_held_through_reads_until_something_else_happens() -> None:
    saves = [tool_call_message("save", {"name": "email"}, f"save{n}") for n in range(3)]
    read = tool_call_message("read_file", {"path": "a"}, "read")
    other = tool_call_message("write_other", {"name": "email"}, "other")
    offered, model = await offered_tools(*saves, read, other)
    assert offered == [EVERY, EVERY, EVERY, EVERY - {"save"}, EVERY - {"save"}, EVERY]
    assert "save cannot be used until you do something else" in model._seen[3][-1].content


async def test_reading_is_never_held_back() -> None:
    reads = [tool_call_message("read_file", {"path": "a"}, f"read{n}") for n in range(3)]
    offered, _ = await offered_tools(*reads)
    assert offered == [EVERY] * 4

    batches = [
        AIMessage(
            content="",
            tool_calls=[
                *tool_call_message("read_file", {"path": "a"}, f"read{n}").tool_calls,
                *tool_call_message("save", {"name": "email"}, f"save{n}").tool_calls,
            ],
        )
        for n in range(3)
    ]
    offered, _ = await offered_tools(*batches)
    assert offered == [EVERY, EVERY, EVERY, EVERY - {"save"}]
