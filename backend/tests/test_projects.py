"""Projects: worktree chats, agent-made projects, appearance, order and removal."""

import json
from pathlib import Path

import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create

from wavefunction.coding_workspace import CodingWorkspace


@pytest.mark.sandbox
async def test_new_worktree_chats_select_a_base_and_persist_project_ownership(coding, tmp_path, monkeypatch):
    import shutil

    from support import git, repository

    client, ws, services = coding
    folder = repository(tmp_path)
    initial = git(folder, "rev-parse", "HEAD")
    name = "release-" * 26
    local_ref, remote_ref = f"refs/heads/{name}", f"refs/remotes/origin/{name}"
    git(folder, "branch", name)
    (folder / "a.txt").write_text("Current HEAD\n")
    git(folder, "commit", "-am", "Advance the checked-out branch")
    head = git(folder, "rev-parse", "HEAD")
    git(folder, "update-ref", remote_ref, head)
    git(folder, "symbolic-ref", "refs/remotes/origin/HEAD", remote_ref)
    (folder / "a.txt").write_text("Uncommitted source work\n")
    response = await client.post("/api/coding/projects", json={"name": "Application", "folder": str(folder)})
    assert response.status_code == 201, response.text
    project = response.json()
    first = await create(client, project_id=project["id"])
    branches = await client.get(f"/api/coding/threads/{first['id']}/git/branches")
    assert branches.status_code == 200, branches.text
    assert branches.json()["current"] == "refs/heads/main" and branches.json()["head"] == head
    assert branches.json()["default"] == local_ref
    assert {entry["ref"] for entry in branches.json()["branches"]} == {
        "refs/heads/main",
        local_ref,
        remote_ref,
    }
    assert all(entry["name"] for entry in branches.json()["branches"])
    owned_parent = folder.parent / ".harness-worktrees"
    try:
        second = await create(client, project_id=project["id"], title="Independent task", worktree=True)
        owned = Path(second["folder"])
        assert owned != folder and (owned / "a.txt").read_text() == "Current HEAD\n"
        assert (folder / "a.txt").read_text() == "Uncommitted source work\n"
        assert git(owned, "rev-parse", "HEAD") == head
        assert first["conversation_id"] != second["conversation_id"]
        assert second["worktree"] and not first["worktree"]
        assert len(services.repository.agent_ids()) == 1
        (owned / "a.txt").write_text("Only the new checkout\n")
        assert (folder / "a.txt").read_text() == "Uncommitted source work\n"
        restarted = CodingWorkspace(services)
        assert restarted.agent_id == ws.agent_id
        assert {t["id"]: t["worktree"] for t in restarted.snapshot()["threads"]} == {
            first["id"]: False,
            second["id"]: True,
        }
        assert restarted.effective_folder(first) == str(folder)
        assert restarted.effective_folder(restarted.thread(second["id"])) == str(owned)
        owned_row = restarted.delivery.worktree(second["id"])
        assert owned_row and owned_row["source"] == str(folder)
        detached = await client.patch(f"/api/coding/threads/{second['id']}", json={"project_id": None})
        assert detached.status_code == 200, detached.text
        assert restarted.thread(second["id"])["project_id"] is None
        assert restarted.effective_folder(restarted.thread(second["id"])) == str(owned)

        # A new worktree starts from a commit and never reads the source's status, which a
        # repository with large untracked folders can make too slow or too long to list.
        from wavefunction import coding_git_ops

        def unread(root):
            raise AssertionError("worktree_add read the repository status")

        (tmp_path / "unread").mkdir()
        source = repository(tmp_path / "unread").resolve()
        with monkeypatch.context() as patched:
            patched.setattr(coding_git_ops, "state", unread)
            added = coding_git_ops.operate(
                {
                    "root": str(source),
                    "action": "worktree_add",
                    "branch": "codex/unread",
                    "destination": str(tmp_path / "unread/checkout"),
                }
            )
        assert added == {"head": git(source, "rev-parse", "HEAD"), "branch": "codex/unread"}

        # Git-valid full refs longer than 200 characters work on both creation routes,
        # and distinguish a local name from the similarly named remote branch.
        local = await create(client, folder=str(folder), worktree=True, worktree_base=local_ref)
        assert git(local["folder"], "rev-parse", "HEAD") == initial
        assert (Path(local["folder"]) / "a.txt").read_text() == "before\n"

        admitted = []

        async def start_run(*args, **kwargs):
            admitted.append(kwargs)
            # A whole run, as start_run returns one: the route's response model needs every field.
            return {
                "id": "synthetic-admission",
                "project_id": ws.agent_id,
                "conversation_id": kwargs["conversation_id"],
                "status": "queued",
                "input": {"text": ""},
                "output": None,
                "error": None,
                "started_at": "",
                "completed_at": None,
                "duration_ms": None,
                "usage": {},
                "kind": "user",
                "parent_run_id": None,
            }

        monkeypatch.setattr(services.runtime, "start_run", start_run)
        before, grants = ws.snapshot(), services.repository.get_setting("folder_grants")
        endpoint = f"/api/coding/threads/{first['id']}/messages"
        for ref in ("refs/heads/missing", "--all"):
            failed = await client.post(
                endpoint, json={"content": "Keep this draft", "worktree": True, "worktree_base": ref}
            )
            assert failed.status_code == 422, failed.text
            assert not admitted and not ws.runs(first["id"])
            assert ws.snapshot() == before
            assert services.repository.get_setting("folder_grants") == grants
        failed = await client.post(
            "/api/coding/threads",
            json={"folder": str(folder), "worktree": True, "worktree_base": "refs/heads/missing"},
        )
        assert failed.status_code == 422, failed.text
        assert ws.snapshot() == before
        assert sorted(services.repository.get_setting("folder_grants"), key=lambda g: g["path"]) == sorted(
            grants, key=lambda g: g["path"]
        )

        # Resolve the selected branch on send, even if its locally known tip moved
        # since the dropdown was loaded. No fetch or source checkout is needed.
        git(folder, "update-ref", remote_ref, initial)

        async def invalid_context(*args, **kwargs):
            raise ValueError("Selected context is no longer available.")

        with monkeypatch.context() as unavailable:
            unavailable.setattr(ws.context, "expand", invalid_context)
            sent = await client.post(
                endpoint,
                json={
                    "content": "Start on the release branch",
                    "worktree": True,
                    "worktree_base": remote_ref,
                },
            )
        assert sent.status_code == 422 and "Selected context" in sent.text
        remote = Path(ws.thread(first["id"])["folder"])
        assert git(remote, "rev-parse", "HEAD") == initial
        assert (remote / "a.txt").read_text() == "before\n"
        assert not admitted and not ws.runs(first["id"])
        owned_before = ws.delivery.worktree(first["id"])
        before = ws.thread(first["id"])
        rejected = await client.post(
            endpoint,
            json={"content": "Keep this draft", "worktree": True, "worktree_base": "refs/heads/main"},
        )
        assert rejected.status_code == 422 and "Refresh" in rejected.text
        assert ws.thread(first["id"]) == before and ws.delivery.worktree(first["id"]) == owned_before
        assert not admitted and not ws.runs(first["id"])
        # Legacy retries without an explicit new base reuse the prepared checkout.
        sent = await client.post(endpoint, json={"content": "Retry the prepared worktree", "worktree": True})
        assert sent.status_code == 202, sent.text
        assert len(admitted) == 1 and admitted[0]["spec"].working_directory == str(remote)
        assert admitted[0]["conversation_id"] == first["conversation_id"]
        assert (folder / "a.txt").read_text() == "Uncommitted source work\n"
        assert set(owned_parent.iterdir()) == {owned, Path(local["folder"]), remote}

    finally:
        # Only this fixture's worktrees; do not execute removal command examples.
        if owned_parent.exists():
            assert owned_parent.is_relative_to(tmp_path)
            shutil.rmtree(owned_parent)
        assert not owned_parent.exists()


