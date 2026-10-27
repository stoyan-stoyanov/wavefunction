"""GitHub for sandboxed commands, without the user's token in them.

As Claude Code's cloud sessions, Codex's credential broker and Cline's cloud sandbox do it, a
command is given a placeholder in place of the GitHub sign-in. gh sends its requests through a
Unix socket the app listens on (gh's `http_unix_socket` setting); git sends its own through the
app as its HTTP proxy for github.com, whose remotes, SSH ones included, it is told to reach as
http://github.com/... — so they still name github.com, which is how gh finds a folder's
repository. The app replaces the placeholder with the token of gh's sign-in on this Mac on the
way out, over HTTPS, so what a
command runs can read the placeholder and nothing more. Like Claude Code's GitHub proxy, it
refuses two pushes: deleting branches and pushing tags. Otherwise git and gh act as the user, as
they do in the user's terminal and in other coding agents.

The placeholder is new each time the app starts, and the loopback proxy takes only requests that
carry it as their proxy password: other processes on this Mac cannot use it to act as the user.
The socket is in a folder only this user can open.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import os
import re
import secrets
import shutil
import tempfile
import zlib
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

import h11
import httpx

from . import sandbox

logger = logging.getLogger(__name__)

# Where the sign-in is sent. gh's requests to other hosts, such as the storage a download is
# redirected to, go out without it.
SIGNED_HOSTS = frozenset({"api.github.com", "uploads.github.com", "github.com"})
# Headers of one connection rather than of what is forwarded.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "expect",
    }
)
UPDATE = re.compile(rb"([0-9a-f]{40,64}) ([0-9a-f]{40,64}) (\S+)")
SIDE_BAND = {"side-band-64k": 65515, "side-band": 995}
NOT_OURS = b"This address serves Wavefunction's commands only.\n"
PROXY_USER = "wavefunction"


def pkt(data: bytes) -> bytes:
    """One pkt-line of Git's wire protocol."""
    return f"{len(data) + 4:04x}".encode() + data


def ref_updates(head: bytes) -> tuple[list[tuple[str, str, str]], set[str]] | None:
    """The ref updates a push asks for, and its capabilities: what comes before the first flush
    of a receive-pack request. None until all of it has arrived."""
    updates, capabilities, at = [], set(), 0
    while True:
        if len(head) < at + 4:
            return None
        size = int(head[at : at + 4], 16)
        if size == 0:
            return updates, capabilities
        if size < 4:
            raise ValueError("not a Git request")
        if len(head) < at + size:
            return None
        line, _, offered = head[at + 4 : at + size].rstrip(b"\n").partition(b"\0")
        at += size
        capabilities |= set(offered.decode(errors="replace").split())
        # Shallow and push-certificate lines are not updates; the updates in a certificate are.
        if found := UPDATE.match(line):
            updates.append(tuple(part.decode() for part in found.groups()))


DELETES = "Wavefunction does not delete branches or tags on GitHub; the user can"
TAGS = "Wavefunction does not push tags to GitHub; the user can"


def refusal(new: str, ref: str) -> str:
    if not new.strip("0"):
        return DELETES
    if ref.startswith("refs/tags/"):
        return TAGS
    return ""


def push_report(
    updates: list[tuple[str, str, str]], refused: dict[str, str], capabilities: set[str]
) -> bytes:
    """The answer to a refused push, as GitHub would give one: git shows each ref as rejected."""
    lines = [b"unpack ok\n"] + [
        f"ng {ref} {refused.get(ref) or 'not pushed, since another update in this push was refused'}\n".encode()
        for _, _, ref in updates
    ]
    report = b"".join(pkt(line) for line in lines) + b"0000"
    band = next((size for name, size in SIDE_BAND.items() if name in capabilities), 0)
    if not band:
        return report
    return b"".join(pkt(b"\x01" + report[at : at + band]) for at in range(0, len(report), band)) + b"0000"


