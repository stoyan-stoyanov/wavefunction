"""Managed-checkout lifecycle with synthetic storage and mocked execution.

Every case owns a fresh fixture. Git, removal and checkout execution are mocked;
fixtures are moved aside to model missing files without executing deletion examples.
"""

import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from support import create

from wavefunction import coding_cleanup, coding_git_ops, coding_snapshots, coding_workspace
from wavefunction.coding_workspace import CodingWorkspace, ThreadInput
from wavefunction.repository import now_iso


@pytest.fixture
async def checkout(coding, tmp_path, monkeypatch):
    client, ws, services = coding
    source = tmp_path.resolve() / "repository"
    source.mkdir()
    (source / ".git").mkdir()
    (source / "source.txt").write_text("Original repository contents\n")
    project = (
        await client.post("/api/coding/projects", json={"name": "Preserved project", "folder": str(source)})
    ).json()
    thread = await create(client, title="Retained conversation", project_id=project["id"], folder=str(source))
    folder = source.parent / ".harness-worktrees" / f"{source.name}-{thread['id']}"
    folder.mkdir(parents=True)
    (folder / ".git").write_text("Synthetic Git pointer; never executed\n")
    (folder / "saved.txt").write_text("Saved branch contents\n")
    ws.allow_folder(str(folder))
    stamp = now_iso()
    with ws.db() as db:
        db.execute("UPDATE coding_threads SET folder = ? WHERE id = ?", (str(folder), thread["id"]))
        db.execute(
            "INSERT INTO coding_worktrees (thread_id, source, folder, branch, original_folder, created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (thread["id"], str(source), str(folder), "codex/preserved", str(source), stamp, stamp),
        )
    repo = services.repository
    run = repo.create_run(
        ws.agent_id,
        {"text": "Keep the task", "desktop_copy": thread["id"]},
        thread["conversation_id"],
        spec={"working_directory": str(folder)},
    )
    repo.update_run(run["id"], status="completed", output={"text": "Saved answer"})
    f = SimpleNamespace(
        client=client,
        ws=ws,
        services=services,
        repo=repo,
        source=source,
        folder=folder,
        project=project,
        thread=thread,
        run=run,
        head="a" * 40,
        calls=[],
        moved=[],
        started=[],
        dirty=False,
        interrupted=False,
        partial_failure=False,
        checkout_failure=False,
        hook_failure=False,
        delete_failure=False,
    )

    def age(days=15):
        old = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with f.ws.db() as db:
            db.execute(
                "UPDATE coding_worktrees SET last_used_at = ? WHERE thread_id = ?", (old, thread["id"])
            )
        return old

    def helper():
        return f.ws.create_thread(
            ThreadInput(folder=str(folder), title="Past read-only helper"),
            parent_thread_id=thread["id"],
            parent_run_id=run["id"],
        )

    async def mock_file_operation(_runner, request, **_kwargs):
        action = request["action"]
        f.calls.append(dict(request))
        assert action in {
            "worktree_cleanup_check",
            "worktree_cleanup_remove",
            "worktree_restore",
            "worktree_restore_checkout",
            "worktree_delete",
        }
        if action == "worktree_delete":
            assert request["root"] == str(source) and request["destination"] == str(folder)
            if f.delete_failure is True:
                raise ValueError("Synthetic removal failed")
            if folder.exists():
                preserved = tmp_path / f"simulated-explicit-removal-{len(f.moved)}"
                folder.rename(preserved)
                f.moved.append(preserved)
            if f.delete_failure == "after":
                raise ValueError("Interrupted after mocked removal")
            return {"removed": str(folder)}
        if action in {"worktree_cleanup_check", "worktree_cleanup_remove"}:
            assert request["root"] == str(source) and request["destination"] == str(folder)
            if f.dirty:
                raise ValueError("The worktree contains unsaved or ignored files.")
            if action == "worktree_cleanup_remove":
                assert f.owned()["cleanup_state"] == "deleting" and f.owned()["retained_head"] == f.head
                if f.partial_failure:
                    folder.joinpath("saved.txt").rename(tmp_path / "partial-removal-kept.txt")
                    f.partial_failure, f.dirty = False, True
                    raise RuntimeError("Interrupted after partial mocked removal")
                preserved = tmp_path / f"simulated-removed-{len(f.moved)}"
                folder.rename(preserved)
                f.moved.append(preserved)
                if f.interrupted:
                    f.interrupted = False
                    raise RuntimeError("Interrupted after mocked removal")
            return {"head": f.head, "branch": "codex/preserved"}
        if action == "worktree_restore":
            assert request["head"] == f.head and request["branch"] == "codex/preserved"
            assert request["root"] == str(source) and request["destination"] == str(folder)
            folder.mkdir()
            (folder / ".git").write_text("Synthetic recreated registration\n")
            return {"head": f.head, "branch": "codex/preserved"}
        assert (
            request["root"] == str(folder)
            and request["head"] == f.head
            and request["branch"] == "codex/preserved"
        )
        if f.checkout_failure:
            f.checkout_failure = False
            raise RuntimeError("Interrupted before checkout population")
        (folder / "saved.txt").write_text("Saved branch contents\n")
        if f.hook_failure:
            f.hook_failure = False
            raise RuntimeError("Post-checkout hook failed")
        return {}

    async def capture_start(agent_id, content, **kwargs):
        assert folder.joinpath("saved.txt").read_text() == "Saved branch contents\n"
        assert kwargs["spec"].working_directory == str(folder)
        f.started.append(kwargs["spec"].working_directory)
        new = repo.create_run(
            agent_id,
            {"text": content, "desktop_copy": kwargs["desktop_copy"]},
            kwargs["conversation_id"],
            spec=kwargs["spec"].model_dump(),
        )
        return repo.update_run(new["id"], status="completed", output={"text": "Resumed"})

    f.age, f.helper = age, helper
    f.owned = lambda: f.ws.delivery.worktree(thread["id"])
    monkeypatch.setattr(ws, "worktree_grants", lambda _folder, grants: grants)
    monkeypatch.setattr(coding_cleanup, "file_operation", mock_file_operation)
    monkeypatch.setattr(services.runtime, "start_run", capture_start)
    monkeypatch.setattr("wavefunction.coding_workspace.discover", lambda *_args, **_kwargs: {"skills": []})
    return f


@pytest.fixture
async def populated_checkout(checkout):
    f = checkout
    uploaded = (
        await f.client.post(
            f"/api/coding/threads/{f.thread['id']}/attachments",
            files={"file": ("retained.txt", b"Retained attachment", "text/plain")},
        )
    ).json()
    stamp = now_iso()
    with f.ws.db() as db:
        db.execute(
            "INSERT INTO coding_recovery (id, thread_id, folder, change_ids, files, state, created_at, updated_at) VALUES ('saved-recovery', ?, ?, '[]', '[]', 'undone', ?, ?)",
            (f.thread["id"], str(f.folder), stamp, stamp),
        )
    f.uploaded = uploaded
    f.history, f.recovery = f.ws.runs(f.thread["id"]), f.ws.recovery.list(f.thread["id"])
    return f


