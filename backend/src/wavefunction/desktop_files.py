"""Files between an agent's desktop and the person it works for.

Out of the desktop onto the Mac, and from attached files or the Mac into the
desktop's Downloads, where its browser's file dialog can pick them. An agent downloaded someone's
lab reports in its desktop and then had to say it had no way to hand them over.

The two tools come with the desktop tools at run time rather than from the task's tool list, as
`choose_repository_folder` comes with the local tools. On the Mac they reach only the folders the person allowed, as the local tools do, and never a
private folder or the app's own data. A folder not allowed yet is asked for with the folder card,
where someone is there to answer it. The files can be medical or financial: what the tools say
names a file, its size and where it went, and its bytes go nowhere but where they were sent.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import posixpath
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, tool
from langgraph.types import interrupt

from . import computer
from .folder_setup import FOLDER, _covered, _row
from .sandbox import GRANTS_SETTING, load_grants, private_folders
from .toolkit import ToolContext, _desktop


def _where(descriptor: int) -> Path:
    """Where an open file or folder really is, whatever links or letter case led to it."""
    if sys.platform == "darwin":
        found = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
        return Path(os.fsdecode(found.split(b"\0", 1)[0]))
    return Path(os.readlink(f"/proc/self/fd/{descriptor}"))


def _grants(context: ToolContext) -> list[Any]:
    return load_grants(context.repository.get_setting(GRANTS_SETTING, []))


def _not_private(path: Path) -> Path:
    if any(path == folder or folder in path.parents for folder in private_folders()):
        raise ValueError(
            f"{path} is private to this Mac — keys, credentials or the app's own "
            "data — so no file is moved into or out of it."
        )
    return path


def _fits(name: str, size: int) -> None:
    if size > computer.MAX_TRANSFER_BYTES:
        raise ValueError(
            f"{name} ({computer.describe_size(size)}) is more than the "
            f"{computer.describe_size(computer.MAX_TRANSFER_BYTES)} a transfer can carry."
        )


def _allow(context: ToolContext, folder: Path, writable: bool) -> Path:
    """The folder to go on in: `folder` once allowed, asking with the folder card where someone
    can answer, or the folder they picked on the card instead of it.

    The only pause in either tool, so a resumed call never hands one card's answer to another.
    """
    if _covered(str(folder), writable, _grants(context)):
        return folder
    what = "save files to" if writable else "read files in"
    if not context.choose_folder:
        raise ValueError(
            f"The user has not allowed agents to {what} {folder}, and this task runs on "
            "its own, so nobody can be asked now. Tell them which folder it needs."
        )
    answer = interrupt(
        {
            "kind": "connector_setup",
            "connectors": [_row(FOLDER, "Folder access", path=str(folder), writable=writable)],
        }
    )
    # The folder picked answers the card, whichever it named. One card can answer for several
    # requests, so the folder itself is checked, not the answer.
    picked = answer.get("folder") if isinstance(answer, dict) else None
    chosen = Path(picked) if picked else folder
    if not _covered(str(chosen), writable, _grants(context)):
        raise ValueError(f"{chosen} was not allowed, so nothing was copied.")
    return chosen


def _read_mac(context: ToolContext, path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        raise ValueError(f"There is no file at {path} on this Mac.") from None
    except OSError as error:
        raise ValueError(f"Could not open {path}: {error.strerror}.") from None
    with os.fdopen(descriptor, "rb") as source:
        # What was opened is checked, not what was named: a link swapped in meanwhile leads nowhere.
        real = _not_private(_where(descriptor))
        if not _covered(str(real), False, _grants(context)):
            raise ValueError(f"{real} is not in a folder the user allowed.")
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{real} is not a file. Add files one at a time.")
        _fits(real.name, info.st_size)
        data = source.read(computer.MAX_TRANSFER_BYTES + 1)
    _fits(real.name, len(data))
    return data


def _write_mac(context: ToolContext, folder: Path, name: str, data: bytes) -> Path:
    """Write a new file in `folder`, never over one: "Report (1).pdf" when "Report.pdf" is taken."""
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        # The folder as opened is what is checked, and the file is made inside that very folder.
        real = _not_private(_where(directory))
        if not _covered(str(real), True, _grants(context)):
            raise ValueError(f"{real} is not a folder the user allowed agents to save to.")
        stem, extension = os.path.splitext(name)
        for number in range(1000):
            candidate = f"{stem} ({number}){extension}" if number else name
            try:
                descriptor = os.open(
                    candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=directory
                )
                break
            except FileExistsError:
                continue
        else:
            raise ValueError(f"{real} has no free name left for {name}.")
        try:
            with os.fdopen(descriptor, "wb") as sink:
                sink.write(data)
        except BaseException:
            os.unlink(candidate, dir_fd=directory)
            raise
        return real / candidate
    finally:
        os.close(directory)


def _plain_name(name: str) -> str:
    """A file name without line breaks or other control characters in it."""
    cleaned = "".join("_" if unicodedata.category(c) == "Cc" else c for c in name).strip()
    return cleaned if cleaned not in {"", ".", ".."} else "download"


def _desktop_path(raw: str) -> str:
    """A path in the desktop, from what the model gave: a bare name is in ~/Downloads."""
    home = computer.DESKTOP_HOME
    text = raw.strip()
    if not text:
        raise ValueError("Name the file in the desktop, such as Report.pdf.")
    if text == "~" or text.startswith("~/"):
        text = home + text[1:]
    elif not text.startswith("/"):
        text = f"{home}/Downloads/{text}"
    path = posixpath.normpath(text)
    if not path.startswith(home + "/"):
        raise ValueError(f"{raw.strip()} is outside the desktop's home folder, {home}.")
    return path


def _mac_path(raw: str, what: str) -> Path:
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError(f"Give the {what} on the Mac by its full path, such as ~/Downloads, not {raw!r}.")
    return _not_private(path.resolve())


async def _desktop_with_files(context: ToolContext) -> Any:
    """The desktop to save from: this conversation's, or a new one when this copy of the agent
    keeps its desktop's home, Downloads and all. A blank new desktop would have nothing in it."""
    if context.computer is None:
        raise ValueError("Computer use is not available in this build, so this tool cannot run.")
    session = context.computer.running(context.conversation_id or context.agent_id, context.desktop_copy)
    if session is not None:
        return session
    if context.desktop_copy:
        return await _desktop(context)
    raise ValueError(
        "The desktop is not open in this conversation, so there is nothing in it "
        "to save. A desktop's files go when it closes."
    )


