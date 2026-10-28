"""Exercise actual Git, Seatbelt, nonempty storage, and conversation ownership."""

import asyncio
import base64
import gzip
import json
import os
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage, ToolMessage
from support import create, git, repository

from wavefunction.github_proxy import GitHubProxy, pkt, ref_updates


async def state(client, thread):
    response = await client.get(f"/api/coding/threads/{thread['id']}/git/state")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.sandbox
async def test_agent_fetches_over_network_by_default_preserves_edits_and_honors_offline(coding, tmp_path):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    client, ws, services = coding
    folder = repository(tmp_path)
    initial = git(folder, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--bare", str(folder), str(remote))
    # The upstream commit is made in another clone, so this checkout stays on the initial one.
    upstream = tmp_path / "upstream"
    git(tmp_path, "clone", "-q", str(remote), str(upstream))
    (upstream / "a.txt").write_text("upstream change\n")
    git(
        upstream,
        "-c",
        "user.name=Synthetic Tester",
        "-c",
        "user.email=synthetic@example.invalid",
        "commit",
        "-qam",
        "New upstream commit",
    )
    latest = git(upstream, "rev-parse", "HEAD")
    git(upstream, "push", "-q", "origin", "main")
    git(remote, "update-server-info")
    (folder / "other.txt").write_text("uncommitted local work\n")
    requests = []

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            requests.append(args)

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(tmp_path)))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        git(folder, "remote", "add", "origin", f"http://127.0.0.1:{server.server_port}/remote.git")
        thread = await create(client, folder=str(folder))
        assert (await client.get("/api/coding/workspace")).json()["access"] == "network"
        script_gateway(
            services.models,
            [
                tool_call_message(
                    "run_command", {"command": "git ls-remote origin refs/heads/main"}, "remote-head"
                ),
                tool_call_message("run_command", {"command": "git fetch origin"}, "fetch"),
                AIMessage(content="Fetched"),
            ],
        )
        run = await ws.send(thread["id"], "Fetch the latest code")
        assert (await services.runtime.wait(run["id"]))["status"] == "completed"
        assert requests and git(folder, "rev-parse", "origin/main") == latest
        events = services.repository.list_run_events(run["id"])
        assert latest in next(
            event["payload"]["result"]
            for event in events
            if event["event_type"] == "tool_completed" and event["payload"].get("id") == "remote-head"
        )
        assert git(folder, "rev-parse", "HEAD") == initial
        assert (folder / "other.txt").read_text() == "uncommitted local work\n"

        script_gateway(
            services.models,
            [
                tool_call_message("run_command", {"command": "git merge --ff-only origin/main"}, "update"),
                AIMessage(content="Updated"),
            ],
        )
        run = await ws.send(thread["id"], "Update the checkout")
        assert (await services.runtime.wait(run["id"]))["status"] == "completed"
        assert git(folder, "rev-parse", "HEAD") == latest
        assert (folder / "other.txt").read_text() == "uncommitted local work\n"

        await client.put("/api/coding/access", json={"access": "sandboxed"})
        count = len(requests)
        script_gateway(
            services.models,
            [
                tool_call_message("run_command", {"command": "git fetch origin"}, "offline-fetch"),
                AIMessage(content="Offline"),
            ],
        )
        run = await ws.send(thread["id"], "Fetch again")
        await services.runtime.wait(run["id"])
        events = services.repository.list_run_events(run["id"])
        offline = next(
            event["payload"]["result"]
            for event in events
            if event["event_type"] == "tool_completed" and event["payload"].get("id") == "offline-fetch"
        )
        assert not offline.startswith("[exit code 0]")
        assert len(requests) == count
        assert not any(event["event_type"] == "action_outcome_unknown" for event in events)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.mark.sandbox