async def test_populated_upgrade_preserves_storage_and_applies_retention_settings(
    populated_checkout, monkeypatch
):
    f = populated_checkout
    skills = [(skill.name, skill.version) for skill in f.services.skills.list()]
    stamp = now_iso()
    with f.ws.db() as db:
        for column in ("cleanup_state", "last_used_at", "retained_head"):
            db.execute(f"ALTER TABLE coding_worktrees DROP COLUMN {column}")
    f.ws = CodingWorkspace(f.services)
    f.services.coding_workspace = f.services.runtime.coding_workspace = f.ws
    monkeypatch.setattr(f.ws, "worktree_grants", lambda _folder, grants: grants)
    assert f.owned()["cleanup_state"] == "active" and f.owned()["last_used_at"] >= stamp
    assert f.owned()["source"] == str(f.source) and f.owned()["branch"] == "codex/preserved"
    assert f.ws.agent_id == f.repo.get_setting("harness_coding_agent")
    assert f.ws.project(f.project["id"])["name"] == "Preserved project"
    assert f.ws.runs(f.thread["id"]) == f.history and f.ws.recovery.list(f.thread["id"]) == f.recovery
    assert [(skill.name, skill.version) for skill in f.services.skills.list()] == skills
    assert f.ws.attachments(f.thread["id"])[0]["id"] == f.uploaded["id"]
    assert f.ws.thread(f.thread["id"])["conversation_id"] == f.thread["conversation_id"]
    assert (await f.client.get("/api/coding/worktree-cleanup")).json() == {"retention_days": 14}
    for invalid in (0, 3651, True, "14"):
        assert (
            await f.client.put("/api/coding/worktree-cleanup", json={"retention_days": invalid})
        ).status_code == 422
    await f.ws.cleanup.sweep()
    assert not f.calls  # Upgraded data receives a complete grace period.
    f.age()
    for days in (None, 30):
        assert (await f.client.put("/api/coding/worktree-cleanup", json={"retention_days": days})).json() == {
            "retention_days": days
        }
        assert CodingWorkspace(f.services).cleanup.settings().retention_days == days
        await f.ws.cleanup.sweep()
        assert not f.calls
    await f.client.put("/api/coding/worktree-cleanup", json={"retention_days": 14})
    removing, finish = asyncio.Event(), asyncio.Event()
    operation = coding_cleanup.file_operation

    async def paused_removal(runner, request, **kwargs):
        if request["action"] == "worktree_cleanup_remove":
            assert f.owned()["cleanup_state"] == "deleting"
            removing.set()
            await finish.wait()
        return await operation(runner, request, **kwargs)

    monkeypatch.setattr(coding_cleanup, "file_operation", paused_removal)
    sweeping = asyncio.create_task(f.ws.cleanup.sweep())
    saving = None
    try:
        await asyncio.wait_for(removing.wait(), timeout=1)
        saving = asyncio.create_task(
            f.client.put("/api/coding/worktree-cleanup", json={"retention_days": None})
        )
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(saving), timeout=0.1)
        assert f.ws.cleanup.settings().retention_days == 14
        assert f.folder.exists() and f.owned()["cleanup_state"] == "deleting"
    finally:
        finish.set()
        await sweeping
        if saving is not None:
            saved = await saving
    assert saved.status_code == 200 and saved.json() == {"retention_days": None}
    assert not f.folder.exists() and f.owned()["cleanup_state"] == "deleted"
    # Re-arm the synthetic checkout as an eligible candidate. The confirmed
    # Never setting must prevent any later check or removal from starting.
    f.moved[-1].rename(f.folder)
    with f.ws.db() as db:
        db.execute(
            "UPDATE coding_worktrees SET cleanup_state = 'active' WHERE thread_id = ?", (f.thread["id"],)
        )
    f.age()
    calls_before = len(f.calls)
    await f.ws.cleanup.sweep()
    assert len(f.calls) == calls_before and f.folder.exists()


async def test_explicit_shared_chat_activity_serializes_retention_but_polling_does_not(checkout):
    f = checkout
    old = f.age()
    await f.client.get("/api/coding/workspace")
    await f.client.get(f"/api/coding/threads/{f.thread['id']}")
    assert f.owned()["last_used_at"] == old
    helper = f.helper()
    try:
        async with f.ws.reviews.locks.setdefault(str(f.folder), asyncio.Lock()):
            activity = asyncio.create_task(f.client.post(f"/api/coding/threads/{helper['id']}/activity"))
            await asyncio.sleep(0)
            assert not activity.done() and f.owned()["last_used_at"] == old
    finally:
        response = await activity
    assert response.status_code == 204 and f.owned()["last_used_at"] > old
    await f.ws.cleanup.sweep()
    assert not f.calls
    # Pinning the same helper must acquire the shared folder lock independently
    # of opening it; otherwise cleanup could miss a concurrently accepted pin.
    try:
        async with f.ws.reviews.locks[str(f.folder)]:
            pin = asyncio.create_task(
                f.client.patch(f"/api/coding/threads/{helper['id']}", json={"pinned": True})
            )
            await asyncio.sleep(0)
            assert not pin.done() and not f.ws.thread(helper["id"])["pinned"]
    finally:
        pinned = await pin
    assert pinned.status_code == 200 and pinned.json()["pinned"]
    f.age()
    await f.ws.cleanup.sweep()
    assert not f.calls  # The pinned helper protects its owner's checkout too.


@pytest.mark.parametrize("guard", ["pinned", "active_run", "settling_run", "terminal", "recovery"])
async def test_cleanup_retains_a_checkout_while_a_protection_is_active(
    populated_checkout, monkeypatch, guard
):
    f = populated_checkout

    def no_history_summary(*_args):
        raise AssertionError("Cleanup protection must not load chat history summaries")

    monkeypatch.setattr(f.repo, "conversation_summary", no_history_summary)
    old = f.age()
    if guard == "pinned":
        f.repo.set_setting("repository_folders", {f.thread["id"]: str(f.source)})
        with f.ws.db() as db:
            db.execute("UPDATE coding_threads SET pinned = 1 WHERE id = ?", (f.thread["id"],))
    elif guard == "active_run":
        f.repo.update_run(f.run["id"], status="running")
    elif guard == "settling_run":
        f.services.runtime.tasks[f.run["id"]] = SimpleNamespace(done=lambda: False)
    elif guard == "terminal":
        f.ws.terminals.sessions["synthetic"] = SimpleNamespace(
            closed=False, cwd=str(f.folder), thread_id=f.thread["id"]
        )
    elif guard == "recovery":
        with f.ws.db() as db:
            db.execute("UPDATE coding_recovery SET state = 'rewinding' WHERE id = 'saved-recovery'")
    try:
        await f.ws.cleanup.sweep()
        assert not f.calls and f.folder.exists()
        if guard == "terminal":
            entered, finish = asyncio.Event(), asyncio.Event()

            async def remove(thread_id, terminal_id):
                assert thread_id == f.thread["id"] and terminal_id == "synthetic"
                f.ws.terminals.sessions[terminal_id].closed = True
                entered.set()
                await finish.wait()
                f.ws.terminals.sessions.pop(terminal_id)

            monkeypatch.setattr(f.ws.terminals, "remove", remove)
            closing = asyncio.create_task(
                f.client.delete(f"/api/coding/threads/{f.thread['id']}/terminals/synthetic")
            )
            sweeping = None
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                sweeping = asyncio.create_task(f.ws.cleanup.sweep())
                await asyncio.sleep(0)
                assert not sweeping.done() and not f.calls
                assert f.owned()["last_used_at"] == old
            finally:
                finish.set()
                closed = await closing
                if sweeping:
                    await sweeping
            assert closed.status_code == 204 and f.owned()["last_used_at"] > old
            assert not f.calls and f.folder.exists()
    finally:
        f.services.runtime.tasks.pop(f.run["id"], None)
        f.ws.terminals.sessions.pop("synthetic", None)


