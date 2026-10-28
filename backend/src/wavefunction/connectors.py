"""MCP servers the user has connected, and the tools they bring.

The built-in registry in :mod:`wavefunction.toolkit` is closed on purpose: a task gets only
capabilities this app already understands. A connector does not open that up so much as move
the decision. *You* add a server; from then on its tools are part of what a task may use. The
agent still cannot invent a capability, it can only use what you have allowed — the same rule
grants follow for folders.

Tools are namespaced `<server>__<tool>` because two servers may both offer `search`, and a spec
that says `search` would then mean whichever happened to load first.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, model_validator

from . import __version__
from .execution_policy import excerpt_text
from .secrets import USER_SECRET_PREFIX

SETTING_KEY = "mcp_servers"
# Some providers' firewalls refuse httpx's default User-Agent outright (Algolia answers
# `python-httpx` with 403), so requests to a server name the app instead.
USER_AGENT = f"Wavefunction/{__version__}"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


class McpServer(BaseModel):
    """One connected MCP server.

    `stdio` runs a command on this machine; `http` talks to a URL. The difference matters beyond
    transport: a command can only ever run where this app runs, so an agent using its tools
    cannot be deployed anywhere else. `reachable_when_deployed` is what says so.
    """

    name: str = Field(min_length=1, max_length=40)
    # `sse` is the older remote transport and plenty of servers still speak only it, so a URL
    # connector that will not talk over streamable HTTP is worth a second try rather than a
    # dead end.
    transport: Literal["stdio", "http", "sse"] = "stdio"
    # stdio
    command: str = ""
    args: list[str] = Field(default_factory=list)
    # How the reference servers take credentials: `GITHUB_PERSONAL_ACCESS_TOKEN` and the like go
    # in the process environment, not on the command line, where they would show up in `ps`.
    env: dict[str, str] = Field(default_factory=dict)
    # Some servers read the directory they are started in. Empty means this app's own.
    cwd: str = ""
    # http / sse
    url: str = ""
    # Header values may name a secret rather than carry one: `{"Authorization": "$LINEAR_KEY"}`
    # is resolved from the secret store at connect time, so a key never lands in settings.
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    # Many hosted servers take no key at all: they answer with a 401 that names an authorization
    # server and expect the client to register itself and send the user to a browser.
    auth: Literal["none", "oauth"] = "none"

    @model_validator(mode="after")
    def validate_transport(self) -> McpServer:
        # The name becomes a tool prefix — `agentcouch__search` — so it has to be lowercase and
        # free of spaces. Someone typing "Agentcouch" means the connector to be called that, so
        # this makes it fit rather than refusing it. Only what cannot be repaired is rejected.
        self.name = self.name.strip().lower().replace(" ", "-")
        if not NAME_PATTERN.match(self.name):
            raise ValueError("a connector name can use letters, digits, dashes and underscores")
        if self.transport == "stdio" and not self.command.strip():
            raise ValueError("a stdio connector needs a command to run")
        if self.remote and not self.url.strip():
            raise ValueError("a remote connector needs a url")
        if self.remote and not self.url.startswith(("http://", "https://")):
            raise ValueError("a connector url has to be http or https")
        return self

    @property
    def remote(self) -> bool:
        return self.transport in {"http", "sse"}


@dataclass
class ConnectorTools:
    """What one server offered, the last time we asked."""

    server: str
    tools: list[BaseTool] = field(default_factory=list)
    error: str = ""
    needs_auth: bool = False

    @property
    def connected(self) -> bool:
        return not self.error


# The built-in tools all cap their own output; a connector's tool is someone else's code, and
# one verbose response would otherwise land whole in the context window and in the run trace.
MAX_CONNECTOR_RESULT = 40_000


def _truncated(text: str, original_size: int) -> str:
    return (
        excerpt_text(text, MAX_CONNECTOR_RESULT)
        + f"\n\n[truncated: the tool returned {original_size} characters; ask it something "
        "narrower to see the rest]"
    )


def _cap(result: Any, store: Any = None) -> Any:
    if isinstance(result, str):
        if len(result) > MAX_CONNECTOR_RESULT and store is not None:
            key = store.put(result)
            return (
                excerpt_text(result, MAX_CONNECTOR_RESULT)
                + f"\n[truncated preview. Complete output: read_tool_output(output_id='{key}').]"
            )
        return _truncated(result, len(result)) if len(result) > MAX_CONNECTOR_RESULT else result
    # Apply one text budget to the whole result; many small rows can be huge together.
    # Image and resource references retain their content-block types.
    if isinstance(result, list):
        media_types = {"image", "image_url", "resource", "resource_link", "file", "audio"}
        protected = [item for item in result if isinstance(item, dict) and item.get("type") in media_types]
        textual = [
            item for item in result if not (isinstance(item, dict) and item.get("type") in media_types)
        ]
        serialized = json.dumps(textual, default=str)
        if len(serialized) > MAX_CONNECTOR_RESULT:
            preview = _cap(serialized, store)
            if protected or any(isinstance(item, dict) and item.get("type") == "text" for item in textual):
                return [{"type": "text", "text": preview}, *protected]
            return preview
        return [_cap(item, store) for item in result]
    if isinstance(result, dict):
        if result.get("type") in {"image", "image_url", "resource", "resource_link", "file", "audio"}:
            return result
        if result.get("type") == "text":
            return {**result, "text": _cap(result.get("text", ""), store)}
        serialized = json.dumps(result, default=str)
        if len(serialized) > MAX_CONNECTOR_RESULT:
            return _cap(serialized, store)
        return result
    # A ToolCall input comes back as a ToolMessage; the text lives in its content.
    content = getattr(result, "content", None)
    if content is not None:
        capped = _cap(content, store)
        if capped is not content:
            return result.model_copy(update={"content": capped})
    return result


class _Capped(BaseTool):
    """A connector tool with its output bounded. Everything else passes straight through.

    Wraps `invoke`/`ainvoke` rather than `_run`/`_arun` because the inner tool's private
    signatures vary by kind; the public surface is the stable one.
    """

    inner: BaseTool
    server: str = ""
    store: Any = Field(default=None, exclude=True)
    available: Callable[[], bool] = Field(default=lambda: True, exclude=True)
    auth_failed: Callable[[BaseException], None] | None = Field(default=None, exclude=True)

    def _check_available(self) -> None:
        if not self.available():
            # A ValueError: the call was never attempted.
            raise ValueError(
                "This connector was removed, disabled, or reconnected. Start a new run to use its current tools."
            )

    def _run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - invoke overridden
        raise NotImplementedError

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        self._check_available()
        try:
            return _cap(self.inner.invoke(input, config, **kwargs), self.store)
        except Exception as error:
            if needs_sign_in(error) and self.auth_failed:
                self.auth_failed(error)
            raise

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        self._check_available()
        try:
            return _cap(await self.inner.ainvoke(input, config, **kwargs), self.store)
        except Exception as error:
            if needs_sign_in(error) and self.auth_failed:
                self.auth_failed(error)
            raise


def tool_name(server: str, tool: str) -> str:
    """`linear__create_issue`. Two servers may both offer `search`."""
    original = f"{server}__{tool}"
    if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", original):
        return original
    # Preserve the original name inside the MCP adapter closure; only its exposed
    # model name changes. Hash the full identity so truncation cannot merge tools.
    digest = hashlib.sha256(original.encode()).hexdigest()[:16]
    prefix = server
    bare = re.sub(r"[^a-z0-9_]", "_", tool.lower())[: 64 - len(prefix) - 19]
    return f"{prefix}__{bare}_{digest}"


class SignInRequired(RuntimeError):
    pass


def needs_sign_in(error: BaseException) -> bool:
    nested = getattr(error, "exceptions", ())
    return (
        isinstance(error, SignInRequired)
        or getattr(getattr(error, "response", None), "status_code", None) == 401
        or (type(error).__name__ == "OAuthTokenError" and "invalid_grant" in str(error))
        or any(needs_sign_in(child) for child in nested)
    )


def describe_failure(error: BaseException) -> str:
    """What actually went wrong, out of the group it arrived in.

    The MCP client runs its transport in an anyio task group, so a plain 401 reaches us as
    "unhandled errors in a TaskGroup (1 sub-exception)" — true, and no help to anyone deciding
    what to fix. This digs out the exception that actually failed and says what it was.
    """
    causes: list[str] = []

    def walk(err: BaseException) -> None:
        nested = getattr(err, "exceptions", None)
        if nested:
            for sub in nested:
                walk(sub)
            return
        text = str(err).strip().split("\n")[0]
        causes.append(f"{type(err).__name__}: {text}" if text else type(err).__name__)

    walk(error)
    unique = list(dict.fromkeys(causes))
    detail = " · ".join(unique) if unique else type(error).__name__
    if needs_sign_in(error):
        detail += ". This server needs you to sign in."
    return detail


def resolve(values: dict[str, str], secrets: Any) -> dict[str, str]:
    """Values as they are, except `$NAME`, which is read from the secret store, and `$$NAME`,
    which is a literal `$NAME`.

    Used for both an http connector's headers and a stdio connector's environment, so a token
    can be kept out of settings whichever way a server wants to be given it.
    """
    resolved = {}
    for key, value in values.items():
        # Substituted wherever it appears, not only when it is the whole value: the common shape
        # is `Bearer $TOKEN`, and a header that had to be all-or-nothing would force the word
        # "Bearer" into the stored secret.
        resolved[key] = SECRET_REFERENCE.sub(
            lambda match: (
                match.group(0)[1:] if match.group(1) else secret_value(secrets, match.group(2)) or ""
            ),
            value,
        )
    return resolved


# `$NAME` in a header or an environment value. Upper-case and underscores only, so a `$TOKEN`
# followed by ordinary prose does not swallow the prose. `$$NAME` escapes it, for a password
# or URL that has a `$` before a capital.
SECRET_REFERENCE = re.compile(r"\$(\$?)([A-Z][A-Z0-9_]*)")


def secret_key(name: str) -> str:
    """Where `$NAME` is kept in the secret store."""
    return USER_SECRET_PREFIX + re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def secret_value(secrets: Any, name: str) -> str | None:
    """Read `$NAME`, whichever place it was saved.

    A name the store refuses is a name nobody ever saved, so it reads as missing rather than
    raising: a typo in one header should leave that connector unable to connect, not take the
    whole page down with a 500.
    """
    for candidate in (secret_key(name), name.lower()):
        try:
            if found := secrets.get(candidate):
                return found
        except (ValueError, KeyError):
            continue
    return None


def secret_names(server: McpServer) -> list[str]:
    """Every `$NAME` this connector's settings refer to, in the order they appear."""
    names: list[str] = []
    for value in (*server.headers.values(), *server.env.values()):
        for escaped, name in SECRET_REFERENCE.findall(value):
            if not escaped and name not in names:
                names.append(name)
    return names


