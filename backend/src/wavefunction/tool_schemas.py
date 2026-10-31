"""What a tool's signature costs, remembered.

Each task decorates its tools afresh, because they close over the task, and LangChain builds a
Pydantic model from every signature. That took most of the time to build an agent, though the
model depends only on the function's code, annotations, defaults and docstring, and never on what
it closes over, so the same inputs give the same model.
"""

from __future__ import annotations

import inspect
from collections import OrderedDict
from collections.abc import Callable, Hashable
from types import FunctionType
from typing import Any

from langchain_core.tools import BaseTool, structured

LIMIT = 512
_build_schema = structured.create_schema_from_function  # pyright: ignore[reportPrivateImportUsage]
_schemas: OrderedDict[Hashable, Any] = OrderedDict()
_sizes: OrderedDict[Hashable, Any] = OrderedDict()


def _remember(cache: OrderedDict[Hashable, Any], key: Hashable, build: Callable[[], Any]) -> Any:
    try:
        found = cache.get(key)
    except TypeError:  # An annotation or default that can't be hashed.
        return build()
    if found is None:
        found = cache[key] = build()
        if len(cache) > LIMIT:
            cache.popitem(last=False)
    else:
        cache.move_to_end(key)
    return found


def _create_schema(model_name: str, func: Callable[..., Any], *, filter_args=None, **options: Any) -> Any:
    def build():
        return _build_schema(model_name, func, filter_args=filter_args, **options)

    source = inspect.unwrap(func)
    if not isinstance(source, FunctionType):
        return build()
    key = (
        model_name,
        source.__code__,
        source.__doc__,
        tuple(source.__annotations__.items()),
        source.__defaults__,
        tuple((source.__kwdefaults__ or {}).items()),
        tuple(filter_args or ()),
        tuple(options.items()),
    )
    return _remember(_schemas, key, build)


structured.create_schema_from_function = _create_schema  # pyright: ignore[reportPrivateImportUsage]


def tool_tokens(tool: BaseTool, count: Callable[[list[BaseTool]], int]) -> int:
    """What `count` says one tool's definition takes in a request."""
    return _remember(_sizes, (tool.name, tool.description, tool.args_schema), lambda: count([tool]))
