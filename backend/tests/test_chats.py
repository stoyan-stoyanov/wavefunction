"""Chats: isolation, rejected folders, search, forks, attachments and history pages."""

import asyncio
from pathlib import Path

import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create


async def test_threads_isolate_context_folders_models_and_attachments(coding, tmp_path):
    client, ws, services = coding
    folders = [tmp_path / "one", tmp_path / "two"]
    for folder in folders:
        folder.mkdir()
        (folder / "marker.txt").write_text(folder.name)
    a = await create(client, folder=str(folders[0]), model={"provider": "demo", "model": "demo-reliable"})
    b = await create(client, folder=str(folders[1]))
    attached = await client.post(
        f"/api/coding/threads/{a['id']}/attachments",
        files={"file": ("private.txt", b"Only thread one knows this", "text/plain")},
    )
    assert attached.status_code == 201, attached.text
    # The composer previews an attached image from its own conversation only.
    import io

    from PIL import Image

    png = io.BytesIO()
    Image.new("RGB", (1600, 800), "red").save(png, format="PNG")
    image = (
        await client.post(
            f"/api/coding/threads/{a['id']}/attachments",
            files={"file": ("shot.png", png.getvalue(), "image/png")},
        )
    ).json()
    preview = await client.get(f"/api/coding/threads/{a['id']}/attachments/{image['id']}")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
    # A bounded thumbnail, never the original upload.
    assert Image.open(io.BytesIO(preview.content)).size == (320, 160)
    assert (await client.get(f"/api/coding/threads/{b['id']}/attachments/{image['id']}")).status_code == 404
    assert (
        await client.get(f"/api/coding/threads/{a['id']}/attachments/{attached.json()['id']}")
    ).status_code == 422
    # Newer attachments come last, as the composer shows them.
    assert [f["name"] for f in ws.attachments(a["id"])] == ["private.txt", "shot.png"] and not ws.attachments(
        b["id"]
    )
    script_gateway(
        services.models,
        [
            tool_call_message("read_documents", {"name": "private.txt"}, "read-upload-one"),
            tool_call_message("read_file", {"path": "marker.txt"}, "read-one"),
            AIMessage(content="First finished"),
        ],
        [
            tool_call_message("read_documents", {"name": "private.txt"}, "read-upload-two"),
            tool_call_message("read_file", {"path": "marker.txt"}, "read-two"),
            AIMessage(content="Second finished"),
        ],
    )
    first = await ws.send(a["id"], "Read the marker")
    await services.runtime.wait(first["id"])
    second = await ws.send(b["id"], "Read the marker")
    await services.runtime.wait(second["id"])
    run_a, run_b = ws.runs(a["id"])[0], ws.runs(b["id"])[0]
    assert run_a["status"] == run_b["status"] == "completed"
    assert run_a["output"]["text"] == "First finished"
    assert run_b["output"]["text"] == "Second finished"
    assert services.repository.get_run(first["id"])["input"]["desktop_copy"] == a["id"]
    assert len(services.repository.get_run(first["id"])["input"]["reference_files"]) == 2
    # Attachments belong to the message: the next draft starts empty, and the image keeps its preview.
    assert not ws.attachments(a["id"])
    assert (await client.get(f"/api/coding/threads/{a['id']}/attachments/{image['id']}")).status_code == 200
    assert not services.repository.get_run(second["id"])["input"]["reference_files"]
    assert "Only thread one knows this" in str(run_a["events"])
    assert "Only thread one knows this" not in str(run_b["events"])
    for run, expected, excluded in [(run_a, "one", "two"), (run_b, "two", "one")]:
        output = [e for e in run["events"] if e["event_type"] == "tool_completed"]
        assert output, run["events"]
        assert expected in str(output)
        assert f"{excluded}/marker" not in str(output)
    assert len(ws.runs(a["id"])) == len(ws.runs(b["id"])) == 1


async def test_private_data_and_relative_folders_rejected(coding):
    client, _, services = coding
    for folder in [
        "relative",
        str(services.settings.data_dir),
        str(services.settings.data_dir.parent),
        str(Path.home() / ".ssh"),
    ]:
        response = await client.post("/api/coding/threads", json={"folder": folder})
        assert response.status_code == 422, response.text


async def test_search_excerpt_shows_the_match_after_text_that_folds_longer(coding):
    client, _, _ = coding
    # Each ß matches as "ss", one character longer, before the match.
    instructions = "Die Straße ist groß. " * 30 + "Bitte prüfe NEEDLE-42 danach."
    chat = await create(client, title="Notizen", instructions=instructions)
    found = (await client.get("/api/coding/search", params={"query": "needle-42"})).json()
    assert [(row["id"], row["excerpt"]) for row in found] == [
        (chat["id"], " groß. Die Straße ist groß. Bitte prüfe NEEDLE-42 danach.")
    ]