def connection_config(server: McpServer, secrets: Any) -> dict[str, Any]:
    """The shape `MultiServerMCPClient` wants, with secrets resolved."""
    if server.transport == "stdio":
        bundled = server.args[:2] in (
            ["-m", "wavefunction.mail_mcp"],
            ["-m", "wavefunction.n8n_mcp"],
            ["-m", "wavefunction.microsoft_mcp"],
        )
        config: dict[str, Any] = {
            "transport": "stdio",
            # Managed Python lives in a versioned runtime directory. Keep the
            # bundled mail adapter on the current runtime after an app update.
            "command": sys.executable if bundled else server.command,
            "args": list(server.args),
        }
        if bundled and getattr(sys, "frozen", False):
            config["args"] = [
                "--python",
                "-c",
                "import runpy, sys; runpy.run_module(sys.argv.pop(1), run_name='__main__')",
                *server.args[1:],
            ]
        if not bundled:
            # Found on the user's own PATH, as in Terminal: opened from Finder or the Dock, the
            # app inherits only /usr/bin:/bin:/usr/sbin:/sbin, and `npx` is the most common
            # command there is. Inherited, not replaced; a PATH the user set still wins.
            from .sandbox import command_path

            # Without the app's own variables, as for commands run outside the sandbox: they hold
            # the token that unlocks its API.
            inherited = {
                name: value
                for name, value in os.environ.items()
                if not name.startswith(("_PYI_", "_MEI", "WAVEFUNCTION_"))
            }
            config["env"] = {**inherited, "PATH": ":".join(command_path()), **resolve(server.env, secrets)}
        elif server.env:
            config["env"] = {**os.environ, **resolve(server.env, secrets)}
        if server.cwd:
            config["cwd"] = server.cwd
        return config
    return {
        "transport": "streamable_http" if server.transport == "http" else "sse",
        "url": server.url,
        "headers": {"User-Agent": USER_AGENT, **resolve(server.headers, secrets)},
    }