async def test_shared_checkout_resolves_chosen_and_project_folder_ownership(checkout, monkeypatch):
    f = checkout

    def no_history_summary(*_args):
        raise AssertionError("Cleanup protection must not load chat history summaries")

    monkeypatch.setattr(f.repo, "conversation_summary", no_history_summary)
    f.age()
    peer = f.ws.create_thread(ThreadInput(folder=str(f.source)))
    try:
        async with f.ws.reviews.locks.setdefault(str(f.folder), asyncio.Lock()):
            moving = asyncio.create_task(
                f.client.patch(f"/api/coding/threads/{peer['id']}", json={"folder": str(f.folder)})
            )
            await asyncio.sleep(0)
            assert not moving.done()
            assert f.ws.effective_folder(f.ws.thread(peer["id"])) == str(f.source)
    finally:
        moved = await moving
    assert moved.status_code == 200 and moved.json()["folder"] == str(f.folder)
    await f.ws.cleanup.sweep()
    assert not f.calls
    with f.ws.db() as db:
        db.execute("UPDATE coding_threads SET folder = ? WHERE id = ?", (str(f.source), peer["id"]))
    f.repo.set_setting("repository_folders", {peer["id"]: str(f.folder)})
    # Saving chat settings resends the unchanged folder; the folder chosen on a card stays.
    saved = await f.client.patch(
        f"/api/coding/threads/{peer['id']}", json={"folder": str(f.source), "title": "Renamed"}
    )
    assert saved.status_code == 200 and f.repo.get_setting("repository_folders") == {
        peer["id"]: str(f.folder)
    }
    await f.ws.cleanup.sweep()
    assert not f.calls
    f.repo.set_setting("repository_folders", {})
    with f.ws.db() as db:
        db.execute("UPDATE coding_projects SET folder = ? WHERE id = ?", (str(f.folder), f.project["id"]))
    try:
        async with f.ws.reviews.locks[str(f.folder)]:
            inheriting = asyncio.create_task(
                f.client.patch(
                    f"/api/coding/threads/{peer['id']}", json={"folder": None, "project_id": f.project["id"]}
                )
            )
            await asyncio.sleep(0)
            assert not inheriting.done()
            assert f.ws.effective_folder(f.ws.thread(peer["id"])) == str(f.source)
    finally:
        inherited = await inheriting
    assert inherited.status_code == 200
    assert f.ws.effective_folder(f.ws.thread(peer["id"])) == str(f.folder)
    await f.ws.cleanup.sweep()
    assert not f.calls
    with f.ws.db() as db:
        db.execute("UPDATE coding_projects SET folder = ? WHERE id = ?", (str(f.source), f.project["id"]))
    # The hidden history a rewind set aside in the checkout does not keep it forever.
    archive = f.ws.create_thread(
        ThreadInput(folder=str(f.folder), title="Before rewind: Retained conversation")
    )
    with f.ws.db() as db:
        db.execute("UPDATE coding_threads SET archived = 1 WHERE id = ?", (archive["id"],))
        db.execute(
            "INSERT INTO coding_recovery (id, thread_id, folder, change_ids, files, state, created_at, updated_at, history) VALUES ('rewound', ?, ?, '[]', '[]', 'rewound', ?, ?, ?)",
            (f.thread["id"], str(f.folder), now_iso(), now_iso(), json.dumps({"archive": archive["id"]})),
        )
    await f.ws.cleanup.sweep()
    assert f.owned()["cleanup_state"] == "deleted"  # No longer independently shared.


async def test_expiration_preserves_history_recency_branches_and_dirty_files(populated_checkout, monkeypatch):
    f = populated_checkout
    # A helper's own checkout goes a day after its last use, checked against its saved final state;
    # a chat's waits for the retention period.
    main = await create(f.client, title="Main chat", folder=str(f.source))
    with f.ws.db() as db:
        db.execute(
            "UPDATE coding_threads SET parent_thread_id = ? WHERE id = ?", (main["id"], f.thread["id"])
        )
        db.execute(
            "INSERT INTO coding_checkpoints (id, thread_id, conversation_id, run_id, kind, event_sequence, folder, code, created_at) "
            "VALUES ('final', ?, ?, ?, 'after_assistant', 0, ?, ?, ?)",
            (
                f.thread["id"],
                f.thread["conversation_id"],
                f.run["id"],
                str(f.folder),
                json.dumps({"tree": "final-tree"}),
                now_iso(),
            ),
        )
    f.age(days=2)
    f.dirty = True
    await f.ws.cleanup.sweep()
    assert [(call["action"], call.get("tree"), call.get("owner")) for call in f.calls] == [
        ("worktree_cleanup_check", "final-tree", f.thread["id"])
    ]
    with f.ws.db() as db:
        db.execute("UPDATE coding_threads SET parent_thread_id = NULL WHERE id = ?", (f.thread["id"],))
        db.execute("DELETE FROM coding_checkpoints WHERE id = 'final'")
    f.calls.clear()
    await f.ws.cleanup.sweep()
    assert not f.calls
    helper = f.helper()
    f.age()
    await f.ws.cleanup.sweep()
    assert f.owned()["cleanup_state"] == "active" and f.folder.exists()
    assert [call["action"] for call in f.calls] == ["worktree_cleanup_check"]
    f.dirty = False
    f.calls.clear()
    updated = f.ws.thread(f.thread["id"])["updated_at"]
    ungrant = f.ws.delivery.ungrant
    revoked = []

    def retry_revocation(folder):
        assert f.owned()["cleanup_state"] == "deleting"
        revoked.append(folder)
        if len(revoked) == 1:
            raise OSError("Synthetic interrupted grant revocation")
        ungrant(folder)

    monkeypatch.setattr(f.ws.delivery, "ungrant", retry_revocation)
    await f.ws.cleanup.sweep()
    assert [call["action"] for call in f.calls] == ["worktree_cleanup_check", "worktree_cleanup_remove"]
    assert not f.folder.exists() and f.moved[0].joinpath("saved.txt").read_text() == "Saved branch contents\n"
    assert f.owned()["cleanup_state"] == "deleting"
    assert str(f.folder) in {g["path"] for g in f.repo.get_setting("folder_grants", [])}
    await f.ws.cleanup.sweep()
    assert revoked == [f.folder, f.folder]
    assert (
        f.owned()["cleanup_state"] == "deleted"
        and f.owned()["retained_head"] == f.head
        and f.owned()["branch"] == "codex/preserved"
    )
    assert f.ws.thread(f.thread["id"])["updated_at"] == updated
    assert f.ws.effective_folder(f.ws.thread(f.thread["id"])) == str(f.folder)
    assert f.ws.thread(helper["id"])["worktree_status"] == "deleted"
    assert f.ws.runs(f.thread["id"]) == f.history and f.ws.recovery.list(f.thread["id"]) == f.recovery
    assert f.ws.attachments(f.thread["id"])[0]["id"] == f.uploaded["id"]
    assert str(f.folder) not in {g["path"] for g in f.repo.get_setting("folder_grants", [])}
    before = len(f.calls)
    assert (await f.client.get(f"/api/coding/threads/{f.thread['id']}")).status_code == 200
    assert len(f.calls) == before and not f.folder.exists()


