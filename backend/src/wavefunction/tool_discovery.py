"""Request-sized tool catalogues.

Only already-enabled tools are searchable. Selection is derived from each conversation's
messages, never mutable state on a middleware shared by concurrent runs.
"""

from __future__ import annotations

import json
import re

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, tool

from .execution_policy import _is_user_message, context_window, emit_policy, tool_schema_tokens
from .tool_schemas import tool_tokens

SEARCH_TOOL = "search_available_tools"
PAGE_SIZE = 5
# On-demand tools loaded at once. A working set, not a saved-catalogue limit: any enabled tool
# can be discovered again, and the everyday tools are not counted against it.
WORKING_SET_SIZE = 32
# How many of a connector's tool names the prompt lists before saying how many more there are.
LISTED_PER_CONNECTOR = 12
STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "please",
    "tool",
    "tools",
    "use",
    "can",
    "you",
    "are",
    "into",
    "then",
}
# Only known optional capabilities are deferred. Coding, questions, helpers and unknown
# built-ins stay available; groups never add a tool to the captured spec.
BUILTIN_GROUPS = {
    "computer": (
        "Browser/desktop input, screenshots, file transfer and takeover",
        (
            "computer_screenshot",
            "computer_click",
            "computer_double_click",
            "computer_type",
            "computer_key",
            "computer_scroll",
            "computer_move",
            "computer_drag",
            "computer_wait",
            "computer_save_file",
            "computer_add_file",
            "computer_request_takeover",
        ),
    ),
    "documents": (
        "Uploaded documents and mail attachments",
        ("list_documents", "read_documents", "search_documents", "save_mail_attachments"),
    ),
    "terminals": (
        "Interactive shell sessions and background servers",
        ("start_terminal", "list_terminals", "read_terminal", "write_terminal", "close_terminal"),
    ),
    "web": ("Search the web and fetch pages", ("web_search", "fetch_page")),
    "setup": (
        "Choose repository, request folder access or sign in to GitHub",
        ("choose_repository_folder", "request_folder_access", "configure_github"),
    ),
    "calculation": ("Calculator", ("calculator",)),
    "skills": ("Create or update the user's Global skills", ("save_skill",)),
    "routines": (
        "Run a project chat on a schedule: create, list or cancel routines",
        ("create_routine", "list_routines", "cancel_routine"),
    ),
    "projects": (
        "The user's projects: list, create one (and clone a repository into it), archive old chats, remove one",
        ("list_projects", "create_project", "archive_chats", "remove_project"),
    ),
}
BUILTIN_GROUP = {name: group for group, (_, names) in BUILTIN_GROUPS.items() for name in names}


def _terms(text: str) -> set[str]:
    # Words of one or two letters ("a", "to", "in", "on") matched nearly every tool.
    return {
        word for word in re.findall(r"[^\W_]+", text.casefold()) if len(word) > 2 and word not in STOP_WORDS
    }


def _name(value: BaseTool | dict) -> str:
    return value.name if isinstance(value, BaseTool) else value.get("function", value).get("name", "")


def _on_demand(name: str) -> bool:
    return "__" in name or name in BUILTIN_GROUP


