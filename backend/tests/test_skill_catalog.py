"""Skills: scopes, the catalog, precedence, validation and immutable capture."""

from conftest import script_gateway, tool_call_message
from langchain_core.messages import AIMessage
from support import create

from wavefunction.coding_workspace import CodingWorkspace


async def test_skill_scopes_catalog_project_files_and_agent_saved_global_skills(coding, tmp_path):
    from wavefunction.skills import write_skill_markdown

    client, ws, services = coding
    # Built-in skills are offered read-only and are never copied into the user's library.
    assert not {"coding-workflow", "debugging", "code-review", "skill-creator"} & {
        s.name for s in services.skills.list()
    }
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / ".git").mkdir()
    project = (await client.post("/api/coding/projects", json={"name": "Repo", "folder": str(folder)})).json()
    thread = await create(client, folder=str(folder))
    script_gateway(services.models, [AIMessage(content="Done")])
    first = await ws.send(thread["id"], "Review the implementation")
    await services.runtime.wait(first["id"])
    spec = services.repository.get_run(first["id"])["spec"]
    assert "code-review" in spec["skill_sources"] and "code-review" not in spec["skill_versions"]

    # Customising a built-in is saving a Global skill with its name, which then takes precedence.
    services.skills.save("code-review", "Review changed code", "My review rules")
    # The agent saves Global skills with save_skill; project skills are repository files.
    script_gateway(
        services.models,
        [
            tool_call_message("search_available_tools", {"query": "skills"}, "find"),
            tool_call_message(
                "save_skill",
                {
                    "name": "Release Notes",
                    "description": "Write release notes. Use when asked for a changelog.",
                    "instructions": "List user-facing changes.",
                },
                "save",
            ),
            AIMessage(content="Saved"),
        ],
    )
    second = await ws.send(thread["id"], "Make a release notes skill")
    await services.runtime.wait(second["id"])
    assert services.repository.get_run(second["id"])["spec"]["skill_versions"]["code-review"] == 1
    assert services.skills.get("release-notes").body == "List user-facing changes."
    created = await client.post(
        f"/api/coding/projects/{project['id']}/skills",
        json={
            "name": "Deploy steps",
            "description": "Deploy this app. Use when asked to ship.",
            "body": "Run make deploy.",
        },
    )
    assert created.status_code == 201, created.text
    path = folder / ".agents/skills/deploy-steps/SKILL.md"
    assert created.json()["path"] == str(path) and "Run make deploy." in path.read_text()
    # Creation stays inside the repository and within what discovery can read back.
    outside = tmp_path / "outside"
    outside.mkdir()
    (folder / ".agents/skills/escape").symlink_to(outside)
    linked = await client.post(
        f"/api/coding/projects/{project['id']}/skills",
        json={"name": "escape", "description": "Escape.", "body": "x"},
    )
    assert linked.status_code == 422 and not any(outside.iterdir())
    (folder / ".agents/skills/escape").unlink()
    large = await client.post(
        f"/api/coding/projects/{project['id']}/skills",
        json={"name": "large", "description": "Large.", "body": "é" * 140000},
    )
    assert large.status_code == 422 and "256 KiB" in large.json()["detail"]
    # Frontmatter beyond name and description belongs to the repository and survives edits.
    path.write_text(path.read_text().replace("\n---\n", "\nlicense: MIT\n---\n", 1))
    (folder / ".agents/skills/code-review").mkdir()
    (folder / ".agents/skills/code-review/SKILL.md").write_text(
        write_skill_markdown("code-review", "Repo review", "Repo rules")
    )

    # A chosen skill named like connector guidance replaces it, as tasks do.
    services.skills.save("microsoft-workspace", "My Microsoft notes", "Use the shared drive.")
    rows = (await client.get("/api/coding/skill-catalog")).json()["skills"]
    by = {(row["name"], row["scope"]): row for row in rows}
    assert by[("microsoft-workspace", "builtin")]["overridden_by"] == "global"
    assert (
        by[("code-review", "builtin")]["overridden_by"] == "global"
        and not by[("code-review", "builtin")]["editable"]
    )
    assert by[("code-review", "global")]["active"] and by[("code-review", "global")]["editable"]
    assert (
        by[("code-review", "project")]["overrides"]
        and by[("code-review", "project")]["project_name"] == "Repo"
    )
    assert by[("release-notes", "global")]["created_at"] and by[("release-notes", "global")]["modified_at"]
    deploy = by[("deploy-steps", "project")]
    assert deploy["editable"] and deploy["created_at"] and deploy["path"] == str(path)
    assert by[("email", "builtin")]["condition"] and by[("skill-creator", "builtin")]["active"]
    assert all("_text" not in row for row in rows)

    # Project edits name the file and the version read; a stale edit is refused.
    edit = {
        "path": deploy["path"],
        "digest": deploy["digest"],
        "description": deploy["description"],
        "body": "Run make ship.",
    }
    assert (await client.put(f"/api/coding/projects/{project['id']}/skills", json=edit)).status_code == 200
    assert "Run make ship." in path.read_text() and "license: MIT" in path.read_text()
    stale = await client.put(f"/api/coding/projects/{project['id']}/skills", json=edit)
    assert stale.status_code == 422 and "changed since" in stale.json()["detail"]
    elsewhere = {**edit, "path": str(tmp_path / "SKILL.md")}
    assert (
        await client.put(f"/api/coding/projects/{project['id']}/skills", json=elsewhere)
    ).status_code == 422
    current = next(
        r
        for r in (await client.get("/api/coding/skill-catalog")).json()["skills"]
        if r["name"] == "deploy-steps"
    )
    # Settings writes only where the folder is still granted for writing.
    from wavefunction.sandbox import GRANTS_SETTING

    grants = services.repository.get_setting(GRANTS_SETTING, [])
    services.repository.set_setting(GRANTS_SETTING, [{**g, "writable": False} for g in grants])
    params = {"path": current["path"], "digest": current["digest"]}
    readonly = await client.delete(f"/api/coding/projects/{project['id']}/skills", params=params)
    assert readonly.status_code == 422 and path.exists()
    services.repository.set_setting(GRANTS_SETTING, grants)
    removed = await client.delete(f"/api/coding/projects/{project['id']}/skills", params=params)
    assert removed.status_code == 204 and not path.exists() and not path.parent.exists()
    assert (folder / ".agents/skills/code-review/SKILL.md").exists()


