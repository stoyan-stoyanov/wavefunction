"""Slash invocation captures the same owned, versioned skills as the picker."""

from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create

from wavefunction.coding_context import TaskContext
from wavefunction.coding_workspace import CodingWorkspace


async def test_global_slash_skill_across_projects_and_version_capture(coding, tmp_path):
    client, ws, services = coding
    name = "thorough-bug-review"
    services.skills.save(name, "Review reproduced bugs", "Report only demonstrated failures.")
    folders = [tmp_path / "one", tmp_path / "two"]
    for folder in folders:
        folder.mkdir()
    project = await client.post(
        "/api/coding/projects", json={"name": "Synthetic project", "folder": str(folders[1])}
    )
    assert project.status_code == 201, project.text
    threads = [
        await create(client, folder=str(folders[0])),
        await create(client, project_id=project.json()["id"]),
    ]
    for thread in threads:
        inventory = (await client.get("/api/coding/skills", params={"thread_id": thread["id"]})).json()
        assert any(
            s["name"] == name and s["active"] and s["source"] == "library" for s in inventory["skills"]
        )
        script_gateway(
            services.models,
            [
                tool_call_message("read_skill", {"name": name}, "read-review"),
                AIMessage(content="Read the captured review guidance"),
            ],
        )
        response = await client.post(
            f"/api/coding/threads/{thread['id']}/messages",
            json={"content": f"/{name}\nFocus on cancellation"},
        )
        assert response.status_code == 202, response.text
        run = await services.runtime.wait(response.json()["id"])
        assert run["status"] == "completed"
        assert run["input"]["coding_context"]["skills"] == [name]
        assert run["input"]["display_text"] == f"/{name}\nFocus on cancellation"
        assert run["input"]["text"].startswith("Focus on cancellation")
        assert "Report only demonstrated failures." in str(ws.runs(thread["id"])[0]["events"])
        assert run["spec"]["skill_versions"][name] == 1
    services.skills.save(name, "Review reproduced bugs", "Updated guidance for future tasks.")
    for thread in threads:
        run = services.repository.get_run(ws.runs(thread["id"])[0]["id"])
        assert run["spec"]["skill_versions"][name] == 1
    restarted = CodingWorkspace(services)
    assert restarted.services.skills.get(name).version == 2


async def test_slash_skill_without_arguments_and_unavailable_selection(coding):
    client, _, services = coding
    services.skills.save("review-bugs", "Review bugs", "Use the saved instructions.")
    thread = await create(client)
    script_gateway(services.models, [AIMessage(content="Reviewed")])
    endpoint = f"/api/coding/threads/{thread['id']}/messages"
    response = await client.post(endpoint, json={"content": "/review-bugs"})
    assert response.status_code == 202, response.text
    run = await services.runtime.wait(response.json()["id"])
    assert run["input"]["text"].startswith("Use the review-bugs skill for this repository.")
    assert run["input"]["coding_context"]["skills"] == ["review-bugs"]
    services.skills.delete("review-bugs")
    stale = await client.post(
        endpoint, json={"content": "/review-bugs", "context": {"skills": ["review-bugs"]}}
    )
    assert stale.status_code == 422 and "unavailable" in stale.text


async def test_prompt_precedence_and_unknown_slash_passthrough(coding):
    client, ws, services = coding
    services.skills.save("review", "A skill sharing a prompt name", "Skill guidance.")
    thread = await create(client)
    workflow = ws.context.workflow(thread["id"])
    script_gateway(services.models, [AIMessage(content="Prepared")])
    response = await client.post(
        f"/api/coding/threads/{thread['id']}/messages", json={"content": "/review\tCheck retries"}
    )
    run = await services.runtime.wait(response.json()["id"])
    # The built-in /review prompt wins over a skill of the same name.
    assert run["input"]["text"].startswith("Review the current changes") and run["input"]["text"].endswith(
        " Check retries"
    )
    assert run["input"]["coding_context"] == {"files": [], "skills": [], "command": "review"}
    context = TaskContext()
    expanded, _ = await ws.context.expand(
        thread["id"], "/unknown literal", context, {"skills": []}, folder=None, workflow=workflow
    )
    assert expanded == "/unknown literal"