@pytest.mark.sandbox
async def test_the_agent_creates_a_project_from_a_repository_archives_stale_chats_and_removes_a_project(
    coding, tmp_path
):
    """Asked in chat: a project in a new folder with a repository cloned into it, a project's
    chats idle for a month archived, a project removed. The new folder asks on the folder card;
    the rest can be taken back, so it is done as asked, leaving the files and histories alone."""
    from datetime import UTC, datetime, timedelta

    from support import repository

    client, ws, services = coding
    source = repository(tmp_path)
    ws.allow_folder(str(source), writable=False)
    home, old = tmp_path / "home", tmp_path / "old"
    home.mkdir()
    old.mkdir()
    (old / "keep.txt").write_text("untouched\n")
    ops = (await client.post("/api/coding/projects", json={"name": "Ops", "folder": str(home)})).json()
    legacy = (await client.post("/api/coding/projects", json={"name": "Old work", "folder": str(old)})).json()
    main = await create(client, project_id=ops["id"], title="Main")
    stale, pinned, recent = [
        await create(client, project_id=legacy["id"], title=title) for title in ("Stale", "Pinned", "Recent")
    ]
    month_ago = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with ws.db() as db:
        db.execute(
            "UPDATE coding_threads SET updated_at = ? WHERE id IN (?, ?)",
            (month_ago, stale["id"], pinned["id"]),
        )
        db.execute("UPDATE coding_threads SET pinned = 1 WHERE id = ?", (pinned["id"],))
    target = tmp_path / "work" / "app"
    script_gateway(
        services.models,
        [
            tool_call_message("search_available_tools", {"query": "projects"}, "find"),
            tool_call_message("create_project", {"name": "App", "folder": str(target)}, "create"),
            tool_call_message(
                "run_command", {"command": f"git clone -q {source} .", "workdir": str(target)}, "clone"
            ),
            tool_call_message("archive_chats", {"project": "Old work", "older_than_days": 30}, "archive"),
            tool_call_message("remove_project", {"project": "old WORK"}, "remove"),
            tool_call_message("remove_project", {"project": "Nope"}, "unknown"),
            AIMessage(content="Done."),
        ],
    )
    run = await ws.send(
        main["id"],
        "Make an App project in work/app from my repo, archive old chats in Old work, then remove it",
    )
    assert (await services.runtime.wait(run["id"]))["status"] == "waiting_approval"
    pause = next(
        e
        for e in reversed(services.repository.list_run_events(run["id"]))
        if e["event_type"] == "run_waiting_for_approval"
    )
    request = pause["payload"]["requests"][0]
    assert (request["kind"], request["folder"], request["writable"]) == (
        "folder_access",
        str(target.resolve()),
        True,
    )
    assert (
        request["description"].startswith("Create the project “App” in a new folder.") and not target.exists()
    )
    response = await client.post(
        f"/api/runs/{run['id']}/approval",
        json={"decision": "approve", "expected_approval": pause["sequence"]},
    )
    assert response.status_code == 202, response.text
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"

    def said(call):
        return next(
            str(e["payload"].get("result", ""))
            for e in services.repository.list_run_events(run["id"])
            if e["event_type"] in ("tool_completed", "tool_failed") and e["payload"].get("id") == call
        )

    # The new project's folder holds the clone, made inside the sandbox with the access just given.
    app = next(p for p in ws.snapshot()["projects"] if p["name"] == "App")
    assert app["folder"] == str(target.resolve()) and (target / "a.txt").read_text() == "before\n"
    assert "[exit code 0]" in said("clone")
    # Only the idle, unpinned chat was archived; the agent's own and recent chats stayed.
    archived = json.loads(said("archive"))
    assert [chat["id"] for chat in archived["archived"]] == [stale["id"]] and archived["archived_count"] == 1
    assert archived["left_in_place"] == {"pinned": 1}
    assert [ws.thread(t["id"])["archived"] for t in (stale, pinned, recent, main)] == [
        True,
        False,
        False,
        False,
    ]
    # Removing the project left its folder, files and chats, which keep working there.
    assert legacy["id"] not in {p["id"] for p in ws.snapshot()["projects"]}
    assert all(
        ws.thread(t["id"])["project_id"] is None and ws.thread(t["id"])["folder"] == str(old.resolve())
        for t in (stale, pinned, recent)
    )
    assert (old / "keep.txt").read_text() == "untouched\n"
    assert "No project is called 'Nope'" in said("unknown")


