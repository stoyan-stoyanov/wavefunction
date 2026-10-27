"""Real stdio/HTTP MCP calls and synthetic OAuth/credential lifecycle checks."""

from __future__ import annotations

import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from wavefunction.connectors import ConnectorRegistry, McpServer, connection_config
from wavefunction.secrets import MemorySecretStore

SERVER = str(Path(__file__).parent / "fixtures" / "weather_mcp_server.py")


def weather_server(name: str = "weather") -> McpServer:
    return McpServer(name=name, transport="stdio", command=sys.executable, args=[SERVER])


@pytest.fixture
def connectors(stack) -> ConnectorRegistry:
    return ConnectorRegistry(stack.repository, MemorySecretStore())


async def test_a_connected_server_offers_its_tools(connectors, tmp_path, monkeypatch) -> None:
    server = weather_server()
    connectors.save([server])

    found = await connectors.refresh(server)

    assert found.connected, found.error
    assert sorted(tool.name for tool in found.tools) == ["forecast", "warnings"]
    # Namespaced, because two servers may both offer `search` and a spec saying `search` would
    # otherwise mean whichever loaded first.
    assert sorted(connectors.tools()) == ["weather__forecast", "weather__warnings"]
    assert {entry["name"] for entry in connectors.catalogue()} == {"weather__forecast", "weather__warnings"}
    assert connectors.tools()["weather__forecast"].metadata["readOnlyHint"] is True
    assert "readOnlyHint" not in (connectors.tools()["weather__warnings"].metadata or {})
    captured = connectors.tools()["weather__forecast"]
    await connectors.refresh(server)
    assert "18C" in str(await captured.ainvoke({"city": "Synthetic City"}))
    connectors.save([server.model_copy(update={"enabled": False})])
    with pytest.raises(ValueError, match="removed, disabled, or reconnected"):
        await captured.ainvoke({"city": "Synthetic City"})
    # Named by a command, as the form suggests (`npx`), it is found on the user's own PATH even
    # when the app was opened from Finder and inherited only the system folders.
    tools = tmp_path / "bin"
    tools.mkdir()
    launcher = tools / "weather-mcp"
    launcher.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{SERVER}' \"$@\"\n")
    launcher.chmod(0o755)
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    monkeypatch.setattr("wavefunction.sandbox.login_path", lambda: (str(tools),))
    named = McpServer(name="named", transport="stdio", command="weather-mcp")
    connectors.save([named])
    found = await connectors.refresh(named)
    assert found.connected, found.error


async def test_a_task_continues_without_a_tool_its_connected_server_dropped(connectors, tmp_path) -> None:
    """A task keeps the tool names it started with. One the server has stopped offering since
    (Notion renamed one) stopped every paused task from resuming; only a connector that is off,
    removed or not connected still stops it, saying what to do."""
    from wavefunction.toolkit import ToolContext, build_tools

    server = weather_server()
    connectors.save([server])
    await connectors.refresh(server)
    files = SimpleNamespace(project_dir=lambda agent_id: tmp_path / agent_id)
    context = ToolContext("agent-1", None, files, None, connectors=connectors, coding_workspace=None)  # type: ignore[arg-type]
    started_with = ["weather__forecast", "weather__retired_tool"]

    assert [tool.name for tool in build_tools(started_with, context)] == ["weather__forecast"]
    connectors.save([server.model_copy(update={"enabled": False})])
    with pytest.raises(ValueError, match=r"^weather is turned off\. Turn it back on"):
        build_tools(started_with, context)
    connectors.save([])
    with pytest.raises(ValueError, match=r"^weather was removed\."):
        build_tools(started_with, context)
    broken = McpServer(name="weather", transport="stdio", command=sys.executable, args=["-c", "pass"])
    connectors.save([broken])
    with pytest.raises(ValueError, match=r"^weather is still connecting\."):
        build_tools(started_with, context)
    assert not (await connectors.refresh(broken)).connected
    with pytest.raises(ValueError, match=r"^weather isn't connected\. Sign in again"):
        build_tools(started_with, context)


