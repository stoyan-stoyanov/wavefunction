"""Hold back an action the model keeps repeating, until it has done something else.

`ProgressMiddleware` says so at three identical results and ends the run at ten. Saying so was
not enough: a model set on one call sent it again after every note and every fresh read of the
file, until the run ended stuck with its work often done. Without the tool it has to finish or
find another way, and rewriting the file with `write_file` usually worked where the edit had not.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .execution_policy import SAFE_READS, _signature

HOLD_AT = 3


def _last_action(messages: list) -> list[tuple[Any, ToolMessage]]:
    """The latest calls that did more than read, each with the result it got."""
    results: dict[str, ToolMessage] = {}
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            results[message.tool_call_id] = message
        elif isinstance(message, AIMessage) and any(c["name"] not in SAFE_READS for c in message.tool_calls):
            return [(call, results[call["id"]]) for call in message.tool_calls if call["id"] in results]
    return []


class RepeatHoldMiddleware(AgentMiddleware):
    """Reading does not lift a hold, because models read and sent the call again, and reading is
    never held back, because other files are how a model gets unstuck.

    Waits that really waited are never counted, so they are never held. Changing the tools misses
    the provider's cache for these steps, which a run going nowhere can afford.
    """

    async def awrap_model_call(self, request, handler):
        counts = (request.state or {}).get("repeated_actions") or {}
        held = {
            call["name"]
            for call, message in _last_action(request.messages)
            if counts.get(_signature(call, message), 0) >= HOLD_AT
        }
        if not held:
            return await handler(request)
        names = ", ".join(sorted(held))
        return await handler(
            request.override(
                tools=[item for item in request.tools if getattr(item, "name", None) not in held],
                messages=[
                    *request.messages,
                    HumanMessage(
                        content=f"{names} cannot be used until you do something else, because it keeps returning the same result. If the work is finished, say so and stop.",
                        additional_kwargs={"lc_source": "loop_feedback"},
                    ),
                ],
            )
        )
