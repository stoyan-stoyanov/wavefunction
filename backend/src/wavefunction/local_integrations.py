"""Account-specific local email and Google Workspace connectors."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import tomllib
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .connectors import McpServer

Kind = Literal["himalaya", "gog", "microsoft", "gh"]
# The ones that become connectors: gh is the sign-in git and gh use in commands, not a connector.
Connectable = Literal["himalaya", "gog", "microsoft"]


class LocalAccount(BaseModel):
    account: str = Field(min_length=1, max_length=254, pattern=r"^[^\r\n\x00]+$")
    allow_write: bool = True
    mode: Literal["mail", "workspace"] = "workspace"
    services: list[str] | None = Field(default=None, min_length=1)


def executable(kind: Kind, root: Path | None = None) -> str:
    # Finder-launched apps do not inherit an interactive shell's Homebrew PATH.
    managed = str(root / "bin" / kind) if root else None
    if kind == "gh":
        # The user's own gh first, such as Homebrew's, found on their login PATH: it is the one
        # they keep up to date. The managed copy is installed only when there is none.
        from .sandbox import command_path

        candidates = [
            *(str(Path(folder) / kind) for folder in command_path() if Path(folder).is_absolute()),
            managed,
        ]
    else:
        candidates = [
            managed,
            shutil.which(kind),
            f"/opt/homebrew/bin/{kind}",
            f"/usr/local/bin/{kind}",
            str(Path.home() / ".local/bin" / kind),
        ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).absolute())
    raise ValueError(f"Install {kind} first, then check again.")


async def run_cli(
    args: list[str],
    *,
    stdin: bytes | None = None,
    timeout: float = 45,
    on_stdout_line: Callable[[str], None] | None = None,
    on_stderr_line: Callable[[str], None] | None = None,
) -> str:
    """Run a helper to completion and return everything it printed.

    Its output is kept whole: a mailbox listing past a megabyte used to end the call with "the
    response is too large", which cut off exactly the valid results the person had asked for.
    What the model sees of a large result is bounded and paged by the connector layer; the
    helper's timeout is what bounds how much it can produce.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=os.name != "nt",
    )

    async def read(stream, on_line):
        result = bytearray()
        pending = b""
        while chunk := await stream.read(16384):
            result.extend(chunk)
            if on_line:
                pending += chunk
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    on_line(line.decode("utf-8", errors="replace"))
        return result.decode("utf-8", errors="replace")

    async def write():
        if stdin is not None:
            process.stdin.write(stdin)
            await process.stdin.drain()
            process.stdin.close()

    tasks = [
        asyncio.create_task(read(process.stdout, on_stdout_line)),
        asyncio.create_task(read(process.stderr, on_stderr_line)),
        asyncio.create_task(write()),
    ]
    try:
        async with asyncio.timeout(timeout):
            out, err, _ = await asyncio.gather(*tasks)
            await process.wait()
        if process.returncode:
            raise ValueError((err or out or "The local integration failed.").strip()[:1500])
        return out
    except TimeoutError as error:
        raise ValueError(
            "The local integration timed out. Check the account connection and try again."
        ) from error
    finally:
        # A helper may outlive its parent while retaining stdout/stderr. Clean
        # the session even when the main CLI has already exited.
        with suppress(ProcessLookupError):
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def inspect(kind: Kind, root: Path | None = None) -> dict:
    if kind == "microsoft":
        from .microsoft_graph import inspect as inspect_microsoft

        return await inspect_microsoft(root)
    if kind == "gh":
        from .github_cli import inspect as inspect_github

        return await inspect_github(root)
    result = {"installed": False, "version": "", "accounts": [], "error": ""}
    try:
        binary = executable(kind, root) if root else executable(kind)
        result["installed"] = True
        version = (await run_cli([binary, "--version"], timeout=10)).strip()
        result["version"] = version.splitlines()[0]
        if kind == "himalaya":
            major = re.search(r"(?:^|\s)v?(\d+)\.", version)
            if not major or major[1] != "2":
                raise ValueError(
                    "This email connector requires Himalaya 2.x. Update Himalaya and check again."
                )
            try:
                raw = json.loads(await run_cli([binary, "--json", "account", "list"]))
            except ValueError as error:
                try:
                    failure = json.loads(str(error))
                except ValueError:
                    raise error from None
                if not isinstance(failure, dict) or not str(failure.get("error", "")).startswith(
                    "No configuration found."
                ):
                    raise
                # Fresh installs have no global config. The mailbox form creates
                # private account configs below instead of using the CLI wizard.
                raw = {"accounts": []}
            result["accounts"] = [{"id": row["name"], "label": row["name"]} for row in raw["accounts"]]
        else:
            # Feature check: older gog builds do not have a typed MCP server.
            await run_cli([binary, "mcp", "--list-tools"], timeout=10)
            raw = json.loads(await run_cli([binary, "--json", "--no-input", "auth", "list"]))
            result["accounts"] = [
                {
                    "id": row["email"],
                    "label": row["email"],
                    **{key: row[key] for key in ("services", "scopes") if key in row},
                }
                for row in raw.get("accounts", [])
                if row.get("client", "default") == "default"
            ]
    except (ValueError, OSError, KeyError, TypeError) as error:
        result["error"] = str(error)
    if kind == "gog" and root and result["installed"] and (root / "google").is_dir():
        try:
            raw = json.loads(
                await run_cli(
                    [binary, "--home", str(root / "google"), "--json", "--no-input", "auth", "list"]
                )
            )
            from .integration_setup import google_clients

            clients = google_clients(root)
            for row in raw.get("accounts", []):
                if row.get("client", "default") == clients.get(row["email"], "default"):
                    # prepare() uses this managed bucket too, so report its grants
                    # rather than those from another app's same-address account.
                    result["accounts"] = [item for item in result["accounts"] if item["id"] != row["email"]]
                    result["accounts"].append(
                        {
                            "id": row["email"],
                            "label": row["email"],
                            **{key: row[key] for key in ("services", "scopes") if key in row},
                        }
                    )
            if result["accounts"]:
                result["error"] = ""
        except (ValueError, OSError, KeyError, TypeError):
            pass
    if kind == "himalaya" and root and result["installed"]:
        import tomllib

        for path in (root / "mail").glob("*.toml"):
            try:
                for account in tomllib.loads(path.read_text()).get("accounts", {}):
                    if account not in {row["id"] for row in result["accounts"]}:
                        result["accounts"].append({"id": account, "label": account})
            except (ValueError, OSError):
                continue
        if result["accounts"]:
            result["error"] = ""
    return result


