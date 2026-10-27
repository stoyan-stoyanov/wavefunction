"""An email's attachments, kept where the person wants them.

The mail tools could search and read a message but not download what came with it, so an agent
asked for the attachments in someone's mail opened their webmail in its desktop's browser and
asked them to sign in there. This saves a message's attachments straight into a folder on the Mac with the folder card, as `computer_save_file` does with a
desktop's downloads. As there, what it says names a file, its size and where it went.

Attachments are picked by file name: `read_mail` numbers them from the message's parts, and
Himalaya's download from one further on, so a number from one would fetch the next file. They
are listed before any is fetched, so one not asked for, or too big to keep, is never downloaded.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool

from . import computer
from .desktop_files import _allow, _fits, _mac_path, _plain_name, _write_mac
from .local_integrations import run_cli
from .toolkit import ToolContext


def mail_servers(registry: Any, tool_names: list[str]) -> list[Any]:
    """The Himalaya mailboxes, Gmail and other IMAP accounts, whose tools an agent has."""
    return [
        server
        for server in (registry.servers() if registry else [])
        if server.env.get("WAVEFUNCTION_LOCAL_INTEGRATION") == "himalaya"
        and {"--binary", "--account"} <= set(server.args)
        and any(name.startswith(f"{server.name}__") for name in tool_names)
    ]


def _argument(server: Any, flag: str) -> str:
    return server.args[server.args.index(flag) + 1]


async def _himalaya(server: Any, *args: str, timeout: float = 45) -> dict:
    config = server.env.get("HIMALAYA_CONFIG")
    return json.loads(
        await run_cli(
            [
                _argument(server, "--binary"),
                *(["--config", config] if config else []),
                "--account",
                _argument(server, "--account"),
                "--json",
                "--log-level",
                "off",
                *args,
            ],
            timeout=timeout,
        )
    )


async def _download(
    server: Any, message_id: str, mailbox: str, ids: list[str], into: Path
) -> list[tuple[str, Path]]:
    """These attachments of a message, as (the name it came with, the file it was saved as)."""
    out = await _himalaya(
        server,
        "attachment",
        "download",
        "--mailbox",
        mailbox,
        "--dir",
        str(into),
        "--",
        message_id,
        *ids,
        timeout=120,
    )
    saved = []
    for row in out.get("attachments", []):
        path = Path(row.get("path") or "")
        path = path if path.is_absolute() else into / path
        # Only a file in the folder itself: a sender picks the name, and "../x" must lead nowhere.
        if path.parent.resolve() == into.resolve() and path.is_file() and not path.is_symlink():
            saved.append((str(row.get("filename") or path.name), path))
    return saved


def build_mail_attachment_tools(context: ToolContext, servers: list[Any]) -> list[BaseTool]:
    """`save_mail_attachments` for an agent with Himalaya mailboxes. Only a task someone started
    stops on the folder card; any other says which folder it needs."""
    accounts = {_argument(server, "--account"): server.name for server in servers}
    # What each mailbox's connection was when this run began. As the connector's own tools do, a
    # mailbox turned off, removed or set up again since then is not read for this run.
    began = {
        server.name: context.connectors.cached(server.name) if context.connectors else None
        for server in servers
    }

    def connected(name: str) -> Any:
        server = (
            next((server for server in context.connectors.servers() if server.name == name), None)
            if context.connectors
            else None
        )
        if server is None or not server.enabled or context.connectors.cached(name) is not began[name]:
            raise ValueError("This mailbox was removed, disabled, or reconnected. Start a new run to use it.")
        return server

    description = (
        "Save an email's attachments into a folder on the user's Mac. "
        "Use it for what came with a message; never open webmail in the desktop for this. "
        "The user is asked to allow a Mac folder they have not allowed yet. Nothing is replaced: "
        "a name already taken gets a number, and the result says which.\n\n"
        "Args:\n"
        "    message_id: The message, from search_mail or read_mail.\n"
        "    mailbox: The mailbox that message_id belongs to.\n"
        "    names: Only these attachments, by the file names read_mail lists. Empty saves them all.\n"
        "    destination: A folder on the user's Mac. Empty is ~/Downloads.\n"
        "    folder: Leave empty; use destination for the Mac folder.\n"
        + (
            "    account: The mailbox's address: " + " or ".join(sorted(accounts)) + ".\n"
            if len(accounts) > 1
            else "    account: Leave empty.\n"
        )
    )

    @tool("save_mail_attachments", description=description, response_format="content_and_artifact")
    async def save_mail_attachments(
        message_id: str,
        mailbox: str,
        names: list[str] | None = None,
        destination: str = "",
        folder: str = "",
        account: str = "",
    ) -> tuple[str, dict]:
        if folder.strip():
            raise ValueError("Give the Mac folder as `destination` and leave `folder` empty.")
        connector = accounts.get(account.strip()) or (
            next(iter(accounts.values())) if len(accounts) == 1 else None
        )
        if connector is None:
            raise ValueError(f"Say which mailbox with `account`: {' or '.join(sorted(accounts))}.")
        server = connected(connector)
        if context.local_read_only:
            raise ValueError("Saving files is disabled for this task.")
        target = _mac_path(destination.strip() or "~/Downloads", "folder")
        if not target.is_dir():
            raise ValueError(f"There is no folder at {target} on this Mac.")
        # Ask before downloading: a paused call starts again from its first line.
        target = _allow(context, target, writable=True)
        wanted = {name.strip().casefold() for name in names or [] if name.strip()}
        message_id, mailbox = message_id.strip(), mailbox.strip()
        listed = (await _himalaya(server, "attachment", "list", "--mailbox", mailbox, "--", message_id)).get(
            "attachments", []
        )
        chosen = [
            row
            for row in listed
            if not wanted
            or {str(row.get("filename", "")).casefold(), Path(str(row.get("filename", ""))).name.casefold()}
            & wanted
        ]
        if not chosen:
            return (
                (
                    f"None of the attachments is called {', '.join(sorted(names or []))}; it has "
                    f"{', '.join(str(row.get('filename')) for row in listed)}."
                    if listed and wanted
                    else "That message has no attachments."
                ),
                {},
            )
        said, fetch, too_big = [], [], []
        for row in chosen:
            try:
                _fits(str(row.get("filename")), int(row.get("size") or 0))
                fetch.append(str(row["id"]))
            except ValueError as error:
                too_big.append(f"{row.get('filename')} was not saved: {error}")
        with tempfile.TemporaryDirectory(prefix="wavefunction-mail-") as temporary:
            found = await _download(server, message_id, mailbox, fetch, Path(temporary)) if fetch else []
            for original, path in found:
                name = _plain_name(path.name)
                try:
                    _fits(name, path.stat().st_size)
                    data = path.read_bytes()
                    written = await asyncio.to_thread(_write_mac, context, target, name, data)
                    renamed = (
                        f" {written.parent} already had a file named {name}, so this one is {written.name}."
                        if written.name != name
                        else ""
                    )
                    said.append(
                        f"Saved {name} ({computer.describe_size(len(data))}) from the email "
                        f"to {written} on this Mac.{renamed}"
                    )
                except ValueError as error:
                    said.append(f"{original} was not saved: {error}")
        return " ".join([*said, *too_big]), {}

    return [save_mail_attachments]