class ConnectorRegistry:
    """Connects to the servers and holds what they offered.

    Tools are fetched once when a server is connected and cached, not fetched per build: loading
    them means a round trip to a process or a URL, and `build_agent` is called on every run.
    A server that has never answered contributes nothing rather than failing the build — an
    agent whose connector is down should say so, not refuse to start.
    """

    def __init__(self, repository: Any, secrets: Any):
        self.repository = repository
        self.secrets = secrets
        self._cache: dict[str, ConnectorTools] = {}
        self.checking: set[str] = set()
        # Each connector's latest sign-in, so an earlier one finishing late leaves it alone.
        self._sign_ins: dict[str, int] = {}
        # The refresh running for a connector's current settings, which a second caller joins.
        self._refreshing: dict[str, asyncio.Task] = {}

    def servers(self) -> list[McpServer]:
        stored = self.repository.get_setting(SETTING_KEY, [])
        if not isinstance(stored, list):
            return []
        servers = []
        for row in stored:
            try:
                servers.append(McpServer.model_validate(row))
            except Exception:  # noqa: BLE001 - one bad row is not a broken list
                continue
        return servers

    def save(self, servers: list[McpServer]) -> None:
        previous = {server.name: server for server in self.servers()}
        enabled = {server.name: server for server in servers if server.enabled}
        self.repository.set_setting(SETTING_KEY, [server.model_dump(mode="json") for server in servers])
        for name in list(self._cache):
            if name not in enabled or previous.get(name) != enabled[name]:
                self._cache.pop(name, None)

    async def refresh(self, server: McpServer, *, replace: bool = False) -> ConnectorTools:
        """Ask a server what it offers, joining a refresh of the same settings already running:
        a second one would start another server process and race it for the cache."""
        key = self._identity(server)
        task = self._refreshing.get(key)
        if task is None or task.done():
            task = self._refreshing[key] = asyncio.ensure_future(self._refresh(server, replace=replace))
            task.add_done_callback(
                lambda done: self._refreshing.pop(key, None) if self._refreshing.get(key) is done else None
            )
        return await asyncio.shield(task)

    def _identity(self, server: McpServer) -> str:
        """The settings and the secrets they name: a refresh started before a token was replaced
        is not the one a retry with the new token should get, nor the answer to keep."""
        return server.model_dump_json() + json.dumps(
            [resolve(server.env, self.secrets), resolve(server.headers, self.secrets)],
            sort_keys=True,
            default=str,
        )

    async def _refresh(self, server: McpServer, *, replace: bool = False) -> ConnectorTools:
        """Ask a server what it offers, and remember the answer.

        An OAuth server is asked with the tokens already held. It is never asked in a way that
        would open a browser — signing in is a thing the user starts, not something a background
        refresh does behind them.
        """
        from langchain_mcp_adapters.client import MultiServerMCPClient

        if not server.enabled:
            self._cache.pop(server.name, None)
            return ConnectorTools(server=server.name, error="Connector is disabled")
        identity = self._identity(server)
        self.checking.add(server.name)
        try:
            if server.env.get("WAVEFUNCTION_LOCAL_INTEGRATION") == "microsoft":
                from .microsoft_graph import verify_server

                await verify_server(server)
            from .sandbox import login_path

            await asyncio.to_thread(login_path)  # Asks the login shell once; not on the event loop.
            config = connection_config(server, self.secrets)
            if server.auth == "oauth":
                config["auth"] = self.stored_oauth(server)
            client = MultiServerMCPClient({server.name: config})
            tools = await asyncio.wait_for(client.get_tools(server_name=server.name), timeout=30)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - includes ExceptionGroup from connector tasks
            found = ConnectorTools(
                server=server.name, error=describe_failure(error), needs_auth=needs_sign_in(error)
            )
        else:
            found = ConnectorTools(server=server.name, tools=list(tools))
        finally:
            self.checking.discard(server.name)
        # Kept only if its settings and keys are still the current ones: an answer for a key
        # replaced meanwhile must not overwrite the answer for the new one.
        if server in self.servers() and server.enabled and self._identity(server) == identity:
            previous = self._cache.get(server.name)

            def signatures(entry: ConnectorTools) -> list[tuple]:
                return [
                    (tool.name, tool.description, tool.get_input_schema().model_json_schema(), tool.metadata)
                    for tool in entry.tools
                ]

            if (
                not replace
                and previous
                and previous.connected
                and found.connected
                and signatures(previous) == signatures(found)
            ):
                # Background discovery must not revoke tools already held by a run.
                # Save, sign-out, and sign-in still replace/remove this entry.
                previous.tools = found.tools
                found = previous
            self._cache[server.name] = found
        return found

    async def refresh_all(self) -> dict[str, ConnectorTools]:
        enabled = {server.name for server in self.servers() if server.enabled}
        for name in set(self._cache) - enabled:
            self._cache.pop(name, None)
        semaphore = asyncio.Semaphore(4)

        async def discover(server: McpServer) -> None:
            async with semaphore:
                await self.refresh(server)

        servers = [server for server in self.servers() if server.enabled]
        self.checking.update(server.name for server in servers)
        try:
            await asyncio.gather(*(discover(server) for server in servers))
        finally:
            self.checking.difference_update(server.name for server in servers)
        return dict(self._cache)

    def stored_oauth(self, server: McpServer) -> Any:
        """An auth provider that uses tokens already held and never opens a browser.

        Its callback raises rather than waiting: reaching it means the tokens are gone, and the
        honest answer is "sign in again", not a browser opening while nobody is looking.
        """
        from .connector_oauth import SecretTokenStorage, check_registered_url, client_metadata, provider_type

        check_registered_url(server.name, self.secrets, server.url)

        async def refuse(_url: str) -> None:
            raise SignInRequired("this connector needs you to sign in again")

        async def no_callback() -> tuple[str, str | None]:
            raise RuntimeError("this connector needs you to sign in again")

        return provider_type(server.name, self.secrets)(
            server_url=server.url,
            client_metadata=client_metadata(server.name, self.secrets, "http://127.0.0.1/callback"),
            storage=SecretTokenStorage(server.name, self.secrets, server.url),
            redirect_handler=refuse,
            callback_handler=no_callback,
        )

    async def sign_in(self, server: McpServer) -> ConnectorTools:
        """Run the browser round trip, then connect with what it produced."""
        from urllib.parse import urlparse

        from langchain_mcp_adapters.client import MultiServerMCPClient

        from .connector_oauth import (
            CALLBACK_TIMEOUT,
            CallbackServer,
            client_key,
            oauth_provider,
            port_key,
            registered_setup,
        )

        # The same port as last time, so the redirect URI still matches what the stored client
        # registered with. If the machine will not give it back, that registration is dead: drop
        # it, so the SDK registers again instead of sending a URI the server refuses.
        remembered = self.secrets.get(port_key(server.name))
        setup = registered_setup(server.name, self.secrets)
        preferred = (
            urlparse(setup["redirect_uri"]).port
            if setup
            else int(remembered)
            if remembered and remembered.isdigit()
            else None
        )
        try:
            callback = CallbackServer(
                preferred,
                required=bool(setup),
                hostname=urlparse(setup["redirect_uri"]).hostname if setup else "127.0.0.1",
            )
        except ValueError as error:
            return ConnectorTools(server=server.name, error=str(error))
        if not callback.reused:
            with suppress(Exception):
                self.secrets.delete(client_key(server.name))
        self.secrets.set(port_key(server.name), str(callback.port))
        # A sign-in abandoned in the browser times out minutes later. By then a newer one may have
        # connected, and the old failure must not replace that.
        attempt = self._sign_ins[server.name] = self._sign_ins.get(server.name, 0) + 1
        self.checking.add(server.name)
        try:
            callback.start()
            from .sandbox import login_path

            await asyncio.to_thread(login_path)  # Asks the login shell once; not on the event loop.
            config = connection_config(server, self.secrets)
            config["auth"] = oauth_provider(server.url, server.name, self.secrets, callback)
            client = MultiServerMCPClient({server.name: config})
            await asyncio.wait_for(client.get_tools(server_name=server.name), timeout=CALLBACK_TIMEOUT + 30)
            # Tools call the server with the provider they were made with. Made with this
            # sign-in's, a token that later cannot be refreshed would open a browser from an
            # agent's tool call; with the stored tokens it asks the user to sign in again.
            config["auth"] = self.stored_oauth(server)
            tools = await asyncio.wait_for(
                MultiServerMCPClient({server.name: config}).get_tools(server_name=server.name), timeout=30
            )
        except asyncio.CancelledError:
            raise
        except BaseException as error:  # noqa: BLE001 - shown against the connector
            found = ConnectorTools(
                server=server.name, error=describe_failure(error), needs_auth=needs_sign_in(error)
            )
        else:
            found = ConnectorTools(server=server.name, tools=list(tools))
        finally:
            latest = self._sign_ins.get(server.name) == attempt
            if latest:
                self.checking.discard(server.name)
            callback.stop()
        if latest and server in self.servers() and server.enabled:
            self._cache[server.name] = found
        return found

    def sign_out(self, server: McpServer) -> None:
        from .connector_oauth import SecretTokenStorage

        SecretTokenStorage(server.name, self.secrets).forget()
        self._cache.pop(server.name, None)

    def cached(self, name: str) -> ConnectorTools | None:
        return self._cache.get(name)

    def tools(self) -> dict[str, BaseTool]:
        """Every connected tool, renamed to its namespaced name.

        The tool is renamed, not merely filed under the new key. What `create_agent` binds is
        `tool.name`, so a tool left with the bare name the server gave it is the one name the
        model is offered — while the spec, the approval policies and the canvas all say
        `server__tool`. The agent then cannot call its own tool, and two servers that both
        publish `search` collide, which is what the namespacing was for.

        The copy matters: the cache holds what the server actually said, and the page shows it.
        """
        found: dict[str, BaseTool] = {}
        enabled = {server.name for server in self.servers() if server.enabled}
        for entry in self._cache.values():
            if not entry.connected or entry.server not in enabled:
                continue
            for tool in entry.tools:
                namespaced = tool_name(entry.server, tool.name)
                found[namespaced] = _Capped(
                    server=entry.server,
                    auth_failed=lambda error, entry=entry: self._auth_failed(entry, error),
                    available=lambda entry=entry: (
                        self._cache.get(entry.server) is entry
                        and any(s.name == entry.server and s.enabled for s in self.servers())
                    ),
                    inner=tool.model_copy(update={"name": namespaced}),
                    name=namespaced,
                    description=tool.description or "",
                    args_schema=tool.args_schema,
                    metadata=tool.metadata,
                )
        return found

    def _auth_failed(self, entry: ConnectorTools, error: BaseException) -> None:
        # An obsolete in-flight call must not invalidate a newly authenticated connection.
        if self._cache.get(entry.server) is entry:
            entry.error = describe_failure(error)
            entry.needs_auth = True

    def catalogue(self) -> list[dict[str, Any]]:
        """Connected tools, each with the connector that offers it."""
        return [
            {"name": tool_name(server.name, tool.name), "connector": server.name}
            for server in self.servers()
            if (entry := self._cache.get(server.name)) is not None and entry.connected
            for tool in entry.tools
        ]