def _config_value(path: Path) -> str:
    """A path as a quoted git config value: unquoted, `;` or `#` in it would start a comment."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


class GitHubProxy:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None):
        self.placeholder = "wavefunction-" + secrets.token_urlsafe(24)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._servers: list[asyncio.AbstractServer] = []
        self._connections: set[asyncio.Task] = set()
        self._folder: Path | None = None
        self._port = 0
        self._starting: asyncio.Lock | None = None

    async def start(self) -> None:
        self._starting = self._starting or asyncio.Lock()
        async with self._starting:
            if self._servers:
                return
            # macOS allows 104 bytes for a socket's path: the user's temporary folder fits, and
            # only they can open the folder made in it.
            folder = Path(tempfile.mkdtemp(prefix="wf-gh-")).resolve()
            servers: list[asyncio.AbstractServer] = []
            try:
                servers.append(
                    await asyncio.start_unix_server(self._serving(self._gh), path=str(folder / "gh.sock"))
                )
                servers.append(await asyncio.start_server(self._serving(self._git), host="127.0.0.1", port=0))
                # gh's configuration, written once in the app's own folder: the task's scratch is
                # the command's to change, and writing there could follow a link it left.
                (folder / "gh").mkdir(mode=0o700)
                handle = os.open(
                    folder / "gh" / "config.yml", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(handle, "w") as config:
                    # In gh's current format: gh rewrites an older one, which it cannot do here.
                    config.write(f'version: "1"\nhttp_unix_socket: {folder / "gh.sock"}\n')
                # git's global configuration for commands: the rules that send GitHub through the
                # proxy, then the user's own files. Of two equally long insteadOf rules git uses
                # the first it read, so a user's GitHub rule (SSH for Go modules, say) can no
                # longer send a remote around the proxy, as it did from GIT_CONFIG_* variables.
                home = Path.home()
                includes = "".join(
                    f"\tpath = {_config_value(path)}\n"
                    for path in (home / ".config/git/config", home / ".gitconfig")
                )
                handle = os.open(
                    folder / "gitconfig", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
                with os.fdopen(handle, "w") as config:
                    config.write(
                        '[url "http://github.com/"]\n\tinsteadOf = https://github.com/\n'
                        "\tinsteadOf = git@github.com:\n\tinsteadOf = ssh://git@github.com/\n"
                        f"[include]\n{includes}"
                    )
            except BaseException:
                # Nothing of a start that failed partway stays open or on disk; the next command
                # tries again.
                for server in servers:
                    server.close()
                    with suppress(Exception):
                        await server.wait_closed()
                shutil.rmtree(folder, ignore_errors=True)
                raise
            self._folder, self._servers = folder, servers
            self._port = servers[1].sockets[0].getsockname()[1]
            self._client = httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                # Long enough for GitHub to pack a large repository;
                # bounded, so a stalled answer does not hold a
                # connection a timed-out command has left.
                timeout=httpx.Timeout(30, read=600, write=600),
            )
            sandbox.LOCAL_SOCKETS.add(str(folder / "gh.sock"))
            sandbox.READABLE_FOLDERS.update({str(folder / "gh"), str(folder / "gitconfig")})

    async def close(self) -> None:
        servers, self._servers = self._servers, []
        for server in servers:
            server.close()
        for task in list(self._connections):
            task.cancel()
        await asyncio.gather(*self._connections, return_exceptions=True)
        for server in servers:
            with suppress(Exception):
                await server.wait_closed()
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._folder is not None:
            sandbox.LOCAL_SOCKETS.discard(str(self._folder / "gh.sock"))
            sandbox.READABLE_FOLDERS.difference_update(
                {str(self._folder / "gh"), str(self._folder / "gitconfig")}
            )
            shutil.rmtree(self._folder, ignore_errors=True)
            self._folder = None

    async def environment(self) -> dict[str, str]:
        """What lets `git` and `gh` in a command reach GitHub as the user, holding only the
        placeholder: gh's configuration sends it through the socket, and git reaches github.com
        through the loopback proxy. Empty where that is not running."""
        try:
            await self.start()
        except OSError:
            logger.exception("GitHub is unavailable to commands: its proxy did not start")
            return {}
        return {
            "GH_TOKEN": self.placeholder,
            "GH_CONFIG_DIR": str(self._folder / "gh"),
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GH_PROMPT_DISABLED": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_GLOBAL": str(self._folder / "gitconfig"),
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "http.http://github.com/.proxy",
            "GIT_CONFIG_VALUE_0": f"http://{PROXY_USER}:{self.placeholder}@127.0.0.1:{self._port}",
            # Sent with the first request rather than after a refusal.
            "GIT_CONFIG_KEY_1": "http.http://github.com/.proxyAuthMethod",
            "GIT_CONFIG_VALUE_1": "basic",
        }

    # -- serving -----------------------------------------------------------------------------

    def _serving(self, handle):
        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            self._connections.add(task)
            connection = h11.Connection(h11.SERVER)
            try:
                request = await self._event(connection, reader)
                if isinstance(request, h11.Request):
                    await handle(connection, reader, writer, request)
            except (ConnectionError, h11.ProtocolError, asyncio.IncompleteReadError):
                pass
            except Exception:  # noqa: BLE001 - one request's failure is not the app's
                logger.exception("A GitHub request from a command failed")
                if connection.our_state in (h11.IDLE, h11.SEND_RESPONSE):
                    with suppress(Exception):
                        await self._answer(connection, writer, 502, b"GitHub could not be reached.\n")
            finally:
                self._connections.discard(task)
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

        return serve

    @staticmethod
    async def _event(connection: h11.Connection, reader: asyncio.StreamReader):
        while True:
            event = connection.next_event()
            if event is not h11.NEED_DATA:
                return event
            connection.receive_data(await reader.read(65536))

    async def _body(self, connection, reader, writer) -> AsyncIterator[bytes]:
        if connection.they_are_waiting_for_100_continue:
            writer.write(connection.send(h11.InformationalResponse(status_code=100, headers=[])))
        while True:
            event = await self._event(connection, reader)
            if isinstance(event, h11.Data):
                yield bytes(event.data)
            elif isinstance(event, h11.EndOfMessage):
                return
            else:
                raise ConnectionError("the request ended early")

    @staticmethod
    async def _answer(connection, writer, status: int, body: bytes, content_type: str = "text/plain") -> None:
        writer.write(
            connection.send(
                h11.Response(
                    status_code=status,
                    headers=[
                        ("content-type", content_type),
                        ("content-length", str(len(body))),
                        ("connection", "close"),
                    ],
                )
            )
        )
        writer.write(connection.send(h11.Data(data=body)))
        writer.write(connection.send(h11.EndOfMessage()))
        await writer.drain()

    async def _gh(self, connection, reader, writer, request: h11.Request) -> None:
        headers = [(name.decode().lower(), value.decode()) for name, value in request.headers]
        host = next((value for name, value in headers if name == "host"), "").lower()
        if not host or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]+", host):
            await self._answer(connection, writer, 400, b"A request for GitHub needs its host.\n")
            return
        # Signed with the user's sign-in: what carries the placeholder, to GitHub itself.
        sign = host in SIGNED_HOSTS and any(
            name == "authorization" and self.placeholder in value for name, value in headers
        )
        kept = [
            (name, value)
            for name, value in headers
            if not (name == "authorization" and self.placeholder in value)
        ]
        await self._forward(
            connection,
            reader,
            writer,
            request,
            f"https://{host}{request.target.decode()}",
            kept,
            sign,
            basic=False,
        )

    async def _git(self, connection, reader, writer, request: h11.Request) -> None:
        """git's requests for github.com, as its HTTP proxy: absolute URLs, with the placeholder
        as the proxy password."""
        target = request.target.decode()
        offered = next(
            (value for name, value in request.headers if name.lower() == b"proxy-authorization"), b""
        )
        expected = b"Basic " + base64.b64encode(f"{PROXY_USER}:{self.placeholder}".encode())
        if not hmac.compare_digest(offered, expected) or not target.startswith("http://github.com/"):
            await self._answer(connection, writer, 407 if not offered else 403, NOT_OURS)
            return
        headers = [
            (name.decode().lower(), value.decode())
            for name, value in request.headers
            if name.lower() != b"authorization"
        ]
        url = "https://github.com/" + target[len("http://github.com/") :]
        if request.method == b"POST" and target.split("?")[0].endswith("/git-receive-pack"):
            await self._push(connection, reader, writer, request, url, headers)
            return
        await self._forward(connection, reader, writer, request, url, headers, True, basic=True)

    async def _push(self, connection, reader, writer, request, url, headers) -> None:
        """A push: read its ref updates before anything reaches GitHub, and refuse it whole when
        one of them is a deletion or a tag."""
        body = self._body(connection, reader, writer)
        gzipped = any(name == "content-encoding" and "gzip" in value for name, value in headers)
        inflate = zlib.decompressobj(16 + zlib.MAX_WBITS) if gzipped else None
        sent, head, parsed = [], b"", None
        async for chunk in body:
            sent.append(chunk)
            head += inflate.decompress(chunk) if inflate else chunk
            if (parsed := ref_updates(head)) is not None:
                break
        else:
            parsed = ref_updates(head) or ([], set())
        updates, capabilities = parsed
        refused = {ref: reason for _, new, ref in updates if (reason := refusal(new, ref))}
        if refused:
            async for _ in body:
                pass
            if "report-status" in capabilities or "report-status-v2" in capabilities:
                await self._answer(
                    connection,
                    writer,
                    200,
                    push_report(updates, refused, capabilities),
                    "application/x-git-receive-pack-result",
                )
            else:
                await self._answer(connection, writer, 403, "; ".join(refused.values()).encode() + b"\n")
            return

        async def replay():
            for chunk in sent:
                yield chunk
            async for chunk in body:
                yield chunk

        await self._forward(
            connection, reader, writer, request, url, headers, True, basic=True, content=replay()
        )

    async def _forward(
        self, connection, reader, writer, request, url, headers, sign, *, basic, content=None
    ) -> None:
        from .github_cli import current_token

        outgoing = [(name, value) for name, value in headers if name not in HOP_BY_HOP]
        token = await current_token() if sign else ""
        if token:
            value = (
                "Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
                if basic
                else f"token {token}"
            )
            outgoing.append(("authorization", value))
        if content is None:
            content = self._body(connection, reader, writer)
            if not any(name in ("content-length", "transfer-encoding") for name, _ in headers):
                async for _ in content:
                    pass  # No body: one sent as an empty chunked stream can be refused.
                content = None
        upstream = self._client.build_request(request.method.decode(), url, headers=outgoing, content=content)
        response = await self._client.send(upstream, stream=True)
        try:
            kept = [
                (name, value)
                for name, value in response.headers.raw
                if name.lower().decode() not in HOP_BY_HOP
            ]
            if basic:
                # A renamed or moved repository redirects Git to its new address: as git's own
                # address for github.com, so it goes through the proxy, where the sign-in is.
                kept = [
                    (
                        name,
                        b"http://github.com/" + value[len(b"https://github.com/") :]
                        if name.lower() == b"location" and value.startswith(b"https://github.com/")
                        else value,
                    )
                    for name, value in kept
                ]
            bodiless = request.method == b"HEAD" or response.status_code in (204, 304)
            if not bodiless and not any(name.lower() == b"content-length" for name, _ in kept):
                kept.append((b"transfer-encoding", b"chunked"))
            kept.append((b"connection", b"close"))
            writer.write(
                connection.send(
                    h11.Response(
                        status_code=response.status_code, headers=kept, reason=response.reason_phrase.encode()
                    )
                )
            )
            async for chunk in response.aiter_raw():
                writer.write(connection.send(h11.Data(data=chunk)))
                await writer.drain()
            writer.write(connection.send(h11.EndOfMessage()))
            await writer.drain()
        finally:
            await response.aclose()
