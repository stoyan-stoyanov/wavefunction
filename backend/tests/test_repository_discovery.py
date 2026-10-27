"""Repository navigation with real tools, including the macOS permission boundary."""

import sys
from dataclasses import replace

import pytest
from langchain_core.messages import HumanMessage

from wavefunction.hands import COMMAND_ACCESS_SETTING, build_hands
from wavefunction.local_coding import EnvironmentMiddleware
from wavefunction.sandbox import GRANTS_SETTING, Grant
from wavefunction.toolkit import ToolContext


def tools(folder, scratch, grants=None):
    return {
        tool.name: tool
        for tool in build_hands(
            scratch,
            lambda: grants or [Grant(folder)],
            lambda: "sandboxed",
            working_directory=str(folder),
            ensure_access=lambda path, writable: None,
        )
    }


@pytest.mark.sandbox
async def test_discovery_respects_ignore_rules_paginates_and_reports_lines(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / ".gitignore").write_text("ignored/\n")
    (folder / "ignored").mkdir()
    (folder / "ignored" / "settings.py").write_text("Save secret\n")
    (folder / "image.bin").write_bytes(b"\x00Save binary")
    for index in range(5):
        (folder / f"page{index}.py").write_text("header\nSave preferences\n")
    local = tools(folder, tmp_path / "scratch")
    page = await local["find_files"].ainvoke({"pattern": "*.py", "limit": 2})
    assert "page0.py" in page and "page1.py" in page and "offset=2" in page
    assert "ignored" not in page
    page = await local["find_files"].ainvoke({"pattern": "*.py", "offset": 2})
    assert "page0.py" not in page and "page4.py" in page
    matches = await local["search_files"].ainvoke({"query": "Save", "glob": "*.py"})
    assert "page0.py:2:Save preferences" in matches and "secret" not in matches
    assert "binary" not in await local["search_files"].ainvoke({"query": "Save"})
    assert "No matches" in await local["search_files"].ainvoke({"query": "absent"})
    with pytest.raises(ValueError, match="search failed"):
        await local["search_files"].ainvoke({"query": "[invalid"})
    with pytest.raises(ValueError, match="Path not found"):
        await local["find_files"].ainvoke({"path": "missing"})
    # A folder in the glob would match nothing; it is refused with where to put it instead.
    assert "page0.py" in await local["find_files"].ainvoke({"pattern": "**/*.py"})
    with pytest.raises(ValueError, match="/repo/src' with the glob '\\*.py'"):
        await local["find_files"].ainvoke({"pattern": "src/**/*.py"})
    with pytest.raises(ValueError, match="names folders"):
        await local["search_files"].ainvoke({"query": "Save", "glob": "ignored/*.py"})
    for pattern in ("packages/*/tests/*.py", "**/tests/*.py", "tests/*.py", "/abs/src/**/*.py"):
        with pytest.raises(ValueError, match="check the matches"):
            await local["find_files"].ainvoke({"pattern": pattern})


@pytest.mark.sandbox
async def test_search_reaches_hidden_configuration_but_not_git_or_env_files(tmp_path):
    folder = tmp_path / "repo"
    (folder / ".github" / "workflows").mkdir(parents=True)
    (folder / ".github" / "workflows" / "ci.yml").write_text("run: python -m pytest -q\n")
    (folder / ".git").mkdir()
    (folder / ".git" / "config").write_text("pytest in git\n")
    (folder / ".env").write_text("PYTEST_TOKEN=synthetic\n")
    (folder / ".env.local").write_text("PYTEST_TOKEN=synthetic\n")
    (folder / ".Env.production").write_text("PYTEST_TOKEN=synthetic\n")
    (folder / ".gitignore").write_text("build/\n")
    (folder / "build").mkdir()
    (folder / "build" / "out.txt").write_text("pytest output\n")
    (folder / "app.py").write_text("def main():\n    run_tests()\n    return 0\n")
    local = tools(folder, tmp_path / "scratch")
    assert ".github/workflows/ci.yml" in await local["find_files"].ainvoke({"pattern": "*.yml"})
    found = await local["search_files"].ainvoke({"query": "PYTEST", "case_insensitive": True})
    assert "ci.yml:1:run: python -m pytest -q" in found
    assert "/.git/" not in found and "/.env" not in found.casefold() and "out.txt" not in found
    assert "No matches" in await local["search_files"].ainvoke({"query": "PYTEST", "glob": "*.yml"})
    around = await local["search_files"].ainvoke({"query": "run_tests", "context_lines": 1})
    assert (
        "app.py-1-def main():" in around
        and "app.py:2:    run_tests()" in around
        and "app.py-3-    return 0" in around
    )
    # Named as the starting point, inside one, or reached through a link, they are still left out.
    (folder / ".envdir" / "nested").mkdir(parents=True)
    (folder / ".envdir" / "nested" / "keys.txt").write_text("PYTEST_KEY=synthetic\n")
    (folder / "settings.txt").symlink_to(folder / ".env")
    (folder / "nested").symlink_to(folder / ".envdir" / "nested")
    # In any letter case too: on macOS `.GIT/config` and `.ENV` open the lowercase entries.
    for root in (
        ".env",
        ".env.local",
        ".git",
        ".git/config",
        "settings.txt",
        ".envdir/nested",
        "nested",
        ".GIT/config",
        ".ENV",
    ):
        with pytest.raises(ValueError, match="is not searched"):
            await local["search_files"].ainvoke({"query": "PYTEST", "path": root, "case_insensitive": True})
    with pytest.raises(ValueError, match="is not searched"):
        await local["find_files"].ainvoke({"path": ".git"})