@pytest.mark.sandbox
async def test_fork_restores_selected_history_code_and_attachments_then_diverges(coding, tmp_path):
    from support import git, repository

    client, ws, services = coding
    folder = repository(tmp_path)
    (folder / "notes.txt").write_bytes(b"Uncommitted source notes\r\n")
    (folder / "a.txt").chmod(0o755)
    thread = await create(client, folder=str(folder))
    base = f"/api/coding/threads/{thread['id']}"
    upload = (
        await client.post(
            base + "/attachments", files={"file": ("context.txt", b"A synthetic requirement", "text/plain")}
        )
    ).json()
    head, index = git(folder, "rev-parse", "HEAD"), (folder / ".git/index").read_bytes()
    script_gateway(
        services.models,
        [
            tool_call_message("read_file", {"path": "a.txt"}, "read-first"),
            tool_call_message(
                "edit_file",
                {"path": "a.txt", "old_text": "before", "new_text": "ATLAS library"},
                "edit-first",
            ),
            AIMessage(content="The architecture is a library."),
        ],
        [
            tool_call_message("read_file", {"path": "a.txt"}, "read-later"),
            tool_call_message(
                "edit_file",
                {"path": "a.txt", "old_text": "ATLAS library", "new_text": "Later continuation"},
                "edit-later",
            ),
            AIMessage(content="Later source answer"),
        ],
    )
    first = await ws.send(thread["id"], "Remember: this library is called ATLAS")
    assert (await services.runtime.wait(first["id"]))["status"] == "completed"
    checkpoint = next(
        point for point in ws.runs(thread["id"])[0]["checkpoints"] if point["kind"] == "after_assistant"
    )
    assert checkpoint["available"]
    later = await ws.send(thread["id"], "Continue only in the source")
    assert (await services.runtime.wait(later["id"]))["status"] == "completed"
    response = await client.post(base + "/fork", json={"checkpoint_id": checkpoint["id"]})
    assert response.status_code == 201, response.text
    branch = response.json()
    owned = Path(branch["folder"])
    assert owned != folder
    assert (owned / "a.txt").read_text() == "ATLAS library\n"
    assert (owned / "a.txt").stat().st_mode & 0o777 == 0o755
    assert (owned / "notes.txt").read_bytes() == b"Uncommitted source notes\r\n"
    assert (folder / "a.txt").read_text() == "Later continuation\n"
    assert git(folder, "rev-parse", "HEAD") == head
    assert (folder / ".git/index").read_bytes() == index
    assert len(ws.runs(branch["id"])) == 1 and len(ws.runs(thread["id"])) == 2
    # The fork shows when each message originally happened, not when it was forked.
    original, copied = ws.runs(thread["id"])[0], ws.runs(branch["id"])[0]
    assert (copied["started_at"], copied["completed_at"]) == (
        original["started_at"],
        original["completed_at"],
    )
    assert [event["created_at"] for event in copied["events"]] == [
        event["created_at"] for event in original["events"]
    ]
    # The sent attachment belongs to its message; the fork's draft starts empty.
    assert not ws.attachments(branch["id"])
    script_gateway(
        services.models,
        [
            tool_call_message("read_file", {"path": "a.txt"}, "read-branch"),
            tool_call_message(
                "edit_file",
                {"path": "a.txt", "old_text": "ATLAS library", "new_text": "Independent branch"},
                "edit-branch",
            ),
            AIMessage(content="Branch-specific reply"),
        ],
    )
    run = await ws.send(branch["id"], "Change only this branch")
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"
    # The branch's next message can still read the file its copied history sent.
    assert [f["id"] for f in services.repository.get_run(run["id"])["input"]["reference_files"]] == [
        upload["id"]
    ]
    assert services.repository.get_run(run["id"])["input"]["initial_reference_files"] == []
    assert (owned / "a.txt").read_text() == "Independent branch\n"
    assert (folder / "a.txt").read_text() == "Later continuation\n"
    saver = await services.runtime.checkpointer()
    state = await saver.aget_tuple({"configurable": {"thread_id": branch["conversation_id"]}})
    assert "ATLAS" in str(state.checkpoint) and "Change only this branch" in str(state.checkpoint)
    assert "Continue only in the source" not in str(state.checkpoint)
    matches = (await client.get("/api/coding/search", params={"query": "ATLAS"})).json()
    assert {thread["id"], branch["id"]} <= {item["id"] for item in matches}
    assert (await client.delete(base)).status_code == 204
    assert services.repository.run_references_file(ws.agent_id, upload["id"])
    assert len(services.repository.agent_ids()) == 1
    copied_point = next(
        point for point in ws.runs(branch["id"])[0]["checkpoints"] if point["kind"] == "after_assistant"
    )
    restored = await client.post(
        f"/api/coding/threads/{branch['id']}/rewind", json={"checkpoint_id": copied_point["id"]}
    )
    assert restored.status_code == 200, restored.text
    assert (owned / "a.txt").read_text() == "ATLAS library\n"
    assert (folder / "a.txt").read_text() == "Later continuation\n"
    assert len(ws.runs(branch["id"])) == 1


