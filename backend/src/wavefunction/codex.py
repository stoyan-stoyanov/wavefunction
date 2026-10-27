"""Sign in with ChatGPT: run models on the user's own subscription instead of a key.

Every other provider here is a key someone pastes. This one is a person: the user signs in to
ChatGPT and their Plus/Pro subscription pays for the tokens, so the agent's work costs nothing
beyond what they already pay OpenAI.

How it works is the same trick the Codex CLI does, and the same one `pi` and `opencode` adopted:
an OAuth 2.0 authorization-code + PKCE login against `auth.openai.com`, then requests to the
ChatGPT backend at `chatgpt.com/backend-api/codex` rather than to `api.openai.com`. The access
token *is* the bearer credential, paired with the account id carried inside its own JWT claims.

Two things to be clear-eyed about:

- This uses the Codex CLI's public client id, because that is the only client id the endpoint
  will authorize. It is unofficial. OpenAI can withdraw it at any time, and if they do, the
  failure is a plain 401 rather than anything subtle — the Settings card says so rather than
  letting someone discover it mid-task.
- A subscription is one person on one machine: the sign-in stays in this app's data folder.

The endpoint is the Responses API with house rules: `store` must be false (it rejects true
outright), the system prompt travels in `instructions` rather than as an input item, and
reasoning has to round-trip encrypted because nothing is kept server-side. :class:`wavefunction.provider_chat.ChatCodex`
is where those three become one small override instead of a fork of `ChatOpenAI`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets as secrets_module
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from langchain_core.runnables.config import ensure_config

logger = logging.getLogger(__name__)

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/oauth/authorize"
TOKEN_URL = f"{ISSUER}/oauth/token"
# Not a preference: this exact port and path are what the Codex client id has registered as a
# redirect, so the login cannot be moved off it.
CALLBACK_PORT = 1455
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/auth/callback"
SCOPE = "openid profile email offline_access"
JWT_CLAIM = "https://api.openai.com/auth"
# How this client names itself, in the authorize call and on every request. Ours rather than the
# Codex CLI's: the client id has to be theirs, but who is calling does not have to be a lie.
ORIGINATOR = "wavefunction"

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
AUTH_FILE_NAME = "codex-auth.json"
# Refresh this far before the token actually expires. A turn can run for minutes, and a token
# that dies mid-run surfaces as a 401 the user cannot act on.
REFRESH_MARGIN_SECONDS = 300
LOGIN_TIMEOUT_SECONDS = 300

# The catalogue lives on the account, not in this file. `GET /codex/models` answers with what
# this subscription actually serves — which is how the ids below stopped being guesses: the
# first list here was read off two other clients and was wrong in both directions, naming
# `gpt-5.5-codex`, which the endpoint refuses outright, and missing the whole 5.6 family.
MODELS_URL = f"{CODEX_BASE_URL}/models"
# The endpoint hides a model from a client too old to drive it, comparing against this. We are
# not the Codex CLI at any version, so pinning a number would mean silently losing new models
# the day OpenAI raises the bar. Asking as a version above every gate is the honest reading of
# a field that only decides what we are allowed to be told about.
CLIENT_VERSION = "99.0.0"
# Only for when the catalogue cannot be reached — offline, or the endpoint moved. Deliberately
# short: a stale list that looks complete is worse than an obviously partial one, and Settings
# lets someone type any id.
FALLBACK_MODELS: list[dict[str, Any]] = [
    {"id": "gpt-5.5", "name": "GPT-5.5", "description": ""},
    {"id": "gpt-5.4", "name": "GPT-5.4", "description": ""},
]


def parse_models(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The models worth offering, from the catalogue's answer.

    `visibility` is the endpoint's own word for what belongs in a picker: it also returns
    internal entries (`codex-auto-review`, `gpt-reserve`) that exist to serve the Codex CLI's
    own machinery and are not models anyone should pick. `priority` is its preferred order.
    """
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    listed = [
        model
        for model in models
        if isinstance(model, dict) and model.get("visibility") == "list" and model.get("slug")
    ]
    listed.sort(key=lambda model: model.get("priority") or 999)
    from .reasoning import catalogue_efforts

    return [
        {
            "id": str(model["slug"]),
            "name": str(model.get("display_name") or model["slug"]),
            "description": str(model.get("description") or ""),
            "reasoning_efforts": catalogue_efforts("codex", model),
        }
        for model in listed
    ]


