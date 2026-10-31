"""Signing in to an MCP server that uses OAuth.

Plenty of hosted MCP servers do not take an API key at all. They answer an unauthenticated
request with a 401 carrying `WWW-Authenticate`, which names a metadata document, which names an
authorization server — and the client is expected to register itself, send the user to a browser,
and exchange the code it gets back. The MCP SDK implements all of that; what it needs from us is
somewhere to keep the tokens and a way to run the browser round trip.

Tokens live in the secret store, beside the model keys, rather than in settings — settings are a
file the user can read and paste into a bug report.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import TCPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.auth.exceptions import OAuthTokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata, OAuthToken

# How long to wait for someone to finish signing in before giving the port back.
CALLBACK_TIMEOUT = 300


def _slug(server: str) -> str:
    """An injective encoding in a namespace distinct from every legacy key."""
    return server.encode("utf-8").hex()


def token_key(server: str) -> str:
    return f"mcp_oauth_v2_tokens_{_slug(server)}"


def client_key(server: str) -> str:
    return f"mcp_oauth_v2_client_{_slug(server)}"


def port_key(server: str) -> str:
    """Where the callback port is remembered.

    It has to be remembered. The port is part of the redirect URI, the redirect URI is what the
    client was registered with, and an authorization server checks the one against the other. A
    fresh random port on the second sign-in is a redirect URI the stored client never registered,
    which is refused — `invalid redirect_uri`, from a flow that worked the first time.
    """
    return f"mcp_oauth_v2_port_{_slug(server)}"


def setup_key(server: str) -> str:
    return f"mcp_oauth_v2_setup_{_slug(server)}"


def session_key(server: str) -> str:
    return f"mcp_oauth_v2_session_{_slug(server)}"


def registered_setup(server: str, secrets: Any) -> dict[str, str]:
    raw = secrets.get(setup_key(server))
    # Only legacy registered clients recorded their server identity. Plain names
    # cannot collide under the old dash/underscore mapping. Everything else must
    # sign in again rather than guessing which service owns an unbound token.
    if not raw and re.fullmatch(r"[a-z0-9]+", server):
        raw = secrets.get(f"mcp_oauth_setup_{server}")
        setup = json.loads(raw) if raw else {}
        if setup.get("server_url"):
            for kind, key in (
                ("tokens", token_key),
                ("client", client_key),
                ("port", port_key),
                ("session", session_key),
                ("setup", setup_key),
            ):
                legacy = f"mcp_oauth_{kind}_{server}"
                value = secrets.get(legacy)
                if value and not secrets.get(key(server)):
                    if kind == "session":
                        value = json.dumps({**json.loads(value), "server_url": setup["server_url"]})
                    elif kind in {"tokens", "client"}:
                        value = json.dumps({**json.loads(value), "_server_url": setup["server_url"]})
                    secrets.set(key(server), value)
                if value:
                    secrets.delete(legacy)
        else:
            raw = None
    return json.loads(raw) if raw else {}


def check_registered_url(server: str, secrets: Any, url: str) -> None:
    setup = registered_setup(server, secrets)
    if setup and setup.get("server_url") != url:
        raise ValueError(
            "The server URL changed. Reopen this connector in Browse connectors to configure its OAuth credentials."
        )


def client_metadata(server: str, secrets: Any, redirect_uri: str) -> OAuthClientMetadata:
    setup = registered_setup(server, secrets)
    return OAuthClientMetadata(
        client_name="Wavefunction",
        redirect_uris=[setup.get("redirect_uri", redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=setup.get("scope") or None,
        # A desktop app cannot keep a secret, so it registers as a public client using PKCE. Left
        # unset, a server assumes client_secret_basic, which servers for public clients refuse.
        token_endpoint_auth_method=setup.get("auth_method") or "none",
    )


def with_secret_method(client_info: OAuthClientInformationFull) -> OAuthClientInformationFull:
    """A server that issues a secret anyway expects it at its token endpoint.

    Supabase answers a public-client registration with a secret and no method, and the SDK would
    then leave the secret out of the token exchange.
    """
    if client_info.client_secret and client_info.token_endpoint_auth_method in (None, "none"):
        client_info.token_endpoint_auth_method = "client_secret_post"
    return client_info


class SecretTokenStorage(TokenStorage):
    """Tokens and the registered client, kept in the app's secret store."""

    def __init__(self, server: str, secrets: Any, server_url: str | None = None):
        self.server = server
        self.secrets = secrets
        self.server_url = server_url

    def _read(self, key: str) -> dict[str, Any] | None:
        raw = self.secrets.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    async def get_tokens(self) -> OAuthToken | None:
        registered_setup(self.server, self.secrets)
        stored = self._read(token_key(self.server))
        if self.server_url and (stored or {}).get("_server_url") != self.server_url:
            return None
        return OAuthToken.model_validate(stored) if stored else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        bound_url = self.server_url or (self._read(token_key(self.server)) or {}).get("_server_url")
        self.secrets.set(
            token_key(self.server), json.dumps({**tokens.model_dump(mode="json"), "_server_url": bound_url})
        )
        session = self._read(session_key(self.server)) or {}
        session["expires_at"] = time.time() + tokens.expires_in if tokens.expires_in is not None else None
        if self.server_url:
            session["server_url"] = self.server_url
        self.secrets.set(session_key(self.server), json.dumps(session))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        setup = registered_setup(self.server, self.secrets)
        if setup:
            return OAuthClientInformationFull(
                **client_metadata(self.server, self.secrets, setup["redirect_uri"]).model_dump(),
                client_id=setup["client_id"],
                client_secret=setup.get("client_secret") or None,
            )
        stored = self._read(client_key(self.server))
        if self.server_url and (stored or {}).get("_server_url") != self.server_url:
            return None
        return with_secret_method(OAuthClientInformationFull.model_validate(stored)) if stored else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        # In place: the SDK exchanges the code with this same object right after registering.
        with_secret_method(client_info)
        self.secrets.set(
            client_key(self.server),
            json.dumps({**client_info.model_dump(mode="json"), "_server_url": self.server_url}),
        )
        if self.server_url:
            session = self._read(session_key(self.server)) or {}
            self.secrets.set(session_key(self.server), json.dumps({**session, "server_url": self.server_url}))

    def forget(self) -> None:
        """Sign out. The next connection starts the flow again."""
        for key in (
            token_key(self.server),
            client_key(self.server),
            port_key(self.server),
            session_key(self.server),
        ):
            try:
                self.secrets.delete(key)
            except Exception:  # noqa: BLE001 - a key that is not there is already forgotten
                continue