async def test_changes_tab_diffs_staged_unstaged_renamed_and_copied_files(coding, tmp_path):
    client, _, _ = coding
    folder = repository(tmp_path)
    (folder / "a.txt").write_text("\n".join(f"line {n}" for n in range(30)) + "\n")
    git(folder, "add", "a.txt")
    git(folder, "commit", "-m", "Long fixture")
    thread = await create(client, folder=str(folder))
    original = (folder / "a.txt").read_text()
    # One edit staged, a second one left unstaged.
    (folder / "a.txt").write_text(original.replace("line 1\n", "first edit\n"))
    git(folder, "add", "a.txt")
    changed = original.replace("line 1\n", "first edit\n").replace("line 28\n", "second edit\n")
    (folder / "a.txt").write_text(changed)
    endpoint = f"/api/coding/threads/{thread['id']}/git/diff"
    unstaged = (await client.get(endpoint, params={"path": "a.txt"})).json()
    assert (
        len(unstaged["hunks"]) == 1
        and "second edit" in unstaged["diff"]
        and "first edit" not in unstaged["diff"]
    )
    staged = (await client.get(endpoint, params={"path": "a.txt", "staged": True})).json()
    assert "first edit" in staged["diff"] and "second edit" not in staged["diff"]
    combined = await client.get(endpoint, params={"path": "a.txt", "combined": True})
    assert combined.status_code == 200, combined.text
    assert "first edit" in combined.json()["diff"] and "second edit" in combined.json()["diff"]
    # A reverted staged edit is absent from the final file preview.
    (folder / "a.txt").write_text(changed.replace("first edit\n", "line 1\n"))
    combined = (await client.get(endpoint, params={"path": "a.txt", "combined": True})).json()
    assert "first edit" not in combined["diff"] and "second edit" in combined["diff"]
    (folder / "a.txt").write_text(changed)
    # Renames retain the original file in the comparison, including later edits.
    git(folder, "mv", "a.txt", "renamed.txt")
    renamed = (await client.get(endpoint, params={"path": "renamed.txt", "combined": True})).json()
    assert "rename from a.txt" in renamed["diff"] and "rename to renamed.txt" in renamed["diff"]
    assert "first edit" in renamed["diff"] and "second edit" in renamed["diff"]
    (folder / "a.txt").write_text("recreated untracked content\n")
    for filename in ("a.txt", "renamed.txt"):
        recreated = (await client.get(endpoint, params={"path": filename, "combined": True})).json()
        assert recreated["warning"] and not recreated["diff"]
    assert (await client.get(endpoint, params={"path": "../outside", "combined": True})).status_code == 422
    assert (await client.get(endpoint, params={"path": ".git/config", "combined": True})).status_code == 422
    # A copied destination must not include its source's independent edits.
    git(folder, "config", "status.renames", "copies")
    (folder / "copy.txt").write_text("other before\n")
    (folder / "other.txt").write_text("other before\nindependent source edit\n")
    git(folder, "add", "copy.txt", "other.txt")
    copied_file = next(item for item in (await state(client, thread))["files"] if item["path"] == "copy.txt")
    assert copied_file["index"] == "C" and copied_file["original_path"] == "other.txt"
    copied = (await client.get(endpoint, params={"path": "copy.txt", "combined": True})).json()
    assert "+other before" in copied["diff"]
    assert "independent source edit" not in copied["diff"] and "diff --git a/other.txt" not in copied["diff"]

    # A repository without commits: the combined diff shows the latest content of a staged file.
    fresh = tmp_path / "new-repo"
    fresh.mkdir()
    git(fresh, "init", "-b", "feature")
    (fresh / "new.txt").write_text("new content\n")
    git(fresh, "add", "new.txt")
    (fresh / "new.txt").write_text("latest content\n")
    newer = await create(client, folder=str(fresh))
    combined = await client.get(
        f"/api/coding/threads/{newer['id']}/git/diff", params={"path": "new.txt", "combined": True}
    )
    assert combined.status_code == 200 and "+latest content" in combined.json()["diff"]
    assert "+new content" not in combined.json()["diff"]