async def test_accepted_attachment_survives_removal_restart_and_fork_until_history_deleted(
    coding, monkeypatch
):
    client, ws, services = coding
    thread = await create(client)
    base = f"/api/coding/threads/{thread['id']}"
    uploaded = (
        await client.post(
            base + "/attachments",
            files={"file": ("requirements.txt", b"Retain the original bytes", "text/plain")},
        )
    ).json()
    script_gateway(
        services.models,
        [
            tool_call_message(
                "ask_user", {"questions": [{"id": "continue", "title": "Ready to read?"}]}, "pause"
            ),
            tool_call_message("read_documents", {}, "read"),
            AIMessage(content="Read the original requirement"),
        ],
        [
            tool_call_message("read_documents", {}, "queued-read"),
            AIMessage(content="Queued task read the same bytes"),
        ],
    )
    # Removal can run in the API's worker pool after send captures the selection,
    # but before the runtime has persisted a run reference.
    entered, release = asyncio.Event(), asyncio.Event()
    start_run = services.runtime.start_run

    async def gated_start(*args, **kwargs):
        entered.set()
        await release.wait()
        return await start_run(*args, **kwargs)

    monkeypatch.setattr(services.runtime, "start_run", gated_start)
    sending = asyncio.create_task(ws.send(thread["id"], "Read the attached requirement after my answer"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert (await client.delete(base + "/attachments/" + uploaded["id"])).status_code == 204
        path = services.repository.package_path(ws.agent_id) / uploaded["relative_path"]
        assert path.read_bytes() == b"Retain the original bytes"
        assert not services.repository.run_references_file(ws.agent_id, uploaded["id"])
    finally:
        release.set()
        run = await sending
        monkeypatch.setattr(services.runtime, "start_run", start_run)
    await services.runtime.wait(run["id"])
    pause = next(
        e for e in services.repository.list_run_events(run["id"]) if e["event_type"] == "run_questions"
    )
    # Reselect the retained upload for a queued follow-up, as a copied composer does.
    with ws.db() as db:
        db.execute(
            "INSERT INTO coding_attachments VALUES (?, ?, ?)", ("reselected", thread["id"], uploaded["id"])
        )
    queued = await ws.send(thread["id"], "Read the same attachment next", delivery="queue")
    assert queued["status"] == "queued"
    # Sending moves the attachment onto the message; the draft starts empty.
    assert not ws.attachments(thread["id"])
    assert services.repository.run_references_file(ws.agent_id, uploaded["id"])
    # Rehydrate the paused graph from its persisted checkpoint after composer removal.
    services.runtime.active.pop(run["id"], None)
    script_gateway(
        services.models,
        [
            tool_call_message("read_documents", {}, "read-after-restart"),
            AIMessage(content="Read the original requirement"),
        ],
    )
    group = pause["payload"]["groups"][0]
    await services.runtime.answer_questions(run["id"], {group["id"] + ":continue": "Yes"}, pause["sequence"])
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"
    assert "Retain the original bytes" in str(services.repository.list_run_events(run["id"]))
    assert (await services.runtime.wait(queued["id"]))["status"] == "completed"
    assert "Retain the original bytes" in str(services.repository.list_run_events(queued["id"]))
    branch = (await client.post(base + "/fork", json={"worktree": False})).json()
    script_gateway(services.models, [AIMessage(content="No new attachment selected")])
    later = await ws.send(thread["id"], "Next task")
    await services.runtime.wait(later["id"])
    # A later message sends nothing new but can still read what earlier messages sent.
    later_input = services.repository.get_run(later["id"])["input"]
    assert later_input["initial_reference_files"] == []
    assert [f["id"] for f in later_input["reference_files"]] == [uploaded["id"]]
    assert (await client.delete(base)).status_code == 204
    assert services.repository.run_references_file(ws.agent_id, uploaded["id"])
    path = services.repository.package_path(ws.agent_id) / uploaded["relative_path"]
    assert path.read_bytes() == b"Retain the original bytes"
    assert (await client.delete(f"/api/coding/threads/{branch['id']}")).status_code == 204
    assert not path.exists()


async def test_history_pages_migrate_populated_data_keep_active_owner_and_reject_foreign_cursors(
    coding, monkeypatch
):
    client, ws, services = coding
    thread, other = await create(client), await create(client)
    repo = services.repository
    runs = []
    for index in range(63):
        run = repo.create_run(ws.agent_id, {"text": f"Turn {index}"}, thread["conversation_id"], spec=None)
        repo.update_run(run["id"], status="waiting_approval" if index == 0 else "queued")
        repo.add_run_event(run["id"], "text_delta", {"text": f"Event {index}"})
        runs.append(run["id"])
    foreign = repo.create_run(ws.agent_id, {"text": "Other chat"}, other["conversation_id"], spec=None)
    # Upgrade an already populated package, as an existing installation would.
    package = repo.package(ws.agent_id)
    with package.connect() as db:
        db.execute("DROP INDEX idx_runs_conversation")
    repo._ensured.clear()
    base = f"/api/coding/threads/{thread['id']}"
    latest = (await client.get(base)).json()
    assert [run["id"] for run in latest["runs"]] == [runs[0], *runs[13:]]
    assert all(len(run["events"]) == 1 for run in latest["runs"])
    assert latest["older_cursor"] == runs[13]
    older = (await client.get(base + "/history", params={"before": latest["older_cursor"]})).json()
    assert [run["id"] for run in older["runs"]] == runs[:13]
    assert older["older_cursor"] is None
    assert older["runs"][-1]["history_order"] < latest["runs"][1]["history_order"]
    assert (await client.get(base + "/history", params={"before": foreign["id"]})).status_code == 404
    assert (await client.get(base + "/history", params={"before": "missing"})).status_code == 404
    assert (await client.get(base + "/history", params={"before": runs[13], "limit": 101})).status_code == 422
    summary = next(item for item in ws.snapshot()["threads"] if item["id"] == thread["id"])
    assert summary["status"] == "waiting_approval" and summary["last_run_id"] == runs[-1]
    with package.connect() as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM runs WHERE conversation_id = ? ORDER BY rowid DESC LIMIT 50",
            (thread["conversation_id"],),
        ).fetchall()
    assert "idx_runs_conversation" in str([tuple(row) for row in plan])
    # New messages do not shift the cursor into duplicates or skip older messages.
    repo.create_run(ws.agent_id, {"text": "Arrived later"}, thread["conversation_id"], spec=None)
    assert (await client.get(base + "/history", params={"before": latest["older_cursor"]})).json() == older
    # A cancelled follow-up finishes before its older owner. Completion indicators
    # must advance when the owner subsequently finishes, regardless of message order.
    assert summary["last_finished_run_id"] is None
    monkeypatch.setattr("wavefunction.repository.now_iso", lambda: "2026-10-03T10:00:00+00:00")
    repo.update_run(runs[-1], status="cancelled")
    assert (
        repo.conversation_summary(ws.agent_id, thread["conversation_id"])["last_finished_run_id"] == runs[-1]
    )
    monkeypatch.setattr("wavefunction.repository.now_iso", lambda: "2026-10-03T10:01:00+00:00")
    repo.update_run(runs[0], status="completed")
    summary = next(item for item in ws.snapshot()["threads"] if item["id"] == thread["id"])
    assert summary["last_finished_run_id"] == runs[0]
    assert repo.conversation_summary(ws.agent_id, other["conversation_id"])["last_finished_run_id"] is None

    # Continuing a long, settled chat must not decode its old transcript just to find
    # an executing owner. Another chat's pending run must not become a predecessor.
    history = {run["id"] for run in repo.list_conversation_runs(ws.agent_id, thread["conversation_id"])}
    for run_id in history:
        repo.update_run(run_id, status="completed")
    original_events, original_points = repo.list_run_events, ws.checkpoints.points

    def events(run_id, *args, **kwargs):
        assert run_id not in history, "Send reread completed activity"
        return original_events(run_id, *args, **kwargs)

    def points(thread_id, run_id):
        assert run_id not in history, "Send reread completed checkpoints"
        return original_points(thread_id, run_id)

    monkeypatch.setattr(
        repo,
        "list_conversation_runs",
        lambda *args, **kwargs: pytest.fail("Send loaded all conversation runs"),
    )
    monkeypatch.setattr(repo, "list_run_events", events)
    monkeypatch.setattr(ws.checkpoints, "points", points)
    script_gateway(services.models, [AIMessage(content="Continued without reading old activity")])
    response = await client.post(base + "/messages", json={"content": "Continue this long chat"})
    assert response.status_code == 202, response.text
    following = response.json()
    assert following["input"]["wait_for"] == []
    assert (await services.runtime.wait(following["id"]))["status"] == "completed"