async def test_continuation_waits_for_checkout_population_and_hook_success(checkout, monkeypatch):
    f = checkout
    f.age()
    await f.ws.cleanup.sweep()
    grants_before = f.repo.get_setting("folder_grants", [])
    calls_before = len(f.calls)
    original_db = f.ws.db
    attempts = []

    @contextmanager
    def failing_intent_write():
        with original_db() as db:

            def execute(sql, *args):
                if "SET cleanup_state = 'restoring'" in sql:
                    attempts.append(sql)
                    raise OSError("Synthetic restoration intent write failure")
                return db.execute(sql, *args)

            yield SimpleNamespace(execute=execute)

    with monkeypatch.context() as failure:
        failure.setattr(f.ws, "db", failing_intent_write)
        with pytest.raises(OSError, match="restoration intent write failure"):
            await f.ws.send(f.thread["id"], "Continue in my managed checkout")
    assert len(attempts) == 1 and f.owned()["cleanup_state"] == "deleted"
    assert f.repo.get_setting("folder_grants", []) == grants_before
    assert len(f.calls) == calls_before and not f.started and not f.folder.exists()
    f.checkout_failure = True
    with pytest.raises(RuntimeError, match="checkout population"):
        await f.ws.send(f.thread["id"], "Continue in my managed checkout")
    assert not f.started and f.owned()["cleanup_state"] == "restoring"
    assert f.folder.joinpath(".git").is_file() and not f.folder.joinpath("saved.txt").exists()
    f.hook_failure = True
    with pytest.raises(RuntimeError, match="Post-checkout hook failed"):
        await f.ws.send(f.thread["id"], "Continue in my managed checkout")
    assert not f.started and f.owned()["cleanup_state"] == "restoring"
    assert f.folder.joinpath("saved.txt").read_text() == "Saved branch contents\n"
    await f.ws.send(f.thread["id"], "Continue in my managed checkout")
    assert f.started == [str(f.folder)] and f.owned()["cleanup_state"] == "active"
    assert f.source.joinpath("source.txt").read_text() == "Original repository contents\n"

    # A helper's checkout comes back as its saved result, not only its branch, so its chat and a
    # plain fork of it show the helper's work.
    main = await create(f.client, title="Main chat", folder=str(f.source))
    with f.ws.db() as db:
        db.execute(
            "UPDATE coding_threads SET parent_thread_id = ? WHERE id = ?", (main["id"], f.thread["id"])
        )
        db.execute(
            "INSERT INTO coding_checkpoints (id, thread_id, conversation_id, run_id, kind, event_sequence, folder, code, created_at) "
            "VALUES ('final', ?, ?, ?, 'after_assistant', 0, ?, ?, ?)",
            (
                f.thread["id"],
                f.thread["conversation_id"],
                f.run["id"],
                str(f.folder),
                json.dumps({"tree": "final-tree", "ref": "refs/final", "tracked": True}),
                now_iso(),
            ),
        )
    f.age(days=2)
    await f.ws.cleanup.sweep()
    assert f.owned()["cleanup_state"] == "deleted" and not f.folder.exists()
    requests, applied = [], []
    row = {
        "path": "result.txt",
        "before_version": "absent",
        "after_version": "v1",
        "before_mode": None,
        "after_mode": "100644",
        "after_data": "SGVscGVyIHJlc3VsdAo=",
        "before_data": None,
    }

    async def checkpoint_git(folder, **request):
        requests.append((str(folder), request["action"], request.get("after")))
        return (
            {"tree": "branch-tree", "ref": "refs/restored"}
            if request["action"] == "checkpoint_capture"
            else {"files": [row]}
        )

    async def apply(root, files, **kwargs):
        applied.append((root, [file["path"] for file in files], kwargs))

    monkeypatch.setattr(f.ws.checkpoints, "git", checkpoint_git)
    monkeypatch.setattr(f.ws.recovery, "apply", apply)
    await f.ws.cleanup.restore(f.thread["id"])
    assert [action for _, action, _ in requests] == [
        "checkpoint_capture",
        "checkpoint_plan",
        "checkpoint_forget",
    ]
    assert requests[1] == (str(f.folder), "checkpoint_plan", "final-tree")
    assert applied == [(str(f.folder), ["result.txt"], {"code_history": True, "resume": True})]
    assert f.owned()["cleanup_state"] == "active"


async def test_partial_removal_requires_repair_before_continuation(checkout, tmp_path):
    f = checkout
    f.age()
    f.partial_failure = True
    await f.ws.cleanup.sweep()
    assert f.folder.exists() and not f.folder.joinpath("saved.txt").exists()
    assert f.owned()["cleanup_state"] == "deleting"
    await f.ws.cleanup.sweep()
    assert f.owned()["cleanup_state"] == "deleting"
    with pytest.raises(ValueError, match="incomplete or changed"):
        await f.ws.send(f.thread["id"], "Do not run against a partial checkout")
    assert not f.started
    assert (tmp_path / "partial-removal-kept.txt").read_text() == "Saved branch contents\n"
    (tmp_path / "partial-removal-kept.txt").rename(f.folder / "saved.txt")
    f.dirty = False
    await f.ws.send(f.thread["id"], "Continue after repairing the checkout")
    assert f.started == [str(f.folder)] and f.owned()["cleanup_state"] == "active"