@pytest.mark.sandbox
async def test_workdir_is_relative_scoped_and_recorded_per_command(tmp_path):
    (tmp_path / "subfolder").mkdir()
    command = tools(tmp_path, tmp_path / "scratch")["run_command"]
    result = await command.ainvoke(
        {
            "type": "tool_call",
            "id": "cwd",
            "name": "run_command",
            "args": {"command": "pwd", "workdir": "subfolder"},
        }
    )
    assert result.artifact["coding"]["cwd"] == str(tmp_path / "subfolder")
    assert str(tmp_path / "subfolder") in result.content
    result = await command.ainvoke({"command": "pwd"})
    assert str(tmp_path) in result and "subfolder" not in result


@pytest.mark.sandbox
async def test_instructions_load_root_to_folder_with_explicit_truncation_and_environment(stack, tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("ROOT: check tests.\n")
    child = tmp_path / "web"
    child.mkdir()
    (child / "AGENTS.md").write_text("WEB: use shared components.\n" + "x" * 33000)
    agent = stack.agent
    stack.repository.set_setting(COMMAND_ACCESS_SETTING, {agent["id"]: "network"})
    stack.repository.set_setting(GRANTS_SETTING, [{"path": str(tmp_path), "writable": False}])
    context = ToolContext(
        agent["id"],
        stack.repository,
        stack.files,
        stack.secrets,
        working_directory=str(child),
        repository_instructions=True,
        coding_workspace=stack.services.coding_workspace,
    )

    async def told(context):
        update = await EnvironmentMiddleware(context).abefore_model(
            {"messages": [HumanMessage(content="Start")]}, None
        )
        return update["messages"][0].content

    text = await told(context)
    assert text.index("ROOT:") < text.index("WEB:")
    assert "Instructions truncated" in text and "offset=31981" in text
    assert "shell /bin/sh" in text and "Network is enabled." in text
    text = await told(replace(context, repository_instructions=False))
    assert "ROOT:" not in text and "WEB:" not in text


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS sandbox boundary")
async def test_search_keeps_out_of_private_folders_and_bundled_rg_runs(tmp_path):
    from wavefunction.config import get_settings

    folder = tmp_path / "allowed"
    folder.mkdir()
    denied = get_settings().data_dir / "synthetic-private-search"
    denied.mkdir(exist_ok=True)
    (folder / "page.py").write_text("Save allowed\n")
    (denied / "secret.py").write_text("Save secret\n")
    (folder / "link.py").symlink_to(denied / "secret.py")
    grants = [Grant(folder)]
    local = tools(folder, tmp_path / "scratch", grants)
    assert "Save allowed" in await local["search_files"].ainvoke({"query": "Save"})
    assert "secret" not in await local["search_files"].ainvoke({"query": "Save"})
    assert "ripgrep 14.1.0" in await local["run_command"].ainvoke({"command": "rg --version"})
    for name, args in [
        ("find_files", {"path": str(denied)}),
        ("search_files", {"query": "Save", "path": str(folder / "link.py")}),
    ]:
        with pytest.raises(PermissionError, match="Permission denied"):
            await local[name].ainvoke(args)
    result = await local["run_command"].ainvoke({"command": "cat secret.py", "workdir": str(denied)})
    assert "Save secret" not in result and "[exit code 0]" not in result
