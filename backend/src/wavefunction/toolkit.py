"""Tool registry and the execution context captured for each run.

Tools expose typed arguments and enforce the caller's access permissions. Failures raise;
ToolErrorMiddleware turns them into tool messages so the agent can recover.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx
from langchain_core.tools import BaseTool, tool

from .files import ProjectFileStore, readable_documents
from .repository import Repository
from .secrets import SecretBackend
from .spec_common import ModelSpec
from .tools import SafeCalculator
from .web import build_fetch_page

if TYPE_CHECKING:
    from .coding_workspace import CodingWorkspace

MAX_DOCUMENT_CHARS = 100_000
MAX_SEARCH_RESULTS = 8


@dataclass
class ToolContext:
    """Captured run ownership and the services available to its tools.

    In Wavefunction, agent_id identifies the built-in agent's storage, conversation_id
    owns checkpoint history, desktop_copy is the chat ID, and run_id owns execution.
    """

    # Stable storage identity; the built-in agent ID survives product renames.
    agent_id: str
    repository: Repository
    files: ProjectFileStore
    secrets: SecretBackend
    # Filled from the captured spec by build_agent; web_search needs the model gateway.
    models: Any = None
    model: ModelSpec | None = None
    # Default document page size, bounded by the model's context window.
    read_page: int = MAX_DOCUMENT_CHARS
    run_id: str | None = None
    parent_run_id: str | None = None
    # Optional services are explicit; their tool bindings come from the task's saved spec.
    skills: Any = None
    connectors: Any = None
    computer: Any = None
    # Checkpoint and live desktop ownership survive approval pauses.
    conversation_id: str | None = None
    # Stable chat identity for its kept browser home and command scratch directory.
    desktop_copy: str | None = None
    # The attachments accepted with this task, even if removed from the composer later.
    reference_files: list[dict[str, Any]] | None = None
    steering: Callable | None = None
    close_steering: Callable | None = None
    apply_steering: Callable | None = None
    helper_updates: Callable | None = None
    record_action_unknown: Callable | None = None
    # A chat's own task (not a helper) may pause for a folder card or the user's input.
    choose_folder: bool = False
    # Read-only callers can withhold connector writes and local mutation/commands.
    local_read_only: bool = False
    # A helper inherits a ceiling on folder access; later grants cannot widen it.
    folder_grants: list[dict[str, Any]] | None = None
    working_directory: str | None = None
    repository_instructions: bool = False
    # What this run's file tools have read or saved, by path, so write_file replaces a file
    # only while it is still what the agent last saw. One per run, shared by its tools.
    file_reads: dict[str, str] = field(default_factory=dict)
    # The chats, checkouts, terminals and reviews every task belongs to.
    coding_workspace: CodingWorkspace = field(kw_only=True)

    def local_grants(self):
        from .sandbox import Grant

        # Validate the saved path before resolving grants: a replaced checkout
        # must not redirect a captured run's write access to another folder.
        live, _ = self.coding_workspace.checkout_access(self.working_directory)
        if self.folder_grants is None:
            return live
        effective = []
        for row in self.folder_grants:
            captured = Grant.parse(row)
            for current in live:
                if captured.path.is_relative_to(current.path):
                    path = captured.path
                elif current.path.is_relative_to(captured.path):
                    path = current.path
                else:
                    continue
                effective.append(Grant(path, captured.writable and current.writable))
        return self.coding_workspace.worktree_grants(self.working_directory, effective)

    @property
    def local_scratch(self) -> Path:
        return self.tool_results_root / "local-computer" / (self.desktop_copy or self.agent_id)

    @property
    def tool_results_root(self) -> Path:
        # Keep the existing scoped evidence directory; old unowned stores are not adopted.
        return self.files.project_dir(self.agent_id) / "outputs" / "tool-results-v2"


@dataclass
class ToolDefinition:
    name: str
    build: Callable[[ToolContext], BaseTool]


def _readable_documents(context: ToolContext) -> list[dict[str, Any]]:
    """Uploaded documents captured for this task, checked against their durable records."""
    allowed = [record["id"] for record in context.reference_files or []]
    return readable_documents(context.repository, context.agent_id, allowed)


def _build_list_documents(context: ToolContext) -> BaseTool:
    @tool("list_documents")
    def list_documents() -> str:
        """List the documents uploaded to this project, with their sizes.

        Call this first to discover what is available before reading anything.
        """
        records = _readable_documents(context)
        if not records:
            return "No documents have been uploaded to this project."
        lines = [f"- {record['name']} ({len(record['extracted_text'])} characters)" for record in records]
        return "Available documents:\n" + "\n".join(lines)

    return list_documents


def _build_read_documents(context: ToolContext) -> BaseTool:
    # The default page is what fits this model's requests whole. The 100,000 characters it was
    # did not fit a 128k model's, so a first read came back as an excerpt with its middle cut.
    page = min(MAX_DOCUMENT_CHARS, context.read_page)

    @tool("read_documents", response_format="content_and_artifact")
    def read_documents(name: str = "", offset: int = 0, limit: int = page) -> tuple[str, dict]:
        """Read a project document's text, or view an uploaded image with a vision model.

        Args:
            offset: Character offset for continuing a previous read.
            limit: Maximum characters per document in this response (up to 100000). The default
                is a page that fits in full; continue from the offset the reply gives.
            name: Document name to read. Leave empty to read every document. Prefer naming a
                single document once you know which one you need — reading everything wastes
                context.
        """
        if offset < 0 or not 1 <= limit <= MAX_DOCUMENT_CHARS:
            raise ValueError(f"offset must be nonnegative and limit between 1 and {MAX_DOCUMENT_CHARS}")
        readable = records = _readable_documents(context)
        if not records:
            return "No documents have been uploaded to this project.", {"source_labels": []}
        if name:
            wanted = name.strip().casefold()
            records = [record for record in records if record["name"].casefold() == wanted] or [
                record for record in records if wanted in record["name"].casefold()
            ]
            if not records:
                # Every document it can read, the project's as well as its own.
                available = ", ".join(item["name"] for item in readable)
                raise ValueError(f"No document named {name!r}. Available documents: {available}")
        sections = [
            f"SOURCE: {record['name']}\n{record['extracted_text'][offset : offset + limit]}"
            + (
                f"\n[More text available: call read_documents with this name and offset={offset + limit}]"
                if len(record["extracted_text"]) > offset + limit
                else ""
            )
            for record in records
        ]
        return "\n\n---\n\n".join(sections), {
            "source_labels": [record["name"] for record in records],
            "document_images": [
                record["id"] for record in records if record.get("media_type", "").startswith("image/")
            ],
        }

    return read_documents


def _chunks(text: str, size: int = 1200) -> list[str]:
    """Paragraph-respecting windows, so a hit comes back with its surroundings intact.

    A paragraph longer than the window is split hard: PDF and OCR text routinely arrives with
    single newlines only, and keeping such a "paragraph" whole would return the entire
    document as one match — exactly the context blow-up this tool exists to prevent.
    """
    pieces: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        while len(paragraph) > size:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:size])
            paragraph = paragraph[size:]
        if len(current) + len(paragraph) > size and current:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        pieces.append(current)
    return pieces


def find_folded(text: str, needle: str) -> int:
    """Where a casefolded `needle` first occurs in `text` ignoring case, as a position in `text`
    itself, or -1. Characters like ß fold to more than one, which moves later positions."""
    folded = text.casefold()
    at = folded.find(needle)
    if at <= 0 or len(folded) == len(text):
        return at
    # The match starts in the first character whose folded form reaches past `at`.
    reached = 0
    for index, character in enumerate(text):
        reached += len(character.casefold())
        if reached > at:
            return index
    return at


def search_document_chunks(
    records: list[dict[str, Any]], query: str, max_results: int
) -> list[tuple[str, int, str]]:
    """The passages that best match the query, as (document, part, text).

    Term-frequency scoring over paragraph windows — deliberately no embeddings: it runs
    identically from source and in the frozen sidecar, needs nothing downloaded, and
    for "find the clause about X in these PDFs" exact words beat approximate meaning.
    """
    terms = [term for term in query.casefold().split() if len(term) >= 2]
    if not terms:
        return []
    scored: list[tuple[int, str, int, str]] = []
    for record in records:
        for part, chunk in enumerate(_chunks(record["extracted_text"]), start=1):
            haystack = chunk.casefold()
            score = sum(haystack.count(term) for term in terms)
            # Every term present is worth more than one term repeated.
            score += sum(5 for term in terms if term in haystack)
            if score > 0:
                scored.append((score, record["name"], part, chunk))
    scored.sort(key=lambda item: -item[0])
    return [(name, part, chunk) for _score, name, part, chunk in scored[:max_results]]


def _build_search_documents(context: ToolContext) -> BaseTool:
    @tool("search_documents", response_format="content_and_artifact")
    def search_documents(query: str, max_results: int = 6) -> tuple[str, dict]:
        """Find the passages in the project's documents that match a query.

        Use this before read_documents when the documents are long or numerous — it returns
        only the relevant parts, where reading everything wastes context. Search with the
        words you expect the document to use.

        Args:
            query: Words to look for, e.g. "termination notice period".
            max_results: How many passages to return, 1-12.
        """
        records = _readable_documents(context)
        if not records:
            return "No documents have been uploaded to this project.", {"source_labels": []}
        found = search_document_chunks(records, query, max(1, min(12, max_results)))
        if not found:
            names = ", ".join(record["name"] for record in records)
            return (
                f"Nothing in the documents matches {query!r}. Try different words, or read a "
                f"document directly. Available: {names}."
            ), {"source_labels": []}
        return "\n\n---\n\n".join(f"SOURCE: {name} (part {part})\n{chunk}" for name, part, chunk in found), {
            "source_labels": [name for name, _, _ in found]
        }

    return search_documents


def _build_calculator(_: ToolContext) -> BaseTool:
    @tool("calculator")
    def calculator(expression: str) -> str:
        """Evaluate an arithmetic expression and return the result.

        Supports + - * / // % ** and the functions abs, round, sqrt, min, max. Write the
        expression yourself — do not paste the user's sentence.

        Args:
            expression: An arithmetic expression, for example "1200 * 0.15" or "sqrt(144)".
        """
        return f"{expression} = {SafeCalculator.render(SafeCalculator.evaluate(expression))}"

    return calculator


def web_search_status(context: ToolContext) -> tuple[bool, str]:
    """Whether web search can actually run, and what to say when it cannot.

    Asked when the tool runs: a search that cannot work says what to connect rather than
    leaving the agent to answer silently from memory.
    """
    provider = (
        context.models.search_provider(context.model)
        if context.models is not None and context.model is not None
        else ("tavily" if context.secrets.get("tavily_api_key") else "none")
    )
    if provider == "built-in":
        where = context.model.provider if context.model else "openrouter"
        if where == "codex":
            return True, "Searches the web on your ChatGPT subscription. No extra key needed."
        return True, "Searches the web through OpenRouter. No extra key needed."
    if provider == "tavily":
        return True, "Searches the web through Tavily."
    where = context.model.provider if context.model else "another provider"
    return False, (
        f"Web search cannot run: this agent's model is on {where}, and no Tavily key is saved. "
        "Either switch the agent to a model on OpenRouter or a ChatGPT sign-in, both of which "
        "search the web themselves at no extra setup, or add a Tavily API key under "
        "Settings > Models > Web search."
    )


def _build_web_search(context: ToolContext) -> BaseTool:
    # Read with the task's model, so a running task's searches keep the policy it began with.
    zero_data_retention = context.models.zero_data_retention() if context.models is not None else False

    @tool("web_search")
    async def web_search(query: str, max_results: int = 5) -> str:
        """Search the web and return titles, URLs, and excerpts.

        Args:
            query: A focused search query that you compose. Do not pass the user's whole message.
            max_results: How many results to return, 1-8.
        """
        count = max(1, min(MAX_SEARCH_RESULTS, max_results))
        # Preferred because it needs nothing configured: OpenRouter and a ChatGPT sign-in both
        # run the search themselves. Tavily stays as the fallback for every other provider,
        # which is the only case that still needs a key.
        if (
            context.models is not None
            and context.model is not None
            and context.models.search_provider(context.model) == "built-in"
        ):
            return await context.models.web_search(
                context.model, query, count, zero_data_retention=zero_data_retention
            )

        api_key = context.secrets.get("tavily_api_key")
        if not api_key:
            _, reason = web_search_status(context)
            raise RuntimeError(f"{reason} Answer without web results and say that you did.")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": count, "search_depth": "basic"},
            )
            response.raise_for_status()
            data = response.json()
        results = data.get("results", [])[:count]
        if not results:
            return f"No web results for {query!r}."
        return "\n\n".join(
            f"SOURCE: {item.get('url', 'unknown')}\n{item.get('title', '')}\n{item.get('content', '')}"
            for item in results
        )

    return web_search


async def _desktop(context: ToolContext, setup: bool = False) -> Any:
    """The conversation's desktop, started on first use with this chat's kept home.

    With `setup`, a task whose desktop cannot start because computer use is not set up on this
    Mac pauses on the setup card for it, and carries on once the desktop is ready,
    rather than ending on "not set up". Only where this is the call's one pause: a paused tool
    runs again from the top, and a later pause in it would be handed this one's answer.
    """
    if context.computer is None:
        raise ValueError("Computer use is not available in this build, so this tool cannot run.")
    from .computer import DesktopUnavailable

    key = context.conversation_id or context.agent_id
    try:
        return await context.computer.session(key, context.desktop_copy)
    except DesktopUnavailable:
        # Starting a desktop has just asked Docker, so this is where things stand now.
        status = context.computer.status_cached()
        docker_ready = status.docker_available and not status.docker_problem
        if not setup or (docker_ready and status.image_ready):
            raise
    from langgraph.types import interrupt

    from .setup_rows import computer_setup_row

    if docker_ready:
        context.computer.start_build()
    interrupt({"kind": "connector_setup", "connectors": [computer_setup_row(context.computer)]})
    return await context.computer.session(key, context.desktop_copy)


# How long the desktop gets to redraw before the picture of an action's result is taken. A
# click that opens a menu is drawn in a frame; a click that loads a page is not, and the
# agent has computer_wait for those. Anthropic's reference loop waits two seconds; one is
# enough for the desktop here, and it is paid on every action.
ACTION_SETTLE_SECONDS = 1.0

# The desktop's own per-action sizes (sandbox_image/server.py). A longer request is done in
# pieces of these sizes, in order, so nothing is refused or silently clamped: text of any length
# is typed exactly, and a scroll of 200 notches scrolls 200 notches.
TYPE_CHUNK = 5_000
SCROLL_CHUNK = 20
KEYS_CHUNK = 32


async def _desktop_act(context: ToolContext, path: str, payload: dict[str, Any]) -> Any:
    session = await _desktop(context, setup=True)
    response = await session.client.post(path, json=payload)
    if response.status_code != 200:
        try:
            detail = response.json().get("error", response.text)
        except ValueError:
            detail = response.text
        raise RuntimeError(f"The computer refused the action: {detail}")
    return session


async def _look(context: ToolContext, session: Any) -> str:
    """Capture the screen and store it; the sentinel goes into the tool result.

    The PNG never enters the result itself: `ComputerUseMiddleware` re-attaches it to the
    model request from disk, and the run's `screenshot` event hands the UI the file to fetch.
    """
    response = await session.client.get("/screenshot")
    if response.status_code != 200:
        raise RuntimeError(f"Could not capture the screen: {response.text[:300]}")
    from .computer import save_screenshot

    return save_screenshot(
        context.files, context.agent_id, context.conversation_id or context.agent_id, response.content
    )


def _told(session: Any, said: str) -> str:
    """What a desktop tool says, with why its desktop opened blank the first time it is said."""
    note, session.note = getattr(session, "note", ""), ""
    return f"{said} {note}" if note else said


async def _acted(context: ToolContext, session: Any, did: str, settle: float = ACTION_SETTLE_SECONDS) -> str:
    """What an action returns: what was done, and the screen as it looks afterwards.

    Every action used to answer with a sentence and leave the looking to a separate
    `computer_screenshot` call: one model turn to click, another to see. The person
    watching the timeline saw the same gap — a picture only when the model chose to look,
    so a run of five clicks left the last picture five steps stale. Now the picture is part
    of the action, for the model and for the timeline alike. A failed capture does not undo
    the action, so it is reported rather than raised.
    """
    import asyncio

    if settle:
        await asyncio.sleep(settle)
    try:
        sentinel = await _look(context, session)
    except RuntimeError as error:
        return _told(session, f"{did} The screen could not be captured afterwards: {error}")
    return _told(session, f"{did} The screen afterwards: {sentinel}")


def _build_computer_request_takeover(context: ToolContext) -> BaseTool:
    @tool("computer_request_takeover")
    async def computer_request_takeover() -> str:
        """Pause for the user to sign in privately or complete MFA in the desktop browser.

        Call this at login pages. Never ask for passwords or verification codes in chat.
        The user controls the browser privately and explicitly resumes you when finished.
        """
        from langgraph.types import interrupt

        session = await _desktop(context)
        interrupt({"kind": "computer_takeover"})
        return await _acted(
            context, session, "The user returned control. Check the current page before acting."
        )

    return computer_request_takeover


def _build_computer_screenshot(context: ToolContext) -> BaseTool:
    @tool("computer_screenshot")
    async def computer_screenshot() -> str:
        """Look at the computer's screen without doing anything.

        Returns a screenshot of the sandboxed desktop. Firefox is already open when the
        desktop starts — click its address bar, type a URL, and press Return to browse. Take
        one before your first action. Every action already returns the screen as it looks
        afterwards, so look again only when something may have changed on its own, such as a
        page that was still loading. The screen is 1280x800 with the origin at the top left.
        """
        session = await _desktop(context, setup=True)
        sentinel = await _look(context, session)
        return _told(session, f"Screenshot captured (1280x800). {sentinel}")

    return computer_screenshot


def _build_computer_click(context: ToolContext) -> BaseTool:
    @tool("computer_click")
    async def computer_click(x: int, y: int, button: str = "left") -> str:
        """Click the mouse on the computer's screen.

        The screen is 1280x800 with the origin at the top left. Take a screenshot first to see
        where things are. The result includes the screen as it looks after the click.

        Args:
            x: Horizontal position in pixels, 0-1279.
            y: Vertical position in pixels, 0-799.
            button: "left", "middle" or "right".
        """
        session = await _desktop_act(context, "/click", {"x": x, "y": y, "button": button})
        return await _acted(context, session, f"Clicked at ({x}, {y}) with the {button} button.")

    return computer_click


def _build_computer_double_click(context: ToolContext) -> BaseTool:
    @tool("computer_double_click")
    async def computer_double_click(x: int, y: int) -> str:
        """Double-click the left mouse button on the computer's screen.

        The result includes the screen as it looks afterwards.

        Args:
            x: Horizontal position in pixels, 0-1279.
            y: Vertical position in pixels, 0-799.
        """
        session = await _desktop_act(context, "/double_click", {"x": x, "y": y})
        return await _acted(context, session, f"Double-clicked at ({x}, {y}).")

    return computer_double_click


def _build_computer_type(context: ToolContext) -> BaseTool:
    @tool("computer_type")
    async def computer_type(text: str) -> str:
        """Type text on the computer, into whatever currently has focus.

        Click the field first so the text lands where you intend. Use computer_key for keys
        like Return or Tab. The result includes the screen as it looks afterwards.

        Args:
            text: The exact characters to type, however many: long text is typed in pieces.
        """
        pieces = [text[start : start + TYPE_CHUNK] for start in range(0, len(text), TYPE_CHUNK)] or [text]
        for piece in pieces:
            session = await _desktop_act(context, "/type", {"text": piece})
        return await _acted(context, session, f"Typed {len(text)} characters.")

    return computer_type


def _build_computer_key(context: ToolContext) -> BaseTool:
    @tool("computer_key")
    async def computer_key(keys: str) -> str:
        """Press one or more keys or chords on the computer.

        The result includes the screen as it looks afterwards.

        Args:
            keys: Space-separated xdotool key names, for example "Return", "Tab",
                "ctrl+a", or "ctrl+a BackSpace" to clear a field. A chord such as "ctrl+a"
                is one key; any number of keys may follow each other.
        """
        tokens = keys.split()
        groups = [
            " ".join(tokens[start : start + KEYS_CHUNK]) for start in range(0, len(tokens), KEYS_CHUNK)
        ] or [keys]
        for group in groups:
            session = await _desktop_act(context, "/key", {"keys": group})
        return await _acted(context, session, f"Pressed {keys}.")

    return computer_key


def _build_computer_scroll(context: ToolContext) -> BaseTool:
    @tool("computer_scroll")
    async def computer_scroll(direction: str = "down", amount: int = 3, x: int = 640, y: int = 400) -> str:
        """Scroll the computer's screen.

        The result includes the screen as it looks afterwards.

        Args:
            direction: "up", "down", "left" or "right".
            amount: How many notches to scroll; a long scroll is done in pieces.
            x: Where to point the mouse while scrolling, in pixels.
            y: Where to point the mouse while scrolling, in pixels.
        """
        remaining = amount
        while True:
            step = min(remaining, SCROLL_CHUNK) if remaining > 0 else amount
            session = await _desktop_act(
                context, "/scroll", {"direction": direction, "amount": step, "x": x, "y": y}
            )
            remaining -= SCROLL_CHUNK
            if remaining <= 0:
                break
        return await _acted(context, session, f"Scrolled {direction} by {amount}.")

    return computer_scroll


def _build_computer_move(context: ToolContext) -> BaseTool:
    @tool("computer_move")
    async def computer_move(x: int, y: int) -> str:
        """Move the mouse pointer without clicking, for example to reveal a hover menu.

        The result includes the screen as it looks afterwards.

        Args:
            x: Horizontal position in pixels, 0-1279.
            y: Vertical position in pixels, 0-799.
        """
        session = await _desktop_act(context, "/move", {"x": x, "y": y})
        return await _acted(context, session, f"Moved the mouse to ({x}, {y}).")

    return computer_move


def _build_computer_drag(context: ToolContext) -> BaseTool:
    @tool("computer_drag")
    async def computer_drag(x1: int, y1: int, x2: int, y2: int) -> str:
        """Drag with the left mouse button held, from one point to another.

        The result includes the screen as it looks afterwards.

        Args:
            x1: Where the drag starts, horizontal pixels.
            y1: Where the drag starts, vertical pixels.
            x2: Where the drag ends, horizontal pixels.
            y2: Where the drag ends, vertical pixels.
        """
        session = await _desktop_act(context, "/drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        return await _acted(context, session, f"Dragged from ({x1}, {y1}) to ({x2}, {y2}).")

    return computer_drag


def _build_computer_wait(context: ToolContext) -> BaseTool:
    @tool("computer_wait")
    async def computer_wait() -> str:
        """Wait two seconds for the computer to catch up — a page loading, a window opening.

        The result includes the screen as it looks after the wait.
        """
        import asyncio

        # The session is touched so a long page load does not count as idleness.
        session = await _desktop(context, setup=True)
        await asyncio.sleep(2)
        return await _acted(context, session, "Waited 2 seconds.", settle=0)

    return computer_wait


def _build_local_tool(context: ToolContext, name: str) -> BaseTool:
    from .hands import build_hands, command_access

    def access_now():
        return command_access(context.repository, context.agent_id)

    # Read grants on every call, including revocation while a task is running.
    ws = context.coding_workspace
    record_changes: Callable | None = None
    changed_by: Callable | None = None
    if (run_id := context.run_id) and context.working_directory:
        try:
            thread = ws.thread(cast(str, context.desktop_copy))
            if thread["conversation_id"] == context.conversation_id:

                def record(runner, **kwargs):
                    return ws.reviews.operation(
                        thread["id"], run_id, context.working_directory, runner, **kwargs
                    )

                def edited_by(path):
                    # Agents sharing a checkout: a refused edit says who changed the file meanwhile.
                    since = context.repository.get_run(run_id)["started_at"]
                    other = ws.reviews.changed_by(thread["id"], context.working_directory, path, since)
                    if other is None:
                        return None
                    if other["id"] == thread.get("parent_thread_id"):
                        return "The main agent"
                    return f"{'Helper' if other['parent_thread_id'] else 'Chat'} {other['title']}"

                record_changes, changed_by = record, edited_by
        except KeyError:
            pass
    from .coding_access import approve_command, ensure_path_access, helper_folder_access, nearest_folder

    def ensure_access(path, writable):
        if context.folder_grants is not None:
            target = Path(path).expanduser().resolve()
            if target.is_relative_to(context.local_scratch.resolve()):
                return
            if any(
                target.is_relative_to(grant.path) and (not writable or grant.writable)
                for grant in context.local_grants()
            ):
                return
            if context.parent_run_id:
                # Outside its inherited folders, a helper asks the user on its own card.
                helper_folder_access(context, nearest_folder(path), writable)
                return
            from .execution_policy import ToolCallRejected

            raise ToolCallRejected(
                "This task can use only its captured folders and write only where it was granted."
            )
        ensure_path_access(ws, context.local_scratch, path, writable)

    tools = build_hands(
        context.local_scratch,
        context.local_grants,
        access_now,
        read_only=context.local_read_only,
        working_directory=context.working_directory,
        reads=context.file_reads,
        record_changes=record_changes,
        changed_by=changed_by,
        private_roots=[ws.services.settings.data_dir],
        ensure_access=ensure_access,
        credentials=ws.services.github_proxy.environment,
        # A task the user started, or one of its helpers, stops on a card for the user to decide.
        approve_command=(lambda *args, **kwargs: approve_command(ws, context.desktop_copy, *args, **kwargs))
        if (context.choose_folder and context.folder_grants is None) or context.parent_run_id
        else None,
    )
    return next(tool for tool in tools if tool.name == name)


def _build_terminal_tool(context: ToolContext, name: str) -> BaseTool:
    from .terminal_tools import build_terminal_tool

    return build_terminal_tool(context, name)


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    definition.name: definition
    for definition in (
        *(
            ToolDefinition(name=name, build=lambda context, name=name: _build_terminal_tool(context, name))
            for name in (
                "start_terminal",
                "list_terminals",
                "read_terminal",
                "write_terminal",
                "close_terminal",
            )
        ),
        ToolDefinition(name="find_files", build=lambda context: _build_local_tool(context, "find_files")),
        ToolDefinition(name="search_files", build=lambda context: _build_local_tool(context, "search_files")),
        ToolDefinition(name="read_file", build=lambda context: _build_local_tool(context, "read_file")),
        ToolDefinition(name="write_file", build=lambda context: _build_local_tool(context, "write_file")),
        ToolDefinition(name="edit_file", build=lambda context: _build_local_tool(context, "edit_file")),
        ToolDefinition(name="run_command", build=lambda context: _build_local_tool(context, "run_command")),
        ToolDefinition(name="list_documents", build=_build_list_documents),
        ToolDefinition(name="read_documents", build=_build_read_documents),
        ToolDefinition(name="search_documents", build=_build_search_documents),
        ToolDefinition(name="calculator", build=_build_calculator),
        ToolDefinition(name="web_search", build=_build_web_search),
        ToolDefinition(name="fetch_page", build=build_fetch_page),
        ToolDefinition(name="computer_request_takeover", build=_build_computer_request_takeover),
        ToolDefinition(name="computer_screenshot", build=_build_computer_screenshot),
        ToolDefinition(name="computer_click", build=_build_computer_click),
        ToolDefinition(name="computer_double_click", build=_build_computer_double_click),
        ToolDefinition(name="computer_type", build=_build_computer_type),
        ToolDefinition(name="computer_key", build=_build_computer_key),
        ToolDefinition(name="computer_scroll", build=_build_computer_scroll),
        ToolDefinition(name="computer_move", build=_build_computer_move),
        ToolDefinition(name="computer_drag", build=_build_computer_drag),
        ToolDefinition(name="computer_wait", build=_build_computer_wait),
    )
}


def _check_connectors(connectors: Any, names: set[str]) -> None:
    """Raise, saying what to do, unless each named connector is connected and has answered."""
    servers = {server.name: server for server in connectors.servers()}
    connecting, problems = [], []
    for name in sorted(names):
        server, found = servers.get(name), connectors.cached(name)
        if server is None:
            problems.append(
                f"{name} was removed. Add it again in Settings → Connectors to continue this task."
            )
        elif not server.enabled:
            problems.append(
                f"{name} is turned off. Turn it back on in Settings → Connectors to continue this task."
            )
        elif found is None:
            connecting.append(name)
        elif not found.connected:
            problems.append(
                f"{name} isn't connected. Sign in again in Settings → Connectors, then try again."
            )
    if problems:
        raise ValueError(" ".join(problems))
    if connecting:
        raise ValueError(
            f"{', '.join(connecting)} {'is' if len(connecting) == 1 else 'are'} still connecting. "
            "Wait for it to finish, then try again."
        )


def build_tools(names: list[str], context: ToolContext) -> list[BaseTool]:
    """Instantiate the named tools, bound to one project.

    A name the built-in registry does not know may still belong to a connector, whose MCP
    server hands over finished tools.
    A configured connector that has not completed discovery blocks a task that needs its tools;
    other tasks can start while discovery runs in the background. A connector that is turned
    off, removed or not connected fails preflight, naming what to fix: a running task must not
    silently substitute old knowledge for a configured data source. A tool its connected server
    no longer offers is left out, as the server would refuse it anyway: a task resumed after
    the server dropped one of the tools it started with continues without it.

    A name that is neither in the registry nor shaped like a connector's tool is a mistake
    rather than an absence, and still raises.
    """
    from .spec_v2 import CONNECTOR_TOOL

    connected = context.connectors.tools() if context.connectors is not None else {}
    unresolved = set(names) - set(TOOL_REGISTRY) - set(connected)
    missing = sorted(name for name in unresolved if not CONNECTOR_TOOL.match(name))
    if missing:
        raise ValueError(f"unknown tool(s): {', '.join(missing)}")
    if unresolved and context.connectors is not None:
        _check_connectors(context.connectors, {name.split("__", 1)[0] for name in unresolved})
    elif unresolved:
        raise ValueError(
            "Required connector tools are unavailable: "
            + ", ".join(sorted(unresolved))
            + ". Reconnect in Settings or update the agent's tools before starting this task."
        )
    from .connectors import _Capped
    from .execution_policy import ResultStore

    for name in names:
        if isinstance(connected.get(name), _Capped):
            connected[name] = connected[name].model_copy(
                update={"store": ResultStore(context.tool_results_root)}
            )
    return [
        TOOL_REGISTRY[name].build(context) if name in TOOL_REGISTRY else connected[name]
        for name in names
        if name in TOOL_REGISTRY or name in connected
    ]