def test_git_state_counts_unlisted_changes_and_flags_conflicts(tmp_path):
    # Past the listed changes (5,000; 1 here) the state keeps the total and the conflict.
    from wavefunction import coding_git_ops

    folder = repository(tmp_path)
    git(folder, "switch", "-c", "theirs")
    (folder / "z.txt").write_text("theirs\n")
    git(folder, "add", "z.txt")
    git(folder, "commit", "-m", "Theirs")
    git(folder, "switch", "main")
    (folder / "z.txt").write_text("ours\n")
    git(folder, "add", "z.txt")
    git(folder, "commit", "-m", "Ours")
    subprocess.run(["git", "-C", str(folder), "merge", "theirs"], capture_output=True)
    (folder / "a.txt").write_text("listed first\n")
    with patch.object(coding_git_ops, "LISTED_FILES", 1):
        current = coding_git_ops.state(folder.resolve())
    assert [file["path"] for file in current["files"]] == ["a.txt"] and current["total_files"] == 2
    assert current["conflicted"]


@pytest.mark.sandbox
async def test_new_worktree_chat_under_a_folder_name_stored_decomposed(coding, tmp_path):
    # Finder stores an accented folder name decomposed (NFD); Git on macOS records it precomposed.
    client, ws, _ = coding
    parent = tmp_path / unicodedata.normalize("NFD", "Développement")
    parent.mkdir()
    folder = repository(parent)
    response = await client.post("/api/coding/threads", json={"folder": str(folder), "worktree": True})
    assert response.status_code == 201, response.text
    thread = response.json()
    branch = f"codex/chat-{thread['id'][:8]}"
    assert (await state(client, thread))["branch"] == branch
    assert (Path(thread["folder"]) / "a.txt").read_text() == "before\n"
    # Removing the checkout first finds it among its repository's worktrees; only that check runs.
    checked = await ws.cleanup.git(ws.delivery.worktree(thread["id"]), "worktree_cleanup_check")
    assert (checked["head"], checked["branch"]) == (git(folder, "rev-parse", "HEAD"), branch)


@pytest.mark.sandbox
async def test_users_own_linked_worktree_reaches_its_shared_git_directory_only(coding, tmp_path):
    client, ws, services = coding
    main = repository(tmp_path)
    feature = tmp_path / "feature"
    git(main, "worktree", "add", "-b", "feature", str(feature))
    (feature / "a.txt").write_text("edited in the user's worktree\n")
    thread = await create(client, folder=str(feature))
    assert [file["path"] for file in (await state(client, thread))["files"]] == ["a.txt"]
    # Code history writes objects and refs there.
    code = await ws.checkpoints.git(feature, action="checkpoint_capture", owner=thread["id"], point="edit")
    assert git(main, "show", f"{code['commit']}:a.txt") == "edited in the user's worktree"
    # A registration written inside a writable folder cannot name an ordinary parent folder.
    forged = tmp_path / "worktrees" / "forged"
    forged.mkdir(parents=True)
    (forged / ".git").write_text(f"gitdir: {forged}\n")
    (forged / "gitdir").write_text(f"{forged / '.git'}\n")
    (forged / "commondir").write_text("../..\n")
    ws.allow_folder(str(forged))
    assert ws.checkout_access(forged)[0] == services.grants()


async def recorded(ws, thread, folder, mutate):
    repo = ws.services.repository
    run = repo.create_run(
        ws.agent_id, {"text": "Synthetic recorded edit"}, thread["conversation_id"], spec=None
    )
    before = await ws.reviews.snapshot(ws.runner(), folder)
    mutate()
    after = await ws.reviews.snapshot(ws.runner(), folder)
    ws.reviews.record(thread["id"], run["id"], folder, before, after, "file")
    repo.update_run(run["id"], status="completed", output={"text": "Edited"})
    with ws.db() as db:
        return {
            row["path"]: dict(row)
            for row in db.execute("SELECT * FROM coding_changes WHERE thread_id = ?", (thread["id"],))
        }