# ---- working out what a server wants ------------------------------------------------------
#
# Someone adding a connector knows a URL. They do not know whether it speaks streamable HTTP or
# SSE, whether the path is /mcp or /sse, or whether it wants a bearer token or a full OAuth round
# trip — and none of that is theirs to work out, because the server will say if it is asked.

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "Wavefunction", "version": "1.0"},
    },
}
PROBE_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
# Where servers put their endpoint. Tried against the domain someone typed, and also against the
# root of a full URL they typed — a server that has moved from /sse to /mcp is common enough
# (Linear and DeepWiki both did) that the address in the docs someone is reading may be dead.
ENDPOINT_GUESSES = ("/mcp", "/sse", "/api/mcp")


def candidate_urls(url: str) -> list[str]:
    """The URLs worth trying for what someone typed, most likely first."""
    cleaned = url.strip()
    if not cleaned:
        return []
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"https://{cleaned}"
    base = cleaned.rstrip("/")
    root = base
    for suffix in ENDPOINT_GUESSES:
        if root.endswith(suffix):
            root = root[: -len(suffix)]
            break
    # What they typed, first and on its own terms. Plenty of servers answer at a bare host —
    # mcp.agentcouch.com has no path — and appending /mcp to a working address to "find" it
    # somewhere else is how a connector ends up pointing at a URL nobody chose.
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(root)
    sibling: list[str] = []
    if parts.hostname and not parts.hostname.startswith("mcp."):
        prefixed = urlunsplit(parts._replace(netloc=f"mcp.{parts.netloc}"))
        sibling = [prefixed, *(prefixed + suffix for suffix in ENDPOINT_GUESSES)]
    # Guesses are a fallback for an address that did not answer, never a correction to one that
    # did: the typed URL and its own paths are exhausted before another host is tried at all.
    out = [base, *(root + suffix for suffix in ENDPOINT_GUESSES), root, *sibling]
    seen: set[str] = set()
    return [c for c in out if c and not (c in seen or seen.add(c))]


