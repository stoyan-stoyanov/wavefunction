"""Editing repository files: the agent's edit-and-test loop and the editor's file boundary."""

import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create

from wavefunction.execution_policy import ToolCallRejected
from wavefunction.hands import build_hands
from wavefunction.sandbox import Grant


@pytest.mark.sandbox
async def test_workbench_rejects_binary_and_oversized_files_without_corrupting_them(coding, tmp_path):
    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    files = {"image.bin": b"test\x00binary", "large.txt": b"a" * 1000001}
    for name, data in files.items():
        (folder / name).write_bytes(data)
    thread = await create(client, folder=str(folder))
    for name, data in files.items():
        response = await client.get(f"/api/coding/threads/{thread['id']}/file", params={"path": name})
        assert response.status_code in (409, 422), response.text
        assert (folder / name).read_bytes() == data
    # An empty file opens as empty text: the agent's "The file is empty." note never reaches it.
    (folder / "__init__.py").write_bytes(b"")
    empty = (
        await client.get(f"/api/coding/threads/{thread['id']}/file", params={"path": "__init__.py"})
    ).json()
    assert empty["text"] == ""
    saved = await client.put(
        f"/api/coding/threads/{thread['id']}/file",
        json={"path": "__init__.py", "content": "VERSION = 1\n", "version": empty["version"]},
    )
    assert saved.status_code == 200, saved.text
    assert (folder / "__init__.py").read_bytes() == b"VERSION = 1\n"
    # So does one holding only a byte-order mark, which keeps it on save.
    (folder / "notes.txt").write_bytes(b"\xef\xbb\xbf")
    marked = (
        await client.get(f"/api/coding/threads/{thread['id']}/file", params={"path": "notes.txt"})
    ).json()
    assert marked["text"] == ""
    saved = await client.put(
        f"/api/coding/threads/{thread['id']}/file",
        json={"path": "notes.txt", "content": "Hello\n", "version": marked["version"]},
    )
    assert saved.status_code == 200 and (folder / "notes.txt").read_bytes() == b"\xef\xbb\xbfHello\n"


@pytest.mark.sandbox
async def test_real_coding_loop_edits_and_runs_tests(coding, tmp_path):
    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / "pricing.py").write_text("def total(price, count):\n    return price + count\n")
    (folder / "test_pricing.py").write_text(
        "import unittest\nfrom pricing import total\nclass TestPricing(unittest.TestCase):\n    def test_total(self): self.assertEqual(total(5, 3), 15)\n"
    )
    thread = await create(client, folder=str(folder))
    script_gateway(
        services.models,
        [
            tool_call_message("read_file", {"path": "pricing.py"}, "read"),
            tool_call_message(
                "edit_file",
                {"path": "pricing.py", "old_text": "price + count", "new_text": "price * count"},
                "edit",
            ),
            tool_call_message("run_command", {"command": "python3 -m unittest -v"}, "test"),
            AIMessage(content="Fixed multiplication and verified the test."),
        ],
    )
    response = await client.post(
        # Composers send the draft untrimmed; the title is its first line with words.
        f"/api/coding/threads/{thread['id']}/messages",
        json={"content": "\nFix total and test it"},
    )
    assert response.status_code == 202, response.text
    run = await services.runtime.wait(response.json()["id"])
    assert run["status"] == "completed", run
    assert "price * count" in (folder / "pricing.py").read_text()
    events = services.repository.list_run_events(run["id"])
    assert any(e["event_type"] == "tool_completed" and "OK" in str(e["payload"]) for e in events), events
    assert ws.thread(thread["id"])["title"] == "Fix total and test it"


@pytest.mark.sandbox
async def test_a_refused_edit_or_command_is_not_reported_as_possibly_completed(coding, tmp_path):
    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    locked = folder / "locked.txt"
    locked.write_text("before\n")
    locked.chmod(0o444)
    thread = await create(client, folder=str(folder))
    script_gateway(
        services.models,
        [
            tool_call_message(
                "edit_file", {"path": "locked.txt", "old_text": "before", "new_text": "after"}, "edit"
            ),
            tool_call_message("run_command", {"command": "echo built", "workdir": "build"}, "command"),
            AIMessage(content="Done"),
        ],
    )
    run = await ws.send(thread["id"], "Edit the file and build")
    await services.runtime.wait(run["id"])
    events = services.repository.list_run_events(run["id"])
    told = {e["payload"]["id"]: e["payload"]["result"] for e in events if e["event_type"] == "tool_failed"}
    # Nothing ran, so the model hears why, and neither call is flagged for checking.
    assert "is read-only" in told["edit"] and "Settings › Access" not in told["edit"]
    assert "not an existing folder" in told["command"]
    assert not any("may have completed" in text for text in told.values())
    assert not any(e["event_type"] == "action_outcome_unknown" for e in events)
    assert locked.read_text() == "before\n"
    # A write the sandbox refuses is refused before anything changed, too.
    tools = {
        tool.name: tool
        for tool in build_hands(
            tmp_path / "scratch",
            lambda: [Grant(folder)],
            lambda: "sandboxed",
            ensure_access=lambda path, writable: None,
        )
    }
    with pytest.raises(ToolCallRejected):
        await tools["write_file"].ainvoke({"path": str(folder / "new.txt"), "content": "new"})
    assert not (folder / "new.txt").exists()