async def test_helper_fork_recovers_owner_checkout_despite_owner_folder_override(
    checkout, tmp_path, monkeypatch
):
    f = checkout
    helper = f.helper()
    helper_run = f.repo.create_run(
        f.ws.agent_id,
        {"text": "Saved helper task", "desktop_copy": helper["id"]},
        helper["conversation_id"],
        spec={"working_directory": str(f.folder)},
    )
    f.repo.update_run(helper_run["id"], status="completed", output={"text": "Saved helper answer"})
    f.age()
    f.interrupted = True
    await f.ws.cleanup.sweep()
    assert not f.folder.exists() and f.owned()["cleanup_state"] == "deleting"
    with f.ws.db() as db:
        db.execute("UPDATE coding_threads SET archived = 1 WHERE id = ?", (helper["id"],))
    calls_before = len(f.calls)
    grants_before = f.repo.get_setting("folder_grants", [])
    with pytest.raises(ValueError, match="no saved code"):
        await f.ws.checkpoints.fork(helper["id"])
    assert not f.folder.exists() and len(f.calls) == calls_before
    assert (
        f.owned()["cleanup_state"] == "deleting" and f.repo.get_setting("folder_grants", []) == grants_before
    )
    with f.ws.db() as db:
        db.execute(
            "INSERT INTO coding_checkpoints (id, thread_id, conversation_id, run_id, kind, event_sequence, folder, code, created_at) VALUES ('other-folder', ?, ?, ?, 'after_assistant', 0, ?, ?, ?)",
            (
                helper["id"],
                helper["conversation_id"],
                helper_run["id"],
                str(f.source),
                json.dumps(
                    {
                        "head": f.head,
                        "commit": f.head,
                        "tree": f.head,
                        "ref": "refs/wavefunction/other-folder",
                    }
                ),
                now_iso(),
            ),
        )
    with pytest.raises(ValueError, match="original repository"):
        await f.ws.checkpoints.fork(helper["id"], checkpoint_id="other-folder")
    assert not f.folder.exists() and len(f.calls) == calls_before
    assert (
        f.owned()["cleanup_state"] == "deleting" and f.repo.get_setting("folder_grants", []) == grants_before
    )
    f.ws.delivery.ungrant(f.source)
    grants_before = f.repo.get_setting("folder_grants", [])
    calls_before = len(f.calls)
    with f.ws.db() as db:
        chats_before = db.execute("SELECT count(*) FROM coding_threads").fetchone()[0]
    try:
        async with f.ws.reviews.locks[str(f.folder)]:
            forking = asyncio.create_task(f.ws.checkpoints.fork(helper["id"], worktree=False))
            await asyncio.sleep(0)
            assert not forking.done()
            with f.ws.db() as db:
                assert db.execute("SELECT count(*) FROM coding_threads").fetchone()[0] == chats_before
    finally:
        history_fork = await forking
    assert not f.folder.exists() and len(f.calls) == calls_before
    assert not history_fork["worktree"] and not history_fork["parent_thread_id"]
    assert history_fork["folder"] == str(f.folder) and history_fork["worktree_status"] == "deleted"
    assert (
        f.owned()["cleanup_state"] == "deleting" and f.repo.get_setting("folder_grants", []) == grants_before
    )
    assert f.ws.runs(history_fork["id"])[0]["output"]["text"] == "Saved helper answer"
    # Moving retained history between collections does not restore or validate
    # an unchanged, expired checkout.
    moved = await f.client.patch(f"/api/coding/threads/{history_fork['id']}", json={"project_id": None})
    assert moved.status_code == 200 and moved.json()["project_id"] is None
    assert moved.json()["folder"] == str(f.folder) and not f.folder.exists()
    assert len(f.calls) == calls_before
    f.ws.allow_folder(str(f.source))
    with f.ws.db() as db:
        db.execute("UPDATE coding_threads SET archived = 0 WHERE id = ?", (helper["id"],))
    other = tmp_path / "other-repository"
    other.mkdir()
    f.repo.set_setting("repository_folders", {f.thread["id"]: str(other)})
    assert f.ws.cleanup.owned(f.thread["id"])["folder"] == str(f.folder)
    assert f.ws.cleanup.owned(helper["id"])["thread_id"] == f.thread["id"]
    captured = []

    async def branch(source_id, target_id, checkpoint_id=None):
        assert source_id == helper["id"] and f.folder.joinpath("saved.txt").is_file()
        assert f.ws.effective_folder(f.ws.thread(target_id)) == str(f.folder)
        captured.append(target_id)

    monkeypatch.setattr(f.ws.checkpoints, "branch", branch)
    forked = await f.ws.checkpoints.fork(helper["id"])
    assert captured == [forked["id"]] and not forked["parent_thread_id"]
    assert f.owned()["cleanup_state"] == "active"
    assert f.ws.effective_folder(f.ws.thread(f.thread["id"])) == str(other)
    assert f.repo.get_setting("repository_folders") == {f.thread["id"]: str(other)}


