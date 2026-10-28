"""What a chat may do: commands outside the sandbox, full access, folder access and Plan mode."""

import asyncio
import json
from pathlib import Path

import pytest
from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create


async def test_a_command_runs_outside_the_sandbox_once_approved_or_freely_in_a_chat_with_full_access(
    coding, tmp_path, monkeypatch
):
    """What the sandbox blocks, such as Docker, runs on the Mac once the user approves it, as
    Codex escalates a command, and "Don't run" tells the agent so. A chat with full access runs
    it without asking, as in Claude Code's bypass-permissions mode, and the agent is told, but a
    command that can't be undone still asks. The command never sees the app's own variables,
    which hold its API token."""
    from wavefunction import hands
    from wavefunction.runtime import RuntimeManager

    client, ws, services = coding
    monkeypatch.setenv("WAVEFUNCTION_DESKTOP_TOKEN", "synthetic-api-token")
    folder = tmp_path / "repo"
    folder.mkdir()
    thread = await create(client, folder=str(folder))
    # Readable on the Mac and private to the sandbox: the app's own data folder.
    private = services.settings.data_dir / "outside.txt"
    private.write_text("OUTSIDE_ONLY")
    read = f"cat {private}"
    synthetic = "printf 'synthetic irreversible action'"
    danger = "Deletes files that undo can't bring back."
    original_classifier, original_outside = hands.command_danger, hands.run_outside
    risk_changed, outside_calls = False, []

    def classify(command, **kwargs):
        return (
            danger
            if command == synthetic or risk_changed and command == read
            else original_classifier(command, **kwargs)
        )

    async def observe_outside(command, **kwargs):
        nonlocal risk_changed
        outside_calls.append(command)
        risk_changed = False
        return await original_outside(command, **kwargs)

    monkeypatch.setattr(hands, "command_danger", classify)
    monkeypatch.setattr(hands, "run_outside", observe_outside)

    def said(run, call):
        return next(
            str(e["payload"].get("result", ""))
            for e in services.repository.list_run_events(run["id"])
            if e["event_type"] in ("tool_completed", "tool_failed") and e["payload"].get("id") == call
        )

    async def card(run):
        await asyncio.wait_for(services.runtime.tasks[run["id"]], timeout=20)
        assert services.repository.get_run(run["id"])["status"] == "waiting_approval"
        return [
            e
            for e in services.repository.list_run_events(run["id"])
            if e["event_type"] == "run_waiting_for_approval"
        ][-1]["payload"]["requests"][0]

    seen = []
    script_gateway(
        services.models,
        [
            tool_call_message("run_command", {"command": read}, "inside"),
            tool_call_message(
                "run_command",
                {"command": read, "outside_sandbox": True, "reason": "Read a private file"},
                "outside",
            ),
            tool_call_message(
                "run_command", {"command": read, "outside_sandbox": True, "reason": "Again"}, "again"
            ),
            AIMessage(content="Read it"),
        ],
        [
            tool_call_message(
                "run_command",
                {"command": f"{read}; env", "outside_sandbox": True, "reason": "And the rest"},
                "free",
            ),
            # Classification is synthetic; even a broken approval gate cannot delete anything.
            tool_call_message(
                "run_command", {"command": synthetic, "outside_sandbox": True, "reason": "Clean up"}, "delete"
            ),
            AIMessage(content="Done"),
        ],
        seen=seen,
    )
    run = await ws.send(thread["id"], "Read the file")
    request = await card(run)
    assert (request["kind"], request["command"], request["reason"]) == (
        "outside_sandbox",
        read,
        "Read a private file",
    )
    # The target can become unrecoverable while an ordinary outside-sandbox card is
    # open. Approving that card must show the new warning before any execution.
    risk_changed = True
    assert (
        await client.post(f"/api/runs/{run['id']}/approval", json={"decision": "approve"})
    ).status_code == 202
    request = await card(run)
    assert (request["kind"], request["danger"]) == ("irreversible", danger)
    assert outside_calls == []
    assert (
        await client.post(f"/api/runs/{run['id']}/approval", json={"decision": "approve"})
    ).status_code == 202
    await card(run)
    assert outside_calls == [read]
    # Changing access while a card is open must not turn a rejection into execution.
    assert (await client.patch(f"/api/coding/threads/{thread['id']}", json={"mode": "full"})).json()[
        "mode"
    ] == "full"
    assert (
        await client.post(f"/api/runs/{run['id']}/approval", json={"decision": "reject"})
    ).status_code == 202
    assert (await services.runtime.wait(run["id"]))["status"] == "completed"
    assert "OUTSIDE_ONLY" not in said(run, "inside") and "OUTSIDE_ONLY" in said(run, "outside")
    assert "did not allow this command" in said(run, "again")

    # "Allow for this chat" gives the chat full access; later commands outside the sandbox just run.
    second = await ws.send(thread["id"], "Read it and the rest")
    request = await card(second)
    assert (request["kind"], request["danger"], request["outside_sandbox"]) == (
        "irreversible",
        "Deletes files that undo can't bring back.",
        True,
    )
    # Captures can change during a pause, including across an app restart. Rebuild
    # from the saved card, mock changed classification, and spy on execution so this
    # regression never runs a deletion even if the gate fails.
    await services.runtime.aclose()
    services.runtime = RuntimeManager(
        services.repository,
        services.models,
        services.files,
        connectors=services.connectors,
        skills=services.skills,
        computer=services.computer,
        coding_workspace=ws,
    )
    script_gateway(services.models, [AIMessage(content="Done")], seen=seen)
    executed = []

    async def must_not_run(*args, **kwargs):
        executed.append(args[0])
        raise AssertionError("A rejected command reached execution")

    monkeypatch.setattr(hands, "command_danger", lambda *args, **kwargs: None)
    monkeypatch.setattr(hands, "run_outside", must_not_run)
    assert (
        await client.post(f"/api/runs/{second['id']}/approval", json={"decision": "reject"})
    ).status_code == 202
    assert (await services.runtime.wait(second["id"]))["status"] == "completed"
    # Only the synthetic irreversible action asked.
    assert (
        len(
            [
                e
                for e in services.repository.list_run_events(second["id"])
                if e["event_type"] == "run_waiting_for_approval"
            ]
        )
        == 1
    )
    free = said(second, "free")
    assert "OUTSIDE_ONLY" in free and "HOME=" in free and "synthetic-api-token" not in free
    assert "can't be undone" in said(second, "delete")
    assert executed == []
    update = [m for m in seen[-1] if m.additional_kwargs.get("lc_source") == "environment"][-1]
    assert "This chat has full access" in update.content