@pytest.mark.sandbox
async def test_incremental_snapshot_reuses_unchanged_bytes_and_sees_external_edits(
    coding, tmp_path, monkeypatch
):
    from wavefunction import change_review

    client, ws, _ = coding
    folder = repository(tmp_path)
    thread = await create(client, folder=str(folder))
    captures = []
    real_operation = change_review.file_operation

    async def observe(runner, request):
        result = await real_operation(runner, request)
        captures.append({path: bool(item.get("cached")) for path, item in result["files"].items()})
        return result

    monkeypatch.setattr(change_review, "file_operation", observe)
    await asyncio.sleep(1.05)  # Files must be outside the coarse-timestamp safety window.
    original = await ws.reviews.snapshot(ws.runner(), folder)
    repeated = await ws.reviews.snapshot(ws.runner(), folder)
    assert original == repeated and not original["warnings"]
    assert captures[-1] == {"a.txt": True, "other.txt": True}
    # An outside save preserves mtime and length; ctime still invalidates the cache.
    path = folder / "a.txt"
    stamp = path.stat()
    path.write_bytes(b"userxx\n")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))

    def edit():
        replacement = folder / "replacement"
        replacement.write_bytes(b"agentx\n")
        os.utime(replacement, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        replacement.replace(path)
        (folder / "other.txt").chmod(0o755)

    changes = await recorded(ws, thread, folder, edit)
    assert captures[-2] == {"a.txt": False, "other.txt": True}
    assert captures[-1] == {"a.txt": False, "other.txt": False}
    # The record starts from the outside save, not the cached bytes.
    assert changes["a.txt"]["before_text"] == "userxx\n" and changes["a.txt"]["after_text"] == "agentx\n"
    assert (
        changes["other.txt"]["kind"] == "modified"
        and changes["other.txt"]["before_text"] == changes["other.txt"]["after_text"]
    )
    # Cached content is not an authority after a grant is revoked.
    ws.services.repository.set_setting("folder_grants", [])
    denied = await ws.reviews.snapshot(ws.runner(), folder)
    assert not denied["files"] and denied["warnings"]


@pytest.mark.sandbox
async def test_file_and_skill_context_capture_with_prompt_and_stale_rejection(coding, tmp_path):
    client, ws, services = coding
    folder = repository(tmp_path)
    (folder / "a.txt").write_text("first\nsecond\nthird\n")
    skill = folder / ".agents/skills/fixture/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: fixture\ndescription: Synthetic\n---\nUse this exact guidance.\n")
    thread = await create(client, folder=str(folder))
    base = f"/api/coding/threads/{thread['id']}"
    file = (await client.get(base + "/file", params={"path": "a.txt"})).json()
    context = {
        "files": [{"path": "a.txt", "start_line": 2, "end_line": 2, "version": file["version"]}],
        "skills": ["fixture"],
        "command": "commit",
    }
    script_gateway(services.models, [AIMessage(content="Prepared message")])
    response = await client.post(base + "/messages", json={"content": "Keep the API", "context": context})
    assert response.status_code == 202, response.text
    run = await services.runtime.wait(response.json()["id"])
    prompt = run["input"]["text"]
    assert "Propose a concise commit" in prompt and "Keep the API" in prompt
    assert "2: second" in prompt and "1: first" not in prompt and "fixture" in prompt
    (folder / "a.txt").write_text("different\n")
    assert (
        await client.post(base + "/messages", json={"content": "Use context", "context": context})
    ).status_code == 422
    assert len(ws.runs(thread["id"])) == 1