def test_header_values_can_name_a_secret_instead_of_carrying_one(connectors) -> None:
    """A key in app settings is a key in a file anyone can read."""
    from wavefunction.connectors import connection_config

    secrets = MemorySecretStore()
    secrets.set("linear_key", "sk-live-abc")
    server = McpServer(
        name="linear",
        transport="http",
        url="https://mcp.example/x",
        headers={"Authorization": "$LINEAR_KEY", "X-Plain": "fine"},
    )

    config = connection_config(server, secrets)
    assert config["headers"] | {"Authorization": "sk-live-abc", "X-Plain": "fine"} == config["headers"]
    assert "sk-live-abc" not in server.model_dump_json()


@pytest.fixture
def http_server(request):
    """A real MCP server over streamable HTTP, on a port nothing else is using."""
    import socket
    import subprocess
    import time
    import urllib.error
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    process = subprocess.Popen(  # noqa: S603
        [sys.executable, str(Path(__file__).parent / "fixtures" / "tracker_mcp_server.py")],
        env={
            **os.environ,
            "TRACKER_PORT": str(port),
            "TRACKER_TRANSPORT": getattr(request, "param", "streamable-http"),
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    path = "sse" if getattr(request, "param", "") == "sse" else "mcp"
    url = f"http://127.0.0.1:{port}/{path}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"MCP fixture exited before becoming ready (exit {process.returncode})")
            try:
                with urllib.request.urlopen(url, timeout=1):
                    break
            except urllib.error.HTTPError as error:
                # HTTP rejects a bare GET; SSE answers successfully. Both are ready.
                error.close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("MCP fixture did not become ready within 30 seconds")
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


async def test_a_url_connector_talks_to_a_real_server(connectors, http_server) -> None:
    """The other transport, and the one that survives a deployment."""
    server = McpServer(name="tracker", transport="http", url=http_server)
    connectors.save([server])

    found = await connectors.refresh(server)

    assert found.connected, found.error
    assert [tool.name for tool in found.tools] == ["create_issue"]
    assert sorted(connectors.tools()) == ["tracker__create_issue"]
    assert "Created: Ship it" in str(await found.tools[0].ainvoke({"title": "Ship it"}))


@pytest.fixture(
    params=[pytest.param(False, id="local"), pytest.param(True, id="live", marks=pytest.mark.live)]
)
def oauth_endpoint(request):
    """Run the same checks locally by default and against AgentCouch on explicit request."""
    if request.param:
        yield SimpleNamespace(url="https://mcp.agentcouch.dev", live=True)
        return

    registrations: list[dict] = []
    paths: list[str] = []
    agents: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self, status, data, headers=None):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - http.server interface
            path = urlparse(self.path).path
            paths.append(path)
            if path == "/mcp":
                agents.append(self.headers.get("User-Agent", ""))
                self.respond(
                    401,
                    {"error": "unauthorized"},
                    {
                        "WWW-Authenticate": f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
                    },
                )
            elif path == "/.well-known/oauth-protected-resource":
                self.respond(200, {"resource": f"{base}/mcp", "authorization_servers": [base]})
            elif path.startswith("/.well-known/oauth-authorization-server"):
                self.respond(
                    200,
                    {
                        "issuer": base,
                        "authorization_endpoint": f"{base}/oauth/authorize",
                        "token_endpoint": f"{base}/token",
                        "registration_endpoint": f"{base}/register",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["none"],
                    },
                )
            else:
                self.respond(404, {"error": "unknown endpoint"})

        def do_POST(self):  # noqa: N802 - http.server interface
            if self.path == "/register":
                paths.append(self.path)
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                registrations.append(data)
                # Like Supabase: a secret, though a public client was asked for.
                self.respond(201, {**data, "client_id": "fixture-client", "client_secret": "fixture-secret"})
            else:
                self.do_GET()

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{httpd.server_port}"
    thread = Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            url=f"{base}/mcp", live=False, registrations=registrations, paths=paths, agents=agents
        )
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


