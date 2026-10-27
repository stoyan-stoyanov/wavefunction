"""Managed, checksum-verified tools and account setup for the desktop UI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import stat
import tarfile
import tempfile
import time
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field, SecretStr

from .local_integrations import Kind, executable, run_cli

RELEASES = {
    ("himalaya", "arm64"): (
        "pimalaya/himalaya",
        "v2.1.0",
        "himalaya.aarch64-darwin.tgz",
        "a5a787b7c4dbf065408e7772908fc75c799626f4cebab8e9c78fafe3e2fa585c",
    ),
    ("himalaya", "x86_64"): (
        "pimalaya/himalaya",
        "v2.1.0",
        "himalaya.x86_64-darwin.tgz",
        "0e61604709e7e84b5142ebebc85c52d7beb38f09ffd3506fae6041245c35d2a7",
    ),
    ("gog", "arm64"): (
        "openclaw/gogcli",
        "v0.39.1",
        "gogcli_0.39.1_darwin_arm64.tar.gz",
        "387062a590d470d0b13b1c79cab72c5908bfc42abc5cf68243bf3f12fc30acda",
    ),
    ("gog", "x86_64"): (
        "openclaw/gogcli",
        "v0.39.1",
        "gogcli_0.39.1_darwin_amd64.tar.gz",
        "e927ddf46cbee95fd4f1cb0946458fcbfff68fe68754121c310cd3aa5aca286c",
    ),
    # Digests from the release's gh_2.101.0_checksums.txt.
    ("gh", "arm64"): (
        "cli/cli",
        "v2.101.0",
        "gh_2.101.0_macOS_arm64.zip",
        "e4303e39d8f07141c4bad4b99b01079f05029c59b27076e8fbc825c985ecdd8b",
    ),
    ("gh", "x86_64"): (
        "cli/cli",
        "v2.101.0",
        "gh_2.101.0_macOS_amd64.zip",
        "a6fd66c88e2f07d6e4e058173db341d07dd74d58cf8f19ae668293d2bb614ca3",
    ),
}


def private_write(path: Path, data: bytes, mode: int = 0o600):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            os.fchmod(file.fileno(), mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def unpack(archive_path: Path, kind: Kind, digest: str, destination: Path) -> None:
    """Verify a downloaded archive against its pinned digest and install the one tool in it.

    Read from disk rather than from one buffer, and the tool is written straight from the
    archive: neither the download nor the binary needs to fit in memory, so there is no size
    at which a genuine release is refused. What is still refused is anything that is not the
    expected release — a wrong digest, a missing or extra member, a link or a path.
    """
    digester = hashlib.sha256()
    with archive_path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digester.update(chunk)
    if digester.hexdigest() != digest:
        raise ValueError("Download verification failed. Please try again.")
    if archive_path.suffix == ".zip":
        # gh's macOS release is a zip with the tool in the release's own folder. Exactly one
        # regular file at exactly that name: a link's mode is not a regular file's.
        with zipfile.ZipFile(archive_path) as archive:
            members = [
                m
                for m in archive.infolist()
                if m.filename == f"{archive_path.stem}/bin/{kind}" and stat.S_ISREG(m.external_attr >> 16)
            ]
            _install_member(members, archive.open, destination)
        return
    with tarfile.open(archive_path, mode="r:gz") as archive:
        members = [m for m in archive.getmembers() if m.name in (kind, f"./{kind}") and m.isfile()]
        _install_member(members, archive.extractfile, destination)


def _install_member(members: list, open_member, destination: Path) -> None:
    if len(members) != 1:
        raise ValueError("The download does not contain the expected tool.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as file, open_member(members[0]) as source:
            shutil.copyfileobj(source, file)
            os.fchmod(file.fileno(), 0o700)
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)


async def install(kind: Kind, root: Path):
    if kind == "microsoft":
        from .microsoft_graph import install as install_microsoft

        return await install_microsoft(root)
    if platform.system() != "Darwin" or (kind, platform.machine()) not in RELEASES:
        raise ValueError("In-app installation currently supports Intel and Apple silicon Macs.")
    repo, version, asset, digest = RELEASES[kind, platform.machine()]
    with tempfile.TemporaryDirectory(prefix="wavefunction-download-") as directory:
        archive_path = Path(directory) / asset
        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=120) as client,
            client.stream(
                "GET", f"https://github.com/{repo}/releases/download/{version}/{asset}"
            ) as response,
        ):
            response.raise_for_status()
            # Streamed to disk: the archive is as large as the release is, and memory does not
            # grow with it. A download that stops short fails the digest check below.
            with archive_path.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
        await asyncio.to_thread(unpack, archive_path, kind, digest, root / "bin" / kind)


class EmailSetup(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+$")
    password: SecretStr = Field(min_length=1, max_length=4096)
    imap_host: str = Field(pattern=r"^[a-zA-Z0-9.-]+$", max_length=253)
    imap_port: int = Field(default=993, ge=1, le=65535)
    smtp_host: str = Field(pattern=r"^[a-zA-Z0-9.-]+$", max_length=253)
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_starttls: bool = False


def mail_path(root: Path, account: str) -> Path:
    return root / "mail" / (hashlib.sha256(account.encode()).hexdigest() + ".toml")


async def setup_email(payload: EmailSetup, root: Path):
    quote = json.dumps
    config = f"[accounts.{quote(payload.email)}]\nemail = {quote(payload.email)}\n"
    for protocol, host, port, tls in [
        ("imap", payload.imap_host, payload.imap_port, False),
        ("smtp", payload.smtp_host, payload.smtp_port, payload.smtp_starttls),
    ]:
        scheme = protocol if tls else protocol + "s"
        config += f"{protocol}.server = {quote(f'{scheme}://{host}:{port}')}\n"
        config += f"{protocol}.starttls = {str(tls).lower()}\n"
        config += f"{protocol}.sasl.plain.username = {quote(payload.email)}\n"
        config += f"{protocol}.sasl.plain.password.raw = {quote(payload.password.get_secret_value())}\n"
    # Validate a temporary config first, retaining any previously working account.
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=root) as temp:
        path = Path(temp) / "config.toml"
        private_write(path, config.encode())
        try:
            await run_cli(
                [
                    executable("himalaya", root),
                    "--config",
                    str(path),
                    "--account",
                    payload.email,
                    "--json",
                    "mailbox",
                    "list",
                ]
            )
        except (ValueError, OSError) as error:
            raise ValueError(
                "Could not sign in to the mailbox. Check your IMAP server, email address, and app password."
            ) from error
        await asyncio.to_thread(private_write, mail_path(root, payload.email), config.encode())


DOWNLOADED_FOR = 24 * 60 * 60


def _desktop_client(text: str) -> dict | None:
    """The `installed` block of a Desktop OAuth client file, or None for anything else."""
    try:
        client = json.loads(text)["installed"]
        return client if client.get("client_id") and client.get("client_secret") else None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def downloaded_credentials(project_id: str = "", folder: Path | None = None) -> dict | None:
    """The Desktop client file Google's console just saved to Downloads, by name only.

    The form used to ask for it through a file chooser, after a person had already clicked
    Download a moment before. Its contents stay on disk: what is returned is where it is and
    which project it is for, so the right one can be offered and confirmed.
    """
    folder = folder or Path.home() / "Downloads"
    fresh = []
    with suppress(OSError):
        for path in folder.glob("client_secret*.json"):
            stat = path.stat()
            if (
                path.is_file()
                and not path.is_symlink()
                and stat.st_size <= 20000
                and time.time() - stat.st_mtime <= DOWNLOADED_FOR
            ):
                fresh.append((stat.st_mtime, path))
    found = None
    for _modified, path in sorted(fresh, reverse=True):
        with suppress(OSError):
            client = _desktop_client(path.read_text())
            if client is None:
                continue
            row = {"name": path.name, "path": str(path), "project_id": str(client.get("project_id") or "")}
            if project_id and row["project_id"] == project_id:
                return row
            found = found or row
    return found


class GoogleSetup(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+$")
    credentials: SecretStr = Field(default=SecretStr(""), max_length=20000)
    # A file `downloaded_credentials` found, read here instead of being sent through the page.
    credentials_path: str = Field(default="", max_length=1024)
    # Mail is asked for, never assumed: it is the most private thing in the account.
    services: list[Literal["drive", "docs", "sheets", "calendar", "gmail"]] = Field(
        default_factory=lambda: ["drive", "docs", "sheets", "calendar"], min_length=1
    )
    allow_write: bool = True


def google_clients(root: Path) -> dict[str, str]:
    path = root / "google-accounts.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


async def remove_google_client(root: Path, name: str, email: str):
    cleanup = [
        executable("gog", root),
        "--home",
        str(root / "google"),
        "--client",
        name,
        "--no-input",
        "--force",
    ]
    with suppress(ValueError, OSError):
        await run_cli([*cleanup, "auth", "remove", email])
    with suppress(ValueError, OSError):
        await run_cli([*cleanup, "auth", "credentials", "remove", name])


# Single sign-in per app process. Never expose subprocess output containing credentials.
auth_task: asyncio.Task | None = None
auth_error = ""
auth_lock = asyncio.Lock()


async def start_google(payload: GoogleSetup, root: Path, protected_clients=lambda: set()):
    async with auth_lock:
        await _start_google(payload, root, protected_clients)


async def _start_google(payload: GoogleSetup, root: Path, protected_clients):
    global auth_task, auth_error
    if auth_task and not auth_task.done():
        raise ValueError("A Google sign-in is already in progress.")
    try:
        text = payload.credentials.get_secret_value()
        if payload.credentials_path:
            # Only where this app looks for one itself: a small, real file in Downloads.
            path = Path(payload.credentials_path)
            if (
                path.is_symlink()
                or not path.is_file()
                or path.stat().st_size > 20000
                or path.resolve().parent != (Path.home() / "Downloads").resolve()
            ):
                raise ValueError()
            text = path.read_text()
        client = _desktop_client(text)
        if client is None:
            raise ValueError()
        # Pin endpoints rather than trusting a selected file with arbitrary token URLs.
        clean = {"installed": {key: client[key] for key in ("client_id", "client_secret")}}
        clean["installed"].update(
            auth_uri="https://accounts.google.com/o/oauth2/auth",
            token_uri="https://oauth2.googleapis.com/token",
            redirect_uris=["http://localhost"],
        )
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise ValueError(
            "Choose a Desktop app OAuth credentials JSON file downloaded from Google Cloud."
        ) from error
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = executable("gog", root)
    # A unique credential/token bucket keeps existing accounts intact, including
    # when the user selects a different OAuth client or cancels in the browser.
    client_name = "wavefunction-" + uuid.uuid4().hex
    prefix = [binary, "--home", str(root / "google"), "--client", client_name, "--no-input"]

    auth_error = ""

    async def authorize():
        global auth_error
        published = False
        try:
            with tempfile.TemporaryDirectory(dir=root) as temp:
                path = Path(temp) / "credentials.json"
                private_write(path, json.dumps(clean).encode())
                await run_cli([*prefix, "auth", "credentials", "set", str(path)])
            permissions = [] if payload.allow_write else ["--readonly"]
            # gog's MCP server reads mail and cannot send it, so reading is all that is asked
            # of Google, even when Docs and Sheets are being connected for editing.
            if "gmail" in payload.services:
                permissions += ["--gmail-scope", "readonly"]
            await run_cli(
                [
                    *prefix,
                    "auth",
                    "add",
                    payload.email,
                    "--services",
                    ",".join(payload.services),
                    *permissions,
                    "--timeout",
                    "5m",
                ],
                timeout=310,
            )
            clients = google_clients(root)
            previous_client = clients.get(payload.email)
            clients[payload.email] = client_name
            private_write(root / "google-accounts.json", json.dumps(clients).encode())
            published = True
            # Existing saved connectors still pin their original bucket. Never
            # invalidate those sessions while cleaning up an unused replacement.
            # The gog store is this app's own.
            if (
                previous_client
                and previous_client.startswith("wavefunction-")
                and previous_client not in clients.values()
                and previous_client not in protected_clients()
            ):
                await remove_google_client(root, previous_client, payload.email)
        except (ValueError, OSError):
            auth_error = "Google sign-in did not finish. Check the browser, OAuth consent settings and enabled APIs, then try again."
        finally:
            if not published:
                await remove_google_client(root, client_name, payload.email)

    auth_task = asyncio.create_task(authorize())


def google_status():
    return {"running": bool(auth_task and not auth_task.done()), "error": auth_error}


async def cancel_google():
    global auth_task, auth_error
    if auth_task:
        auth_task.cancel()
        await asyncio.gather(auth_task, return_exceptions=True)
    auth_task = None
    auth_error = ""