async def test_saved_prompts_keep_legacy_configuration(coding, tmp_path):
    client, ws, services = coding
    folder = repository(tmp_path)
    thread = await create(client, folder=str(folder))
    base = f"/api/coding/threads/{thread['id']}/workflow"
    custom = {"name": "verify", "description": "Verify this change", "body": "Check the API: $ARGUMENTS"}
    legacy_test = {
        "name": "test",
        "description": "Run project checks",
        "body": "Run the relevant configured project checks for this change. Fix failures caused by our work, preserve unrelated edits, and report the actual results. $ARGUMENTS",
    }
    legacy = {
        "prompts": [custom, legacy_test],
        "checks": [{"name": "Format", "command": "exit 7", "timeout": 5}],
    }
    with ws.db() as db:
        db.execute(
            "INSERT INTO coding_workflows VALUES (?, ?, ?)",
            (ws.context.owner(thread["id"]), json.dumps(legacy), "2026-10-02T00:00:00Z"),
        )
    config = (await client.get(base)).json()
    assert config["prompts"][0] == custom
    assert "repository instructions" in config["prompts"][1]["body"]
    script_gateway(services.models, [AIMessage(content="Verified")])
    response = await client.post(
        f"/api/coding/threads/{thread['id']}/messages", json={"content": "/verify with a synthetic account"}
    )
    assert response.status_code == 202, response.text
    run = await services.runtime.wait(response.json()["id"])
    assert run["input"]["text"] == "Check the API: with a synthetic account"


async def test_structured_question_answer_ownership_replay_and_restart(coding, tmp_path):
    client, ws, services = coding
    folder = repository(tmp_path)
    thread, other = await create(client, folder=str(folder)), await create(client, folder=str(folder))
    script_gateway(
        services.models,
        [
            tool_call_message(
                "ask_user",
                {
                    "questions": [
                        {
                            "id": "delivery",
                            "title": "Which delivery?",
                            "options": [
                                {"label": "Commit", "description": "Save locally"},
                                {"label": "PR", "description": "Publish for review"},
                            ],
                        }
                    ]
                },
                "question",
            ),
            AIMessage(content="Used the user's decision"),
        ],
    )
    run = await ws.send(thread["id"], "Ask a structured question")
    await services.runtime.wait(run["id"])
    assert services.repository.get_run(run["id"])["status"] == "waiting_approval"
    event = next(e for e in ws.runs(thread["id"])[0]["events"] if e["event_type"] == "run_questions")
    group = event["payload"]["groups"][0]
    data = {"sequence": event["sequence"], "answers": {group["id"] + ":delivery": "Commit"}}
    url = f"/api/coding/threads/{thread['id']}/runs/{run['id']}/answers"
    assert (
        await client.post(f"/api/coding/threads/{other['id']}/runs/{run['id']}/answers", json=data)
    ).status_code == 404
    # Discard in-memory agent; rebuild from the durable checkpoint as after a restart.
    services.runtime.active.pop(run["id"], None)
    response = await client.post(url, json=data)
    assert response.status_code == 200, response.text
    await services.runtime.wait(run["id"])
    assert services.repository.get_run(run["id"])["status"] == "completed"
    assert (await client.post(url, json=data)).status_code == 422