async def prepare(kind: Connectable, payload: LocalAccount, root: Path | None = None) -> McpServer:
    if kind == "microsoft":
        from .microsoft_graph import prepare as prepare_microsoft

        return await prepare_microsoft(root, payload.account, payload.mode, payload.allow_write)
    status = await inspect(kind, root) if root else await inspect(kind)
    if status["error"]:
        raise ValueError(status["error"])
    if payload.account not in {row["id"] for row in status["accounts"]}:
        raise ValueError("Configure this account first, then check again.")
    binary = executable(kind, root) if root else executable(kind)
    label = re.sub(r"[^a-z0-9]+", "-", payload.account.lower()).strip("-")[:22]
    suffix = f"{label}-{hashlib.sha256(payload.account.encode()).hexdigest()[:6]}"
    if kind == "himalaya":
        # Listing tools alone cannot prove credentials work. Make a mailbox read
        # before saving the connector, without reading any message body.
        from .integration_setup import mail_path

        config = mail_path(root, payload.account) if root else None
        config_args = ["--config", str(config)] if config and config.is_file() else []
        await run_cli([binary, *config_args, "--account", payload.account, "--json", "mailbox", "list"])
        args = ["-m", "wavefunction.mail_mcp", "--binary", binary, "--account", payload.account]
        if payload.allow_write:
            args.append("--allow-write")
        provider = "other"
        if config_args:
            server = (
                tomllib.loads(config.read_text())["accounts"][payload.account]
                .get("imap", {})
                .get("server", "")
            )
            from urllib.parse import urlsplit

            if urlsplit(server).hostname == "imap.gmail.com":
                provider = "google"
        return McpServer(
            name=f"mail-{suffix}",
            command=sys.executable,
            args=args,
            env={
                "WAVEFUNCTION_LOCAL_INTEGRATION": "himalaya",
                "WAVEFUNCTION_ACCOUNT_PROVIDER": provider,
                **({"HIMALAYA_CONFIG": str(config)} if config_args else {}),
            },
        )
    home_args = []
    selected = payload.services if payload.services is not None else ["drive", "docs", "sheets", "calendar"]
    if not set(selected) <= {"drive", "docs", "sheets", "calendar", "gmail"}:
        raise ValueError("Choose Google Drive, Docs, Sheets, Calendar, or Gmail.")
    client_name = "default"
    if root and (root / "google").is_dir():
        raw = json.loads(
            await run_cli([binary, "--home", str(root / "google"), "--json", "--no-input", "auth", "list"])
        )
        from .integration_setup import google_clients

        managed_client = google_clients(root).get(payload.account, "default")
        if any(
            row.get("email") == payload.account and row.get("client", "default") == managed_client
            for row in raw.get("accounts", [])
        ):
            home_args = ["--home", str(root / "google")]
            client_name = managed_client
    # A real API read detects expired credentials/API configuration failures.
    checks = []
    if set(selected) & {"drive", "docs", "sheets"}:
        checks.append(["drive", "ls", "--max", "1"])
    if "calendar" in selected:
        checks.append(["calendar", "calendars", "--max", "1"])
    if "gmail" in selected:
        checks.append(["gmail", "search", "newer_than:1d", "--max", "1"])
    for check in checks:
        await run_cli(
            [
                binary,
                *home_args,
                "--client",
                client_name,
                "--account",
                payload.account,
                "--no-input",
                "--json",
                *check,
            ]
        )
    args = [*home_args, "--client", client_name, "--account", payload.account, "--no-input"]
    if not payload.allow_write:
        args.append("--readonly")
    if "gmail" in selected:
        # The server has no tool that sends. This holds that line if a later gog adds one.
        args.append("--gmail-no-send")
    args += ["mcp", "--allow-tool", ",".join(selected)]
    if payload.allow_write:
        args.append("--allow-write")
    return McpServer(
        name=f"google-{suffix}", command=binary, args=args, env={"WAVEFUNCTION_LOCAL_INTEGRATION": "gog"}
    )