def speaks_mcp(response: Any) -> bool:
    """Whether an answer is an MCP endpoint rather than merely an answer.

    A domain root usually returns 200 and a marketing page. Taking that as a working connector is
    worse than finding nothing, because it saves a URL that will never produce a tool.
    """
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type:
        return True
    if "application/json" not in content_type:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and ("jsonrpc" in body or "result" in body or "error" in body)


def suggested_name(url: str) -> str:
    """A connector name from the host, so nobody has to invent one.

    `mcp.agentcouch.dev` becomes `agentcouch`: the leading `mcp`/`api` label and the public
    suffix are noise, and what is left is what the person would have called it anyway.
    """
    from urllib.parse import urlparse

    host = (urlparse(url if "//" in url else f"https://{url}").hostname or "").lower()
    labels = [label for label in host.split(".") if label not in {"www", "mcp", "api"}]
    if len(labels) > 1:
        labels = labels[:-1]  # drop the TLD
    name = labels[-1] if labels else "connector"
    return re.sub(r"[^a-z0-9_-]+", "-", name).strip("-") or "connector"


def _auth_from_challenge(challenge: str) -> dict[str, Any]:
    """Read a `WWW-Authenticate` header into what the user has to do about it.

    A challenge naming `resource_metadata` is the OAuth discovery chain — the server is telling
    us where to register and where to send the person. Anything else is a server that just wants
    a token in a header.
    """
    match = re.search(r'resource_metadata="([^"]+)"', challenge or "")
    if match:
        return {"auth": "oauth", "resource_metadata": match.group(1)}
    return {"auth": "token"}