def _reference_file(context: ToolContext, wanted: str) -> tuple[str, bytes]:
    """Read an attached file captured by this task, using its displayed name."""
    allowed = {row["id"] for row in context.reference_files or []}
    for record in context.repository.list_project_files(context.agent_id):
        if record["id"] not in allowed:
            continue
        if record["name"].casefold() == wanted.casefold():
            path = context.repository.package_path(context.agent_id) / record["relative_path"]
            _fits(record["name"], path.stat().st_size)
            return record["name"], path.read_bytes()
    raise ValueError(
        f"There is no document named {wanted!r} here. For a file on the Mac, give "
        "its full path, such as ~/Documents/cv.pdf."
    )


def build_desktop_file_tools(context: ToolContext) -> list[BaseTool]:
    """The file tools for an agent with the desktop tools. Only a task someone started stops on
    the folder card (`choose_folder`); any other says which folder it needs."""
    save_description = (
        "Copy a file out of the desktop into a folder on the user's Mac. "
        "Use it to hand over what was downloaded there: the desktop's files cannot be reached "
        "from the Mac. The user is asked to allow a Mac folder they have not allowed yet. Nothing "
        'is replaced: a name already taken gets a number, as in "Report (1).pdf", and the result '
        "says which.\n\n"
        "Args:\n"
        '    path: The file in the desktop: a name in ~/Downloads, such as "Report.pdf", or a path '
        "under /home/desktop.\n"
        "    destination: A folder on the user's Mac. Empty is ~/Downloads.\n"
        "    folder: Leave empty; use destination for the Mac folder."
    )
    add_description = (
        "Copy a file into the desktop's ~/Downloads, where the desktop browser's file dialog can "
        "pick it to attach or upload. The user is asked to allow a Mac folder they have not allowed "
        "yet.\n\n"
        "Args:\n"
        "    source: An attached document by the name list_documents gives, or a file on the "
        "user's Mac by its full path, such as ~/Documents/cv.pdf."
    )

    @tool("computer_save_file", description=save_description, response_format="content_and_artifact")
    async def computer_save_file(path: str, destination: str = "", folder: str = "") -> tuple[str, dict]:
        source = _desktop_path(path)
        name = _plain_name(posixpath.basename(source))
        if folder.strip():
            raise ValueError("Give the Mac folder as `destination` and leave `folder` empty.")
        if context.local_read_only:
            raise ValueError("Saving files is disabled for this task.")
        target = _mac_path(destination.strip() or "~/Downloads", "folder")
        if not target.is_dir():
            raise ValueError(f"There is no folder at {target} on this Mac.")
        session = await _desktop_with_files(context)
        data = await context.computer.read_file(session, source)
        chosen = _allow(context, target, writable=True)
        written = await asyncio.to_thread(_write_mac, context, chosen, name, data)
        renamed = (
            f" {written.parent} already had a file named {name}, so this one is {written.name}."
            if written.name != name
            else ""
        )
        instead = (
            f" The user chose {chosen} instead of {target}, so save their other files there too."
            if chosen != target
            else ""
        )
        return (
            f"Saved {name} ({computer.describe_size(len(data))}) from the desktop to {written} "
            f"on this Mac.{renamed}{instead}"
        ), {}

    @tool("computer_add_file", description=add_description)
    async def computer_add_file(source: str) -> str:
        wanted = source.strip()
        if not wanted:
            raise ValueError("Name the document, or give the full path of a file on the Mac.")
        if wanted.startswith(("/", "~")):
            path = _mac_path(wanted, "file")
            try:
                info = path.stat()
            except (FileNotFoundError, NotADirectoryError):
                raise ValueError(f"There is no file at {path} on this Mac.") from None
            except OSError:
                info = None  # The Mac may keep a folder closed until it is allowed.
            if info is not None and not stat.S_ISREG(info.st_mode):
                raise ValueError(f"{path} is not a file. Add files one at a time.")
            session = await _desktop(context)
            # The folder picked on the card answers it: the file is looked for there.
            path = _allow(context, path.parent, writable=False) / path.name
            name, data = path.name, await asyncio.to_thread(_read_mac, context, path)
        else:
            name, data = await asyncio.to_thread(_reference_file, context, wanted)
            session = await _desktop(context)
        got = await context.computer.add_file(session, name, data)
        renamed = (
            f" Its Downloads already had a file named {name}, so this one is {got}." if got != name else ""
        )
        return (
            f"Added {name} ({computer.describe_size(len(data))}) to the desktop as "
            f"{computer.DESKTOP_HOME}/Downloads/{got}.{renamed} Choose it there in the "
            "browser's file dialog."
        )

    return [computer_save_file, computer_add_file]