async def test_project_appearance_validates_and_persists_on_create_and_edit(coding, tmp_path):
    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    payload = {"name": "Project", "folder": str(folder)}
    project = (await client.post("/api/coding/projects", json=payload)).json()
    assert project["icon"] == "folder" and project["icon_color"] is None
    response = await client.post(
        "/api/coding/projects", json={"name": "Styled", "icon": "terminal", "icon_color": "#5896ed"}
    )
    assert response.status_code == 201, response.text
    styled = response.json()
    assert styled["icon"] == "terminal" and styled["icon_color"] == "#5896ed"
    assert ws.project(project["id"]) == project
    before = ws.snapshot()
    for invalid in [{"icon": "unknown"}, {"icon_color": "red"}]:
        assert (
            await client.post("/api/coding/projects", json={"name": "Rejected", **invalid})
        ).status_code == 422
    assert ws.snapshot() == before
    url = "/api/coding/projects/" + project["id"]
    for invalid in [
        {"icon": "missing-icon"},
        {"icon_color": "url(https://invalid.test)"},
        {"icon_color": "#12"},
        {"icon": None},
    ]:
        response = await client.patch(url, json={**payload, **invalid})
        assert response.status_code == 422, response.text
        assert ws.project(project["id"])["icon"] == "folder"
        assert ws.project(project["id"])["icon_color"] is None
    response = await client.patch(url, json={**payload, "icon": "rocket", "icon_color": "#Aa80E5"})
    assert response.status_code == 200, response.text
    assert response.json()["icon"] == "rocket"
    assert response.json()["icon_color"] == "#Aa80E5"
    # Editing other settings does not reset the appearance.
    assert (await client.patch(url, json=payload)).json()["icon"] == "rocket"
    assert CodingWorkspace(services).project(project["id"])["icon_color"] == "#Aa80E5"
    assert (await client.patch(url, json={**payload, "icon_color": None})).json()["icon_color"] is None