async def test_repository_global_skills_precedence_validation_and_immutable_capture(coding, tmp_path):
    from wavefunction.skills import SkillsMiddleware, write_skill_markdown
    from wavefunction.spec_v2 import AgentSpecV2

    client, ws, services = coding
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / ".git").mkdir()

    def write(root, name, body):
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(write_skill_markdown(name, "Synthetic guidance", body))
        return path

    local = write(folder / ".agents/skills", "code-review", "Original repository guidance")
    write(tmp_path / "home/.claude/skills", "code-review", "Global override should lose")
    write(tmp_path / "home/.config/opencode/skills", "global-helper", "Global guidance")
    # Another tool's bundled skills in a hidden folder are not the user's.
    write(tmp_path / "home/.codex/skills/.system", "skill-creator", "Codex's own guidance")
    bad = folder / ".claude/skills/bad/SKILL.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("No frontmatter")
    large = write(folder / ".opencode/skills", "oversized", "x" * 270000)
    outside = write(tmp_path / "outside", "escaped", "Should not be discovered")
    link = folder / ".agents/skills/link"
    link.mkdir()
    (link / "SKILL.md").symlink_to(outside)
    thread = await create(client, folder=str(folder))
    inventory = (await client.get("/api/coding/skills", params={"thread_id": thread["id"]})).json()
    selected = {s["name"]: s for s in inventory["skills"] if s["active"]}
    assert selected["code-review"]["source"] == "repository"
    assert selected["global-helper"]["source"] == "global"
    assert selected["skill-creator"]["source"] == "builtin"
    assert {"oversized", "escaped"}.isdisjoint(selected)
    assert len(inventory["warnings"]) == 3
    assert all("_text" not in s for s in inventory["skills"])
    assert sum(s["name"] == "code-review" and not s["active"] for s in inventory["skills"]) == 2
    script_gateway(
        services.models,
        [tool_call_message("read_skill", {"name": "code-review"}, "skill"), AIMessage(content="Reviewed")],
    )
    run = await ws.send(thread["id"], "Review with repository guidance")
    local.write_text(write_skill_markdown("code-review", "Synthetic guidance", "Changed after task started"))
    await services.runtime.wait(run["id"])
    spec = AgentSpecV2.model_validate(services.repository.get_run(run["id"])["spec"])
    pinned = services.skills.select(spec.skills, spec.skill_versions, spec.skill_sources, spec.skill_paths)
    text = SkillsMiddleware(pinned).tools[0].invoke({"name": "code-review"})
    assert "Original repository guidance" in text and str(local) in text
    assert "Changed after" not in text
    assert "Original repository guidance" in str(services.repository.list_run_events(run["id"]))
    # Revocation excludes repository skills from both discovery and new run captures.
    services.repository.set_setting("folder_grants", [])
    revoked = (await client.get("/api/coding/skills", params={"thread_id": thread["id"]})).json()
    assert not any(s["source"] == "repository" for s in revoked["skills"])
    assert "Changed after task started" not in str(revoked)
    without_access = await ws.send(thread["id"], "Review without repository access")
    await services.runtime.wait(without_access["id"])
    denied_spec = AgentSpecV2.model_validate(services.repository.get_run(without_access["id"])["spec"])
    assert denied_spec.skill_paths.get("code-review") != str(local)
    ws.allow_folder(str(folder.resolve()))
    restored = (await client.get("/api/coding/skills", params={"thread_id": thread["id"]})).json()
    assert any(
        s["path"] == str(local) and s["body"] == "Changed after task started" for s in restored["skills"]
    )
    local.unlink()
    CodingWorkspace(services)
    assert (
        services.skills.captured("code-review", spec.skill_sources["code-review"]).body
        == "Original repository guidance"
    )
    assert services.skills.get("code-review") is None
    assert large.exists()
    assert (await client.get("/api/coding/skills", params={"thread_id": "missing"})).status_code == 404