async def test_a_chat_message_answers_open_questions_and_closing_them_lets_the_task_continue(
    coding, tmp_path
):
    """A message typed while the agent waited on its question was queued behind that same task
    and sat at the end of the chat for as long as the task kept asking. It is the answer."""
    client, ws, services = coding
    thread = await create(client, folder=str(repository(tmp_path)))
    question = {
        "questions": [
            {
                "id": "target",
                "title": "Which project?",
                "options": [{"label": "Staging"}, {"label": "Production"}],
            }
        ]
    }
    script_gateway(
        services.models,
        [
            tool_call_message("ask_user", question, "first"),
            AIMessage(content="Using the project from your message"),
        ],
    )
    run = await ws.send(thread["id"], "Set up the app")
    assert (await services.runtime.wait(run["id"]))["status"] == "waiting_approval"
    reply = await client.post(
        f"/api/coding/threads/{thread['id']}/messages",
        json={"content": "Use the staging project, it has the keys"},
    )
    assert reply.status_code == 202 and reply.json()["id"] == run["id"]
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"
    assert [row["id"] for row in ws.runs(thread["id"])] == [run["id"]]
    events = services.repository.list_run_events(run["id"])
    asked = next(e for e in events if e["event_type"] == "run_questions")
    assert (
        next(e for e in events if e["event_type"] == "steering_requested")["payload"]["reply_to"]
        == asked["sequence"]
    )
    assert next(e for e in events if e["event_type"] == "questions_answered")["payload"] == {
        "sequence": asked["sequence"],
        "replied": True,
    }
    saver = await services.runtime.checkpointer()

    async def messages():
        saved = await saver.aget_tuple({"configurable": {"thread_id": thread["conversation_id"]}})
        return saved.checkpoint["channel_values"]["messages"]

    history = await messages()
    answered = max(i for i, m in enumerate(history) if isinstance(m, ToolMessage) and m.name == "ask_user")
    assert json.loads(history[answered].content)["answered_in_chat"] is True
    assert history[answered + 1].content == "Use the staging project, it has the keys"

    script_gateway(
        services.models,
        [
            tool_call_message("ask_user", question, "second"),
            AIMessage(content="Continuing without that decision"),
        ],
    )
    second = await ws.send(thread["id"], "Set up the next part")
    await services.runtime.wait(second["id"])
    asked = next(
        e for e in services.repository.list_run_events(second["id"]) if e["event_type"] == "run_questions"
    )
    url = f"/api/coding/threads/{thread['id']}/runs/{second['id']}/answers"
    closed = {"sequence": asked["sequence"], "dismiss": True}
    assert (await client.post(url, json={**closed, "answers": {"any": "answer"}})).status_code == 422
    assert (await client.post(url, json=closed)).status_code == 200
    assert (await services.runtime.wait(second["id"]))["status"] == "completed"
    history = await messages()
    answered = max(i for i, m in enumerate(history) if isinstance(m, ToolMessage) and m.name == "ask_user")
    assert json.loads(history[answered].content)["dismissed"] is True


class FakeGitHub:
    """GitHub's API and Git over HTTPS, as far as these commands go, recording what reached it."""

    def __init__(self, refs):
        self.refs = dict(refs)
        self.requests = []

    @staticmethod
    def respond(content: bytes, content_type: str, status: int = 200) -> httpx.Response:
        # Streamed, as a real connection's are: the proxy relays bytes as they come.
        return httpx.Response(
            status,
            headers={"content-type": content_type, "content-length": str(len(content))},
            stream=httpx.ByteStream(content),
        )

    async def __call__(self, request):
        body = await request.aread()
        self.requests.append(
            (request.method, request.url.host, request.url.path, request.headers.get("authorization", ""))
        )
        if request.url.path.startswith("/synthetic-owner/old-name.git/"):
            # Renamed: GitHub sends Git on to the repository's new address.
            moved = str(request.url).replace("/old-name.git/", "/private-repo.git/")
            return httpx.Response(
                301, headers={"location": moved, "content-length": "0"}, stream=httpx.ByteStream(b"")
            )
        if request.url.host == "api.github.com" and request.url.path == "/user":
            return self.respond(b'{"login": "synthetic-user"}', "application/json")
        if request.url.host == "api.github.com" and request.url.path == "/graphql":
            empty = {"totalCount": 0, "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": ""}}
            return self.respond(
                json.dumps({"data": {"repository": {"pullRequests": empty}}}).encode(), "application/json"
            )
        if request.url.path.endswith("/info/refs"):
            service = request.url.params["service"]
            lines = [f"{sha} {ref}" for ref, sha in self.refs.items()]
            lines[0] += "\0report-status delete-refs side-band-64k ofs-delta object-format=sha1"
            advertised = (
                pkt(f"# service={service}\n".encode())
                + b"0000"
                + b"".join(pkt(f"{line}\n".encode()) for line in lines)
            )
            return self.respond(advertised + b"0000", f"application/x-{service}-advertisement")
        if request.url.path.endswith("/git-receive-pack"):
            if request.headers.get("content-encoding") == "gzip":
                body = gzip.decompress(body)
            updates, _ = ref_updates(body)
            self.refs.update({ref: new for _, new, ref in updates})
            report = (
                b"".join(
                    pkt(f"{line}\n".encode()) for line in ["unpack ok", *(f"ok {ref}" for *_, ref in updates)]
                )
                + b"0000"
            )
            return self.respond(pkt(b"\x01" + report) + b"0000", "application/x-git-receive-pack-result")
        return self.respond(b'{"message": "Not Found"}', "application/json", 404)