async def test_signing_in_registers_a_client_and_opens_a_browser(
    stack, tmp_path, oauth_endpoint, monkeypatch
) -> None:
    """The OAuth flow against a real server, as far as a machine can take it.

    Everything up to the human click is real: discovery from the 401, dynamic client
    registration, and an authorize URL for the server's own authorization server. Approving is
    the part nobody here can do.
    """
    import wavefunction.connector_oauth as oauth
    from wavefunction.connector_oauth import client_key, token_key
    from wavefunction.secrets import EnvFileSecretStore

    secrets = EnvFileSecretStore(tmp_path / ".wavefunction.env")
    registry = ConnectorRegistry(stack.repository, secrets)
    server = McpServer(name="agentcouch", transport="http", url=oauth_endpoint.url, auth="oauth")
    registry.save([server])

    opened: dict[str, str] = {}
    monkeypatch.setattr(oauth.webbrowser, "open", lambda url: opened.setdefault("url", url))
    monkeypatch.setattr(oauth, "CALLBACK_TIMEOUT", 5 if oauth_endpoint.live else 0.05)
    result = await registry.sign_in(server)

    assert "oauth/authorize" in opened.get("url", ""), "no browser round trip was started"
    assert "response_type=code" in opened["url"]
    # It registered itself with the authorization server, and that survives for the next attempt.
    assert secrets.get(client_key("agentcouch"))
    if not oauth_endpoint.live:
        (registration,) = oauth_endpoint.registrations
        params = parse_qs(urlparse(opened["url"]).query)
        assert registration["client_name"] == "Wavefunction"
        # A public client, as a desktop app is, and named so firewalls that refuse httpx let it in.
        assert registration["token_endpoint_auth_method"] == "none"
        assert oauth_endpoint.agents and all(
            agent.startswith("Wavefunction/") for agent in oauth_endpoint.agents
        )
        # The secret the server issued anyway is the one its token endpoint will expect.
        issued = secrets.get(client_key("agentcouch"))
        assert issued is not None
        assert json.loads(issued)["token_endpoint_auth_method"] == "client_secret_post"
        assert registration["redirect_uris"] == params["redirect_uri"]
        assert params["client_id"] == ["fixture-client"]
        assert params["code_challenge_method"] == ["S256"]
        assert "/.well-known/oauth-protected-resource" in oauth_endpoint.paths
    # Nobody approved, so there are no tokens and the connector says why.
    assert not result.connected
    assert not secrets.get(token_key("agentcouch"))

    registry.sign_out(server)
    assert not secrets.get(client_key("agentcouch"))