async def test_confirmed_chat_deletion_removes_dirty_checkout_but_keeps_repository(checkout, monkeypatch):
    f = checkout
    base = f"/api/coding/threads/{f.thread['id']}"
    f.dirty = True
    (f.folder / "unsaved.txt").write_text("Uncommitted user work\n")
    original_history = f.ws.runs(f.thread["id"])
    screenshots = []
    monkeypatch.setattr(
        coding_workspace, "shutil", SimpleNamespace(rmtree=lambda path, **_kwargs: screenshots.append(path))
    )
    # Explicit deletion also works for archived/pinned chats; automatic retention
    # must continue to protect them and dirty files.
    patched = await f.client.patch(base, json={"archived": True, "pinned": True, "title": "Saved task"})
    assert patched.status_code == 200
    assert (await f.client.post(base + "/messages", json={"content": "No"})).status_code == 422
    assert (await f.client.get("/api/coding/threads/missing")).status_code == 404
    assert (await f.client.patch(base, json={"project_id": "missing"})).status_code == 404
    started, cancelled = asyncio.Event(), asyncio.Event()
    late_terminal = SimpleNamespace(
        id="late-terminal", thread_id=f.thread["id"], cwd=str(f.folder), closed=False
    )

    async def close_terminal():
        late_terminal.closed = True

    late_terminal.close = close_terminal

    async def finishing():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            # Model an in-flight tool registering its terminal as cancellation settles.
            # Chat deletion must collect sessions after the run can no longer add one.
            f.ws.terminals.sessions[late_terminal.id] = late_terminal
            cancelled.set()

    task = asyncio.create_task(finishing())
    f.services.runtime.tasks[f.run["id"]] = task
    await started.wait()
    unconfirmed = await f.client.delete(base)
    assert unconfirmed.status_code == 422 and "Confirm deletion" in unconfirmed.json()["detail"]
    assert not cancelled.is_set() and not f.calls and not screenshots
    assert f.ws.runs(f.thread["id"]) == original_history
    assert f.folder.joinpath("unsaved.txt").read_text() == "Uncommitted user work\n"
    # A failed removal settles a finishing task but retains retryable history and
    # checkout ownership. No deletion commands run: file execution is mocked.
    f.delete_failure = True
    failed = await f.client.delete(base, params={"delete_worktree": "true"})
    assert failed.status_code == 422 and "Synthetic removal failed" in failed.json()["detail"]
    assert cancelled.is_set() and task.done() and f.run["id"] not in f.services.runtime.tasks
    assert late_terminal.closed and late_terminal.id not in f.ws.terminals.sessions
    assert f.owned()["cleanup_state"] == "active" and f.owned()["branch"] == "codex/preserved"
    assert f.ws.runs(f.thread["id"]) == original_history and not screenshots
    helper = f.helper()
    shared_subfolder = f.folder / "package"
    shared_subfolder.mkdir()
    peer = await create(f.client, folder=str(shared_subfolder), title="Independent chat")
    before = len(f.calls)
    rejected = await f.client.delete(base, params={"delete_worktree": "true"})
    assert rejected.status_code == 422 and "Another chat uses this worktree" in rejected.json()["detail"]
    assert len(f.calls) == before and f.ws.thread(peer["id"])["title"] == "Independent chat"
    # Manually archiving an independent chat does not turn it into rewind history,
    # even when its folder and title resemble an automatically created archive.
    assert (
        await f.client.patch(
            f"/api/coding/threads/{peer['id']}",
            json={"folder": str(f.folder), "archived": True, "title": "Before rewind: Independent chat"},
        )
    ).status_code == 200
    rejected = await f.client.delete(base, params={"delete_worktree": "true"})
    assert rejected.status_code == 422 and "Another chat uses this worktree" in rejected.json()["detail"]
    assert len(f.calls) == before and f.ws.thread(peer["id"])["folder"] == str(f.folder)
    assert f.folder.joinpath("unsaved.txt").read_text() == "Uncommitted user work\n"
    assert (
        await f.client.patch(f"/api/coding/threads/{peer['id']}", json={"folder": str(f.source)})
    ).status_code == 200
    f.delete_failure = "after"
    interrupted = await f.client.delete(base, params={"delete_worktree": "true"})
    assert (
        interrupted.status_code == 422 and "Interrupted after mocked removal" in interrupted.json()["detail"]
    )
    assert not f.folder.exists() and f.owned()["cleanup_state"] == "active"
    assert f.ws.runs(f.thread["id"]) == original_history
    runner_folders = []
    original_runner = f.ws.runner

    def observe_runner(folder=None):
        runner_folders.append(folder)
        return original_runner(folder)

    monkeypatch.setattr(f.ws, "runner", observe_runner)
    f.delete_failure = False
    deleted = await f.client.delete(base, params={"delete_worktree": "true"})
    assert deleted.status_code == 204, deleted.text
    assert (await f.client.get(base)).status_code == 404
    # Its helpers go with it, rather than staying behind as chats of their own.
    assert (await f.client.get(f"/api/coding/threads/{helper['id']}")).status_code == 404
    with f.ws.db() as db:
        assert (
            db.execute("SELECT 1 FROM coding_worktrees WHERE thread_id = ?", (f.thread["id"],)).fetchone()
            is None
        )
    assert (
        not f.folder.exists() and f.moved[-1].joinpath("unsaved.txt").read_text() == "Uncommitted user work\n"
    )
    assert [call["action"] for call in f.calls] == ["worktree_delete"] * 3
    assert runner_folders == [f.source]  # Retry must not inspect the vanished checkout's .git.
    assert f.source.joinpath("source.txt").read_text() == "Original repository contents\n"
    assert f.source.joinpath(".git").is_dir() and len(screenshots) == 2  # The chat's and its helper's.
    assert f.ws.thread(peer["id"])["folder"] == str(f.source)
    assert len(f.repo.agent_ids()) == 1
    # A chat whose source repository was moved away has no Git registration to remove: it is
    # deleted all the same, and its checkout's files stay where they are.
    moved = await create(f.client, title="Source moved away")
    orphan = f.source.parent / ".harness-worktrees" / f"moved-away-{moved['id']}"
    orphan.mkdir()
    (orphan / "work.txt").write_text("Kept\n")
    with f.ws.db() as db:
        db.execute("UPDATE coding_threads SET folder = ? WHERE id = ?", (str(orphan), moved["id"]))
        db.execute(
            "INSERT INTO coding_worktrees (thread_id, source, folder, branch, original_folder, created_at, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                moved["id"],
                str(f.source.parent / "moved-away"),
                str(orphan),
                "codex/moved",
                None,
                now_iso(),
                now_iso(),
            ),
        )
    calls = len(f.calls)
    removed = await f.client.delete(f"/api/coding/threads/{moved['id']}", params={"delete_worktree": "true"})
    assert removed.status_code == 204, removed.text
    assert len(f.calls) == calls and (orphan / "work.txt").read_text() == "Kept\n"