@pytest.mark.sandbox
@pytest.mark.destructive  # Asks the proxy to delete a branch on a mock GitHub.
async def test_github_commands_reach_github_through_the_proxy_without_the_token(
    coding, tmp_path, monkeypatch
):
    """`git push` in a command could not reach SSH keys or the Keychain, so an agent rebuilt an
    86-file commit through the GitHub connector's file API. Commands now reach GitHub through the
    app's proxy holding only a placeholder, as in Claude Code's cloud sessions: the sign-in is
    added on the way out, and as there, deleting branches or pushing tags is refused."""
    client, ws, services = coding
    folder = repository(tmp_path)
    head = git(folder, "rev-parse", "HEAD")
    git(folder, "switch", "-c", "feature/pr")
    git(folder, "remote", "add", "origin", "git@github.com:synthetic-owner/private-repo.git")
    thread = await create(client, folder=str(folder))
    sentinel = tmp_path / "unrelated.txt"
    sentinel.write_text("untouched")
    github = FakeGitHub({"refs/heads/main": head, "refs/heads/old": head})
    await services.github_proxy.close()
    services.github_proxy = GitHubProxy(transport=httpx.MockTransport(github))
    # A start that fails partway leaves no listener or folder behind; the next command retries.
    opened, start_unix_server = [], asyncio.start_unix_server

    async def unix(*args, **kwargs):
        opened.append(await start_unix_server(*args, **kwargs))
        return opened[-1]

    async def refused(*args, **kwargs):
        raise OSError("synthetic: no port")

    folders = set(Path(tempfile.gettempdir()).glob("wf-gh-*"))
    with monkeypatch.context() as patch:
        patch.setattr("wavefunction.github_proxy.asyncio.start_unix_server", unix)
        patch.setattr("wavefunction.github_proxy.asyncio.start_server", refused)
        assert await services.github_proxy.environment() == {}
    assert not opened[0].is_serving() and set(Path(tempfile.gettempdir()).glob("wf-gh-*")) == folders
    await client.put("/api/coding/access", json={"access": "sandboxed"})

    async def token():
        return "synthetic-token-never-return"

    monkeypatch.setattr("wavefunction.github_cli.token", token)
    # Commands that find the sign-in stale together share one lookup of gh.
    from wavefunction import github_cli

    asked = []

    async def slow():
        asked.append(True)
        await asyncio.sleep(0.05)
        return "synthetic-token-never-return"

    with monkeypatch.context() as patch:
        patch.setattr(github_cli, "token", slow)
        patch.setattr(github_cli, "_command_token", None)
        assert set(await asyncio.gather(*(github_cli.current_token() for _ in range(5)))) == {
            "synthetic-token-never-return"
        }
    assert asked == [True]
    script_gateway(
        services.models,
        [
            tool_call_message("run_command", {"command": 'echo "gh ${GH_TOKEN:-signed out}"'}, "offline"),
            AIMessage(content="Offline"),
        ],
    )
    offline = await ws.send(thread["id"], "Check GitHub while offline")
    await services.runtime.wait(offline["id"])
    await client.put("/api/coding/access", json={"access": "network"})
    commands = {
        "user": "gh api user --jq .login",
        "refs": "git ls-remote origin",
        "placeholder": 'echo "$GH_TOKEN"',
        "renamed": "git ls-remote https://github.com/synthetic-owner/old-name.git",
        # Remotes still name github.com, which is how gh finds the folder's repository.
        "remotes": "git remote -v",
        "prs": "gh pr list",
        "delete": "git push origin --delete old",
        "tag": "git tag v1 && git push origin v1",
        "push": "git push origin feature/pr",
        # A command cannot make the app write through a link it leaves: the next command's
        # setup writes nothing it could have placed.
        "link": f'ln -sf {sentinel} "$GH_CONFIG_DIR/config.yml"; echo tried',
        "after": "true",
    }
    script_gateway(
        services.models,
        [
            *(
                tool_call_message("run_command", {"command": command}, name)
                for name, command in commands.items()
            ),
            AIMessage(content="Online"),
        ],
    )
    online = await ws.send(thread["id"], "Use GitHub")
    assert (await services.runtime.wait(online["id"]))["status"] == "completed"

    def said(run, call):
        return next(
            e["payload"]["result"]
            for e in services.repository.list_run_events(run["id"])
            if e["event_type"] == "tool_completed" and e["payload"]["id"] == call
        )

    # Offline, nothing is handed over. Online, gh and git reach GitHub signed in, from the sandbox.
    assert "gh signed out" in said(offline, "offline")
    assert said(online, "user").splitlines()[-1] == "synthetic-user"
    assert f"{head}\trefs/heads/main" in said(online, "refs") and f"{head}\trefs/heads/main" in said(
        online, "renamed"
    )
    assert said(online, "placeholder").splitlines()[-1] == services.github_proxy.placeholder
    assert "origin\thttp://github.com/synthetic-owner/private-repo.git (push)" in said(online, "remotes")
    assert said(online, "prs").startswith("[exit code 0]")  # Without --repo: gh found the repository.
    assert "[remote rejected]" in said(online, "delete") and "does not delete branches" in said(
        online, "delete"
    )
    assert "[remote rejected]" in said(online, "tag") and "does not push tags" in said(online, "tag")
    assert said(online, "push").startswith("[exit code 0]") and github.refs["refs/heads/feature/pr"] == head
    assert said(online, "after").startswith("[exit code 0]") and sentinel.read_text() == "untouched"
    basic = "Basic " + base64.b64encode(b"x-access-token:synthetic-token-never-return").decode()
    assert {(host, auth) for _, host, _, auth in github.requests} == {
        ("api.github.com", "token synthetic-token-never-return"),
        ("github.com", basic),
    }
    # The refused pushes never reached GitHub.
    assert [(method, path) for method, _, path, _ in github.requests if method != "GET"] == [
        ("POST", "/graphql"),
        ("POST", "/synthetic-owner/private-repo.git/git-receive-pack"),
    ]
    events = str(services.repository.list_run_events(online["id"]))
    assert "synthetic-token" not in events and basic not in events

    # `gh auth switch` rewrites gh's accounts file: the next request goes out as the new account.
    async def switched():
        return "synthetic-switched-token"

    with monkeypatch.context() as patch:
        patch.setattr("wavefunction.github_cli.token", switched)
        patch.setattr("wavefunction.github_cli._gh_accounts_stamp", lambda: 2)
        script_gateway(
            services.models,
            [
                tool_call_message("run_command", {"command": commands["user"]}, "switched"),
                AIMessage(content="Switched"),
            ],
        )
        switch = await ws.send(thread["id"], "Check the account")
        await services.runtime.wait(switch["id"])
    assert github.requests[-1][3] == "token synthetic-switched-token"
    assert git(folder, "remote", "get-url", "origin") == "git@github.com:synthetic-owner/private-repo.git"
    assert "synthetic-token" not in git(folder, "config", "--list")
