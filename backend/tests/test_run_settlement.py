from unittest.mock import patch

import pytest
from conftest import agent_spec


async def test_compilation_failure_does_not_leave_a_queued_run(stack):
    agent = stack.agent
    with (
        patch("wavefunction.runtime.build_agent", side_effect=ValueError("Skill unavailable")),
        pytest.raises(ValueError),
    ):
        await stack.runtime.start_run(agent["id"], "test", spec=agent_spec())
    conversation = stack.repository.current_conversation(agent["id"])
    assert stack.repository.list_conversation_runs(agent["id"], conversation["id"])[0]["status"] == "failed"
    await stack.runtime.aclose()


@pytest.mark.sandbox
async def test_helper_lifecycle_keeps_access_limits_asks_for_more_and_passes_to_the_next_task(
    coding, tmp_path
):
    import asyncio
    import json
    import os
    import shlex
    import shutil
    from pathlib import Path

    from conftest import ScriptedModel, tool_call_message
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from support import create, git, repository

    from wavefunction.coding_access import full_access

    client, ws, services = coding
    folder = repository(tmp_path)
    existing, added_later, extra = (
        tmp_path / "existing-grant",
        tmp_path / "later-grant",
        tmp_path / "helper-extra",
    )
    for grant in (existing, added_later, extra):
        grant.mkdir()
        (grant / "keep.txt").write_text("Unrelated data")
    ws.allow_folder(str(existing))
    parent = await create(client, folder=str(folder))
    permission_ready, parent_ready, writer_go = asyncio.Event(), asyncio.Event(), asyncio.Event()
    child_ready = asyncio.Queue()
    child, followup, readonly_tools, terminals, paths = {}, {}, set(), [], {}
    forbidden = [folder / "a.txt", existing / "keep.txt", added_later / "keep.txt"]
    command = "; ".join("printf ESCAPED > " + shlex.quote(str(path)) for path in forbidden)

    async def dead(pid):
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.05)
        pytest.fail(f"Helper terminal process {pid} survived shutdown")

    class Editor(ScriptedModel):
        async def _agenerate(self, messages, **kwargs):
            if self._cursor == 0:
                # An isolated helper learns where its ignored environment files are.
                assert f"This folder is a Git worktree of {folder}." in str([m.content for m in messages])
                await permission_ready.wait()
                message = tool_call_message("run_command", {"command": command}, "boundary")
            elif self._cursor == 1:
                message = tool_call_message(
                    "start_terminal",
                    {
                        "command": f"printf 'Retained child work\\n' > child.txt && git add -- child.txt && (sleep 60 & echo $! > child-{len(terminals)}.pid; wait)",
                        "title": "Owned child process",
                    },
                    "start-child",
                )
            else:
                session = next(
                    session
                    for session in reversed(list(ws.terminals.sessions.values()))
                    if session.owner == "agent" and not session.closed
                )
                for _ in range(40):
                    pid_file = Path(session.cwd) / f"child-{len(terminals)}.pid"
                    if pid_file.exists():
                        break
                    await asyncio.sleep(0.025)
                pid = int(pid_file.read_text())
                terminals.append((session, pid))
                await child_ready.put((session, pid))
                await asyncio.Event().wait()
                raise AssertionError("The test must stop the active helper")
            self._cursor += 1
            return ChatResult(generations=[ChatGeneration(message=message)])

    class Readonly(ScriptedModel):
        def bind_tools(self, tools, **kwargs):
            readonly_tools.update(tool.name for tool in tools)
            return self

    class Shared(ScriptedModel):
        async def _agenerate(self, messages, **kwargs):
            assert "working in the parent's checkout alongside other agents" in str(
                [m.content for m in messages]
            )
            await writer_go.wait()
            return await super()._agenerate(messages, **kwargs)

    class Parent(ScriptedModel):
        async def _agenerate(self, messages, **kwargs):
            tools = {
                message.tool_call_id: message for message in messages if isinstance(message, ToolMessage)
            }
            if self._cursor == 0:
                message = tool_call_message(
                    "spawn_agent",
                    {
                        "task": "Make an isolated change and run a background check",
                        "title": "Editor",
                        "isolated": True,
                    },
                    "spawn",
                )
            elif self._cursor == 1:
                child.update(json.loads(tools["spawn"].content))
                # A grant acquired after spawn must not widen the helper's captured ceiling.
                ws.allow_folder(str(added_later))
                permission_ready.set()
                await asyncio.wait_for(child_ready.get(), 10)
                message = tool_call_message("stop_agent", {"agent_id": child["agent_id"]}, "stop")
            elif self._cursor == 2:
                stopped = json.loads(tools["stop"].content)
                assert stopped["status"] == "cancelled", stopped
                # Done, a helper leaves the sidebar; a follow-up brings it back below.
                assert ws.thread(child["agent_id"])["archived"]
                session, pid = terminals[0]
                await asyncio.sleep(0.75)
                assert not session.closed and session.process.returncode is None
                os.kill(pid, 0)
                message = tool_call_message(
                    "send_message",
                    {"agent_id": child["agent_id"], "message": "Continue with the same access limits"},
                    "continue",
                )
                message.tool_calls.append(
                    {"name": "list_terminals", "args": {}, "id": "terminals", "type": "tool_call"}
                )
            elif self._cursor == 3:
                followup.update(json.loads(tools["continue"].content))
                assert not ws.thread(child["agent_id"])["archived"]
                # The main agent sees its helpers' terminals, by helper, and can close them.
                listed = json.loads(tools["terminals"].content)
                assert any(row["id"] == terminals[0][0].id and row["helper"] == "Editor" for row in listed), (
                    listed
                )
                assert followup["run_id"] != child["run_id"]
                await asyncio.wait_for(child_ready.get(), 10)
                # A helper can run on another of the user's enabled models, and only on one of those.
                message = tool_call_message(
                    "spawn_agent",
                    {
                        "task": "Inspect without making changes",
                        "title": "Reader",
                        "read_only": True,
                        "model": "openai::gpt-helper",
                    },
                    "reader",
                )
                message.tool_calls.append(
                    {
                        "name": "spawn_agent",
                        "args": {"task": "Inspect", "model": "openai::not-enabled"},
                        "id": "unlisted",
                        "type": "tool_call",
                    }
                )
            elif self._cursor == 4:
                assert (
                    "Choose one of the user's enabled models: demo::demo-reliable, openai::gpt-helper"
                    in tools["unlisted"].content
                )
                reader = json.loads(tools["reader"].content)
                assert ws.thread(reader["agent_id"])["model"]["model"] == "gpt-helper"
                assert reader["folder"] == str(paths["checkout"])
                await services.runtime.wait(reader["run_id"])
                message = tool_call_message(
                    "spawn_agent", {"task": "Write shared.txt", "title": "Writer"}, "writer"
                )
                message.tool_calls.append(
                    {"name": "read_file", "args": {"path": "a.txt"}, "id": "parent-read", "type": "tool_call"}
                )
            elif self._cursor == 5:
                # By default a helper edits the parent's checkout directly: no worktree, nothing to merge.
                writer = json.loads(tools["writer"].content)
                assert (
                    writer["folder"] == str(paths["checkout"])
                    and ws.delivery.worktree(writer["agent_id"]) is None
                )
                writer_go.set()
                approval = f"/api/runs/{writer['run_id']}/approval"

                def mirrored():
                    return [
                        e
                        for e in services.repository.list_run_events(run["id"])
                        if e["event_type"] in {"helper_waiting", "helper_resolved"}
                    ]

                # Outside its inherited folders a helper asks on its own card, copied to the main chat.
                assert (await services.runtime.wait(writer["run_id"]))["status"] == "waiting_approval"
                card = mirrored()[-1]["payload"]["pause"]
                assert card["payload"]["requests"][0]["folder"] == str(extra)
                # An approval card is the user's, so the sidebar marks it as needing them.
                summary = services.repository.conversation_summary(
                    ws.agent_id, ws.thread(writer["agent_id"])["conversation_id"]
                )
                assert (summary["status"], summary["asks_questions"], summary["pause_sequence"]) == (
                    "waiting_approval",
                    False,
                    card["sequence"],
                )
                assert (
                    await client.post(
                        approval, json={"decision": "approve", "expected_approval": card["sequence"]}
                    )
                ).status_code == 202
                # A command outside the sandbox asks too, under the main chat's full-access setting.
                assert (await services.runtime.wait(writer["run_id"]))["status"] == "waiting_approval"
                card = mirrored()[-1]["payload"]["pause"]
                # Same status, new pause: the sidebar summary still tells the two apart.
                summary = services.repository.conversation_summary(
                    ws.agent_id, ws.thread(writer["agent_id"])["conversation_id"]
                )
                assert summary["pause_sequence"] == card["sequence"]
                assert card["payload"]["requests"][0]["kind"] == "outside_sandbox"
                assert not full_access(ws, writer["agent_id"])
                with ws.db() as db:
                    db.execute("UPDATE coding_threads SET mode = 'full' WHERE id = ?", (parent["id"],))
                assert full_access(ws, writer["agent_id"])
                with ws.db() as db:
                    db.execute("UPDATE coding_threads SET mode = 'auto' WHERE id = ?", (parent["id"],))
                assert (
                    await client.post(
                        approval, json={"decision": "reject", "expected_approval": card["sequence"]}
                    )
                ).status_code == 202
                # A folder allowed read-only on its card can be widened on a later one.
                assert (await services.runtime.wait(writer["run_id"]))["status"] == "waiting_approval"
                card = mirrored()[-1]["payload"]["pause"]
                assert (
                    card["payload"]["requests"][0]["folder"],
                    card["payload"]["requests"][0]["writable"],
                ) == (str(extra), True)
                assert (
                    await client.post(
                        approval, json={"decision": "reject", "expected_approval": card["sequence"]}
                    )
                ).status_code == 202
                await services.runtime.wait(writer["run_id"])
                assert [e["event_type"] for e in mirrored()] == ["helper_waiting", "helper_resolved"] * 3
                assert str(extra) in str(
                    services.repository.get_run(writer["run_id"])["input"]["folder_grants"]
                )
                assert not (extra / "outside.txt").exists() and not (extra / "written.txt").exists()
                assert (paths["checkout"] / "shared.txt").read_text() == "Shared helper edit\n"
                assert (paths["checkout"] / "a.txt").read_text() == "Writer edit\n"
                message = tool_call_message("get_agent", {"agent_id": writer["agent_id"]}, "writer-result")
            elif self._cursor == 6:
                result = json.loads(tools["writer-result"].content)
                assert result["status"] == "completed" and "diff" not in result, result
                # It read a.txt before the helper edited it, so its replacement is stale.
                message = tool_call_message(
                    "write_file", {"path": "a.txt", "content": "Parent overwrite\n"}, "overwrite"
                )
            else:
                # Sharing the checkout, a write refused as stale names the helper that changed the file.
                assert "Helper Writer changed this file" in tools["overwrite"].content, tools[
                    "overwrite"
                ].content
                assert (paths["checkout"] / "a.txt").read_text() == "Writer edit\n"
                parent_ready.set()
                await asyncio.Event().wait()
                raise AssertionError("The test must cancel this parent")
            self._cursor += 1
            return ChatResult(generations=[ChatGeneration(message=message)])

    models = [
        Parent(),
        Editor(),
        Editor(),
        Readonly(script=[AIMessage(content="Inspected only")]),
        Shared(
            script=[
                tool_call_message("read_file", {"path": str(extra / "keep.txt")}, "shared-extra"),
                tool_call_message(
                    "run_command",
                    {
                        "command": "printf no > " + shlex.quote(str(extra / "outside.txt")),
                        "outside_sandbox": True,
                        "reason": "Check the extra folder",
                    },
                    "shared-outside",
                ),
                tool_call_message(
                    "write_file", {"path": str(extra / "written.txt"), "content": "no"}, "shared-extra-write"
                ),
                tool_call_message(
                    "write_file", {"path": "shared.txt", "content": "Shared helper edit\n"}, "shared-write"
                ),
                tool_call_message(
                    "edit_file",
                    {"path": "a.txt", "old_text": "before", "new_text": "Writer edit"},
                    "shared-edit",
                ),
                AIMessage(content="Wrote shared.txt"),
            ]
        ),
    ]
    services.models.chat_model = lambda *_args, **_kwargs: models.pop(0)
    services.repository.set_setting("enabled_models", ["demo::demo-reliable", "openai::gpt-helper"])
    # The parent runs in its own managed worktree, the app's default; shared helpers join it.
    run = await ws.send(parent["id"], "Exercise helper lifecycle", worktree=True)
    paths["checkout"] = Path(ws.root(parent["id"]))
    assert paths["checkout"] != folder
    try:
        await asyncio.wait_for(parent_ready.wait(), 25)
        response = await client.post(f"/api/runs/{run['id']}/cancel")
        assert response.status_code == 200, response.text
        assert (await services.runtime.wait(run["id"]))["status"] == "cancelled"
        assert services.repository.get_run(followup["run_id"])["status"] == "cancelled"
        assert not services.runtime.tasks
        # The main task's end closes its helpers' terminals and their processes.
        for session, pid in terminals:
            assert session.closed
            await dead(pid)
        assert (Path(child["folder"]) / "child.txt").read_text() == "Retained child work\n"
        assert git(child["folder"], "diff", "--cached", "--name-only") == "child.txt"
        assert (folder / "a.txt").read_text() == "before\n"
        assert all((grant / "keep.txt").read_text() == "Unrelated data" for grant in (existing, added_later))
        assert readonly_tools and not (
            {
                "run_command",
                "write_file",
                "edit_file",
                "start_terminal",
                "write_terminal",
                "spawn_agent",
                "configure_connector",
                "choose_repository",
            }
            & readonly_tools
        )
        assert (
            services.repository.get_run(followup["run_id"])["input"]["folder_grants"]
            == services.repository.get_run(child["run_id"])["input"]["folder_grants"]
        )
        # Every helper, finished or stopped with its main task, is archived.
        with ws.db() as db:
            assert {
                row["archived"]
                for row in db.execute(
                    "SELECT archived FROM coding_threads WHERE parent_thread_id = ?", (parent["id"],)
                )
            } == {1}
        # The main chat's card for each helper it started shows the start of its instruction.
        started = {
            e["payload"]["agent_name"]: e["payload"]["task"]
            for e in services.repository.list_run_events(run["id"])
            if e["event_type"] == "delegation_started" and not e["payload"].get("followup")
        }
        assert started["Editor"] == "Make an isolated change and run a background check"
        # Follow-up work on a helper is recorded on the main task, which accounts for each helper run.
        assert any(
            e["payload"].get("followup") and e["payload"]["run_id"] == followup["run_id"]
            for e in services.repository.list_run_events(run["id"])
            if e["event_type"] == "delegation_started"
        )

        # Told to continue, the next task takes over the stopped task's helpers and is told of them.
        class Continuation(ScriptedModel):
            async def _agenerate(self, messages, **kwargs):
                tools = {
                    message.tool_call_id: message for message in messages if isinstance(message, ToolMessage)
                }
                if self._cursor == 0:
                    assert any(
                        "<helper_updates>" in str(m.content) and child["agent_id"] in str(m.content)
                        for m in messages
                    )
                    message = tool_call_message("get_agent", {"agent_id": child["agent_id"]}, "adopted")
                else:
                    assert json.loads(tools["adopted"].content)["status"] == "cancelled", tools[
                        "adopted"
                    ].content
                    message = AIMessage(content="Checked the earlier helpers")
                self._cursor += 1
                return ChatResult(generations=[ChatGeneration(message=message)])

        models.append(Continuation())
        resumed = await ws.send(parent["id"], "Continue")
        assert (await services.runtime.wait(resumed["id"]))["status"] == "completed"
        assert ws.thread(child["agent_id"])["parent_run_id"] == resumed["id"]
    finally:
        permission_ready.set()
        writer_go.set()
        if run["id"] in services.runtime.tasks:
            await services.runtime.cancel(run["id"])
        await ws.terminals.shutdown()
        for session, pid in terminals:
            assert session.closed and session.process.returncode is not None
            await dead(pid)
        with ws.db() as db:
            owned = [dict(row) for row in db.execute("SELECT * FROM coding_worktrees")]
        for worktree in owned:
            checkout = Path(worktree["folder"]).resolve()
            assert checkout.is_relative_to(tmp_path.resolve())
            if checkout.exists():
                # Remove only this fixture's disposable files; never execute deletion examples.
                shutil.rmtree(checkout)


def test_restart_settles_work_paused_on_a_desktop_that_is_gone(stack):
    from wavefunction.repository import DESKTOP_CLOSED

    agent = stack.agent
    conversation = stack.repository.current_conversation(agent["id"])
    run = stack.repository.create_run(
        agent["id"], {"text": "go"}, conversation["id"], spec=agent_spec().model_dump(mode="json")
    )
    stack.repository.update_run(run["id"], status="waiting_computer", usage={"total_tokens": 42})
    stack.repository.add_run_event(run["id"], "assistant_delta", {"text": "Opening the si"})
    assert stack.repository.has_active_work(agent["id"])

    assert stack.repository.settle_stale_runs() == 1

    settled = stack.repository.get_run(run["id"])
    assert settled["status"] == "failed" and settled["error"] == DESKTOP_CLOSED
    assert settled["usage"] == {"total_tokens": 42}
    # The interrupted chat releases the quit dialog and retains its usage evidence.
    assert not stack.repository.has_active_work(agent["id"])
    assert not [
        e for e in stack.repository.list_run_events(run["id"]) if e["event_type"] == "assistant_delta"
    ]