async def test_a_new_chat_starts_in_the_mode_last_chosen(coding, tmp_path):
    """The composer and Settings save the mode new chats start in; one asked for in another mode
    keeps it."""
    client, _, _ = coding
    folder = str(tmp_path / "repo")
    Path(folder).mkdir()
    assert (await client.get("/api/coding/workspace")).json()["default_mode"] == "auto"
    assert (await create(client, folder=folder))["mode"] == "auto"
    saved = await client.put("/api/coding/default-mode", json={"mode": "full"})
    assert saved.json() == {"mode": "full"}
    assert (await client.put("/api/coding/default-mode", json={"mode": "admin"})).status_code == 422
    assert (await client.get("/api/coding/workspace")).json()["default_mode"] == "full"
    assert (await create(client, folder=folder))["mode"] == "full"
    assert (await create(client, folder=folder, mode="plan"))["mode"] == "plan"


@pytest.mark.sandbox
async def test_plan_withholds_mutating_tools_and_rejects_terminal(coding, tmp_path):
    from langchain_core.messages import ToolMessage
    from support import repository

    client, ws, services = coding
    folder = repository(tmp_path)
    thread = await create(client, folder=str(folder), mode="plan")
    seen = []
    script_gateway(
        services.models,
        [
            tool_call_message("search_available_tools", {"query": "documents", "limit": 1}, "discover"),
            tool_call_message("read_documents", {}, "read"),
            tool_call_message("search_available_tools", {"query": "computer_click"}, "denied"),
            AIMessage(content="Plan only"),
        ],
        [AIMessage(content="Still planning")],
        [AIMessage(content="Separate plan")],
        seen=seen,
    )
    run = await ws.send(thread["id"], "Plan a change")
    await services.runtime.wait(run["id"])
    spec = services.repository.get_run(run["id"])["spec"]
    names = {b["name"] for b in spec["tools"] if b["policy"] != "deny"}
    assert {"read_file", "search_files"} <= names
    forbidden = {"write_file", "edit_file", "run_command", "computer_click", "start_terminal"}
    assert not forbidden & names

    def selected(run_id):
        return [
            event["payload"]["tools"]
            for event in services.repository.list_run_events(run_id)
            if event["event_type"] == "tools_selected"
        ]

    offered = selected(run["id"])
    documents = {"list_documents", "read_documents", "search_documents"}
    # Even a small catalogue defers optional schemas, while coding and framework
    # tools stay available. Discovery loads the captured group, not denied tools.
    assert len(names) < 40
    # Helper tools are offered without discovery, so delegation never waits on a search.
    assert {
        "read_file",
        "search_files",
        "search_available_tools",
        "write_todos",
        "read_tool_output",
        "spawn_agent",
        "wait_agents",
    } <= set(offered[0])
    assert not documents & set(offered[0])
    assert documents <= set(offered[1])
    assert offered[1][: len(offered[0])] == offered[0]
    assert not forbidden & {name for step in offered for name in step}
    results = {message.tool_call_id: message for message in seen[-1] if isinstance(message, ToolMessage)}
    assert documents <= {item["name"] for item in json.loads(str(results["discover"].content))["tools"]}
    assert "No documents" in results["read"].content
    assert not forbidden & {item["name"] for item in json.loads(str(results["denied"].content))["tools"]}

    # Loaded tools survive a follow-up without changing the existing schema prefix,
    # but they do not leak into another conversation using the same services.
    follow_up = await ws.send(thread["id"], "Continue the plan")
    await services.runtime.wait(follow_up["id"])
    assert selected(follow_up["id"])[0] == offered[-1]
    other = await create(client, folder=str(folder), mode="plan")
    # A new Plan chat starts in its own worktree, as the composer sends by default: that
    # changes no files, so Plan mode does not refuse it.
    separate = await ws.send(other["id"], "Plan independently", worktree=True)
    await services.runtime.wait(separate["id"])
    assert selected(separate["id"])[0] == offered[0]
    assert ws.delivery.worktree(other["id"]) and ws.root(other["id"]) != folder.resolve()
    response = await client.post(
        f"/api/coding/threads/{thread['id']}/terminal", json={"command": "touch forbidden"}
    )
    assert response.status_code == 422
    assert not (folder / "forbidden").exists()