class ConnectorOAuthProvider(OAuthClientProvider):
    """Keep Basic client authentication exclusively in the Authorization header.

    The pinned SDK also sends client_id in the form. Strict servers such as
    Notion reject that combination as multiple authentication methods.
    """

    @staticmethod
    def _single_client_auth(request):
        if not request.headers.get("Authorization", "").lower().startswith("basic "):
            return request
        import httpx

        fields = [
            (key, value)
            for key, value in parse_qsl(request.content.decode(), keep_blank_values=True)
            if key not in {"client_id", "client_secret"}
        ]
        headers = dict(request.headers)
        headers.pop("content-length", None)
        return httpx.Request(
            request.method,
            request.url,
            headers=headers,
            content=urlencode(fields).encode(),
            extensions=request.extensions,
        )

    async def _exchange_token_authorization_code(self, auth_code, code_verifier, *, token_data=None):
        request = await super()._exchange_token_authorization_code(
            auth_code, code_verifier, token_data=token_data
        )
        return self._single_client_auth(request)

    async def _refresh_token(self):
        return self._single_client_auth(await super()._refresh_token())

    async def _auth_flow(self, request):
        await self._adopt_stored_tokens()
        flow = super()._auth_flow(request)
        try:
            sent = await flow.__anext__()
            while True:
                sent = await flow.asend((yield sent))
        except StopAsyncIteration:
            pass
        finally:
            await flow.aclose()

    async def _adopt_stored_tokens(self) -> None:
        """Take the stored tokens when another connection to this server refreshed after these were
        read. A running task keeps its connection while new tasks get one of their own, and a server
        that rotates refresh tokens revokes the old one when the other connection uses it: refreshing
        with the one held here would fail and ask the user to sign in, though valid tokens are stored."""
        if not self._initialized or self.context.is_token_valid():
            return
        storage = self.context.storage
        stored, current = await storage.get_tokens(), self.context.current_tokens
        if stored is None or current is None or stored.refresh_token == current.refresh_token:
            return
        self.context.current_tokens = stored
        session = storage._read(session_key(storage.server)) or {}
        self.context.token_expiry_time = (
            session.get("expires_at") if session.get("server_url") == storage.server_url else None
        )

    async def _initialize(self) -> None:
        await super()._initialize()
        storage = self.context.storage
        session = storage._read(session_key(storage.server)) or {}
        if session.get("server_url") != storage.server_url:
            return
        self.context.token_expiry_time = session.get("expires_at")
        if session.get("metadata"):
            self.context.oauth_metadata = OAuthMetadata.model_validate(session["metadata"])

    async def _perform_authorization(self):
        storage = self.context.storage
        setup = registered_setup(storage.server, storage.secrets)
        if setup.get("scope"):
            self.context.client_metadata.scope = setup["scope"]
        if self.context.oauth_metadata:
            session = {
                "server_url": storage.server_url,
                "metadata": self.context.oauth_metadata.model_dump(mode="json"),
            }
            storage.secrets.set(session_key(storage.server), json.dumps(session))
        return await super()._perform_authorization()

    async def _handle_refresh_response(self, response):
        # Several servers omit a refresh token when the existing one is retained.
        import httpx

        await response.aread()
        if response.status_code == 200 and self.context.current_tokens:
            try:
                data = response.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("access_token") and "refresh_token" not in data:
                data["refresh_token"] = self.context.current_tokens.refresh_token
                response = httpx.Response(200, json=data)
        return await super()._handle_refresh_response(response)


