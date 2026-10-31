"""Helpers: a parent chat delegates to isolated helpers and integrates their results."""

import asyncio
from pathlib import Path

import pytest
from conftest import tool_call_message
from langchain_core.messages import AIMessage
from support import create, reached


@pytest.mark.sandbox
async def test_parent_orchestrates_isolated_children_and_integrates_their_reviewed_results(
    coding, tmp_path, monkeypatch
):
    import json
    import shlex
    import shutil
    from dataclasses import replace

    from conftest import ScriptedModel
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from support import git, repository

    from wavefunction.steering import PARALLEL_HINT
    from wavefunction.toolkit import TOOL_REGISTRY

    client, ws, services = coding
    folder = repository(tmp_path)
    git(folder, "config", "commit.gpgsign", "false")
    remote = folder / ".git/upstream.git"
    git(folder, "clone", "--bare", str(folder), str(remote))
    git(folder, "remote", "add", "origin", remote.as_uri())
    (folder / "staged-only.txt").write_text("Unrelated staged work\n")
    git(folder, "add", "staged-only.txt")
    parent_index = (folder / ".git/index").read_bytes()
    parent_head = git(folder, "rev-parse", "HEAD")
    parent, foreign = await create(client, folder=str(folder)), await create(client, folder=str(folder))
    alpha_ready, release, waiting_for_steering = asyncio.Event(), asyncio.Event(), asyncio.Event()
    children, results, seen_tools = {}, {}, {}
    captures, observations = [], {}
    names = ("alpha", "beta", "gamma", "delta")
    checkpoint_git = ws.checkpoints.git

    async def observe_git(folder, **request):
        if request.get("action") == "checkpoint_capture":
            captures.append(request["owner"])
        return await checkpoint_git(folder, **request)

    monkeypatch.setattr(ws.checkpoints, "git", observe_git)

    @tool("calculator")
    async def slow(expression: str) -> str:
        """Keep all four helpers active while their parent redirects Alpha."""
        alpha_ready.set()
        await release.wait()
        return "2"

    monkeypatch.setitem(
        TOOL_REGISTRY, "calculator", replace(TOOL_REGISTRY["calculator"], build=lambda _: slow)
    )

    class ChildModel(ScriptedModel):
        def bind_tools(self, tools, **kwargs):
            seen_tools[id(self)] = {tool.name for tool in tools}
            return self

        async def _agenerate(self, messages, **kwargs):
            system = "\n".join(
                str(message.content) for message in messages if isinstance(message, SystemMessage)
            )
            assert "Working as a helper" in system and "Coordinating helpers" not in system
            for message in messages:
                if isinstance(message, ToolMessage) and message.tool_call_id.startswith("git-"):
                    assert "[exit code 0]" in message.content, message.content
                if isinstance(message, ToolMessage) and message.tool_call_id == "fetch-alpha":
                    assert message.status == "success", message.content
                    assert json.loads(str(message.content))["remote"] == "origin"
            result = await super()._agenerate(messages, **kwargs)
            reply = result.generations[0].message
            assert isinstance(reply, AIMessage)
            for call in reply.tool_calls:
                if str(call["id"]).startswith("git-"):
                    name = str(call["id"]).removeprefix("git-")
                    sibling = next(child for key, child in children.items() if key != name)
                    sibling_git = Path(git(sibling["folder"], "rev-parse", "--absolute-git-dir"))
                    for target in [sibling_git / "index", folder / ".git/HEAD", folder / "other.txt"]:
                        call["args"]["command"] += (
                            f"; if printf CORRUPTED > {shlex.quote(str(target))}; then exit 92; fi"
                        )
            return result

    class ParentModel(ScriptedModel):
        async def _agenerate(self, messages, **kwargs):
            system = "\n".join(
                str(message.content) for message in messages if isinstance(message, SystemMessage)
            )
            assert "Coordinating helpers" in system and "Working as a helper" not in system
            tools = {
                message.tool_call_id: message for message in messages if isinstance(message, ToolMessage)
            }
            for name in names:
                if f"spawn-{name}" in tools:
                    spawned = tools[f"spawn-{name}"]
                    assert spawned.status == "success", spawned.content
                    children[name] = json.loads(str(spawned.content))
            if self._cursor == 0:
                message = tool_call_message("read_file", {"path": "other.txt"}, "read-parent")
            elif self._cursor == 1:
                message = tool_call_message(
                    "edit_file",
                    {"path": "other.txt", "old_text": "other before", "new_text": "Parent-only change"},
                    "edit-parent",
                )
            elif 2 <= self._cursor <= 5:
                if self._cursor == 3:
                    await reached(alpha_ready, services.runtime, children["alpha"]["run_id"])
                name = names[self._cursor - 2]
                message = tool_call_message(
                    "spawn_agent",
                    {
                        "task": f"Remember {name} and implement its change",
                        "title": name.title(),
                        "isolated": True,
                    },
                    f"spawn-{name}",
                )
            elif self._cursor == 6:
                assert len(children) == 4 and all(
                    services.repository.get_run(child["run_id"])["status"] == "running"
                    for child in children.values()
                )
                message = tool_call_message("get_agent", {"agent_id": foreign["id"]}, "foreign")
            elif self._cursor == 7:
                assert "another task" in str(tools["foreign"].content), tools["foreign"].content
                message = tool_call_message(
                    "send_message",
                    {"agent_id": children["alpha"]["agent_id"], "message": "Use the redirected alpha result"},
                    "steer-alpha",
                )
            elif self._cursor == 8:
                assert json.loads(str(tools["steer-alpha"].content))["run_id"] == children["alpha"]["run_id"]
                message = AIMessage(content="Premature final answer while helpers still work")
            elif self._cursor == 9:
                assert any("Helpers still need attention" in str(item.content) for item in messages)
                assert all(
                    services.repository.get_run(child["run_id"])["status"] == "running"
                    for child in children.values()
                )
                waiting_for_steering.set()
                message = tool_call_message(
                    "wait_agents",
                    {"agent_ids": [child["agent_id"] for child in children.values()], "timeout_seconds": 30},
                    "wait-steering",
                )
            elif self._cursor == 10:
                assert json.loads(str(tools["wait-steering"].content))["reason"] == "steering"
                user_messages = [str(item.content) for item in messages if isinstance(item, HumanMessage)]
                assert any(
                    "Coordinate four helpers without losing the parent change" in text
                    for text in user_messages
                )
                assert (
                    sum(
                        "Also retain the original parent edit when integrating" in text
                        for text in user_messages
                    )
                    == 1
                )
                release.set()
                message = tool_call_message(
                    "wait_agents",
                    {"agent_ids": [child["agent_id"] for child in children.values()], "timeout_seconds": 30},
                    "wait-first",
                )
            elif self._cursor == 11:
                observed = json.loads(
                    next(
                        str(message.content)
                        for message in reversed(messages)
                        if isinstance(message, ToolMessage) and message.name == "wait_agents"
                    )
                )
                assert all(child["status"] not in {"failed", "cancelled"} for child in observed["agents"]), (
                    observed
                )
                for child in observed["agents"]:
                    if child["status"] == "completed":
                        results[child["agent_id"]] = child
                unfinished = [
                    child["agent_id"] for child in children.values() if child["agent_id"] not in results
                ]
                question = next(
                    (
                        child
                        for child in observed["agents"]
                        if child["agent_id"] == children["gamma"]["agent_id"]
                        and child["status"] == "waiting_approval"
                    ),
                    None,
                )
                if question and "answer-gamma" not in tools:
                    gamma_run = children["gamma"]["run_id"]
                    # A helper's question is its parent's to answer; the sidebar shows it as running work.
                    assert services.repository.conversation_summary(
                        ws.agent_id, ws.thread(question["agent_id"])["conversation_id"]
                    )["asks_questions"]
                    if "question-gamma" not in tools:
                        # Only the parent can answer a question it already received: waiting
                        # for it again cannot change anything.
                        message = tool_call_message(
                            "wait_agents",
                            {
                                "agent_ids": [question["agent_id"]],
                                "timeout_seconds": 5,
                                "after": {question["agent_id"]: question["cursor"]},
                            },
                            "question-gamma",
                        )
                    elif "permission-gamma" not in tools:
                        assert json.loads(str(tools["question-gamma"].content))["reason"] == "needs_answer"
                        assert tools["question-gamma"].artifact == {"polled": False}
                        # Ordinary clarifications belong to the parent; an approval card
                        # does not. Model the latter pause without executing any command.
                        with pytest.raises(ValueError, match="running parent"):
                            await services.runtime.reply(
                                gamma_run, "Foreign guidance", parent_run_id="foreign-parent"
                            )
                        original_pause = services.runtime._latest_pause
                        observations["pause_override"] = pytest.MonkeyPatch()
                        observations["pause_override"].setattr(
                            services.runtime,
                            "_latest_pause",
                            lambda run_id: (
                                {**original_pause(run_id), "event_type": "run_waiting_for_approval"}
                                if run_id == gamma_run
                                else original_pause(run_id)
                            ),
                        )
                        message = tool_call_message(
                            "send_message",
                            {"agent_id": question["agent_id"], "message": "Proceed with the operation"},
                            "permission-gamma",
                        )
                    elif "needs-user-gamma" not in tools:
                        assert "cannot approve" in str(tools["permission-gamma"].content), tools[
                            "permission-gamma"
                        ].content
                        assert not any(
                            event["event_type"] == "steering_requested"
                            for event in services.repository.list_run_events(gamma_run)
                        )
                        # Only the user can resume an approval pause: it neither holds the
                        # parent's turn open nor keeps a wait polling.
                        parent_run = services.repository.get_run(gamma_run)["parent_run_id"]
                        pending = services.runtime._helper_updates(parent_run, {})["pending"]
                        assert question["agent_id"] not in {item["agent_id"] for item in pending}
                        # A finished parent turn would wait for it without model calls or an idle limit.
                        observations["user_wait"] = asyncio.create_task(
                            services.runtime._await_user_helpers(parent_run, [gamma_run])
                        )
                        await asyncio.sleep(0.05)
                        assert not observations["user_wait"].done()
                        assert (
                            f"user-helpers:{parent_run}"
                            in services.runtime.active[parent_run].executing_tools
                        )
                        message = tool_call_message(
                            "wait_agents",
                            {
                                "agent_ids": [question["agent_id"]],
                                "timeout_seconds": 30,
                                "after": {question["agent_id"]: question["cursor"]},
                            },
                            "needs-user-gamma",
                        )
                    else:
                        observations.pop("pause_override").undo()
                        assert json.loads(str(tools["needs-user-gamma"].content))["reason"] == "needs_user"
                        assert tools["needs-user-gamma"].artifact == {"polled": False}
                        assert not observations["user_wait"].done()
                        message = tool_call_message(
                            "send_message",
                            {"agent_id": question["agent_id"], "message": "Use gamma as the final value"},
                            "answer-gamma",
                        )
                    self._cursor -= 1
                elif unfinished:
                    message = tool_call_message(
                        "wait_agents", {"agent_ids": unfinished, "timeout_seconds": 30}, "wait-remaining"
                    )
                    self._cursor -= 1
                else:
                    assert len(results) == 4 and all(
                        result["files"] == ["a.txt"] for result in results.values()
                    ), results
                    assert all(
                        result["base_commit"]
                        and result["base_tree"]
                        and result["result_commit"]
                        and result["result_ref"]
                        and "diff --git" in result["diff"]
                        for result in results.values()
                    )
                    # A helper's reported patch belongs to its settled task, even if the
                    # user edits that retained checkout before the parent reads it again.
                    observations["captures"] = len(captures)
                    (Path(children["alpha"]["folder"]) / "a.txt").write_text("User edit after helper\n")
                    message = tool_call_message(
                        "get_agent", {"agent_id": children["alpha"]["agent_id"]}, "reread-alpha"
                    )
            elif self._cursor == 12:
                # Answering Gamma changed its status, which ended that wait.
                await asyncio.wait_for(observations.pop("user_wait"), 5)
                reread = json.loads(str(tools["reread-alpha"].content))
                assert reread["diff"] == results[children["alpha"]["agent_id"]]["diff"]
                assert reread["cursor"] == results[children["alpha"]["agent_id"]]["cursor"]
                assert reread["result_commit"] == results[children["alpha"]["agent_id"]]["result_commit"]
                assert git(folder, "show", f"{reread['result_commit']}:a.txt") == "alpha"
                assert git(folder, "rev-parse", reread["result_ref"]) == reread["result_commit"]
                # Every helper has finished and its result was read: a wait for news has nothing
                # left to wait for, however long the timeout.
                message = tool_call_message(
                    "wait_agents",
                    {
                        "agent_ids": list(results),
                        "timeout_seconds": 600,
                        "after": {key: result["cursor"] for key, result in results.items()},
                    },
                    "unchanged",
                )
            elif self._cursor == 13:
                unchanged = json.loads(str(tools["unchanged"].content))
                assert unchanged["reason"] == "settled"
                assert all(
                    child["unchanged"] and not ({"output", "diff", "events"} & child.keys())
                    for child in unchanged["agents"]
                )
                assert len(captures) == observations["captures"]
                assert (
                    not {child["run_id"] for child in children.values()} & services.runtime.conditions.keys()
                )
                # It answered at once, so it is no poll: repeating it counts toward the loop limit.
                assert tools["unchanged"].artifact == {"polled": False}
                message = tool_call_message("read_file", {"path": "a.txt"}, "read-integration")
            elif self._cursor == 14:
                message = tool_call_message(
                    "edit_file",
                    {"path": "a.txt", "old_text": "before", "new_text": "Integrated four helpers"},
                    "integrate",
                )
            else:
                message = AIMessage(content="All four reviewed helper changes are integrated")
            self._cursor += 1
            return ChatResult(generations=[ChatGeneration(message=message)])

    models = [
        ParentModel(),
        *(
            ChildModel(
                script=[
                    tool_call_message("calculator", {"expression": "1+1"}, f"wait-{name}"),
                    *(
                        [
                            tool_call_message(
                                "ask_user",
                                {
                                    "questions": [
                                        {"id": "value", "title": "Which final value should Gamma use?"}
                                    ]
                                },
                                "ask-gamma",
                            )
                        ]
                        if name == "gamma"
                        else []
                    ),
                    tool_call_message("read_file", {"path": "a.txt"}, f"read-{name}"),
                    tool_call_message(
                        "edit_file", {"path": "a.txt", "old_text": "before", "new_text": name}, f"edit-{name}"
                    ),
                    tool_call_message(
                        "run_command",
                        {
                            "command": "set -e; git add -- a.txt && git diff --cached --exit-code -- other.txt staged-only.txt && "
                            "git commit -m 'Owned helper change' && "
                            f"if printf CORRUPTED > {shlex.quote(str(folder / '.git/index'))}; then exit 91; fi"
                        },
                        f"git-{name}",
                    ),
                    AIMessage(content=f"{name.title()} completed"),
                ]
            )
            for name in names
        ),
    ]
    services.models.chat_model = lambda *_args, **_kwargs: models.pop(0)
    run = await ws.send(parent["id"], "Coordinate four helpers without losing the parent change")
    try:
        await reached(waiting_for_steering, services.runtime, run["id"])
        steered = await ws.send(parent["id"], "Also retain the original parent edit when integrating")
        assert steered["id"] == run["id"]
        result = await services.runtime.wait(run["id"])
        assert result["status"] == "completed", result
        assert len(children) == len(results) == 4
        assert (folder / "a.txt").read_text() == "Integrated four helpers\n"
        assert (folder / "other.txt").read_text() == "Parent-only change\n"
        assert (folder / ".git/index").read_bytes() == parent_index
        assert git(folder, "rev-parse", "HEAD") == parent_head
        assert len({child["folder"] for child in children.values()}) == 4
        saver = await services.runtime.checkpointer()
        for name, child in children.items():
            thread = ws.thread(child["agent_id"])
            assert thread["parent_thread_id"] == parent["id"] and thread["parent_run_id"] == run["id"]
            assert Path(child["folder"]) != folder
            assert (Path(child["folder"]) / ".git").is_file()
            assert (
                Path(git(child["folder"], "rev-parse", "--path-format=absolute", "--git-common-dir"))
                == folder / ".git"
            )
            assert (Path(child["folder"]) / "a.txt").read_text() == (
                "User edit after helper" if name == "alpha" else name
            ) + "\n"
            assert (Path(child["folder"]) / "other.txt").read_text() == "Parent-only change\n"
            assert git(Path(child["folder"]), "log", "-1", "--format=%s") == "Owned helper change"
            assert git(Path(child["folder"]), "diff", "--cached", "--name-only") == ""
            state = await saver.aget_tuple({"configurable": {"thread_id": thread["conversation_id"]}})
            assert f"Remember {name}" in str(state.checkpoint)
            assert all(f"Remember {other}" not in str(state.checkpoint) for other in names if other != name)
            if name == "alpha":
                assert "Use the redirected alpha result" in str(state.checkpoint)
                assert PARALLEL_HINT not in str(state.checkpoint)
                events = services.repository.list_run_events(child["run_id"])
                assert len([event for event in events if event["event_type"] == "steering_applied"]) == 1
            if name == "gamma":
                events = services.repository.list_run_events(child["run_id"])
                answer = next(event for event in events if event["event_type"] == "questions_answered")
                assert answer["payload"]["replied"] and answer["payload"]["source"] == "parent"
                messages = state.checkpoint["channel_values"]["messages"]
                tool_answer = json.loads(
                    next(
                        str(message.content)
                        for message in messages
                        if isinstance(message, ToolMessage) and message.tool_call_id == "ask-gamma"
                    )
                )
                assert tool_answer["answered_by_parent"] and "not user authorization" in tool_answer["note"]
                assert any(
                    "Parent agent clarification (not a user approval)" in str(message.content)
                    for message in messages
                )
        parent_state = await saver.aget_tuple({"configurable": {"thread_id": parent["conversation_id"]}})
        notices = [
            message
            for message in parent_state.checkpoint["channel_values"]["messages"]
            if message.additional_kwargs.get("lc_source") == "helper_update"
            and str(message.content).startswith("<helper_updates>")
        ]
        updates = [
            json.loads(message.content.split("\n", 1)[1].split("\n</helper_updates>", 1)[0])
            for message in notices
        ]
        identities = [(update["agent_id"], update["cursor"]) for update in updates]
        assert len(identities) == len(set(identities))
        assert {update["agent_id"] for update in updates if update["status"] == "completed"} == {
            child["agent_id"] for child in children.values()
        }
        assert any(
            update["agent_id"] == children["gamma"]["agent_id"] and update["status"] == "waiting_approval"
            for update in updates
        )
        # Gamma's question was the parent's to answer: the main chat never showed a card for it.
        assert not [
            event
            for event in services.repository.list_run_events(run["id"])
            if event["event_type"] in {"helper_waiting", "helper_resolved"}
            and event["payload"]["agent_id"] == children["gamma"]["agent_id"]
        ]
        # The main task's cost includes what its helpers spent in their own chats.
        services.repository.update_run(
            children["alpha"]["run_id"],
            status="completed",
            usage={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
        )
        detail = (await client.get(f"/api/coding/threads/{parent['id']}")).json()
        assert (
            next(item for item in detail["runs"] if item["id"] == run["id"])["helper_usage"]["total_tokens"]
            == 7
        )
        assert all(
            "run_command" in names and not ({"spawn_agent", "send_message", "stop_agent"} & names)
            for names in seen_tools.values()
        )
        assert not services.runtime.tasks
        assert len(services.repository.agent_ids()) == 1
    finally:
        if override := observations.pop("pause_override", None):
            override.undo()
        if waiter := observations.pop("user_wait", None):
            waiter.cancel()
        release.set()
        if run["id"] in services.runtime.tasks:
            await services.runtime.cancel(run["id"])
        with ws.db() as db:
            owned = [dict(row) for row in db.execute("SELECT * FROM coding_worktrees")]
        for worktree in owned:
            checkout = Path(worktree["folder"]).resolve()
            assert checkout.is_relative_to(tmp_path.resolve()), checkout
            if checkout.exists():
                shutil.rmtree(checkout)