async def probe_server(url: str) -> dict[str, Any]:
    """Ask a URL what it is and what it wants.

    Returns the transport, the URL that actually answered, and one of: `none` (it is already
    usable), `oauth` (a browser round trip), `token` (a header), or `unreachable`.
    """
    import httpx

    attempts: list[str] = []
    typed = next(iter(candidate_urls(url)), url)

    def answer(**found: Any) -> dict[str, Any]:
        # `guessed` is what lets the UI say "found it over here" instead of silently swapping
        # the address someone typed for one they did not.
        return {**found, "typed_url": typed, "guessed": found.get("url", typed) != typed}

    async with httpx.AsyncClient(
        timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        deadline = asyncio.get_running_loop().time() + 20
        for candidate in candidate_urls(url):
            if asyncio.get_running_loop().time() >= deadline:
                break
            for transport in ("http", "sse"):
                try:
                    async with (
                        asyncio.timeout_at(deadline),
                        client.stream(
                            "POST" if transport == "http" else "GET",
                            candidate,
                            **(
                                {"json": INITIALIZE, "headers": PROBE_HEADERS}
                                if transport == "http"
                                else {"headers": {"Accept": "text/event-stream"}}
                            ),
                        ) as response,
                    ):
                        # An SSE body intentionally stays open. Discovery only needs its headers.
                        if (
                            response.status_code < 300
                            and "text/event-stream" in response.headers.get("content-type", "").lower()
                        ):
                            return answer(
                                url=str(response.url),
                                transport=transport,
                                reachable=True,
                                auth="none",
                                detail="",
                            )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk[: 65536 - len(body)])
                            if len(body) >= 65536:
                                break
                        response = httpx.Response(
                            response.status_code,
                            headers=response.headers,
                            content=bytes(body),
                            request=response.request,
                        )
                except (httpx.HTTPError, TimeoutError) as error:
                    attempts.append(f"{candidate}: {type(error).__name__}")
                    break  # the host itself is not answering; another verb will not help
                # The URL after any redirect is the one to keep — a 308 from a bare domain to the
                # real endpoint is the common case, and saving the typed one would break later.
                final = str(response.url)
                if response.status_code == 401:
                    challenge = response.headers.get("www-authenticate", "")
                    return answer(
                        url=final,
                        transport=transport,
                        reachable=True,
                        detail=challenge,
                        **_auth_from_challenge(challenge),
                    )
                if response.status_code in {402, 403}:
                    return answer(
                        url=final,
                        transport=transport,
                        reachable=True,
                        auth="token",
                        detail=response.text[:200],
                    )
                if response.status_code < 300 and speaks_mcp(response):
                    return answer(url=final, transport=transport, reachable=True, auth="none", detail="")
                attempts.append(f"{candidate} [{transport}]: {response.status_code}")
    return answer(
        url=typed,
        transport="http",
        reachable=False,
        auth="unreachable",
        detail="; ".join(attempts[:4]) or "nothing answered at that address",
    )