class ToolDiscoveryMiddleware(AgentMiddleware):
    def __init__(self, tools: list[BaseTool]):
        self.catalogue = {item.name: item for item in tools}
        if SEARCH_TOOL in self.catalogue:
            raise ValueError(f"{SEARCH_TOOL} is reserved for the harness")
        self.costs = {name: tool_tokens(item, tool_schema_tokens) for name, item in self.catalogue.items()}
        self.index = {}
        for name, item in self.catalogue.items():
            group = BUILTIN_GROUP.get(name, "")
            description = BUILTIN_GROUPS[group][0] if group else ""
            self.index[name] = (_terms(name) | _terms(group), _terms(item.description) | _terms(description))
        self.tools = [self._search_tool()]

    def matches(self, query: str) -> list[str]:
        terms = _terms(query)
        if not terms:
            return sorted(self.catalogue)
        scored = []
        for name, (name_terms, description_terms) in self.index.items():
            score = 8 * len(terms & name_terms) + len(terms & description_terms)
            if name.casefold() in query.casefold():
                score += 100
            if score:
                scored.append((-score, name))
        return [name for _, name in sorted(scored)]

    def _search_tool(self) -> BaseTool:
        @tool(SEARCH_TOOL, response_format="content_and_artifact")
        def search_available_tools(
            query: str = "", offset: int = 0, limit: int = PAGE_SIZE
        ) -> tuple[str, dict]:
            """Find enabled tools by group, connector, task or exact name.

            Related built-ins load together next step. Empty query browses; paginate with
            next_offset or raise limit. Search again if a schema is absent. Grants no access.
            """
            if offset < 0 or not 1 <= limit <= 25:
                raise ValueError("Use a non-negative offset and a limit from 1 to 25.")
            matches = self.matches(query)
            page = matches[offset : offset + limit]
            related = [
                name
                for match in page
                if match in BUILTIN_GROUP
                for name in BUILTIN_GROUPS[BUILTIN_GROUP[match]][1]
                if name in self.catalogue
            ]
            names = list(dict.fromkeys([*page, *related]))
            result = {
                "tools": [
                    {"name": name, "description": self.catalogue[name].description[:600]} for name in names
                ],
                "total_matches": len(matches),
                "next_offset": offset + len(page) if offset + len(page) < len(matches) else None,
                "note": "Loaded next step; search an exact name if its schema is absent."
                if names
                else "No matches. Try fewer words, a connector name, or an empty query to browse.",
            }
            return json.dumps(result), {"discovered_tools": names}

        search_available_tools.metadata = {"readOnlyHint": True}
        return search_available_tools

    def prepare(self, request: ModelRequest) -> ModelRequest:
        available = {_name(item): item for item in request.tools}
        ordinary = [item for item in request.tools if _name(item) != SEARCH_TOOL]
        window = context_window(request.model)
        small = max(512, min(8192, window // 5))
        # The soft target for schemas grows with the window. It was capped at 8,192 tokens,
        # so a model with a million tokens of room was offered the same handful as a 41k one.
        budget = max(small, window // 10)
        # Framework tools (skills, overflow reads, todos) must remain usable too. Tool policies
        # were applied before this catalogue was built; never load from the global registry.
        everyday = [
            name
            for name in available
            if name != SEARCH_TOOL and (name not in self.catalogue or not _on_demand(name))
        ]
        on_demand = [name for name in self.catalogue if name in available and _on_demand(name)]
        if not on_demand:
            # The agent's own tools are what it was built to work with. A small window used
            # to make them compete for room, and a coding agent on a 32k model was not
            # offered read_file, edit_file or run_command.
            return request.override(tools=ordinary)
        if tool_schema_tokens([available[name] for name in everyday]) > window // 4:
            # A window too small to hold even the everyday tools: optional tools compete for it.
            return self._compete(request, available, ordinary, small, everyday)

        # What the agent found or used in this conversation stays loaded, newest first. The
        # set used to be rebuilt from the last user message, so every message cost a search
        # again; and its first members were guessed from the words of that message, which
        # offered an agent five arbitrary tools and hid its own `write_artifact`.
        loaded = self._found(request.messages)
        picked: list[str] = []
        used = 0
        for name in dict.fromkeys(loaded):
            if name not in on_demand or name in picked:
                continue
            cost = self.costs[name]
            # An explicitly discovered tool must get a chance even when its schema alone
            # exceeds the soft target. The final request budget still applies.
            if len(picked) >= WORKING_SET_SIZE or (used + cost > budget and picked):
                continue
            picked.append(name)
            used += cost
        offered = self._offered(request, set(everyday), picked, loaded)
        emit_policy("tools_selected", tools=[_name(item) for item in offered], available=len(ordinary))
        return request.override(
            tools=offered,
            system_prompt=(request.system_prompt or "") + self._on_demand_note(on_demand),  # pyright: ignore[reportCallIssue]
        )

    @staticmethod
    def _found(messages) -> list[str]:
        """Tools the conversation searched for or called, newest first."""
        loaded: list[str] = []
        for message in reversed(messages):
            if (
                isinstance(message, HumanMessage)
                and message.additional_kwargs.get("lc_source") == "summarization"
            ):
                loaded.extend(message.additional_kwargs.get("loaded_tools", []))
            elif (
                isinstance(message, ToolMessage) and message.name == SEARCH_TOOL and message.status != "error"
            ):
                loaded.extend((message.artifact or {}).get("discovered_tools", []))
            elif isinstance(message, AIMessage):
                loaded.extend(call["name"] for call in message.tool_calls)
        return loaded

    @staticmethod
    def _offered(request: ModelRequest, pinned: set[str], picked: list[str], loaded: list[str]) -> list:
        """The pinned tools where they are, then the loaded ones in the order they were found.

        A found tool used to take its place in the catalogue's order, in the middle of the
        list, which changed the start of every request after it and so everything a provider
        had cached. Appended in the order found, the list only grows at its end.
        """
        available = {_name(item): item for item in request.tools}
        order = [name for name in dict.fromkeys([*reversed(loaded), *picked]) if name in picked]
        return [item for item in request.tools if _name(item) in pinned or _name(item) == SEARCH_TOOL] + [
            available[name] for name in order
        ]

    def _on_demand_note(self, on_demand: list[str]) -> str:
        """What exists, so the model knows what to search for instead of being handed a guess."""
        connectors: dict[str, list[str]] = {}
        groups = set()
        for name in on_demand:
            if name in BUILTIN_GROUP:
                groups.add(BUILTIN_GROUP[name])
                continue
            server, _, short = name.partition("__")
            connectors.setdefault(server, []).append(short)
        lines = [
            f"- {group}: {description}"
            for group, (description, _) in BUILTIN_GROUPS.items()
            if group in groups
        ]
        for server, names in connectors.items():
            listed = ", ".join(names[:LISTED_PER_CONNECTOR])
            more = (
                f", and {len(names) - LISTED_PER_CONNECTOR} more" if len(names) > LISTED_PER_CONNECTOR else ""
            )
            lines.append(f"- {server} ({len(names)} tools): {listed}{more}")
        return (
            "\n\nAvailable on demand: search_available_tools\n"
            + "\n".join(lines)
            + "\nSearch a group/exact name before declaring unavailable. "
            "Discovery grants no permissions."
        )

    def _compete(
        self, request: ModelRequest, available: dict, ordinary: list, budget: int, everyday: list[str]
    ) -> ModelRequest:
        """A small window: optional tools compete for the schema budget.

        Everyday tools are all offered. Of the optional tools, recent searches and
        calls come first, then the best matches for the current request, which is the only hint
        there is when the agent cannot be shown what it has.
        """
        pinned = set(everyday)
        recent = self._found(request.messages)
        query = ""
        for message in reversed(request.messages):
            if _is_user_message(message):
                query = message.text
                break
        priorities = list(dict.fromkeys([*recent, *self.matches(query)[:PAGE_SIZE]]))
        used = tool_schema_tokens([available[name] for name in pinned])
        picked: list[str] = []
        for name in priorities:
            if name not in self.catalogue or name not in available or name in pinned or name in picked:
                continue
            cost = self.costs[name]
            if len(picked) >= WORKING_SET_SIZE or (used + cost > budget and (picked or name not in recent)):
                continue
            picked.append(name)
            used += cost
        offered = self._offered(request, pinned, picked, recent)
        prompt = (request.system_prompt or "") + self._on_demand_note(
            [name for name in self.catalogue if name in available and _on_demand(name)]
        )
        emit_policy("tools_selected", tools=[_name(item) for item in offered], available=len(ordinary))
        return request.override(tools=offered, system_prompt=prompt)  # pyright: ignore[reportCallIssue]

    async def awrap_model_call(self, request, handler):
        return await handler(self.prepare(request))