async def test_project_order_removal_and_return_persist_and_upgrade_old_databases(coding, tmp_path):
    client, ws, services = coding
    first, second, third = [
        (await client.post("/api/coding/projects", json={"name": name})).json()
        for name in ("First", "Second", "Third")
    ]
    assert [first["position"], second["position"], third["position"]] == [0, 1, 2]
    ids = [third["id"], first["id"], second["id"]]
    response = await client.put("/api/coding/projects/order", json={"ids": ids})
    assert response.status_code == 200, response.text
    assert [p["id"] for p in response.json()] == ids
    for invalid in [ids[:2], [*ids, ids[0]], [ids[0], ids[0], ids[1]], [*ids[:2], "missing"]]:
        rejected = await client.put("/api/coding/projects/order", json={"ids": invalid})
        assert rejected.status_code == 422, rejected.text
    assert [p["id"] for p in CodingWorkspace(services).snapshot()["projects"]] == ids
    fourth = (await client.post("/api/coding/projects", json={"name": "Fourth"})).json()
    assert [p["id"] for p in ws.snapshot()["projects"]] == [*ids, fourth["id"]]
    # A database from before reordering keeps its projects in creation order and adds new ones last.
    with ws.db() as db:
        db.execute("ALTER TABLE coding_projects DROP COLUMN position")
    created = [first["id"], second["id"], third["id"], fourth["id"]]
    assert [p["id"] for p in CodingWorkspace(services).snapshot()["projects"]] == created
    fifth = (await client.post("/api/coding/projects", json={"name": "Fifth"})).json()
    assert [p["id"] for p in ws.snapshot()["projects"]] == [*created, fifth["id"]]
    # Removing a project leaves its chats the slash commands they used, which were the project's.
    chat = await create(client, project_id=fifth["id"])
    workflow = f"/api/coding/threads/{chat['id']}/workflow"
    deploy = {"name": "deploy", "description": "Deploy", "body": "Deploy: $ARGUMENTS"}
    with ws.db() as db:  # Commands saved on the project, and the chat's own from before it joined it.
        db.execute(
            "INSERT INTO coding_workflows VALUES (?, ?, '')",
            ("project:" + fifth["id"], json.dumps({"prompts": [deploy]})),
        )
        db.execute(
            "INSERT INTO coding_workflows VALUES (?, '{\"prompts\": []}', '')", ("thread:" + chat["id"],)
        )
    assert (await client.delete(f"/api/coding/projects/{fifth['id']}")).status_code == 204
    assert (await client.get(workflow)).json()["prompts"] == [deploy]
    # A project still on the default commands leaves its chats on them, too.
    plain = (await client.post("/api/coding/projects", json={"name": "Plain"})).json()
    joined = await create(client, project_id=plain["id"])
    with ws.db() as db:
        db.execute(
            "INSERT INTO coding_workflows VALUES (?, '{\"prompts\": []}', '')", ("thread:" + joined["id"],)
        )
    assert (await client.delete(f"/api/coding/projects/{plain['id']}")).status_code == 204
    commands = (await client.get(f"/api/coding/threads/{joined['id']}/workflow")).json()["prompts"]
    assert [prompt["name"] for prompt in commands] == ["review", "commit", "pr", "test"]
    # Instructions saved on projects before they were dropped move to the projects' chats.
    guided, unguided = [
        await create(client, project_id=first["id"], instructions=text) for text in ("Mine.", "")
    ]
    with ws.db() as db:
        db.execute("ALTER TABLE coding_projects ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE coding_projects SET instructions = 'Keep tests.' WHERE id = ?", (first["id"],))
    CodingWorkspace(services)
    with ws.db() as db:
        assert "instructions" not in {row["name"] for row in db.execute("PRAGMA table_info(coding_projects)")}
    assert [ws.thread(t["id"])["instructions"] for t in (guided, unguided, joined)] == [
        "Keep tests.\n\nMine.",
        "Keep tests.",
        "",
    ]
    # A project made again for a folder takes back the chats the removed one left there and in its
    # worktrees, and no others.
    repo, worktree, other = (tmp_path / name for name in ("repo", "repo-task", "other"))
    for folder in (repo, worktree, other):
        folder.mkdir()
    removed = (await client.post("/api/coding/projects", json={"name": "Repo", "folder": str(repo)})).json()
    in_repo = await create(client, project_id=removed["id"])
    in_worktree = await create(client, project_id=removed["id"], folder=str(worktree))
    elsewhere = await create(client, folder=str(other))
    with ws.db() as db:
        db.execute(
            "INSERT INTO coding_workflows VALUES (?, ?, '')",
            ("project:" + removed["id"], json.dumps({"prompts": [deploy]})),
        )
        db.execute(
            "INSERT INTO coding_worktrees (thread_id, source, folder, branch, created_at) VALUES (?, ?, ?, 'task', '')",
            (in_worktree["id"], removed["folder"], in_worktree["folder"]),
        )
    assert (await client.delete(f"/api/coding/projects/{removed['id']}")).status_code == 204
    assert {ws.thread(t["id"])["project_id"] for t in (in_repo, in_worktree)} == {None}
    again = (await client.post("/api/coding/projects", json={"name": "Repo", "folder": str(repo)})).json()
    assert [ws.thread(t["id"])["project_id"] for t in (in_repo, in_worktree, elsewhere)] == [
        again["id"],
        again["id"],
        None,
    ]
    workflow = (await client.get(f"/api/coding/threads/{in_worktree['id']}/workflow")).json()
    assert (workflow["scope"], workflow["prompts"]) == ("project", [deploy])
    # When the repository moves, the chats taken back follow the project, as its new ones do; a
    # worktree chat keeps its worktree.
    moved = tmp_path / "repo-moved"
    moved.mkdir()
    fresh = await create(client, project_id=again["id"])
    edited = await client.patch(
        f"/api/coding/projects/{again['id']}", json={"name": "Repo", "folder": str(moved)}
    )
    assert edited.status_code == 200, edited.text
    folders = {t["id"]: t["folder"] for t in (await client.get("/api/coding/workspace")).json()["threads"]}
    assert [folders[t["id"]] for t in (fresh, in_repo, in_worktree)] == [
        str(moved.resolve()),
        str(moved.resolve()),
        in_worktree["folder"],
    ]
