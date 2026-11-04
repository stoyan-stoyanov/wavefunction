"""Agent builds repeat as little as they can without sharing anything between tasks."""

from dataclasses import replace

from langchain_core.tools import tool

from wavefunction import tool_schemas  # noqa: F401 - what makes schemas reused
from wavefunction.toolkit import ToolContext


def test_a_tool_schema_is_reused_only_for_the_same_signature():
    def build(default=1, doc="Say it."):
        def say(text: str, times: int = default) -> str:
            return text * times

        say.__doc__ = doc
        return tool(say)

    assert build().args_schema is build().args_schema
    assert build(2).args_schema.model_fields["times"].default == 2  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
    assert build(doc="Say it twice.").description == "Say it twice."


def test_a_changed_context_never_reuses_the_tools_built_for_another():
    context = ToolContext("agent", None, None, None, coding_workspace=None)  # pyright: ignore
    context.tool_groups["local"] = {}
    assert replace(context, working_directory="/synthetic").tool_groups == {}
    assert "local" in context.tool_groups