class RegisteredOAuthProvider(ConnectorOAuthProvider):
    """Adapt registered services whose token response differs from standard OAuth."""

    async def _slack_response(self, response):
        """Slack's user OAuth endpoint returns token_type=user and HTTP-200 errors."""
        if self.context.storage.server != "slack":
            return response
        import httpx

        try:
            await response.aread()
            data = response.json()
        except ValueError:
            raise OAuthTokenError("Slack returned an invalid sign-in response. Try again.") from None
        if not isinstance(data, dict) or response.status_code != 200 or data.get("ok") is False:
            code = data.get("error", "sign_in_failed") if isinstance(data, dict) else "sign_in_failed"
            code = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,80}", code) else "sign_in_failed"
            raise OAuthTokenError(
                f"Slack sign-in failed: {code}. Check your app permissions and callback URL."
            )
        if data.get("token_type") == "user":
            data["token_type"] = "Bearer"
        return httpx.Response(200, json=data)

    async def _handle_token_response(self, response):
        return await super()._handle_token_response(await self._slack_response(response))

    async def _handle_refresh_response(self, response):
        try:
            response = await self._slack_response(response)
            return await super()._handle_refresh_response(response)
        except OAuthTokenError:
            self.context.clear_tokens()
            return False


def provider_type(server: str, secrets: Any):
    return RegisteredOAuthProvider if registered_setup(server, secrets) else ConnectorOAuthProvider


class _LoopbackHTTPServer(HTTPServer):
    def server_bind(self) -> None:
        """Skips HTTPServer's lookup of this address's name, which nothing here reads. It runs on
        the event loop, so a resolver that doesn't answer would freeze every chat until it gave up:
        35 s on GitHub's macOS 15 and 26 runners."""
        TCPServer.server_bind(self)


class CallbackServer:
    """A loopback server that catches the redirect at the end of the flow.

    The port is kept across sign-ins rather than picked fresh each time, because it is part of
    the redirect URI the client registered with. `reused` says whether the remembered port was
    actually available: when it was not, the stored registration no longer matches and the caller
    has to register again rather than send a redirect URI the server will refuse.
    """

    def __init__(
        self, preferred: int | None = None, *, required: bool = False, hostname: str = "127.0.0.1"
    ) -> None:
        if hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("OAuth callbacks must use a loopback hostname")
        self.hostname = hostname
        if required and not self._bindable(preferred):
            raise ValueError(
                f"OAuth callback port {preferred} is in use. Close the other sign-in window or change the callback URL in both Wavefunction and your provider."
            )
        self.port = preferred if required else self._bindable(preferred) or self._free()
        self.reused = preferred is not None and self.port == preferred
        self._result: dict[str, str] = {}
        self._done = asyncio.Event()
        # Taken in start(), not here: there is no loop to capture until something is running, and
        # constructing a callback server is not itself an async act.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: HTTPServer | None = None

    @staticmethod
    def _free() -> int:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    @staticmethod
    def _bindable(port: int | None) -> int | None:
        """The port if this machine will still give it to us, otherwise nothing."""
        if not port:
            return None
        try:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
        except OSError:
            return None
        return port

    @property
    def redirect_uri(self) -> str:
        return f"http://{self.hostname}:{self.port}/callback"

    def start(self) -> None:
        self._loop = asyncio.get_event_loop()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - http.server's spelling
                if urlparse(self.path).path != "/callback":
                    self.send_error(404)
                    return
                query = parse_qs(urlparse(self.path).query)
                outer._result = {k: v[0] for k, v in query.items()}
                body = (
                    b"<title>Connected</title><p style='font-family:system-ui;padding:2rem'>"
                    b"You can close this tab and go back to Wavefunction.</p>"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if outer._loop is not None:
                    outer._loop.call_soon_threadsafe(outer._done.set)

            def log_message(self, *args: Any) -> None:
                """Quiet: this is not the app's log."""

        self._server = _LoopbackHTTPServer(("127.0.0.1", self.port), Handler)
        Thread(target=self._server.serve_forever, daemon=True).start()

    async def wait(self) -> tuple[str, str | None]:
        """The code and state the authorization server sent back."""
        try:
            await asyncio.wait_for(self._done.wait(), timeout=CALLBACK_TIMEOUT)
        except TimeoutError as error:
            raise TimeoutError("nobody finished signing in") from error
        if "error" in self._result:
            raise ValueError(self._result.get("error_description") or self._result["error"])
        code = self._result.get("code")
        if not code:
            raise ValueError("the sign-in finished without an authorization code")
        return code, self._result.get("state")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def oauth_provider(
    url: str, server_name: str, secrets: Any, callback: CallbackServer, open_browser: bool = True
) -> OAuthClientProvider:
    """The SDK's OAuth client, wired to this app's storage and a real browser."""
    check_registered_url(server_name, secrets, url)

    async def redirect(authorization_url: str) -> None:
        if open_browser:
            webbrowser.open(authorization_url)

    return provider_type(server_name, secrets)(
        server_url=url,
        client_metadata=client_metadata(server_name, secrets, callback.redirect_uri),
        storage=SecretTokenStorage(server_name, secrets, url),
        redirect_handler=redirect,
        callback_handler=callback.wait,
    )
