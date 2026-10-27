"""The model's environment and context: cache-stable prefixes, budgets and compaction."""

from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create


async def test_environment_changes_join_the_conversation_and_leave_its_prompt_and_history_alone(
    coding, tmp_path, monkeypatch
):
    """What a provider caches is the request's prefix: the system prompt and the history before the
    newest message. Those stay byte-identical across turns; a changed environment is appended at
    the start of the next turn, only the parts that changed, and never in the middle of a turn."""
    from langchain_core.messages import SystemMessage, ToolMessage

    from wavefunction import github_cli, sandbox
    from wavefunction.hands import COMMAND_ACCESS_SETTING
    from wavefunction.local_coding import environment_context

    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    thread = await create(client, folder=str(folder))
    seen = []
    script_gateway(
        services.models,
        [tool_call_message("run_command", {"command": "true"}, "check"), AIMessage(content="First")],
        [AIMessage(content="Second")],
        seen=seen,
    )
    first = await ws.send(thread["id"], "Check the repository")
    turn = services.runtime.tasks[first["id"]]
    await services.runtime.wait(first["id"])
    await turn
    services.repository.set_setting(COMMAND_ACCESS_SETTING, {ws.agent_id: "sandboxed"})
    # Offline, gh is not asked for a sign-in no command could use.
    asked = []

    async def token():
        asked.append(True)
        raise github_cli.SignInRequired("signed out")

    github_cli._command_token = None
    monkeypatch.setattr(github_cli, "token", token)
    second = await ws.send(thread["id"], "And now?")
    await services.runtime.wait(second["id"])
    assert not asked

    def shown(messages):
        return [(type(message).__name__, message.content) for message in messages]

    told = [message for message in seen[0] if message.additional_kwargs.get("lc_source") == "environment"]
    assert len(told) == 1 and {"date", "local", "github", "repository_instructions"} <= set(
        told[0].additional_kwargs["environment"]
    )
    assert "Network is enabled." in told[0].content and "not signed in to GitHub" in told[0].content
    # The second call of the turn sees the tool's result, and no environment between.
    assert isinstance(seen[1][-1], ToolMessage) and shown(seen[1][: len(seen[0])]) == shown(seen[0])
    # The next turn: the same system prompt and history, then the request and what changed.
    assert all(isinstance(call[0], SystemMessage) and call[0].content == seen[0][0].content for call in seen)
    assert "Network is" not in seen[0][0].content and "Today is" not in seen[0][0].content
    assert shown(seen[2][: len(seen[1]) + 1]) == shown(seen[1]) + [("AIMessage", "First")]
    request, update = seen[2][-2:]
    assert request.content == "And now?"
    assert set(update.additional_kwargs["environment"]) == {"local", "github"}
    assert "Network is disabled." in update.content and "no network access" in update.content

    # Each request reports what fills it and when the conversation would be compacted.
    budgets = [
        event["payload"]
        for run in (first, second)
        for event in services.repository.list_run_events(run["id"])
        if event["event_type"] == "context_budget"
    ]
    assert len(budgets) == 3
    for budget in budgets:
        assert sum(budget["breakdown"].values()) == budget["estimated_tokens"]
        assert budget["compact_at"] == min(350_000, int(budget["context_window"] * 0.7))
        assert budget["breakdown"]["system_prompt"] > 0 and budget["breakdown"]["tools"] > 0
    # Compact now: the conversation becomes its summary in a run of its own that asks for no
    # answer. The next turn keeps the system prompt and is told its environment again.
    nothing = await create(client, folder=str(folder))
    refused = await client.post(f"/api/coding/threads/{nothing['id']}/compact")
    assert refused.status_code == 422 and "nothing to compact" in refused.text
    script_gateway(
        services.models,
        [AIMessage(content="The repository was checked twice.")],
        [AIMessage(content="Third")],
        seen=seen,
    )
    response = await client.post(f"/api/coding/threads/{thread['id']}/compact")
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "compact"
    compacted = await services.runtime.wait(response.json()["id"])
    assert compacted["status"] == "completed" and compacted["output"]["text"] == "Context compacted."
    assert len(seen) == 4
    third = await ws.send(thread["id"], "What next?")
    await services.runtime.wait(third["id"])
    after = seen[-1]
    assert after[0].content == seen[0][0].content
    assert not any(message.content in ("Check the repository", "First") for message in after)
    assert any("The repository was checked twice." in str(message.content) for message in after)
    assert any(message.additional_kwargs.get("lc_source") == "environment" for message in after)

    # Different chats in the same repository need the same model context, while their
    # command shims and temporary files retain independent operational paths.
    scratch = [(tmp_path / name).resolve() for name in ("chat-one", "chat-two")]
    grants = [sandbox.Grant(folder)]
    contexts = [environment_context(folder, path, "network", grants) for path in scratch]
    assert contexts[0] == contexts[1] and str(folder) in contexts[0]
    with monkeypatch.context() as isolated:
        isolated.setattr(sandbox, "command_path", lambda: ["/usr/bin", "/bin"])
        isolated.setattr(sandbox, "_user_folder", lambda _name: None)
        environments = [sandbox.Sandbox(grants, scratch=path).environment() for path in scratch]
    assert environments[0]["PATH"] != environments[1]["PATH"]
    for path, environment in zip(scratch, environments, strict=True):
        assert environment["PATH"].split(":")[0] == str(path / "bin")
        assert environment["TMPDIR"] == str(path)
        # Without a repository, scratch is the command cwd and must still be disclosed.
        no_folder = environment_context(None, path, "network", [], choose=True)
        assert f"commands start in {path}" in no_folder and "absolute paths" in no_folder