async def list_models(auth: CodexAuth) -> list[dict[str, Any]]:
    """What this subscription serves, asked of the account itself.

    Falls back rather than failing: not being able to reach the catalogue is a reason to show a
    short list and let someone type an id, not a reason for the Settings page to break.
    """
    credentials = auth.credentials()
    if credentials is None:
        return []
    try:
        token = await asyncio.to_thread(auth.access_token)
        credentials = auth.credentials() or credentials
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                MODELS_URL,
                params={"client_version": CLIENT_VERSION},
                headers={**codex_headers(credentials), "Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
        return parse_models(response.json()) or FALLBACK_MODELS
    except (httpx.HTTPError, CodexAuthError, ValueError) as error:
        logger.warning("Could not read the ChatGPT model catalogue: %s", error)
        return FALLBACK_MODELS


class CodexAuthError(RuntimeError):
    """A sign-in or refresh that failed in a way the user has to act on."""


@dataclass(frozen=True)
class CodexCredentials:
    access: str
    refresh: str
    expires_at: float
    account_id: str
    email: str = ""

    def expiring(self, margin: float = REFRESH_MARGIN_SECONDS) -> bool:
        return time.time() >= self.expires_at - margin

    def to_json(self) -> dict[str, Any]:
        return {
            "access": self.access,
            "refresh": self.refresh,
            "expires_at": self.expires_at,
            "account_id": self.account_id,
            "email": self.email,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> CodexCredentials:
        return cls(
            access=str(raw["access"]),
            refresh=str(raw["refresh"]),
            expires_at=float(raw.get("expires_at", 0)),
            account_id=str(raw.get("account_id", "")),
            email=str(raw.get("email", "")),
        )


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """The payload of a JWT, without verifying it.

    Verification is the token endpoint's job — this token came straight from it over TLS. All
    that is wanted here is the account id OpenAI put inside, which the request headers need.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return {}


def account_id_from_tokens(access: str, id_token: str = "") -> str:
    """The ChatGPT account the tokens belong to.

    Looked for in three places because OpenAI has put it in all of them: the namespaced claim on
    either token, then the flat claim, then the first organization. Without it the backend
    answers 401, so an empty result is a failed login rather than a detail to shrug at.
    """
    for token in (id_token, access):
        if not token:
            continue
        claims = decode_jwt_claims(token)
        namespaced = claims.get(JWT_CLAIM)
        if isinstance(namespaced, dict) and namespaced.get("chatgpt_account_id"):
            return str(namespaced["chatgpt_account_id"])
        if claims.get("chatgpt_account_id"):
            return str(claims["chatgpt_account_id"])
        organizations = claims.get("organizations")
        if isinstance(organizations, list) and organizations:
            first = organizations[0]
            if isinstance(first, dict) and first.get("id"):
                return str(first["id"])
    return ""


def email_from_tokens(access: str, id_token: str = "") -> str:
    """Whose account this is, for the Settings card to show. Absent is fine."""
    for token in (id_token, access):
        if not token:
            continue
        if email := decode_jwt_claims(token).get("email"):
            return str(email)
    return ""


def compute_residency(access: str) -> str:
    """The data-residency region this account is pinned to, when it is pinned to one.

    An account with a residency constraint is rejected without this header. `no_constraint` is
    the ordinary answer and means send nothing.
    """
    claims = decode_jwt_claims(access)
    namespaced = claims.get(JWT_CLAIM)
    residency = ""
    if isinstance(namespaced, dict):
        residency = str(namespaced.get("chatgpt_compute_residency") or "")
    residency = residency or str(claims.get("chatgpt_compute_residency") or "")
    return "" if residency in ("", "no_constraint") else residency


def generate_pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


class CodexAuthStore:
    """The tokens on disk, beside the credentials file and with the same permissions.

    Not in `.wavefunction.env` with the API keys: this is a rotating structured record, not a value
    someone pasted, and it is rewritten on every refresh. Written by atomic replace so a refresh
    interrupted halfway cannot leave a truncated file that signs the user out.
    """

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> CodexCredentials | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, json.JSONDecodeError):
            logger.warning("Could not read %s; treating ChatGPT as signed out", self.path.name)
            return None
        try:
            return CodexCredentials.from_json(raw)
        except (KeyError, TypeError, ValueError):
            return None

    def save(self, credentials: CodexCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(credentials.to_json(), handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise CodexAuthError("Could not save the ChatGPT sign-in on this Mac") from error
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            raise CodexAuthError("Could not remove the saved ChatGPT sign-in") from error


# ---- the sign-in flow ----------------------------------------------------------------------


SUCCESS_PAGE = """<!doctype html><meta charset="utf-8"><title>Signed in</title>
<style>body{font:16px -apple-system,system-ui,sans-serif;display:grid;place-items:center;
height:100vh;margin:0;color:#1d1d1f;background:#f2f2f5}div{text-align:center}
h1{font-size:20px;margin:0 0 8px}p{color:#6e6e73;margin:0}</style>
<div><h1>Signed in to ChatGPT</h1><p>You can close this window and go back to Wavefunction.</p></div>
"""

FAILURE_PAGE = """<!doctype html><meta charset="utf-8"><title>Sign-in failed</title>
<style>body{font:16px -apple-system,system-ui,sans-serif;display:grid;place-items:center;
height:100vh;margin:0;color:#1d1d1f;background:#f2f2f5}div{text-align:center}
h1{font-size:20px;margin:0 0 8px}p{color:#6e6e73;margin:0}</style>
<div><h1>Sign-in failed</h1><p>{reason}</p></div>
"""


def authorize_url(challenge: str, state: str) -> str:
    """Where the user signs in.

    `codex_cli_simplified_flow` and `id_token_add_organizations` are not decoration: without the
    first the authorize page runs the full consent flow this client id is not set up for, and
    without the second the id token arrives with no organization to fall back on when the
    account id claim is absent.
    """
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": ORIGINATOR,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _credentials_from_token_response(payload: dict[str, Any]) -> CodexCredentials:
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    id_token = str(payload.get("id_token") or "")
    if not access or not refresh:
        raise CodexAuthError("OpenAI's reply to the sign-in was missing its tokens")
    account_id = account_id_from_tokens(access, id_token)
    if not account_id:
        raise CodexAuthError(
            "This ChatGPT account did not come back with an account id, so requests would be "
            "rejected. Signing in again usually fixes it."
        )
    expires_in = payload.get("expires_in")
    expires_at = time.time() + (float(expires_in) if expires_in else 3600.0)
    return CodexCredentials(
        access=access,
        refresh=refresh,
        expires_at=expires_at,
        account_id=account_id,
        email=email_from_tokens(access, id_token),
    )


def exchange_code(code: str, verifier: str) -> CodexCredentials:
    """Turn the authorization code into tokens. Synchronous: it runs once, off the event loop."""
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as error:
        raise CodexAuthError("Could not reach OpenAI to finish signing in") from error
    if response.status_code != 200:
        raise CodexAuthError(f"OpenAI rejected the sign-in ({response.status_code})")
    return _credentials_from_token_response(response.json())


def refresh_credentials(current: CodexCredentials) -> CodexCredentials:
    """A new access token from the refresh token.

    The refresh token is rotated when OpenAI sends a new one and kept when it does not, because
    dropping it would sign the user out at the next refresh for no reason. The account id is
    carried over for the same reason: a refresh response without an id token still belongs to
    the account that was already established.
    """
    try:
        with httpx.Client(timeout=30) as client:
            response = client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": current.refresh,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as error:
        raise CodexAuthError("Could not reach OpenAI to refresh the ChatGPT sign-in") from error
    if response.status_code != 200:
        raise CodexAuthError("The ChatGPT sign-in has expired. Sign in again in Settings › Models.")
    payload = response.json()
    access = str(payload.get("access_token") or "")
    if not access:
        raise CodexAuthError("OpenAI's refresh reply was missing its access token")
    id_token = str(payload.get("id_token") or "")
    expires_in = payload.get("expires_in")
    return CodexCredentials(
        access=access,
        refresh=str(payload.get("refresh_token") or current.refresh),
        expires_at=time.time() + (float(expires_in) if expires_in else 3600.0),
        account_id=account_id_from_tokens(access, id_token) or current.account_id,
        email=email_from_tokens(access, id_token) or current.email,
    )


@dataclass
class PendingLogin:
    """The sign-in the user is in the middle of."""

    url: str
    state: str
    verifier: str
    started_at: float
    error: str = ""


class CodexAuth:
    """The ChatGPT sign-in, from the button in Settings to the bearer token on a request."""

    def __init__(self, store: CodexAuthStore):
        self.store = store
        self._pending: PendingLogin | None = None
        self._task: Any = None
        # One refresh at a time. Several agents can build models at once — a project running two
        # instances, or evals scoring in parallel — and each racing its own refresh would spend
        # the rotating refresh token more than once, which invalidates it.
        self._refresh_lock = threading.Lock()
        self._cached: CodexCredentials | None = None
        self._loaded = False
        # Settled by the callback handler, awaited by the waiter task. Created when a login
        # starts rather than when the task first runs, because the browser can arrive first.
        self._callback: asyncio.Future[tuple[str, str, str]] | None = None

    # ---- state ---------------------------------------------------------

    def credentials(self) -> CodexCredentials | None:
        if not self._loaded:
            self._cached = self.store.load()
            self._loaded = True
        return self._cached

    def connected(self) -> bool:
        return self.credentials() is not None

    def status(self) -> dict[str, Any]:
        credentials = self.credentials()
        pending = self._pending
        return {
            "connected": credentials is not None,
            "account_id": credentials.account_id if credentials else "",
            "email": credentials.email if credentials else "",
            "expires_at": credentials.expires_at if credentials else 0,
            "signing_in": pending is not None and not pending.error,
            "error": pending.error if pending else "",
        }

    def logout(self) -> None:
        self.store.clear()
        self._cached = None
        self._loaded = True

    def _remember(self, credentials: CodexCredentials) -> None:
        self.store.save(credentials)
        self._cached = credentials
        self._loaded = True

    # ---- the token a request actually carries --------------------------

    def access_token(self) -> str:
        """A live access token, refreshed first when it is about to expire.

        The synchronous transport calls this directly. Async requests offload it to a worker
        so a refresh and its shared lock cannot block health, streaming or cancellation.
        """
        credentials = self.credentials()
        if credentials is None:
            raise CodexAuthError("Sign in with ChatGPT in Settings › Models before using this model")
        if not credentials.expiring():
            return credentials.access
        with self._refresh_lock:
            # Re-read inside the lock: whoever held it may have just refreshed for us.
            current = self.credentials()
            if current is not None and not current.expiring():
                return current.access
            if current is None:
                raise CodexAuthError("ChatGPT was signed out. Sign in again in Settings › Models.")
            refreshed = refresh_credentials(current)
            if self.credentials() is not current:
                raise CodexAuthError("ChatGPT sign-in changed while refreshing. Try the request again.")
            self._remember(refreshed)
            return refreshed.access

    def request_credentials(self) -> CodexCredentials:
        self.access_token()
        credentials = self.credentials()
        if credentials is None:
            raise CodexAuthError("ChatGPT was signed out. Sign in again in Settings › Models.")
        return credentials

    # ---- signing in ----------------------------------------------------

    async def begin_login(self) -> str:
        """Start a sign-in and return the URL the user has to open.

        The listener is started before the URL is handed back, so a browser that is already open
        and very fast cannot arrive before anything is listening.
        """
        await self.cancel_login()
        verifier, challenge = generate_pkce()
        state = secrets_module.token_urlsafe(24)
        url = authorize_url(challenge, state)
        server = await self._listen()
        self._callback = asyncio.get_running_loop().create_future()
        self._pending = PendingLogin(url=url, state=state, verifier=verifier, started_at=time.time())
        self._task = asyncio.create_task(self._await_callback(server, state, verifier))
        return url

    async def cancel_login(self) -> None:
        task, self._task, self._pending = self._task, None, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _listen(self) -> asyncio.Server:
        try:
            return await asyncio.start_server(self._handle_request, "127.0.0.1", CALLBACK_PORT)
        except OSError as error:
            raise CodexAuthError(
                f"Port {CALLBACK_PORT} is already in use, and OpenAI will only send the sign-in "
                "back to that port. Quit anything else signing in to ChatGPT — the Codex CLI, "
                "or another copy of Wavefunction — and try again."
            ) from error

    async def _handle_request(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Answer the browser and hand the query back through `self._callback`.

        Hand-rolled rather than a real server because this handles exactly one request shape:
        OpenAI's redirect. Everything else gets a 404 and is forgotten.
        """
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            target = line.decode("latin-1").split(" ")[1] if b" " in line else "/"
            parsed = urllib.parse.urlparse(target)
            query = urllib.parse.parse_qs(parsed.query)
            if self._callback is None or self._callback.done():
                await self._respond(writer, 404, "<h1>Nothing is signing in</h1>")
                return
            if parsed.path != "/auth/callback":
                await self._respond(writer, 404, "<h1>Not found</h1>")
                return
            error = (query.get("error") or [""])[0]
            code = (query.get("code") or [""])[0]
            state = (query.get("state") or [""])[0]
            expected = self._pending.state if self._pending else ""
            if error:
                await self._respond(writer, 400, FAILURE_PAGE.replace("{reason}", error))
            elif not code:
                await self._respond(
                    writer, 400, FAILURE_PAGE.replace("{reason}", "No authorization code came back.")
                )
            elif state != expected:
                # The page has to agree with what the app decided. Rendering success here and
                # rejecting the sign-in behind it tells the user the opposite of what happened.
                # The waiter checks this again and is the authority; this is what they read.
                await self._respond(
                    writer,
                    400,
                    FAILURE_PAGE.replace(
                        "{reason}", "This sign-in did not match the one Wavefunction started."
                    ),
                )
            else:
                await self._respond(writer, 200, SUCCESS_PAGE)
            self._callback.set_result((code, state, error))
        except (TimeoutError, asyncio.IncompleteReadError, IndexError, UnicodeDecodeError):
            pass
        except asyncio.InvalidStateError:
            # A second request after the first already settled the login: the browser reloading
            # the callback page, or a favicon fetch. The answer is already given.
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n".encode("latin-1")
            + payload
        )
        with contextlib.suppress(Exception):
            await writer.drain()

    async def _await_callback(self, server: asyncio.Server, state: str, verifier: str) -> None:
        """Wait for the redirect, exchange the code, and store the result.

        Failures are recorded on the pending login rather than raised: nothing is awaiting this
        task, and the Settings card polls the status to find out how it went.
        """
        callback = self._callback
        assert callback is not None
        try:
            async with server:
                code, returned_state, error = await asyncio.wait_for(callback, timeout=LOGIN_TIMEOUT_SECONDS)
                if error:
                    raise CodexAuthError(f"OpenAI reported: {error}")
                if returned_state != state:
                    # The one check that matters here: without it a link someone else crafted
                    # could complete a sign-in into this app.
                    raise CodexAuthError("The sign-in came back with the wrong state and was ignored")
                credentials = await asyncio.to_thread(exchange_code, code, verifier)
                self._remember(credentials)
                self._pending = None
        except TimeoutError:
            if self._pending is not None:
                self._pending.error = "The sign-in was not finished in time. Try again."
        except CodexAuthError as failure:
            if self._pending is not None:
                self._pending.error = str(failure)
        except Exception as failure:  # noqa: BLE001 - reported through the status endpoint
            logger.exception("ChatGPT sign-in failed")
            if self._pending is not None:
                self._pending.error = str(failure)


# ---- the model ------------------------------------------------------------------------------


def _item_text(content: Any) -> str:
    """The text of one Responses input item, whatever shape it arrived in."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("text")]
        return "\n".join(parts)
    return ""


# ---- repairing the stream --------------------------------------------------------------------
#
# The ChatGPT backend never streams tool-call arguments. It sends
# `response.function_call_arguments.done` once, carrying the whole JSON, where the OpenAI API
# sends a run of `.delta` events and then a `.done`. LangChain accumulates the deltas and ignores
# the done — so every tool call arrived with `{}` for arguments: the model asked for `add(21, 21)`
# and the agent ran `add()`. Tools that take no arguments worked, which is what made it look like
# tool calling worked at all.
#
# Rewriting the wire is the narrow fix. Repairing it later is not possible — by the time a chunk
# reaches us the arguments have already been dropped — and overriding LangChain's stream parser
# would mean copying a hundred lines that are otherwise correct. This transform is scoped to
# ChatCodex's own HTTP client, so nothing else sees it.

ARGUMENTS_DONE = "response.function_call_arguments.done"
ARGUMENTS_DELTA = "response.function_call_arguments.delta"


def repair_sse_line(line: bytes, streamed: set[str]) -> bytes:
    """One SSE line, with a one-shot arguments event turned into the delta event, and a failed
    response turned into an error.

    `streamed` remembers which items already sent a real delta, so if OpenAI ever starts
    streaming these the done event is left alone rather than appending the arguments twice.

    A stream can answer 200 and then end in `response.failed` (a server error, a rate limit, a
    context overflow) or an `error` event. LangChain finishes such a stream as an ordinary reply
    holding whatever text arrived, and the OpenAI SDK raises only for an event with a top-level
    `error`. So the failure is given one, with its code, and is retried or recovered like any other.
    """
    if not line.startswith(b"data:"):
        return line
    raw = line[5:].strip()
    if not raw or raw == b"[DONE]":
        return line
    try:
        event = json.loads(raw)
    except ValueError:
        return line
    if not isinstance(event, dict):
        return line
    if event.get("type") in {"response.failed", "error"} and not event.get("error"):
        failure = (event.get("response") or {}).get("error") if event["type"] == "response.failed" else event
        failure = failure if isinstance(failure, dict) else {}
        error = {
            "code": failure.get("code") or "server_error",
            "message": failure.get("message") or "The model's response failed.",
        }
        return b"data: " + json.dumps({**event, "error": error}).encode()
    item = str(event.get("item_id", ""))
    if event.get("type") == ARGUMENTS_DELTA:
        streamed.add(item)
        return line
    if event.get("type") != ARGUMENTS_DONE or item in streamed:
        return line
    repaired = {**event, "type": ARGUMENTS_DELTA, "delta": event.get("arguments", "")}
    repaired.pop("arguments", None)
    return b"data: " + json.dumps(repaired).encode()


class _LineRepair:
    """Buffers a byte stream into lines and repairs the arguments events in them.

    Buffered because a line can be split across network chunks: rewriting half an event would
    corrupt the stream far more thoroughly than the bug being fixed.
    """

    def __init__(self) -> None:
        self._buffer = b""
        self._streamed: set[str] = set()

    def feed(self, chunk: bytes) -> bytes:
        """Complete lines, repaired. Byte-exact for every line that is not rewritten.

        Exactness matters because this sits in front of every Codex response, not only the
        event streams: a plain JSON error body must come out the length it went in.
        """
        self._buffer += chunk
        out = bytearray()
        while (break_at := self._buffer.find(b"\n")) != -1:
            line, self._buffer = self._buffer[:break_at], self._buffer[break_at + 1 :]
            carriage = line.endswith(b"\r")
            repaired = repair_sse_line(line[:-1] if carriage else line, self._streamed)
            out += repaired + (b"\r\n" if carriage else b"\n")
        return bytes(out)

    def flush(self) -> bytes:
        """Whatever is left when the stream ends without a final newline."""
        remainder, self._buffer = self._buffer, b""
        return repair_sse_line(remainder, self._streamed) if remainder else b""


class RepairingByteStream(httpx.SyncByteStream):
    def __init__(self, stream: httpx.SyncByteStream) -> None:
        self._stream = stream
        self._repair = _LineRepair()

    def __iter__(self):
        for chunk in self._stream:
            if repaired := self._repair.feed(chunk):
                yield repaired
        if trailing := self._repair.flush():
            yield trailing

    def close(self) -> None:
        self._stream.close()


class RepairingAsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream) -> None:
        self._stream = stream
        self._repair = _LineRepair()

    async def __aiter__(self):
        async for chunk in self._stream:
            if repaired := self._repair.feed(chunk):
                yield repaired
        if trailing := self._repair.flush():
            yield trailing

    async def aclose(self) -> None:
        await self._stream.aclose()


def _repaired(response: httpx.Response, stream: Any) -> httpx.Response:
    """The same response, reading through the repair.

    Applied to everything rather than only to declared event streams: this backend answers a
    streaming request with no `content-type` at all, so gating on one silently skipped the
    repair for exactly the model that needed it. The transform is byte-exact for anything that
    is not a one-shot arguments event or a failed response, and these clients only talk to the
    ChatGPT backend and OpenAI's Responses API.
    """
    # No `request=`: at transport level it has not been set yet, and reading it raises. The
    # client attaches it to whatever this returns.
    return httpx.Response(
        status_code=response.status_code,
        headers=response.headers,
        stream=stream,
        extensions=response.extensions,
    )


def _auth_failure(error: CodexAuthError) -> httpx.Response:
    # A transport exception is retried and wrapped as a network outage by the SDK.
    # A local authentication failure is terminal and carries actionable UI copy.
    return httpx.Response(401, json={"error": {"code": "codex_local_auth", "message": str(error)}})


class _TurnRouting:
    """One server routing token per model/turn, never a global conversation cache.

    ModelGateway creates fresh clients for each run. ChatGPT needs both the session headers
    and its first x-codex-turn-state response header replayed through that run's tool loop.
    A stable prompt_cache_key alone does not keep requests on the server holding the cache.
    """

    def __init__(self) -> None:
        self._state: tuple[tuple[str, str, str], str] | None = None

    def prepare(self, request: httpx.Request) -> tuple[str, str, str] | None:
        session = request.headers.get("session-id")
        if not session or request.url.host != httpx.URL(CODEX_BASE_URL).host:
            return None
        owner = (
            request.headers.get("chatgpt-account-id", ""),
            session,
            request.headers.get("thread-id", session),
        )
        state = self._state
        if state is not None and state[0] == owner:
            request.headers["x-codex-turn-state"] = state[1]
        return owner

    def remember(self, owner: tuple[str, str, str] | None, response: httpx.Response) -> None:
        # The first value belongs to this turn. Do not replace it on retries.
        if (
            owner is not None
            and response.is_success
            and (self._state is None or self._state[0] != owner)
            and (token := response.headers.get("x-codex-turn-state"))
        ):
            self._state = (owner, token)


class CodexTransport(httpx.HTTPTransport):
    def __init__(self, *args, auth: CodexAuth | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth = auth
        self._routing = _TurnRouting()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.auth is not None:
            try:
                _authenticate_codex_request(request, self.auth.request_credentials())
            except CodexAuthError as error:
                return _auth_failure(error)
        owner = self._routing.prepare(request)
        response = super().handle_request(request)
        self._routing.remember(owner, response)
        return _repaired(response, RepairingByteStream(response.stream))


class AsyncCodexTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *args, auth: CodexAuth | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.auth = auth
        self._routing = _TurnRouting()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.auth is not None:
            try:
                credentials = await asyncio.to_thread(self.auth.request_credentials)
                _authenticate_codex_request(request, credentials)
            except CodexAuthError as error:
                return _auth_failure(error)
        owner = self._routing.prepare(request)
        response = await super().handle_async_request(request)
        self._routing.remember(owner, response)
        return _repaired(response, RepairingAsyncByteStream(response.stream))


def _authenticate_codex_request(request: httpx.Request, credentials: CodexCredentials) -> None:
    if request.url.host != httpx.URL(CODEX_BASE_URL).host:
        raise CodexAuthError("Refusing to send ChatGPT credentials to another host")
    request.headers.pop("x-openai-internal-codex-residency", None)
    request.headers.update(codex_headers(credentials))
    request.headers["Authorization"] = f"Bearer {credentials.access}"


def codex_http_clients(auth: CodexAuth | None = None) -> tuple[httpx.Client, httpx.AsyncClient]:
    """The pair `ChatCodex` needs, both repairing the stream. Without `auth`, for OpenAI's own
    Responses API, which ends a failed stream the same way."""
    return (
        httpx.Client(transport=CodexTransport(auth=auth), timeout=httpx.Timeout(120.0)),
        httpx.AsyncClient(transport=AsyncCodexTransport(auth=auth), timeout=httpx.Timeout(120.0)),
    )


def with_cache_key(payload: dict) -> dict:
    """Name the conversation a request belongs to as its `prompt_cache_key`.

    Keep the key stable across turns and retries, separate across conversations. It assists
    routing/accounting; cache reuse still requires matching prompt prefixes and an available
    provider cache. A call outside a LangGraph conversation sends none.
    """
    thread = (ensure_config().get("configurable") or {}).get("thread_id")
    if thread and "prompt_cache_key" not in payload:
        key = str(thread)
        payload["prompt_cache_key"] = key if len(key) <= 64 else hashlib.sha256(key.encode()).hexdigest()
    return payload


def codex_headers(credentials: CodexCredentials) -> dict[str, str]:
    """What every request to the ChatGPT backend carries besides the bearer token."""
    headers = {
        "chatgpt-account-id": credentials.account_id,
        "originator": ORIGINATOR,
        "OpenAI-Beta": "responses=experimental",
    }
    if residency := compute_residency(credentials.access):
        headers["x-openai-internal-codex-residency"] = residency
    return headers


def describe_codex_error(error: BaseException) -> str:
    """A subscription's rate limit, said the way a person would say it.

    The raw body is `{"error":{"code":"usage_limit_reached","plan_type":"plus","resets_at":…}}`,
    which tells someone nothing about what to do. Returns "" when this is not that error, so the
    caller can fall through to its ordinary handling.
    """
    body = getattr(getattr(error, "response", None), "text", "") or str(error)
    if not any(
        marker in body for marker in ("usage_limit_reached", "usage_not_included", "rate_limit_exceeded")
    ):
        return ""
    plan = ""
    resets = ""
    with contextlib.suppress(ValueError, json.JSONDecodeError, AttributeError, TypeError):
        start = body.index("{")
        parsed = json.loads(body[start:]).get("error", {})
        if parsed.get("plan_type"):
            plan = f" ({str(parsed['plan_type']).lower()} plan)"
        if parsed.get("resets_at"):
            minutes = max(0, round((float(parsed["resets_at"]) - time.time()) / 60))
            resets = f" It resets in about {minutes} minute{'s' if minutes != 1 else ''}."
    if "usage_not_included" in body:
        return (
            f"This model is not included in your ChatGPT plan{plan}. Pick another model in Settings › Models."
        )
    return f"You have hit your ChatGPT usage limit{plan}.{resets}"


async def search_web(auth: CodexAuth, model: str, query: str, max_results: int) -> tuple[str, dict | None]:
    """Search the web on the subscription, with the model's own search tool.

    The endpoint carries OpenAI's `web_search` tool and every model this app offers declares
    `supports_search_tool`, so a ChatGPT sign-in already includes search, with no Tavily key
    needed for something the user already pays for.

    Returns the answer followed by the sources it cited, which is the shape `ModelGateway`'s
    other search already returns: what the pages said, and where it came from. With it, the
    usage the endpoint reported for the search, which is a whole model call of its own.
    """
    credentials = auth.credentials()
    if credentials is None:
        raise CodexAuthError("Sign in with ChatGPT in Settings › Models before searching")
    token = await asyncio.to_thread(auth.access_token)
    credentials = auth.credentials() or credentials
    headers = {
        **codex_headers(credentials),
        "Authorization": f"Bearer {token}",
        "accept": "text/event-stream",
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "store": False,
        "stream": True,
        "instructions": (
            "Search the web and summarise what the sources say, citing them. Be specific and "
            "quote figures and dates as the sources give them."
        ),
        "input": [{"role": "user", "content": [{"type": "input_text", "text": query}]}],
        "tools": [{"type": "web_search"}],
    }

    answer: list[str] = []
    # Kept in order and de-duplicated: the model cites the same page several times, and a list
    # repeating one URL eight times reads as eight sources.
    sources: dict[str, str] = {}
    usage = None
    async with (
        httpx.AsyncClient(timeout=180) as client,
        client.stream("POST", f"{CODEX_BASE_URL}/responses", headers=headers, json=body) as response,
    ):
        if response.status_code != 200:
            await response.aread()
            raise CodexAuthError(
                describe_codex_error_text(response.text) or f"The web search failed ({response.status_code})"
            )
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if event.get("type") == "response.output_text.delta":
                answer.append(str(event.get("delta", "")))
            elif event.get("type") == "response.output_text.annotation.added":
                annotation = event.get("annotation") or {}
                if annotation.get("type") == "url_citation" and annotation.get("url"):
                    sources.setdefault(str(annotation["url"]), str(annotation.get("title") or ""))
            elif event.get("type") == "response.completed":
                usage = (event.get("response") or {}).get("usage")

    blocks = []
    if text := "".join(answer).strip():
        blocks.append(text)
    for url, title in list(sources.items())[:max_results]:
        blocks.append(f"SOURCE: {url}" + (f"\n{title}" if title else ""))
    return ("\n\n".join(blocks) if blocks else f"No web results for {query!r}."), usage


def describe_codex_error_text(body: str) -> str:
    """The readable part of an error body, when there is one."""
    return describe_codex_error(Exception(body))


__all__ = [
    "CODEX_BASE_URL",
    "FALLBACK_MODELS",
    "list_models",
    "parse_models",
    "AUTH_FILE_NAME",
    "CodexAuth",
    "CodexAuthError",
    "CodexAuthStore",
    "CodexCredentials",
    "codex_headers",
    "describe_codex_error",
    "search_web",
    "with_cache_key",
]
