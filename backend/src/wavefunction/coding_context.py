"""Explicit task context and reusable project prompts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field

from . import responses as api
from .hands import file_operation


class FileReference(BaseModel):
    path: str = Field(min_length=1, max_length=4000)
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    version: str = Field(min_length=1)


class TaskContext(BaseModel):
    files: list[FileReference] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=20)
    command: str | None = Field(default=None, max_length=60)


DEFAULT_PROMPTS = [
    {
        "name": "review",
        "description": "Review current code changes",
        "body": "Review the current changes for bugs, regressions, missing tests, and unintended edits. Explain findings with file and line references. $ARGUMENTS",
    },
    {
        "name": "commit",
        "description": "Prepare a commit message",
        "body": "Inspect Git status and staged diffs. Propose a concise commit title and body for the intended changes, noting checks and unrelated work. Do not commit or push until I request it. $ARGUMENTS",
    },
    {
        "name": "pr",
        "description": "Prepare a pull request",
        "body": "Inspect the current branch and its changes from the base branch. Draft a concrete PR title and Markdown body describing the problem, behavior, and actual verification. Identify uncommitted or unrelated work. Do not publish until I request it. $ARGUMENTS",
    },
    {
        "name": "test",
        "description": "Run project checks",
        "body": "Read the repository instructions and project scripts to find and run the relevant tests and checks for this change. Fix failures caused by our work, preserve unrelated edits, and report the actual results. $ARGUMENTS",
    },
]
# Where a message's attached repository excerpts begin; the context indicator counts them as documents.
ATTACHED_CONTEXT = "\n\nAttached repository context: "
_LEGACY_TEST_PROMPT = {
    "name": "test",
    "description": "Run project checks",
    "body": "Run the relevant configured project checks for this change. Fix failures caused by our work, preserve unrelated edits, and report the actual results. $ARGUMENTS",
}


class Context:
    def __init__(self, ws):
        self.ws = ws
        with ws.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS coding_workflows (owner TEXT PRIMARY KEY, content TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

    def owner(self, thread_id):
        thread = self.ws.thread(thread_id)
        return "project:" + thread["project_id"] if thread["project_id"] else "thread:" + thread_id

    def workflow(self, thread_id):
        owner = self.owner(thread_id)
        with self.ws.db() as db:
            row = db.execute("SELECT content FROM coding_workflows WHERE owner = ?", (owner,)).fetchone()
        saved = json.loads(row["content"]) if row else {"prompts": DEFAULT_PROMPTS}
        # Older configurations also contain saved shell checks. Keep their prompts,
        # updating only the unchanged built-in template that referred to those checks.
        content = {
            "prompts": [
                DEFAULT_PROMPTS[-1] if prompt == _LEGACY_TEST_PROMPT else prompt
                for prompt in saved["prompts"]
            ]
        }
        return {
            **content,
            "scope": "project" if owner.startswith("project:") else "conversation",
            "version": hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest(),
        }

    async def capture(self, thread_id, reference, folder):
        if not folder:
            raise ValueError("Choose a repository before attaching file context.")
        root = Path(folder).resolve()
        path = root / reference.path
        if (
            Path(reference.path).is_absolute()
            or not path.resolve().is_relative_to(root)
            or any(p == ".git" for p in Path(reference.path).parts)
            or path.is_symlink()
        ):
            raise ValueError("Attach a regular file inside this repository.")
        # A complete bounded text read; no truncated excerpt masquerades as editable text.
        snapshot = await file_operation(
            self.ws.runner(root), {"op": "review_snapshot", "root": str(root), "paths": [reference.path]}
        )
        content = snapshot["files"].get(reference.path)
        if not content or content["kind"] != "text":
            raise ValueError("Attach a complete text file under 512 KiB, outside private or linked paths.")
        if reference.version != content["version"]:
            raise ValueError(f"{reference.path} changed. Reattach its current lines before sending.")
        lines = content["text"].splitlines()
        end = reference.end_line or len(lines) or 1
        if reference.start_line > end or end > (len(lines) or 1):
            raise ValueError("Choose lines within this file.")
        excerpt = "\n".join(
            f"{n}: {line}"
            for n, line in enumerate(lines[reference.start_line - 1 : end], reference.start_line)
        )
        if len(excerpt) > 60000:
            raise ValueError("Choose a smaller line range (at most 60,000 characters).")
        return {**reference.model_dump(), "end_line": end, "text": excerpt, "folder": str(root)}

    async def expand(self, thread_id, message, context, inventory, *, folder, workflow):
        active = {s["name"]: s for s in inventory["skills"] if s["active"]}
        requested = list(dict.fromkeys(context.skills))
        command = context.command
        arguments = message
        if not command and message.startswith("/"):
            parts = message.split(maxsplit=1)
            first, tail = parts[0], parts[1] if len(parts) > 1 else ""
            if any(p["name"] == first[1:] for p in workflow["prompts"]):
                command, arguments = first[1:], tail
            elif first[1:] in active:
                requested = list(dict.fromkeys([*requested, first[1:]]))
                message = tail or f"Use the {first[1:]} skill for this repository."
        if command:
            template = next((p for p in workflow["prompts"] if p["name"] == command), None)
            if template is None:
                raise ValueError("This prompt command was removed. Reload project commands.")
            message = template["body"].replace("$ARGUMENTS", arguments)
            context.command = command
        files = [await self.capture(thread_id, ref, folder) for ref in context.files]
        if sum(len(f["text"]) for f in files) > 120000:
            raise ValueError("Attached file context exceeds 120,000 characters. Select fewer lines.")
        if any(name not in active for name in requested):
            raise ValueError("A selected skill is unavailable. Refresh skills before sending.")
        if len(requested) > 20:
            raise ValueError("Select at most 20 skills for one task.")
        context.skills = requested
        if requested:
            message += (
                "\n\nExplicitly requested skills: "
                + ", ".join(requested)
                + ". Read and follow their captured instructions with read_skill."
            )
        for f in files:
            message += f"{ATTACHED_CONTEXT}{f['path']} lines {f['start_line']}–{f['end_line']} (version {f['version']}, folder {f['folder']}):\n{f['text']}"
        return message, files


def register_context(router, workspace):
    @router.get("/threads/{thread_id}/context/files", response_model=api.ContextFiles)
    async def files(thread_id: str, query: str = ""):
        ws = workspace()
        root = ws.root(thread_id)
        return await file_operation(
            ws.runner(root), {"op": "context_files", "root": str(root), "query": query}
        )

    @router.get("/threads/{thread_id}/workflow", response_model=api.Workflow)
    def workflow(thread_id: str):
        return workspace().context.workflow(thread_id)
