"""GitHub sign-in: a paused request resumes after restart once gh is signed in."""

import asyncio
import json

from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create


async def test_github_setup_resumes_the_captured_request_after_restart(coding, tmp_path, monkeypatch):
    from wavefunction.runtime import RuntimeManager

    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    thread = await create(client, folder=str(folder))
    other_thread = await create(client, folder=str(folder))
    signed_in = {"account": ""}

    async def account(root=None):
        return signed_in["account"]

    monkeypatch.setattr("wavefunction.github_cli.signed_in_account", account)
    discover_setup = tool_call_message(
        "search_available_tools", {"query": "configure_github", "limit": 1}, "discover"
    )
    script_gateway(
        services.models,
        [discover_setup, tool_call_message("configure_github", {}, "setup")],
        [discover_setup, tool_call_message("configure_github", {}, "setup")],
    )
    run = await ws.send(thread["id"], "When were the last pull requests?")
    await asyncio.wait_for(services.runtime.tasks[run["id"]], timeout=10)
    paused = services.repository.get_run(run["id"])
    assert paused["status"] == "waiting_connector", paused
    # The sidebar summary names the pause it waits on, a connector setup as much as an approval.
    setup_pause = services.repository.list_run_events(
        run["id"], limit=1, tail=True, event_types=("run_connector_setup_required",)
    )
    assert (
        services.repository.conversation_summary(ws.agent_id, paused["conversation_id"])["pause_sequence"]
        == setup_pause[0]["sequence"]
    )
    offered = [
        event["payload"]["tools"]
        for event in services.repository.list_run_events(run["id"])
        if event["event_type"] == "tools_selected"
    ]
    setup_tools = {"configure_github", "choose_repository_folder", "request_folder_access"}
    assert not setup_tools.intersection(offered[0])
    assert setup_tools <= set(offered[1])
    assert offered[1][: len(offered[0])] == offered[0]
    captured = paused["spec"]
    setup = (await client.get(f"/api/runs/{run['id']}/connectors")).json()
    assert [(row["id"], row["local_kind"], row["state"]) for row in setup["connectors"]] == [
        ("github", "github", "setup")
    ]
    # Another chat waits on the same sign-in without being held up by the first.
    other = await ws.send(other_thread["id"], "Which checks failed?")
    await asyncio.wait_for(services.runtime.tasks[other["id"]], timeout=10)
    assert services.repository.get_run(other["id"])["status"] == "waiting_connector"

    # Reopen the saved checkpoint without the original in-memory graph.
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
    await services.runtime.resume_connected()
    assert services.repository.get_run(run["id"])["status"] == "waiting_connector"
    # The card says it is done only once gh says so.
    refused = await client.post(f"/api/runs/{run['id']}/connectors/github")
    assert refused.status_code == 409 and "not signed in" in refused.text
    await services.runtime.resume_connected()
    assert services.repository.get_run(run["id"])["status"] == "waiting_connector"

    signed_in["account"] = "synthetic-user"
    accepted = await client.post(f"/api/runs/{run['id']}/connectors/github")
    assert accepted.json() == {"account": "synthetic-user"}
    # gh's sign-in is the Mac's: signing in from one chat's card carries both chats on.
    script_gateway(
        services.models,
        [AIMessage(content="Signed in; listing pull requests with gh")],
        [AIMessage(content="Signed in; reading checks with gh")],
    )
    await services.runtime.resume_connected()
    completed = await services.runtime.wait(run["id"])
    assert completed["status"] == "completed", completed
    assert (await services.runtime.wait(other["id"]))["status"] == "completed"
    result = next(
        event["payload"]
        for event in services.repository.list_run_events(run["id"])
        if event["event_type"] == "tool_completed" and event["payload"].get("tool") == "configure_github"
    )
    assert "signed in as synthetic-user" in json.dumps(result) and "git and gh" in json.dumps(result)
    assert completed["conversation_id"] == thread["conversation_id"]
    assert completed["spec"]["system_prompt"] == captured["system_prompt"]
    assert not any(
        event["event_type"] == "action_outcome_unknown"
        for event in services.repository.list_run_events(run["id"])
    )