@pytest.mark.sandbox
async def test_file_boundary_and_concurrent_edit_protection(coding, tmp_path, monkeypatch):
    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / "app.py").write_text("before\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    (folder / "escape").symlink_to(outside)
    thread = await create(client, folder=str(folder))
    url = f"/api/coding/threads/{thread['id']}/file"
    for path in ["../outside.txt", str(outside), "escape", ".git/config"]:
        assert (await client.get(url, params={"path": path})).status_code == 422
    response = await client.get(url, params={"path": "app.py"})
    assert response.status_code == 200, response.text
    content = response.json()
    assert content["text"] == "before\n"
    (folder / "app.py").write_text("someone else\n")
    stale = await client.put(
        url, json={"path": "app.py", "content": "overwrite", "version": content["version"]}
    )
    assert stale.status_code == 422 and (folder / "app.py").read_text() == "someone else\n"
    latest = (await client.get(url, params={"path": "app.py"})).json()
    saved = await client.put(url, json={"path": "app.py", "content": "after\n", "version": latest["version"]})
    assert saved.status_code == 200, saved.text
    assert (folder / "app.py").read_text() == "after\n"

    # Files the chat links (previews, Open in) have the same boundary as the editor.
    (folder / "out").mkdir()
    (folder / "out" / "report.pdf").write_bytes(b"%PDF-1.4 synthetic")
    (folder / "out" / "data.csv").write_text("name,total\nAda,3\n")
    (folder / "run.command").write_text("echo synthetic\n")
    preview = f"/api/coding/threads/{thread['id']}/preview"
    report = (await client.get(preview, params={"path": "out/report.pdf"})).json()
    assert (
        report["kind"] == "pdf"
        and report["openable"]
        and report["path"] == str((folder / "out/report.pdf").resolve())
    )
    assert (await client.get(preview, params={"path": str(folder / "out/data.csv")})).json()[
        "kind"
    ] == "table"
    assert not (await client.get(preview, params={"path": "run.command"})).json()["openable"]
    assert (
        await client.get(preview + "/content", params={"path": "out/report.pdf"})
    ).content == b"%PDF-1.4 synthetic"
    for path in ["../outside.txt", str(outside), "escape", ".git/config"]:
        assert (await client.get(preview, params={"path": path})).status_code == 422, path
    assert (await client.get(preview, params={"path": "out/missing.pdf"})).status_code == 404
    launched = []

    async def fake_exec(*command, **kwargs):
        launched.append(command)

        class Process:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return Process()

    # Only while opening: the test's later commands run through the real subprocess launcher.
    with monkeypatch.context() as patched:
        patched.setattr("wavefunction.file_previews.asyncio.create_subprocess_exec", fake_exec)
        for target in ("finder", "app"):
            assert (
                await client.post(preview + "/open", json={"path": "out/report.pdf", "target": target})
            ).status_code == 204
        # A runnable file is only revealed, never opened.
        assert (
            await client.post(preview + "/open", json={"path": "run.command", "target": "app"})
        ).status_code == 422
    pdf = str((folder / "out/report.pdf").resolve())
    assert launched == [("/usr/bin/open", "-R", pdf), ("/usr/bin/open", "--", pdf)]
    assert len(launched) == 2
    services.repository.set_setting("folder_grants", [])
    tree_url = f"/api/coding/threads/{thread['id']}/tree"
    assert (await client.get(tree_url)).status_code == 409
    assert (await client.get(url, params={"path": "app.py"})).status_code == 409
    ws.allow_folder(str(folder.resolve()))
    assert any(entry["name"] == "app.py" for entry in (await client.get(tree_url)).json())
    assert outside.read_text() == "keep"
    # Equal contents/versions in another repository must not accept the old editor's save.
    other = tmp_path / "other-repo"
    other.mkdir()
    (other / "app.py").write_text("after\n")
    changed = await client.patch(f"/api/coding/threads/{thread['id']}", json={"folder": str(other)})
    assert changed.status_code == 200
    stale_folder = await client.put(
        url,
        json={
            "path": "app.py",
            "content": "old draft",
            "version": saved.json()["version"],
            "folder": str(folder),
        },
    )
    assert stale_folder.status_code == 422 and "folder changed" in stale_folder.text
    assert (await client.get(url, params={"path": "app.py", "folder": str(folder)})).status_code == 422
    assert (other / "app.py").read_text() == "after\n"
    current = await client.put(
        url,
        json={
            "path": "app.py",
            "content": "new draft",
            "version": saved.json()["version"],
            "folder": str(other),
        },
    )
    assert current.status_code == 200 and (other / "app.py").read_text() == "new draft"
    assert (folder / "app.py").read_text() == "after\n"
