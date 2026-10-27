"""Change records: each chat's own edits and command changes."""

import asyncio

import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create


@pytest.mark.sandbox
async def test_change_records_track_owned_edits_and_commands_per_chat(coding, tmp_path, monkeypatch):
    from wavefunction.sandbox import CommandResult, Sandbox

    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / "app.py").write_text("value = 1\n")
    (folder / "dirty.txt").write_text("Pre-existing user draft\n")
    (folder / "remove.txt").write_text("Delete this\n")
    (folder / ".env").write_text("SYNTHETIC_SECRET=before\n")
    (folder / "binary.bin").write_bytes(b"before\0")
    # An unrelated capture warning must not lose the recoverable untracked deletion.
    (folder / "linked.txt").symlink_to(folder / "dirty.txt")
    command = "printf 'synthetic file changes'"
    original_run = Sandbox.run

    async def simulate_changes(runner, text, **kwargs):
        if text != command:
            return await original_run(runner, text, **kwargs)
        # The folder is not held while a command runs: the user can open a terminal there.
        opened = await asyncio.wait_for(
            client.post(f"/api/coding/threads/{second['id']}/terminals", json={}), 5
        )
        assert opened.status_code == 201, opened.text
        await ws.terminals.forget(second["id"])
        (folder / "app.py").write_text("value = 3\n")
        (folder / "new.txt").write_text("new file")
        (folder / "binary.bin").write_bytes(b"after\0")
        (folder / ".env").write_text("SYNTHETIC_SECRET=after\n")
        # Simulate absence without executing deletion: retain the fixture's bytes
        # outside the repository while exercising the real capture.
        (folder / "remove.txt").rename(tmp_path / "preserved-remove.txt")
        return CommandResult(stdout="", stderr="", exit_code=0)

    monkeypatch.setattr(Sandbox, "run", simulate_changes)
    first, second = (await create(client, folder=str(folder)), await create(client, folder=str(folder)))
    script_gateway(
        services.models,
        [
            tool_call_message("read_file", {"path": "app.py"}, "read"),
            tool_call_message(
                "edit_file", {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"}, "edit"
            ),
            tool_call_message("run_command", {"command": command}, "command"),
            AIMessage(content="Updated and verified"),
        ],
    )
    run = await ws.send(first["id"], "Change code and create/remove files")
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"
    with ws.db() as db:
        rows = db.execute("SELECT * FROM coding_changes WHERE thread_id = ?", (first["id"],)).fetchall()
        warnings = [
            row["warnings"]
            for row in db.execute(
                "SELECT warnings FROM coding_review_runs WHERE thread_id = ?", (first["id"],)
            )
        ]
    changes = {row["path"]: dict(row) for row in rows}
    assert set(changes) == {"app.py", "new.txt", "remove.txt", "binary.bin"}, changes
    assert (
        changes["app.py"]["before_text"] == "value = 1\n" and changes["app.py"]["after_text"] == "value = 3\n"
    )
    assert changes["new.txt"]["kind"] == "added" and changes["remove.txt"]["kind"] == "deleted"
    assert changes["binary.bin"]["kind"] == "binary" and changes["binary.bin"]["after_text"] is None
    assert (folder / "dirty.txt").read_text() == "Pre-existing user draft\n"
    assert any("Skipped linked/outside file: linked.txt" in w for w in warnings)
    assert "SYNTHETIC_SECRET" not in str(changes)
    with ws.db() as db:
        assert not db.execute("SELECT 1 FROM coding_changes WHERE thread_id = ?", (second["id"],)).fetchone()
    branch_response = await client.post(f"/api/coding/threads/{first['id']}/fork", json={"worktree": False})
    assert branch_response.status_code == 201, branch_response.text
    branch = branch_response.json()
    with ws.db() as db:
        assert len(
            db.execute("SELECT 1 FROM coding_changes WHERE thread_id = ?", (branch["id"],)).fetchall()
        ) == len(changes)
    assert (await client.delete(f"/api/coding/threads/{first['id']}")).status_code == 204
    with ws.db() as db:
        assert not db.execute("SELECT 1 FROM coding_changes WHERE thread_id = ?", (first["id"],)).fetchone()
        assert db.execute("SELECT 1 FROM coding_changes WHERE thread_id = ?", (branch["id"],)).fetchone()
