"""Preview and open files the agent links in a chat, limited to that chat's folders."""

from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import Literal

from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import responses as api
from .sandbox import private_folders

# The webview buffers the whole file before showing it.
MAX_PREVIEW_BYTES = 25 * 1024 * 1024
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
TABLE_TYPES = {".csv", ".tsv"}
# Opening these in their default app runs them, so they are only revealed in Finder.
# (Bundles such as .app are folders, which are never previewed or opened.)
# Scripts too: an interpreter such as Python Launcher can be their default app.
RUNNABLE_TYPES = {
    ".command",
    ".tool",
    ".terminal",
    ".scpt",
    ".applescript",
    ".pkg",
    ".mpkg",
    ".jar",
    ".shortcut",
    ".webloc",
    ".inetloc",
    ".fileloc",
    ".py",
    ".pyw",
    ".sh",
    ".bash",
    ".zsh",
    ".csh",
    ".ksh",
    ".fish",
    ".pl",
    ".rb",
    ".php",
    ".js",
    ".mjs",
    ".swift",
    ".osascript",
}


class OpenFile(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    target: Literal["finder", "app"]


def link_path(link: str) -> Path:
    """The path a chat link names: absolute, or relative to the chat's folder."""
    return Path(link).expanduser()


def kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in IMAGE_TYPES:
        return "image"
    if suffix in TABLE_TYPES:
        return "table"
    with path.open("rb") as handle:
        sample = handle.read(8192)
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as error:
        # A sample can end inside a multibyte character.
        if error.start < len(sample) - 3:
            return "other"
    return "other" if b"\0" in sample else "text"


def register_file_previews(router, workspace):
    def resolve(thread_id: str, link: str) -> Path:
        ws = workspace()
        thread = ws.thread(thread_id)
        roots = [grant.path for grant in ws.services.grants()]
        folder = ws.effective_folder(thread)
        if folder:
            roots.insert(0, ws.root(thread_id))
        path = link_path(link)
        if not path.is_absolute():
            if not folder:
                raise ValueError("Choose a repository folder to open files from this chat.")
            path = roots[0] / path
        resolved = path.resolve()
        if (
            not any(resolved.is_relative_to(Path(root).resolve()) for root in roots)
            or ".git" in resolved.parts
            or any(resolved.is_relative_to(private) for private in private_folders())
        ):
            raise ValueError("Only files in this chat's folders can be opened.")
        if not resolved.is_file():
            raise KeyError("File not found")
        return resolved

    @router.get("/threads/{thread_id}/preview", response_model=api.FilePreview)
    async def preview(thread_id: str, path: str):
        target = resolve(thread_id, path)
        size = target.stat().st_size
        found = await asyncio.to_thread(kind, target)
        return {
            "path": str(target),
            "name": target.name,
            "kind": found,
            "size": size,
            "previewable": size <= MAX_PREVIEW_BYTES,
            "openable": target.suffix.lower() not in RUNNABLE_TYPES and not os.access(target, os.X_OK),
        }

    @router.get("/threads/{thread_id}/preview/content")
    async def preview_content(thread_id: str, path: str):
        target = resolve(thread_id, path)
        if target.stat().st_size > MAX_PREVIEW_BYTES:
            raise ValueError("This file is too large to preview.")
        media_type = (
            "text/plain; charset=utf-8"
            if target.suffix.lower() in TABLE_TYPES
            else (mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        )
        # SVG is shown as an <img>, which never runs its scripts.
        return FileResponse(target, media_type=media_type, headers={"Content-Security-Policy": "sandbox"})

    @router.post("/threads/{thread_id}/preview/open", status_code=204)
    async def open_file(thread_id: str, payload: OpenFile):
        target = resolve(thread_id, payload.path)
        # Swapped for a link since it was checked: open only the checked file itself.
        if target.is_symlink():
            raise ValueError("The file changed. Open its link again.")
        if payload.target == "app" and (
            target.suffix.lower() in RUNNABLE_TYPES or os.access(target, os.X_OK)
        ):
            raise ValueError("Wavefunction won't run this file. Show it in Finder to open it yourself.")
        # By absolute path: an `open` placed earlier on PATH must never be what runs.
        command = (
            ["/usr/bin/open", "-R", str(target)]
            if payload.target == "finder"
            else ["/usr/bin/open", "--", str(target)]
        )
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, error = await process.communicate()
        if process.returncode:
            raise ValueError(error.decode(errors="replace").strip() or "The file could not be opened.")