async def test_a_background_refresh_never_opens_a_browser(
    stack, tmp_path, oauth_endpoint, monkeypatch
) -> None:
    """Signing in is something the user starts.

    A refresh runs on startup and whenever the page is opened. If that could open a browser, an
    expired token would throw one at whoever happened to be nearby.
    """
    import wavefunction.connector_oauth as oauth
    from wavefunction.secrets import EnvFileSecretStore

    registry = ConnectorRegistry(stack.repository, EnvFileSecretStore(tmp_path / ".wavefunction.env"))
    server = McpServer(name="agentcouch", transport="http", url=oauth_endpoint.url, auth="oauth")
    registry.save([server])

    opened: list[str] = []
    monkeypatch.setattr(oauth.webbrowser, "open", opened.append)
    found = await registry.refresh(server)

    assert opened == [], "a background refresh opened a browser"
    assert not found.connected
    assert "sign in" in found.error.lower()


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The app with its own data directory, so these tests never touch real settings."""
    from fastapi.testclient import TestClient

    from wavefunction import main

    isolated = main.Services(
        main.Settings(data_dir=tmp_path, desktop_token=None, development_auth_bypass=True)
    )
    monkeypatch.setattr(main, "services", isolated)
    with TestClient(main.app) as client:
        yield client


def test_adding_a_connector_connects_and_lists_its_tools(api):
    """Adding a stdio server, end to end, against the fixture server."""
    response = api.post(
        "/api/connectors", json={"name": "weather", "command": sys.executable, "args": [SERVER]}
    )
    assert response.status_code == 201, response.text
    added = response.json()
    assert added["connected"] is True
    assert "forecast" in {tool["name"] for tool in added["tools"]}
    assert [row["name"] for row in api.get("/api/connectors").json()] == ["weather"]


def test_editing_a_connector_saves_it_and_tries_it_again(api):
    """A detail panel is only useful if what it saves is also re-tested."""
    api.post(
        "/api/connectors", json={"name": "weather", "transport": "stdio", "command": "nope-not-a-command"}
    )
    failed = api.get("/api/connectors").json()[0]
    assert failed["connected"] is False and failed["error"]
    from wavefunction import main

    assert main.services.connectors.tools() == {}
    assert main.services.connectors.catalogue() == []

    fixed = api.put(
        "/api/connectors/weather",
        json={"name": "weather", "transport": "stdio", "command": sys.executable, "args": [SERVER]},
    )
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["connected"] is True
    assert api.get("/api/connectors").json()[0]["command"] == sys.executable

    # A key the settings name takes effect when it is saved or cleared, not at the next restart.
    import asyncio

    def forecast():
        return str(
            asyncio.run(main.services.connectors.tools()["weather__forecast"].ainvoke({"city": "Oslo"}))
        )

    api.put(
        "/api/connectors/weather",
        json={
            "name": "weather",
            "transport": "stdio",
            "command": sys.executable,
            "args": [SERVER],
            "env": {"WEATHER_UNITS": "$WEATHER_UNITS"},
        },
    )
    assert "18C" in forecast()
    held = main.services.connectors.tools()["weather__forecast"]  # As a running task holds it.
    assert api.put("/api/connectors/secrets/WEATHER_UNITS", json={"value": "F"}).status_code == 204
    assert "18F" in forecast()
    with pytest.raises(ValueError, match="removed, disabled, or reconnected"):
        asyncio.run(held.ainvoke({"city": "Oslo"}))
    assert api.delete("/api/connectors/secrets/WEATHER_UNITS").status_code == 204
    assert "18C" in forecast()


# These use the real EnvFileSecretStore on purpose. MemorySecretStore takes any key, so it hid a
# crash that only the real store — which refuses keys outside its allowlist — could produce.


def real_store(tmp_path):
    from wavefunction.secrets import EnvFileSecretStore

    return EnvFileSecretStore(tmp_path / ".wavefunction.env")


def test_a_saved_token_reaches_the_server_without_sitting_in_settings(tmp_path, monkeypatch):
    from wavefunction.connectors import secret_key

    store = real_store(tmp_path)
    store.set(secret_key("GITHUB_TOKEN"), "ghp_real")
    server = McpServer(name="github", command="npx", env={"GITHUB_PERSONAL_ACCESS_TOKEN": "$GITHUB_TOKEN"})

    config = connection_config(server, store)

    assert config["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_real"
    assert "ghp_real" not in server.model_dump_json()
    # The connector gets the user's environment, not the app's own token.
    monkeypatch.setenv("WAVEFUNCTION_DESKTOP_TOKEN", "app-token")
    assert "WAVEFUNCTION_DESKTOP_TOKEN" not in connection_config(server, store)["env"]


# ---- working out what a server wants from just a URL ----------------------------------------


# ---- the redirect URI an OAuth server checks against ----------------------------------------


async def test_a_sign_in_that_cannot_reuse_its_port_registers_again(connectors, monkeypatch):
    """Dropping the stored client is what makes the next sign-in able to succeed at all."""
    from wavefunction.connector_oauth import CallbackServer, client_key, port_key

    server = McpServer(name="agentcouch", transport="http", url="https://mcp.example/mcp")
    connectors.save([server])
    connectors.secrets.set(client_key("agentcouch"), '{"client_id": "old"}')
    # A port from a previous run that this machine will not hand back.
    holder = CallbackServer()
    holder.start()
    connectors.secrets.set(port_key("agentcouch"), str(holder.port))
    try:
        await connectors.sign_in(server)
    finally:
        holder.stop()

    assert connectors.secrets.get(client_key("agentcouch")) is None, (
        "the stale registration survived, so the next sign-in would send a refused redirect_uri"
    )
    assert connectors.secrets.get(port_key("agentcouch")) != str(holder.port)


async def test_a_sign_in_that_keeps_its_port_keeps_its_registration(connectors):
    """The other half: re-registering every time would be just as broken."""
    from wavefunction.connector_oauth import client_key, port_key

    server = McpServer(name="notion", transport="http", url="https://mcp.example/mcp")
    connectors.save([server])
    connectors.secrets.set(client_key("notion"), '{"client_id": "keep-me"}')
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    connectors.secrets.set(port_key("notion"), str(free))

    await connectors.sign_in(server)

    assert connectors.secrets.get(client_key("notion")) == '{"client_id": "keep-me"}'
    assert connectors.secrets.get(port_key("notion")) == str(free)
