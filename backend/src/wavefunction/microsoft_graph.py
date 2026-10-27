"""Local Microsoft Graph access through Microsoft's official PowerShell SDK."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import sys
import tarfile
import tempfile
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from .connectors import McpServer, SignInRequired
from .integration_setup import private_write
from .local_integrations import run_cli

Mode = Literal["mail", "workspace"]
GRAPH = "https://graph.microsoft.com/v1.0/"
PWSH_VERSION = "7.6.6"
MODULE_VERSION = "2.39.0"
PWSH_HASHES = {
    "arm64": "6df833d094ebac1c1a74340d7b3437f4aaf5e03ce640484a1c4359f3ce8b3db1",
    "x64": "e325ed9f666894eb39a5ea52800b602da2fb4242bbe9747ceddb39cdc66de805",
}
# Verified against PowerShell Gallery's published SHA512 PackageHash.
MODULE_HASH = "2b91eed8dbb28875f0e7884ebf4e087d44fb49722fd730f7ed1fc1d873b90d62"
ERRORS = {
    "sign_in_required": "Microsoft sign-in expired or was revoked. Sign in again to continue.",
    "approval_required": "Microsoft requires permission or administrator approval. Ask your organization to approve Microsoft Graph Command Line Tools, then sign in again.",
    "access_denied": "Microsoft denied access to this resource. Check the account, requested permissions, and your organization’s access policy.",
    "account_mismatch": "Microsoft returned a different account. Sign in to the account this agent is using.",
    "cache_unavailable": "Microsoft could not save sign-in securely on this Mac. Check Keychain access and try again.",
    "not_found": "Microsoft could not find this resource. Check its ID and the connected account.",
    "throttled": "Microsoft is limiting requests. Wait briefly before trying again.",
    "request_failed": "Microsoft could not complete the request. Check your connection and the requested resource. A failed write may already have reached Microsoft; check its result before retrying.",
}


class MicrosoftSetup(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+$")
    account_type: Literal["personal", "work"] = "personal"
    mode: Mode = "workspace"
    allow_write: bool = True
    services: list[Literal["mail", "files", "calendar"]] | None = Field(default=None, min_length=1)


def selected_services(mode: Mode, services: list[str] | None = None) -> list[str]:
    return (
        ["mail"] if mode == "mail" else (services if services is not None else ["mail", "files", "calendar"])
    )


def scopes(mode: Mode, allow_write: bool, account_type: str, services: list[str] | None = None) -> list[str]:
    selected = selected_services(mode, services)
    result = ["User.Read"]
    if "mail" in selected:
        result.append("Mail.ReadWrite" if allow_write else "Mail.Read")
        if allow_write:
            result.append("Mail.Send")
    if "calendar" in selected:
        result.append("Calendars.ReadWrite" if allow_write else "Calendars.Read")
    if "files" in selected:
        result.append("Files.ReadWrite" if allow_write else "Files.Read")
        if account_type == "work":
            result.append("Sites.ReadWrite.All" if allow_write else "Sites.Read.All")
    return result


def runtime_dir(root: Path) -> Path:
    return root / "microsoft" / f"pwsh-{PWSH_VERSION}-graph-{MODULE_VERSION}"


def account_path(root: Path, email: str, mode: Mode) -> Path:
    return (
        root
        / "microsoft"
        / "accounts"
        / f"{mode}-{hashlib.sha256(email.casefold().encode()).hexdigest()}.json"
    )


def read_account(root: Path, email: str, mode: Mode) -> dict:
    try:
        return json.loads(account_path(root, email, mode).read_text())
    except (OSError, ValueError):
        raise SignInRequired("Connect this Microsoft account to continue.") from None


async def install(root: Path):
    arch = {"arm64": "arm64", "x86_64": "x64"}.get(platform.machine())
    if platform.system() != "Darwin" or not arch:
        raise ValueError("Microsoft integration installation supports Intel and Apple silicon Macs.")
    target = runtime_dir(root)
    if (target / "pwsh" / "pwsh").is_file() and (
        target / "graph" / "Microsoft.Graph.Authentication.psd1"
    ).is_file():
        return

    async def download(url, digest, destination: Path):
        # Streamed to disk with a running digest: the archive never sits in memory, and there
        # is no size at which a genuine release is refused — only a wrong digest is.
        digester = hashlib.sha256()
        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=120) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
                    digester.update(chunk)
        if digester.hexdigest() != digest:
            raise ValueError("Microsoft support download verification failed.")

    with tempfile.TemporaryDirectory(prefix="wavefunction-microsoft-") as downloads:
        runtime_archive = Path(downloads) / "powershell.tar.gz"
        module_archive = Path(downloads) / "Microsoft.Graph.Authentication.zip"
        await asyncio.gather(
            download(
                f"https://github.com/PowerShell/PowerShell/releases/download/v{PWSH_VERSION}/powershell-{PWSH_VERSION}-osx-{arch}.tar.gz",
                PWSH_HASHES[arch],
                runtime_archive,
            ),
            download(
                f"https://www.powershellgallery.com/api/v2/package/Microsoft.Graph.Authentication/{MODULE_VERSION}",
                MODULE_HASH,
                module_archive,
            ),
        )

        def publish():
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.TemporaryDirectory(dir=target.parent) as directory:
                stage = Path(directory) / "runtime"
                (stage / "pwsh").mkdir(parents=True)
                with tarfile.open(runtime_archive) as archive:
                    archive.extractall(stage / "pwsh", filter="data")
                with zipfile.ZipFile(module_archive) as archive:
                    for entry in archive.infolist():
                        if Path(entry.filename).is_absolute() or ".." in Path(entry.filename).parts:
                            raise ValueError("Invalid Microsoft module archive.")
                    archive.extractall(stage / "graph")
                (stage / "pwsh" / "pwsh").chmod(0o700)
                if not (stage / "graph" / "Microsoft.Graph.Authentication.psd1").is_file():
                    raise ValueError("Microsoft module is incomplete.")
                # Another install may have finished while these verified downloads ran.
                if not target.exists():
                    os.rename(stage, target)

        await asyncio.to_thread(publish)


async def inspect(root: Path) -> dict:
    installed = (runtime_dir(root) / "pwsh" / "pwsh").is_file()
    accounts = []
    for path in (root / "microsoft" / "accounts").glob("*.json"):
        try:
            row = json.loads(path.read_text())
            accounts.append({key: row[key] for key in ("email", "mode", "account_type", "allow_write")})
        except (ValueError, OSError, KeyError):
            continue
    return {
        "installed": installed,
        "version": f"Microsoft Graph {MODULE_VERSION}",
        "accounts": [{"id": row["email"], "label": row["email"], **row} for row in accounts],
        "error": "",
    }


async def invoke(root: Path, payload: dict, *, timeout: float = 60, on_device_code=None) -> dict:
    directory = runtime_dir(root)

    def progress(line):
        if on_device_code and line.startswith("WAVEFUNCTION_GRAPH:"):
            row = json.loads(line.removeprefix("WAVEFUNCTION_GRAPH:"))
            if row.get("type") == "device_code":
                on_device_code(row["code"])

    try:
        raw = await run_cli(
            [
                str(directory / "pwsh" / "pwsh"),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(Path(__file__).with_suffix(".ps1")),
                "-ModulePath",
                str(directory / "graph" / "Microsoft.Graph.Authentication.psd1"),
            ],
            stdin=json.dumps(payload).encode(),
            timeout=timeout,
            on_stdout_line=progress,
        )
    except ValueError as error:
        raw = str(error)
    except OSError:
        raise ValueError("Install Microsoft support to connect this account.") from None
    rows = [
        json.loads(line.removeprefix("WAVEFUNCTION_GRAPH:"))
        for line in raw.splitlines()
        if line.startswith("WAVEFUNCTION_GRAPH:")
    ]
    if not rows:
        raise ValueError("Microsoft did not finish the request. Check your connection and try again.")
    result = rows[-1]
    if result.get("type") == "error":
        code = result.get("error", "request_failed")
        if payload.get("operation") == "reuse_sign_in" and code == "account_mismatch":
            raise ValueError(ERRORS[code])
        if code in ("sign_in_required", "account_mismatch") or (
            payload.get("operation") == "reuse_sign_in" and code == "approval_required"
        ):
            raise SignInRequired(ERRORS[code])
        raise ValueError(ERRORS.get(code, ERRORS["request_failed"]))
    return result


async def request(root: Path, email: str, mode: Mode, method: str, path: str, **payload):
    saved = read_account(root, email, mode)
    if method != "GET" and not saved["allow_write"]:
        raise ValueError("This Microsoft connection has read-only access.")
    if not path.startswith(GRAPH):
        path = GRAPH + path.lstrip("/")
    result = await invoke(
        root,
        {
            "operation": "request",
            "account_path": str(account_path(root, email, mode)),
            "uri": path,
            "method": method,
            **payload,
        },
    )
    return result.get("data")


class MicrosoftSignIn:
    def __init__(self, root: Path):
        self.root = root
        self.task: asyncio.Task | None = None
        self.attempt = ""
        self.result: dict = {}

    def start(self, payload: MicrosoftSetup) -> dict:
        if self.task and not self.task.done():
            raise ValueError("A Microsoft sign-in is already in progress. Finish or cancel it first.")
        self.attempt = uuid.uuid4().hex
        self.result = {"running": True, "account": "", "error": ""}
        self.task = asyncio.create_task(self._sign_in(payload))
        return {"id": self.attempt}

    def status(self, attempt: str) -> dict:
        if attempt != self.attempt:
            raise KeyError(attempt)
        return dict(self.result)

    async def cancel(self, attempt: str | None = None):
        if attempt is not None and attempt != self.attempt:
            raise KeyError(attempt)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.result = {"running": False, "account": "", "error": "Microsoft sign-in was cancelled."}

    async def _sign_in(self, payload: MicrosoftSetup):
        email = payload.email.strip().casefold()
        selected_scopes = scopes(payload.mode, payload.allow_write, payload.account_type, payload.services)
        try:
            # Outlook and Microsoft 365 use the same SDK cache. Reuse the saved
            # identity with the requested scopes; only interactive setup may ask
            # Microsoft for additional consent or a renewed sign-in.
            saved_accounts = []
            for mode in (payload.mode, "workspace" if payload.mode == "mail" else "mail"):
                path = account_path(self.root, email, mode)
                if path.is_file():
                    saved_accounts.append((path, json.loads(path.read_text())))
            source = next(
                (path for path, saved in saved_accounts if saved["account_type"] == payload.account_type),
                None,
            )
            result = None
            if source:
                with suppress(SignInRequired):
                    result = await invoke(
                        self.root,
                        {
                            "operation": "reuse_sign_in",
                            "account_path": str(source),
                            "scopes": selected_scopes,
                        },
                    )
            if result is None:
                result = await invoke(
                    self.root,
                    {
                        "operation": "sign_in",
                        "email": email,
                        "scopes": selected_scopes,
                        "tenant": "consumers" if payload.account_type == "personal" else "organizations",
                    },
                    timeout=310,
                    on_device_code=lambda code: self.result.update(device_code=code),
                )
            profile = result["profile"]
            record = json.loads(result["record"])
            identities = {str(profile.get(key, "")).casefold() for key in ("mail", "userPrincipalName")}
            identities.add(str(record.get("username", "")).casefold())
            if email not in identities:
                raise ValueError(f"You signed in to a different account. Choose {email} to continue.")
            if not profile.get("id") or not record.get("homeAccountId"):
                raise ValueError("Microsoft did not return a reusable sign-in. Try again.")
            destination = account_path(self.root, email, payload.mode)
            if any(saved.get("user_id") != profile["id"] for _, saved in saved_accounts):
                raise ValueError(
                    "This is a different Microsoft account. Reconnect with the original account."
                )
            data = {
                **payload.model_dump(),
                "email": email,
                "scopes": selected_scopes,
                "user_id": profile["id"],
                "record": result["record"],
            }
            private_write(destination, json.dumps(data).encode())
            self.result["account"] = email
        except (ValueError, SignInRequired) as error:
            self.result["error"] = str(error)
        except Exception:
            self.result["error"] = (
                "Microsoft sign-in could not be completed. Check your account and try again."
            )
        finally:
            self.result["running"] = False


async def prepare(root: Path, email: str, mode: Mode, allow_write: bool) -> McpServer:
    saved = read_account(root, email, mode)
    if allow_write and not saved["allow_write"]:
        raise ValueError("Sign in again with permission to make changes, or choose read-only access.")
    await request(root, email, mode, "GET", "me?$select=id")
    suffix = hashlib.sha256(email.casefold().encode()).hexdigest()[:12]
    return McpServer(
        name=f"microsoft-{mode}-{suffix}",
        command=sys.executable,
        args=[
            "-m",
            "wavefunction.microsoft_mcp",
            "--root",
            str(root),
            "--account",
            email,
            "--mode",
            mode,
            "--services",
            ",".join(selected_services(mode, saved.get("services"))),
            *(["--allow-write"] if allow_write else []),
        ],
        env={"WAVEFUNCTION_LOCAL_INTEGRATION": "microsoft"},
    )


async def verify_server(server: McpServer):
    args = server.args
    await request(
        Path(args[args.index("--root") + 1]),
        args[args.index("--account") + 1],
        args[args.index("--mode") + 1],
        "GET",
        "me?$select=id",
    )