async def test_expired_owner_keeps_rewind_history_and_deletes_its_helpers_with_it(checkout, monkeypatch):
    f = checkout
    helper = f.helper()
    helper_run = f.repo.create_run(
        f.ws.agent_id,
        {"text": "Inspect", "desktop_copy": helper["id"], "local_read_only": True},
        helper["conversation_id"],
        spec={"working_directory": str(f.folder)},
    )
    f.repo.update_run(helper_run["id"], status="completed", output={"text": "Retained helper answer"})
    f.age()
    await f.ws.cleanup.sweep()
    assert f.owned()["cleanup_state"] == "deleted"
    # Rewind archives are hidden from the sidebar but keep history and code refs
    # in the shared repository after the owning chat is explicitly deleted.
    archived = f.ws.create_thread(
        ThreadInput(folder=str(f.source), title="Before rewind: Retained conversation")
    )
    archived_run = f.repo.create_run(
        f.ws.agent_id,
        {"text": "Previous continuation", "desktop_copy": archived["id"]},
        archived["conversation_id"],
        spec={"working_directory": str(f.folder)},
    )
    f.repo.update_run(archived_run["id"], status="completed", output={"text": "Retained rewind history"})
    # The retained rewind history shows screenshots kept in the deleted chat's folder.
    from wavefunction.computer import screenshot_folder

    f.repo.add_run_event(
        archived_run["id"],
        "screenshot",
        {"file": f"{screenshot_folder(f.thread['conversation_id'])}/0001-shared.png"},
    )
    archived_helper = f.ws.create_thread(
        ThreadInput(folder=str(f.source), title="Helper from archived continuation"),
        parent_thread_id=archived["id"],
        parent_run_id=archived_run["id"],
    )
    newest_archive = f.ws.create_thread(
        ThreadInput(folder=str(f.source), title="Before rewind: Recent continuation")
    )
    archived_ref = "refs/wavefunction/checkpoints/archived-only"
    refs = [
        "refs/wavefunction/checkpoints/own-point",
        "refs/wavefunction/checkpoints/shared-point",
        "refs/wavefunction/checkpoints/shared-base",
    ]
    with f.ws.db() as db:
        db.execute(
            "UPDATE coding_threads SET archived = 1, folder = ? WHERE id = ?", (str(f.folder), archived["id"])
        )
        db.execute(
            "UPDATE coding_threads SET archived = 1, folder = ? WHERE id = ?",
            (str(f.folder), newest_archive["id"]),
        )
        db.execute(
            "UPDATE coding_threads SET folder = ? WHERE id = ?", (str(f.folder), archived_helper["id"])
        )
        # A second rewind moves the earlier journal to the new archive. Both
        # retained histories must remain recognizable through this ownership chain.
        for owner, archive in ((f.thread, newest_archive), (newest_archive, archived)):
            history = {
                "source": archive["conversation_id"],
                "target": owner["conversation_id"],
                "archive": archive["id"],
            }
            db.execute(
                "INSERT INTO coding_recovery (id, thread_id, folder, change_ids, files, state, created_at, updated_at, history) VALUES (?, ?, ?, '[]', '[]', 'rewound', ?, ?, ?)",
                (
                    f"rewind-{archive['id']}",
                    owner["id"],
                    str(f.folder),
                    now_iso(),
                    now_iso(),
                    json.dumps(history),
                ),
            )
        db.execute(
            "INSERT INTO coding_checkpoints (id, thread_id, conversation_id, run_id, kind, event_sequence, folder, code, created_at) VALUES ('archived-point', ?, ?, ?, 'after_assistant', 0, ?, ?, ?)",
            (
                archived["id"],
                archived["conversation_id"],
                archived_run["id"],
                str(f.folder),
                json.dumps({"ref": archived_ref}),
                now_iso(),
            ),
        )
        for index, ref in enumerate(refs):
            db.execute(
                "INSERT INTO coding_checkpoints (id, thread_id, conversation_id, run_id, kind, event_sequence, folder, code, created_at) VALUES (?, ?, ?, ?, 'after_assistant', 0, ?, ?, ?)",
                (
                    f"own-{index}",
                    f.thread["id"],
                    f.thread["conversation_id"],
                    f.run["id"],
                    str(f.folder),
                    json.dumps({"ref": ref}),
                    now_iso(),
                ),
            )
        db.execute(
            "INSERT INTO coding_checkpoints (id, thread_id, conversation_id, run_id, kind, event_sequence, folder, code, created_at) VALUES ('shared', ?, ?, ?, 'after_assistant', 0, ?, ?, ?)",
            (
                helper["id"],
                helper["conversation_id"],
                helper_run["id"],
                str(f.folder),
                json.dumps({"ref": refs[1]}),
                now_iso(),
            ),
        )
        db.execute(
            "UPDATE coding_worktrees SET base_ref = 'refs/wavefunction/checkpoints/own-base' WHERE thread_id = ?",
            (f.thread["id"],),
        )
    f.repo.set_setting("repository_folders", {helper["id"]: str(f.folder), archived["id"]: str(f.folder)})
    forgotten, screenshots = [], []

    async def forget(folder, **request):
        assert folder == str(f.source) and request["action"] == "checkpoint_forget"
        if "refs/wavefunction/checkpoints/own-point" in request["refs"]:
            assert f.owned() is not None  # Metadata remains available until chat deletion cascades.
        forgotten.append(sorted(request["refs"]))

    monkeypatch.setattr(f.ws.checkpoints, "git", forget)
    monkeypatch.setattr(
        coding_workspace, "shutil", SimpleNamespace(rmtree=lambda path, **_kwargs: screenshots.append(path))
    )
    deleted = await f.client.delete(f"/api/coding/threads/{f.thread['id']}")
    assert deleted.status_code == 204, deleted.text
    # The chat's own refs go first; one its helper shares goes with the helper, deleted with it.
    flat = [ref for batch in forgotten for ref in batch]
    assert sorted(flat[:3]) == [
        "refs/wavefunction/checkpoints/own-base",
        "refs/wavefunction/checkpoints/own-point",
        refs[2],
    ]
    assert flat[3:] == [refs[1]]
    assert (await f.client.get(f"/api/coding/threads/{helper['id']}")).status_code == 404
    retained_archive = f.ws.thread(archived["id"])
    assert retained_archive["archived"] and retained_archive["folder"] == str(f.source)
    assert f.ws.thread(newest_archive["id"])["folder"] == str(f.source)
    assert retained_archive["conversation_id"] == archived["conversation_id"]
    assert f.ws.runs(archived["id"])[0]["output"]["text"] == "Retained rewind history"
    retained_helper = f.ws.thread(archived_helper["id"])
    assert retained_helper["parent_thread_id"] == archived["id"] and retained_helper["folder"] == str(
        f.source
    )
    archived_point = f.ws.checkpoints.point(archived["id"], "archived-point")
    assert archived_point["folder"] == str(f.source) and archived_point["code"] == {"ref": archived_ref}
    assert archived_ref not in forgotten
    assert f.repo.get_setting("repository_folders") == {}
    # The chat's screenshots are still shown by the rewind history; only its helper's go.
    from wavefunction.computer import screenshots_root

    assert not f.folder.exists()
    assert screenshots == [
        screenshots_root(f.services.files, f.ws.agent_id) / screenshot_folder(helper["conversation_id"])
    ]


@pytest.fixture
def native_git(tmp_path, monkeypatch):
    source, folder = tmp_path.resolve() / "repo", tmp_path.resolve() / "managed"
    source.mkdir()
    folder.mkdir()
    (folder / ".git").write_text("Mocked metadata\n")
    head, name = "a" * 40, "codex/retained"
    state = {
        "status": b"",
        "index_flag": b"H",
        "locked": False,
        "registered": True,
        "submodule": False,
        "head": head,
        "branch": name,
        "tip": head,
        "hook_failure": False,
    }
    mutations = []

    def fake_git(root, *args, **_kwargs):
        if args == ("worktree", "list", "--porcelain", "-z"):
            if not state["registered"]:
                return b""
            return (
                b"worktree "
                + str(folder).encode()
                + b"\0HEAD "
                + head.encode()
                + (b"\0locked protected" if state["locked"] else b"")
                + b"\0\0"
            )
        if args == ("status", "--porcelain=v1", "-z", "--untracked-files=no"):
            return state["changed"]
        if args == ("ls-files", "-z", "--others"):
            return state["untracked"]
        if args[:1] == ("status",):
            assert args == ("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching")
            return state["status"]
        if args == ("ls-files", "--stage"):
            return b"160000 aaaa 0\tmodule\n" if state["submodule"] else b"100644 aaaa 0\tfile.txt\n"
        if args[:2] == ("update-ref", "-d"):
            return b""
        if args == ("ls-files", "-v", "-z"):
            return state["index_flag"] + b" file.txt\0"
        if args in {("rev-parse", "--verify", "HEAD"), ("rev-parse", "HEAD")}:
            return state["head"].encode()
        if args == ("symbolic-ref", "--quiet", "--short", "HEAD"):
            return state["branch"].encode()
        if args == ("rev-parse", "--verify", "refs/heads/" + name):
            return state["tip"].encode()
        if args[:2] == ("branch", "--contains"):
            assert args == ("branch", "--contains", state["head"], "--format=%(refname:short)")
            return name.encode() if state["tip"] == state["head"] else b""
        if args == ("check-ref-format", "--branch", name):
            return name.encode()
        if args[:2] in {("worktree", "remove"), ("worktree", "add")}:
            mutations.append((root, args))
            return b""
        if args[:1] in {("reset",), ("hook",)}:
            mutations.append((root, args))
            if args[0] == "hook" and state["hook_failure"]:
                raise RuntimeError("Post-checkout hook failed")
            return b""
        raise AssertionError(f"Unexpected Git invocation: {root}, {args}")

    monkeypatch.setattr(coding_git_ops, "git", fake_git)
    return SimpleNamespace(
        source=source, folder=folder, head=head, name=name, state=state, mutations=mutations
    )


