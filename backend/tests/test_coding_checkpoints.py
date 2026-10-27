"""Conversation/code restore workflows against real isolated Git repositories."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from support import create, git, repository

from wavefunction.coding_workspace import CodingWorkspace
from wavefunction.toolkit import TOOL_REGISTRY

# Rewind and its tasks run real Git removals in temporary repositories; CI runs these, never a local check.
pytestmark = pytest.mark.destructive


def edits(before, after, answer):
    return [
        tool_call_message("read_file", {"path": "a.txt"}, "read-" + after),
        tool_call_message(
            "edit_file", {"path": "a.txt", "old_text": before, "new_text": after}, "edit-" + after
        ),
        AIMessage(content=answer),
    ]


def boundary(ws, thread_id, run_id, kind):
    run = next(run for run in ws.runs(thread_id) if run["id"] == run_id)
    return next(point for point in run["checkpoints"] if point["kind"] == kind)


@pytest.mark.sandbox
async def test_rewind_restores_history_code_and_draft_preserving_the_later_continuation(coding, tmp_path):
    client, ws, services = coding
    folder = repository(tmp_path)
    (folder / "a.txt").write_bytes(b"before\r\n")
    (folder / "a.txt").chmod(0o755)
    thread = await create(client, folder=str(folder))
    base = f"/api/coding/threads/{thread['id']}"
    uploaded = (
        await client.post(
            base + "/attachments", files={"file": ("context.txt", b"Historical requirement", "text/plain")}
        )
    ).json()
    file = (await client.get(base + "/file", params={"path": "a.txt"})).json()
    context = {"files": [{"path": "a.txt", "version": file["version"], "start_line": 1, "end_line": 1}]}
    script_gateway(
        services.models,
        edits("before", "first", "First saved reply"),
        edits("first", "second", "Later saved reply"),
    )
    first = (
        await client.post(base + "/messages", json={"content": "First captured request", "context": context})
    ).json()
    assert (await services.runtime.wait(first["id"]))["status"] == "completed"
    after_first = boundary(ws, thread["id"], first["id"], "after_assistant")
    second = await ws.send(thread["id"], "Later request to set aside")
    assert (await services.runtime.wait(second["id"]))["status"] == "completed"
    after_second = boundary(ws, thread["id"], second["id"], "after_assistant")
    # The runs' per-step states are gone; resuming and the message points still find theirs,
    # including the first reply's, which the rewind below restores.
    saver = await services.runtime.checkpointer()
    async with saver.conn.execute(
        "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ?", (thread["conversation_id"],)
    ) as rows:
        kept = {row[0] async for row in rows}
    with ws.db() as db:
        recorded = {
            json.loads(row["model_config"])["configurable"]["checkpoint_id"]
            for row in db.execute(
                "SELECT model_config FROM coding_checkpoints WHERE thread_id = ? AND model_config IS NOT NULL",
                (thread["id"],),
            )
        }
    assert kept == recorded | {max(kept)}
    before_restore = ws.runs(thread["id"])
    (folder / "other.txt").write_text("Unrelated staged work\n")
    git(folder, "add", "other.txt")
    staged = git(folder, "diff", "--cached")
    head = git(folder, "rev-parse", "HEAD")

    response = await client.post(base + "/rewind", json={"checkpoint_id": after_first["id"]})
    assert response.status_code == 200, response.text
    restored = response.json()
    archived = ws.thread(restored["archived_thread_id"])
    assert archived["archived"] and archived["conversation_id"] == thread["conversation_id"]
    assert restored["thread"]["id"] == thread["id"] and restored["draft"] is None
    assert (folder / "a.txt").read_bytes() == b"first\r\n"
    assert (folder / "a.txt").stat().st_mode & 0o777 == 0o755
    assert (folder / "other.txt").read_text() == "Unrelated staged work\n"
    assert git(folder, "diff", "--cached") == staged and git(folder, "rev-parse", "HEAD") == head
    assert [run["output"]["text"] for run in ws.runs(thread["id"])] == ["First saved reply"]
    assert [run["id"] for run in ws.runs(archived["id"])] == [run["id"] for run in before_restore]
    assert services.repository.get_run(second["id"])["input"]["desktop_copy"] == archived["id"]
    # The attachment went with the first message, so the draft after its reply is empty.
    assert not ws.attachments(thread["id"])
    state = await saver.aget_tuple({"configurable": {"thread_id": restored["thread"]["conversation_id"]}})
    assert "First captured request" in str(state.checkpoint) and "Later request to set aside" not in str(
        state.checkpoint
    )

    # The archived future remains recoverable in an independent checkout.
    future = await client.post(
        f"/api/coding/threads/{archived['id']}/fork", json={"checkpoint_id": after_second["id"]}
    )
    assert future.status_code == 201, future.text
    future_folder = Path(future.json()["folder"])
    try:
        assert (future_folder / "a.txt").read_bytes() == b"second\r\n"
        assert (folder / "a.txt").read_bytes() == b"first\r\n"
        assert len(ws.runs(future.json()["id"])) == 2
        before_first = next(
            point for point in ws.runs(thread["id"])[0]["checkpoints"] if point["kind"] == "before_user"
        )
        # An unsent attachment in the current draft gives way to the edited message's own files.
        await client.post(
            base + "/attachments", files={"file": ("unsent.txt", b"Not part of that message", "text/plain")}
        )
        response = await client.post(base + "/rewind", json={"checkpoint_id": before_first["id"]})
        assert response.status_code == 200, response.text
        assert response.json()["draft"]["content"] == "First captured request"
        assert response.json()["draft"]["context"]["files"][0]["version"] == file["version"]
        # Editing the message again re-attaches the file it sent.
        assert [f["id"] for f in ws.attachments(thread["id"])] == [uploaded["id"]]
        assert (folder / "a.txt").read_bytes() == b"before\r\n"
        assert not ws.runs(thread["id"])
        script_gateway(services.models, [AIMessage(content="New continuation")])
        followup = await ws.send(thread["id"], "Start from the restored state")
        assert (await services.runtime.wait(followup["id"]))["status"] == "completed"
        current = ws.thread(thread["id"])
        state = await saver.aget_tuple({"configurable": {"thread_id": current["conversation_id"]}})
        assert "Start from the restored state" in str(state.checkpoint)
        assert "Later request to set aside" not in str(state.checkpoint)
        assert git(folder, "diff", "--cached") == staged
    finally:
        if future_folder.exists():
            git(folder, "worktree", "remove", "--force", str(future_folder))


@pytest.mark.sandbox
async def test_rewind_rejects_foreign_stale_and_busy_checkout_without_mutating_history(
    coding, tmp_path, monkeypatch
):
    client, ws, services = coding
    folder = repository(tmp_path)
    owner, other = await create(client, folder=str(folder)), await create(client, folder=str(folder))
    endpoint = f"/api/coding/threads/{owner['id']}/rewind"
    script_gateway(services.models, edits("before", "agent", "Recorded edit"))
    run = await ws.send(owner["id"], "Make a change")
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"
    point = boundary(ws, owner["id"], run["id"], "before_user")
    before, history = ws.snapshot(), ws.runs(owner["id"])
    foreign = await client.post(
        f"/api/coding/threads/{other['id']}/rewind", json={"checkpoint_id": point["id"]}
    )
    assert foreign.status_code == 404, foreign.text
    (folder / "a.txt").write_text("External editor change\n")
    failed = await client.post(endpoint, json={"checkpoint_id": point["id"]})
    assert failed.status_code == 422 and "a.txt" in failed.text
    assert (folder / "a.txt").read_text() == "External editor change\n"
    assert ws.snapshot() == before and ws.runs(owner["id"]) == history
    (folder / "a.txt").write_text("agent\n")
    git(folder, "add", "a.txt")
    staged = git(folder, "diff", "--cached")
    failed = await client.post(endpoint, json={"checkpoint_id": point["id"]})
    assert failed.status_code == 422 and "stag" in failed.text.lower()
    assert git(folder, "diff", "--cached") == staged and (folder / "a.txt").read_text() == "agent\n"
    assert ws.snapshot() == before and ws.runs(owner["id"]) == history
    git(folder, "reset", "--", "a.txt")

    began, release = asyncio.Event(), asyncio.Event()

    @tool("calculator")
    async def slow(expression: str) -> str:
        """Keep the other chat busy in this checkout."""
        began.set()
        await release.wait()
        return "2"

    monkeypatch.setitem(
        TOOL_REGISTRY, "calculator", replace(TOOL_REGISTRY["calculator"], build=lambda _: slow)
    )
    script_gateway(
        services.models,
        [
            tool_call_message("calculator", {"expression": "1+1"}, "busy"),
            AIMessage(content="Other task finished"),
        ],
    )
    busy = await ws.send(other["id"], "An independent task in the shared checkout")
    try:
        await asyncio.wait_for(began.wait(), 5)
        failed = await client.post(endpoint, json={"checkpoint_id": point["id"]})
        assert failed.status_code == 422, failed.text
        assert (
            "running" in failed.text.lower()
            or "active" in failed.text.lower()
            or "finish" in failed.text.lower()
        )
        assert (
            ws.runs(owner["id"]) == history
            and ws.thread(owner["id"])["conversation_id"] == owner["conversation_id"]
        )
        assert (folder / "a.txt").read_text() == "agent\n"
        assert services.repository.get_run(busy["id"])["status"] == "running"
        # A new worktree leaves the busy checkout's files, index and HEAD as they are.
        forked = await client.post(f"/api/coding/threads/{owner['id']}/fork", json={})
        assert forked.status_code == 201, forked.text
        assert Path(forked.json()["folder"]) != folder and (folder / "a.txt").read_text() == "agent\n"
        assert services.repository.get_run(busy["id"])["status"] == "running"
        # The busy chat itself stays where its task works.
        with pytest.raises(ValueError, match="this chat's task"):
            await ws.checkpoints.new_worktree(other["id"])
        assert ws.root(other["id"]) == folder.resolve()
    finally:
        release.set()
        await services.runtime.wait(busy["id"])

    # A later incomplete snapshot cannot silently reuse an older complete one.
    linked = folder / "linked.txt"
    linked.symlink_to(folder / "a.txt")
    try:
        script_gateway(services.models, [AIMessage(content="Latest code could not be captured")])
        uncaptured = await ws.send(owner["id"], "Continue with unsupported linked source")
        assert (await services.runtime.wait(uncaptured["id"]))["status"] == "completed"
        latest = boundary(ws, owner["id"], uncaptured["id"], "after_assistant")
        assert not latest["available"] and "linked" in latest["unavailable_reason"]
        snapshot, history = ws.snapshot(), ws.runs(owner["id"])
        failed = await client.post(endpoint, json={"checkpoint_id": point["id"]})
        assert failed.status_code == 422, failed.text
        assert ws.snapshot() == snapshot and ws.runs(owner["id"]) == history
        assert (folder / "a.txt").read_text() == "agent\n"
    finally:
        linked.unlink()

    # A rewind's restored code is where the next rewind starts, though the boundary's own
    # checkpoint belongs to the message it set aside: here the user's edit before that message.
    (folder / "a.txt").write_text("user tweak\n")
    script_gateway(services.models, edits("user tweak", "third", "Third reply"))
    third = await ws.send(owner["id"], "A request to edit and send again")
    assert (await services.runtime.wait(third["id"]))["status"] == "completed"
    # The set-aside conversation's desktop closes with it, releasing the chat's kept home.
    from wavefunction.computer import DesktopSession

    conversation = ws.thread(owner["id"])["conversation_id"]
    services.computer._sessions[conversation] = DesktopSession(
        conversation, "synthetic", 0, "synthetic", None, copy=owner["id"], home="synthetic-home"
    )
    monkeypatch.setattr(
        services.computer, "_drop", lambda key: asyncio.sleep(0, services.computer._sessions.pop(key, None))
    )
    edited = await client.post(
        endpoint, json={"checkpoint_id": boundary(ws, owner["id"], third["id"], "before_user")["id"]}
    )
    assert edited.status_code == 200, edited.text
    assert conversation not in services.computer._sessions
    assert (folder / "a.txt").read_text() == "user tweak\n"
    first = boundary(ws, owner["id"], ws.runs(owner["id"])[0]["id"], "before_user")
    again = await client.post(endpoint, json={"checkpoint_id": first["id"]})
    assert again.status_code == 200, again.text
    assert (folder / "a.txt").read_text() == "before\n" and not ws.runs(owner["id"])

    # A fork carries uncommitted edits to a file the checkout writes through a filter (CRLF
    # here, LFS alike): its code is planned from the bytes the new checkout holds.
    (folder / ".gitattributes").write_text("*.bat text eol=crlf\n")
    (folder / "run.bat").write_bytes(b"echo one\r\n")
    git(folder, "add", ".gitattributes", "run.bat")
    git(folder, "commit", "-q", "-m", "Windows script")
    (folder / "run.bat").write_bytes(b"echo two\r\n")
    carried = await client.post(f"/api/coding/threads/{owner['id']}/fork", json={})
    assert carried.status_code == 201, carried.text
    assert (Path(carried.json()["folder"]) / "run.bat").read_bytes() == b"echo two\r\n"

    # A message recorded in another checkout of the repository stays tied to it after a rewind
    # in this one, so a second rewind cannot write that checkout's files here.
    pair = tmp_path / "pair"
    pair.mkdir()
    first, second = repository(pair), pair / "second"
    git(first, "worktree", "add", "-q", "-b", "feature", str(second), "main")
    (first / "a.txt").write_text("first checkout's work\n")
    moving = await create(client, folder=str(first))
    route = f"/api/coding/threads/{moving['id']}"
    script_gateway(services.models, [AIMessage(content="one")], [AIMessage(content="two")])
    for text, place in (("In the first checkout", None), ("In the second checkout", second)):
        if place:
            assert (await client.patch(route, json={"folder": str(place)})).status_code == 200
        sent = await ws.send(moving["id"], text)
        assert (await services.runtime.wait(sent["id"]))["status"] == "completed"

    def point(index, kind):
        return next(p for p in ws.runs(moving["id"])[index]["checkpoints"] if p["kind"] == kind)

    assert (
        await client.post(route + "/rewind", json={"checkpoint_id": point(1, "after_assistant")["id"]})
    ).status_code == 200
    refused = await client.post(route + "/rewind", json={"checkpoint_id": point(0, "before_user")["id"]})
    assert refused.status_code == 422 and (second / "a.txt").read_text() == "before\n"


@pytest.mark.sandbox
async def test_interrupted_rewind_recovers_once_after_restart_and_refuses_intervening_edits(
    coding, tmp_path, monkeypatch
):
    client, ws, services = coding
    folder = repository(tmp_path)
    thread = await create(client, folder=str(folder))
    base = f"/api/coding/threads/{thread['id']}"
    script_gateway(services.models, edits("before", "saved", "Saved response"))
    run = await ws.send(thread["id"], "A task interrupted by restoring its beginning")
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"
    point = boundary(ws, thread["id"], run["id"], "before_user")

    original_history, apply = services.repository._history, ws.recovery.apply

    def interrupted(*args):
        raise RuntimeError("Synthetic interruption between workspace and run-history updates")

    async def interrupt_after_files(*args, **kwargs):
        result = await apply(*args, **kwargs)
        monkeypatch.setattr(services.repository, "_history", interrupted)
        return result

    monkeypatch.setattr(ws.recovery, "apply", interrupt_after_files)
    with pytest.raises(RuntimeError, match="Synthetic interruption"):
        await client.post(base + "/rewind", json={"checkpoint_id": point["id"]})
    monkeypatch.setattr(services.repository, "_history", original_history)
    assert (folder / "a.txt").read_text() == "before\n"
    current_conversation = ws.thread(thread["id"])["conversation_id"]
    assert current_conversation != thread["conversation_id"]
    with ws.db() as db:
        row = dict(
            db.execute("SELECT * FROM coding_recovery WHERE thread_id = ?", (thread["id"],)).fetchone()
        )
    assert row["state"] == "rewinding"
    archive_id = json.loads(row["history"])["archive"]

    restarted = CodingWorkspace(services)
    services.coding_workspace = restarted
    services.runtime.coding_workspace = restarted
    (folder / "a.txt").write_text("External work after interruption\n")
    failed = await client.get(base)
    assert failed.status_code == 422 and "a.txt" in failed.text
    assert (folder / "a.txt").read_text() == "External work after interruption\n"
    assert restarted.thread(thread["id"])["conversation_id"] == current_conversation
    (folder / "a.txt").write_text("before\n")
    recovered = await client.get(base)
    assert recovered.status_code == 200, recovered.text
    assert not restarted.runs(thread["id"])
    assert len(restarted.runs(archive_id)) == 1 and restarted.thread(archive_id)["archived"]
    assert services.repository.get_run(run["id"])["input"]["desktop_copy"] == archive_id
    current = restarted.thread(thread["id"])
    await restarted.checkpoints.recover(thread["id"])
    assert restarted.thread(thread["id"]) == current
    with restarted.db() as db:
        assert (
            db.execute("SELECT state FROM coding_recovery WHERE id = ?", (row["id"],)).fetchone()["state"]
            == "rewound"
        )
        assert db.execute("SELECT COUNT(*) FROM coding_threads WHERE archived = 1").fetchone()[0] == 1
    assert not restarted.recovery.list(thread["id"])

    # Ignored files are not code history. A reply that changes what is ignored leaves the
    # user's files alone when it is rewound: neither deleted nor taken for a conflict.
    (folder / ".gitignore").write_text("local.cfg\n")
    (folder / "local.cfg").write_text("user settings\n")
    (folder / "app.log").write_text("existing log\n")
    script_gateway(
        services.models,
        [
            tool_call_message("read_file", {"path": ".gitignore"}, "read-ignore"),
            tool_call_message(
                "edit_file",
                {"path": ".gitignore", "old_text": "local.cfg", "new_text": "*.log"},
                "edit-ignore",
            ),
            AIMessage(content="Ignore logs instead"),
        ],
    )
    ignores = await restarted.send(thread["id"], "Ignore logs instead of local.cfg")
    assert (await services.runtime.wait(ignores["id"]))["status"] == "completed"
    response = await client.post(
        base + "/rewind",
        json={"checkpoint_id": boundary(restarted, thread["id"], ignores["id"], "before_user")["id"]},
    )
    assert response.status_code == 200, response.text
    assert (folder / ".gitignore").read_text() == "local.cfg\n"
    assert (folder / "local.cfg").read_text() == "user settings\n" and (
        folder / "app.log"
    ).read_text() == "existing log\n"

    # A case-only rename on the default case-insensitive volume rewinds to the original name.
    (folder / "button.tsx").write_text("export const Button = () => null;\n")
    git(folder, "add", "button.tsx")
    git(folder, "commit", "-q", "-m", "Component")
    script_gateway(
        services.models,
        [
            tool_call_message("run_command", {"command": "git mv button.tsx Button.tsx"}, "rename"),
            AIMessage(content="Renamed the component file"),
        ],
    )
    renamed = await restarted.send(thread["id"], "Use PascalCase for the component file")
    assert (await services.runtime.wait(renamed["id"]))["status"] == "completed"
    git(folder, "reset", "-q", "--", "button.tsx", "Button.tsx")  # Unstages only; rewind requires it.
    response = await client.post(
        base + "/rewind",
        json={"checkpoint_id": boundary(restarted, thread["id"], renamed["id"], "before_user")["id"]},
    )
    assert response.status_code == 200, response.text
    assert [p.name for p in folder.iterdir() if p.suffix == ".tsx"] == ["button.tsx"]

    # A tracked file is code history even in a folder named like build output.
    (folder / "build").mkdir()
    (folder / "build" / "package.sh").write_text("echo package v1\n")
    git(folder, "add", "build/package.sh")
    git(folder, "commit", "-q", "-m", "Packaging script")
    script_gateway(
        services.models,
        [
            tool_call_message("read_file", {"path": "build/package.sh"}, "read-script"),
            tool_call_message(
                "edit_file", {"path": "build/package.sh", "old_text": "v1", "new_text": "v2"}, "edit-script"
            ),
            AIMessage(content="Bumped the package version"),
        ],
    )
    bumped = await restarted.send(thread["id"], "Bump the packaging script")
    assert (await services.runtime.wait(bumped["id"]))["status"] == "completed"
    response = await client.post(
        base + "/rewind",
        json={"checkpoint_id": boundary(restarted, thread["id"], bumped["id"], "before_user")["id"]},
    )
    assert response.status_code == 200, response.text
    assert (folder / "build" / "package.sh").read_text() == "echo package v1\n"
    # A tracked file the reply deleted there comes back too; a tracked secret is never captured.
    (folder / ".env").write_text("SYNTHETIC_SECRET=committed\n")
    git(folder, "add", "-f", ".env")
    git(folder, "commit", "-q", "-m", "Tracked environment")
    (folder / ".env").write_text("SYNTHETIC_SECRET=local-only\n")
    script_gateway(
        services.models,
        [
            tool_call_message(
                "run_command",
                {"command": "git rm -q --cached build/package.sh && mv build/package.sh package.old"},
                "remove-script",
            ),
            AIMessage(content="Moved the packaging script"),
        ],
    )
    moved = await restarted.send(thread["id"], "Move the packaging script")
    assert (await services.runtime.wait(moved["id"]))["status"] == "completed"
    git(folder, "reset", "-q", "--", "build/package.sh")  # Unstages only; rewind requires it.
    response = await client.post(
        base + "/rewind",
        json={"checkpoint_id": boundary(restarted, thread["id"], moved["id"], "before_user")["id"]},
    )
    assert response.status_code == 200, response.text
    assert (folder / "build" / "package.sh").read_text() == "echo package v1\n"
    assert "local-only" not in git(folder, "log", "--all", "-p", "--", ".env")
    # Committed dependencies with links (a JavaScript action's node_modules) leave history working,
    # and build output that was on disk at a message is kept by a rewind there, though committed since.
    (folder / "node_modules" / ".bin").mkdir(parents=True)
    (folder / "node_modules" / "tool.js").write_text("module.exports = 1;\n")
    (folder / "node_modules" / ".bin" / "tool").symlink_to("../tool.js")
    git(folder, "add", "node_modules")
    git(folder, "commit", "-q", "-m", "Vendored action")
    (folder / "dist").mkdir()
    (folder / "dist" / "app.js").write_text("built\n")
    script_gateway(
        services.models,
        [
            tool_call_message(
                "run_command", {"command": "git add dist && git commit -qm Build"}, "commit-build"
            ),
            AIMessage(content="Committed the build"),
        ],
    )
    built = await restarted.send(thread["id"], "Commit the build")
    assert (await services.runtime.wait(built["id"]))["status"] == "completed"
    response = await client.post(
        base + "/rewind",
        json={"checkpoint_id": boundary(restarted, thread["id"], built["id"], "before_user")["id"]},
    )
    assert response.status_code == 200, response.text
    assert (folder / "dist" / "app.js").read_text() == "built\n"