@pytest.mark.sandbox
async def test_folder_access_approval_rejection_and_native_command_boundary(coding, tmp_path, monkeypatch):
    import shlex

    client, ws, services = coding
    folder, outside = tmp_path / "repo", tmp_path / "outside"
    folder.mkdir()
    outside.mkdir()
    (outside / "input.txt").write_text("external requirement")
    thread = await create(client, folder=str(folder))
    assert (await client.get("/api/coding/workspace")).json()["access"] == "network"
    script_gateway(
        services.models,
        [
            tool_call_message(
                "write_file", {"path": "inside.txt", "content": "no approval needed"}, "inside"
            ),
            tool_call_message(
                "run_command", {"command": "cat " + shlex.quote(str(outside / "input.txt"))}, "shell-boundary"
            ),
            tool_call_message("read_file", {"path": str(outside / "input.txt")}, "outside"),
            AIMessage(content="Read the approved folder"),
        ],
    )
    run = await ws.send(thread["id"], "Use the external requirement")
    assert (await services.runtime.wait(run["id"]))["status"] == "waiting_approval"
    assert (folder / "inside.txt").read_text() == "no approval needed"
    events = services.repository.list_run_events(run["id"])
    assert "Operation not permitted" in str(events)
    assert "external requirement" not in str([e for e in events if e["event_type"] == "tool_result"])
    pause = next(e for e in reversed(events) if e["event_type"] == "run_waiting_for_approval")
    assert pause["payload"]["requests"][0]["folder"] == str(outside)
    # Written while the card waits, a message may be a new request: it keeps the delegation hint.
    await ws.send(thread["id"], "Also fix the copy")
    held = next(
        e for e in services.repository.list_run_events(run["id"]) if e["event_type"] == "steering_requested"
    )
    assert held["payload"]["pause_reply"] is False
    # Resuming rebuilds the task from its captured spec, as after a restart.
    captured = services.repository.get_run(run["id"])["spec"]
    services.runtime.active.pop(run["id"], None)
    script_gateway(
        services.models,
        [
            tool_call_message(
                "run_command", {"command": "cat " + shlex.quote(str(outside / "input.txt"))}, "approved-shell"
            ),
            tool_call_message(
                "write_file", {"path": str(outside / "rejected.txt"), "content": "no"}, "upgrade"
            ),
            AIMessage(content="Kept read-only"),
        ],
    )
    response = await client.post(
        f"/api/runs/{run['id']}/approval",
        json={"decision": "approve", "expected_approval": pause["sequence"]},
    )
    assert response.status_code == 202, response.text
    assert (await services.runtime.wait(run["id"]))["status"] == "waiting_approval"
    assert services.repository.get_run(run["id"])["spec"] == captured
    assert "external requirement" in str(services.repository.list_run_events(run["id"]))
    denied = await ws.runner().run("printf denied > " + shlex.quote(str(outside / "blocked.txt")), cwd=folder)
    assert denied.exit_code != 0 and not (outside / "blocked.txt").exists()
    # A late click from the read-only card cannot approve the subsequent write request.
    assert (
        await client.post(
            f"/api/runs/{run['id']}/approval",
            json={"decision": "approve", "expected_approval": pause["sequence"]},
        )
    ).status_code == 409
    upgrade = next(
        e
        for e in reversed(services.repository.list_run_events(run["id"]))
        if e["event_type"] == "run_waiting_for_approval"
    )
    assert upgrade["payload"]["requests"][0]["writable"]
    assert (
        await client.post(
            f"/api/runs/{run['id']}/approval",
            json={
                "decision": "reject",
                "feedback": "Keep read-only",
                "expected_approval": upgrade["sequence"],
            },
        )
    ).status_code == 202
    await services.runtime.wait(run["id"])
    assert not (outside / "rejected.txt").exists()
    # A denied card is a plain refusal, not an action that may have completed.
    assert not any(
        e["event_type"] == "action_outcome_unknown" for e in services.repository.list_run_events(run["id"])
    )
    # A call held on a card the user stopped never ran, and the model is told so.
    from langchain_core.messages import ToolMessage

    from wavefunction.runtime import NOT_RUN_CALL

    (tmp_path / "elsewhere").mkdir()
    script_gateway(
        services.models,
        [
            tool_call_message("read_file", {"path": str(tmp_path / "elsewhere" / "x.txt")}, "held"),
            AIMessage(content="unused"),
        ],
        [AIMessage(content="Something else")],
    )
    held = await ws.send(thread["id"], "Read the other folder")
    assert (await services.runtime.wait(held["id"]))["status"] == "waiting_approval"
    assert (await client.post(f"/api/runs/{held['id']}/cancel")).json()["status"] == "cancelled"
    # Stopping the paused task drops its per-step states too, keeping what resuming and the
    # message points read.
    saver = await services.runtime.checkpointer()
    async with saver.conn.execute(
        "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ?", (held["conversation_id"],)
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
    after = await ws.send(thread["id"], "Do something else")
    assert (await services.runtime.wait(after["id"]))["status"] == "completed"
    saved = await (await services.runtime.checkpointer()).aget_tuple(
        {"configurable": {"thread_id": ws.thread(thread["id"])["conversation_id"]}}
    )
    assert [
        m.content
        for m in saved.checkpoint["channel_values"]["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id == "held"
    ] == [NOT_RUN_CALL]
    # One the user approved and then stopped while it ran may have run, and the model is told that.
    from wavefunction import hands
    from wavefunction.runtime import INTERRUPTED_CALL

    started = asyncio.Event()

    async def running(command, **kwargs):  # Stands in for the command; nothing runs.
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(hands, "run_outside", running)
    script_gateway(
        services.models,
        [
            tool_call_message(
                "run_command",
                {"command": "printf long-job", "outside_sandbox": True, "reason": "Needs the Mac"},
                "approved",
            ),
            AIMessage(content="unused"),
        ],
        [AIMessage(content="Next answer")],
    )
    approved = await ws.send(thread["id"], "Run the long job")
    assert (await services.runtime.wait(approved["id"]))["status"] == "waiting_approval"
    assert (
        await client.post(f"/api/runs/{approved['id']}/approval", json={"decision": "approve"})
    ).status_code == 202
    await asyncio.wait_for(started.wait(), 20)
    assert (await client.post(f"/api/runs/{approved['id']}/cancel")).json()["status"] == "cancelled"
    after = await ws.send(thread["id"], "What happened?")
    assert (await services.runtime.wait(after["id"]))["status"] == "completed"
    saved = await (await services.runtime.checkpointer()).aget_tuple(
        {"configurable": {"thread_id": ws.thread(thread["id"])["conversation_id"]}}
    )
    assert [
        m.content
        for m in saved.checkpoint["channel_values"]["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id == "approved"
    ] == [INTERRUPTED_CALL]


@pytest.mark.sandbox
async def test_a_toolchain_on_path_reads_its_install_folder_but_home_stays_closed(tmp_path, monkeypatch):
    from wavefunction import sandbox

    # Go's installer puts /usr/local/go/bin on PATH, and its tools read the sources beside it.
    # In home a bin can sit beside credentials, as ~/.cargo/bin does.
    install, home, project = tmp_path / "go", tmp_path / "home", tmp_path / "project"
    (install / "src").mkdir(parents=True)
    (install / "src/runtime.go").write_text("package runtime\n")
    (home / ".cargo").mkdir(parents=True)
    (home / ".cargo/credentials.toml").write_text("SYNTHETIC_TOKEN\n")
    project.mkdir()
    tools = {install / "bin/gotool": "src/runtime.go", home / ".cargo/bin/cargotool": "credentials.toml"}
    for tool, read in tools.items():
        tool.parent.mkdir()
        tool.write_text(f'#!/bin/sh\ncat "$(dirname "$0")/../{read}"\n')
        tool.chmod(0o755)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sandbox, "login_path", lambda: (str(install / "bin"), str(home / ".cargo/bin")))
    # As a task runs its commands: reads limited to its folders and toolchain support.
    box = sandbox.Sandbox(
        [sandbox.Grant(project, writable=True)], scratch=tmp_path / "scratch", restrict_reads=True
    )
    result = await box.run("gotool; cargotool", cwd=project)
    assert "package runtime" in result.stdout, result.stderr
    assert "SYNTHETIC_TOKEN" not in result.stdout and "Operation not permitted" in result.stderr