def test_native_cleanup_rechecks_clean_registered_checkout_before_removal(native_git, tmp_path, monkeypatch):
    source, folder, head, name, state, mutations = (
        native_git.source,
        native_git.folder,
        native_git.head,
        native_git.name,
        native_git.state,
        native_git.mutations,
    )
    request = {
        "root": str(source),
        "action": "worktree_cleanup_remove",
        "destination": str(folder),
        "head": head,
        "branch": name,
    }
    assert coding_git_ops.operate({**request, "action": "worktree_cleanup_check"}) == {
        "head": head,
        "branch": name,
    }
    assert not mutations
    for status in (b" M file.txt\0", b"?? new.txt\0", b"!! .env\0"):
        state["status"] = status
        with pytest.raises(ValueError, match="unsaved or ignored"):
            coding_git_ops.operate(request)
        assert not mutations
    state["status"] = b""
    for flag in (b"h", b"S", b"s"):
        state["index_flag"] = flag
        with pytest.raises(ValueError, match="assume-unchanged or skip-worktree"):
            coding_git_ops.operate(request)
        assert not mutations  # Hidden tracked edits must never reach removal.
    state["index_flag"] = b"H"
    for field, invalid in (
        ("locked", True),
        ("registered", False),
        ("submodule", True),
        ("branch", ""),
        ("tip", "b" * 40),
    ):
        original = state[field]
        state[field] = invalid
        with pytest.raises(ValueError):
            coding_git_ops.operate(request)
        assert not mutations
        state[field] = original
    with pytest.raises(ValueError, match="changed before cleanup"):
        coding_git_ops.operate({**request, "head": "b" * 40})
    linked = tmp_path / "linked"
    linked.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError, match="linked"):
        coding_git_ops.operate({**request, "destination": str(linked)})
    assert not mutations
    assert coding_git_ops.operate(request) == {"head": head, "branch": name}
    assert mutations == [(source, ("worktree", "remove", str(folder)))]  # Mock only; never executed.
    mutations.clear()
    # A helper's checkout may hold build output: it goes only if the rest is exactly its saved
    # final state and nothing private is in it.
    helper = {**request, "tree": "final-tree", "owner": "helper-thread"}
    monkeypatch.setattr(
        coding_snapshots,
        "capture",
        lambda _root, owner, point: {
            "tree": state["snapshot"],
            "ref": f"refs/wavefunction/checkpoints/{point}",
        },
    )
    # Code history leaves private files out, so one that differs from the branch keeps it: tracked,
    # untracked, or inside an ignored folder.
    for snapshot, changed, untracked, refusal in (
        ("edited-tree", b"", b"node_modules/x.js\0", "changed after"),
        ("final-tree", b"", b"node_modules/x.js\0.env.local\0", "private files"),
        ("final-tree", b"", b"node_modules/pkg/.env\0", "private files"),
        ("final-tree", b" M .env.example\0", b"", "private files"),
    ):
        state["snapshot"], state["changed"], state["untracked"] = snapshot, changed, untracked
        with pytest.raises(ValueError, match=refusal):
            coding_git_ops.operate(helper)
        assert not mutations
    state["changed"], state["untracked"] = b"", b"node_modules/x.js\0"
    assert coding_git_ops.operate(helper) == {"head": head, "branch": name}
    assert mutations == [
        (source, ("worktree", "remove", "--force", str(folder)))
    ]  # Mock only; never executed.
    mutations.clear()
    state["status"] = b""
    explicit = {"root": str(source), "action": "worktree_delete", "destination": str(folder)}
    state["status"] = b" M file.txt\0?? new.txt\0!! .env\0"
    state["index_flag"] = b"S"
    state["submodule"] = True
    for field, invalid in (("locked", True), ("registered", False), ("tip", "b" * 40)):
        original = state[field]
        state[field] = invalid
        with pytest.raises(ValueError):
            coding_git_ops.operate(explicit)
        assert not mutations
        state[field] = original
    with pytest.raises(ValueError, match="linked"):
        coding_git_ops.operate({**explicit, "destination": str(linked)})
    assert not mutations
    assert coding_git_ops.operate(explicit) == {"removed": str(folder)}
    assert mutations == [(source, ("worktree", "remove", "--force", str(folder)))]
    assert state["branch"] == name and state["tip"] == head  # No branch deletion/reset dispatched.
    assert folder.is_dir()  # The recorded force invocation never executes.
    mutations.clear()
    with pytest.raises(ValueError):
        coding_git_ops.operate({**explicit, "destination": str(source)})
    assert not mutations
    # Retry after interrupted removal clears only the remaining Git registration;
    # once that too is gone, another retry is harmless.
    folder.rename(tmp_path / "preserved-explicit-checkout")
    assert coding_git_ops.operate(explicit) == {"removed": str(folder)}
    assert mutations == [(source, ("worktree", "remove", "--force", str(folder)))]
    mutations.clear()
    state["registered"] = False
    assert coding_git_ops.operate(explicit) == {"removed": str(folder)}
    assert not mutations and source.is_dir()


def test_native_restore_preserves_files_and_requires_retained_branch_and_successful_hook(
    native_git, tmp_path, monkeypatch
):
    source, folder, head, name, state, mutations = (
        native_git.source,
        native_git.folder,
        native_git.head,
        native_git.name,
        native_git.state,
        native_git.mutations,
    )
    restore = {
        "root": str(source),
        "action": "worktree_restore",
        "destination": str(folder),
        "head": head,
        "branch": name,
    }
    with pytest.raises(ValueError, match="occupied"):
        coding_git_ops.operate(restore)
    folder.rename(tmp_path / "preserved-synthetic-checkout")
    state["tip"] = "b" * 40
    with pytest.raises(ValueError, match="retained branch changed"):
        coding_git_ops.operate(restore)
    assert not mutations
    state["tip"] = head
    assert coding_git_ops.operate(restore) == {"head": head, "branch": name}
    assert mutations == [(source, ("worktree", "add", "--no-checkout", str(folder), name))]
    mutations.clear()
    folder.mkdir()
    (folder / ".git").write_text("Restored mocked registration\n")
    checkout = {"root": str(folder), "action": "worktree_restore_checkout", "head": head, "branch": name}
    state["head"] = "b" * 40
    with pytest.raises(ValueError, match="restoring checkout changed"):
        coding_git_ops.operate(checkout)
    assert not mutations
    state["head"] = head
    monkeypatch.setattr(coding_git_ops, "state", lambda root: {"head": head, "root": str(root)})
    assert coding_git_ops.operate(checkout)["state"]["root"] == str(folder)
    assert mutations[0] == (folder, ("reset", "--hard", "--no-recurse-submodules", "HEAD"))
    assert all(root == folder for root, _args in mutations)
    mutations.clear()
    (folder / "file.txt").write_text("Files appeared after an interrupted checkout\n")
    state["status"] = b"?? file.txt\0"
    with pytest.raises(ValueError, match="restoring checkout contains changes"):
        coding_git_ops.operate(checkout)
    assert not mutations
    state["status"] = b""
    state["hook_failure"] = True
    with pytest.raises(RuntimeError, match="Post-checkout hook failed"):
        coding_git_ops.operate(checkout)
    hook = (folder, ("hook", "run", "--ignore-missing", "post-checkout", "--", "0" * len(head), head, "1"))
    assert mutations == [hook]
    assert (folder / "file.txt").read_text() == "Files appeared after an interrupted checkout\n"
    mutations.clear()
    state["hook_failure"] = False
    assert coding_git_ops.operate(checkout)["state"]["head"] == head
    assert mutations == [hook]  # Retry the hook; never reset already populated files.
