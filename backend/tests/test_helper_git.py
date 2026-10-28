"""Linked helpers can commit while keeping checkout permissions conversation-owned, and a checkout
stopped during its setup still expires."""

import asyncio
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support import create, git, repository

from wavefunction import coding_cleanup
from wavefunction.coding_workspace import ThreadInput
from wavefunction.toolkit import TOOL_REGISTRY, ToolContext


@pytest.fixture
async def helper(coding, tmp_path):
    client, ws, services = coding
    source = repository(tmp_path)
    parent = await create(client, folder=str(source))
    grants = [{"path": str(grant.path), "writable": False} for grant in services.grants()]
    child = ws.create_thread(
        ThreadInput(folder=str(source)), parent_thread_id=parent["id"], parent_run_id="synthetic-parent"
    )
    hook = source / ".git/hooks/post-checkout"
    hook.write_text(
        '#!/bin/sh\nprintf "%s %s %s\\n" "$1" "$2" "$3" >> "$(git rev-parse --absolute-git-dir)/checkout-args"\n'
    )
    hook.chmod(0o755)
    try:
        await ws.checkpoints.branch(parent["id"], child["id"], live=True)
        child = ws.thread(child["id"])
        admin = Path(git(child["folder"], "rev-parse", "--absolute-git-dir"))
        head = git(source, "rev-parse", "HEAD")
        assert (admin / "checkout-args").read_text() == f"{'0' * len(head)} {head} 1\n"
        hook.unlink()
        grants.append({"path": child["folder"], "writable": True})
        context = ToolContext(
            agent_id=ws.agent_id,
            repository=services.repository,
            files=services.files,
            secrets=services.secrets,
            desktop_copy=child["id"],
            working_directory=child["folder"],
            conversation_id=child["conversation_id"],
            folder_grants=grants,
            coding_workspace=ws,
        )
        yield source, child, context
    finally:
        hook.unlink(missing_ok=True)


@pytest.mark.sandbox
@pytest.mark.destructive  # Deletes a branch on its synthetic remote.
async def test_captured_helper_can_commit_with_confined_hooks_and_honors_revocation(coding, helper, tmp_path):
    _, ws, services = coding
    source, child, captured = helper
    root, sibling = Path(child["folder"]), tmp_path / "sibling"
    remote = source / ".git/upstream.git"
    git(source, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Upstream history")
    git(source, "clone", "--bare", str(source), str(remote))
    git(source, "remote", "add", "origin", remote.as_uri())
    git(remote, "branch", "obsolete")
    git(source, "fetch", "--depth=1", "origin")
    git(source, "pack-refs", "--all", "--prune")
    assert "refs/remotes/origin/obsolete" in (source / ".git/packed-refs").read_text()
    assert not (source / ".git/refs/remotes/origin/obsolete").exists()
    assert git(source, "rev-list", "--count", "origin/main") == "1"
    git(remote, "branch", "-D", "obsolete")
    git(source, "worktree", "add", "-b", "sibling", str(sibling))
    admin = Path(git(root, "rev-parse", "--absolute-git-dir"))
    sibling_admin = Path(git(sibling, "rev-parse", "--absolute-git-dir"))
    protected = [
        source / "a.txt",
        source / "other.txt",
        source / ".git/index",
        source / ".git/HEAD",
        sibling / "a.txt",
        sibling_admin / "index",
        sibling_admin / "HEAD",
    ]
    before = {path: path.read_bytes() for path in protected}
    parent_head = git(source, "rev-parse", "HEAD")
    hook, marker = source / ".git/hooks/pre-commit", admin / "hook-confined"
    upload = source / ".git/hooks/audit-upload-pack"
    fetch_marker = admin / "fetch-confined"
    grants = services.repository.get_setting("folder_grants")
    try:
        # Hooks execute with the same checkout boundary as the helper's command.
        hook.write_text(
            "#!/bin/sh\n"
            + "".join(
                f"if printf corrupted > {shlex.quote(str(path))}; then exit 42; fi\n" for path in protected
            )
            + f"printf checked > {shlex.quote(str(marker))}\n"
        )
        hook.chmod(0o755)
        command = TOOL_REGISTRY["run_command"].build(captured)
        for text in (
            "printf 'Helper edit\\n' > a.txt",
            "git add -- a.txt",
            "git -c commit.gpgsign=false commit -m 'Helper change'",
        ):
            result = await command.ainvoke({"command": text})
            assert "[exit code 0]" in result, result
        assert marker.read_text() == "checked"
        assert git(root, "show", "HEAD:a.txt") == "Helper edit"
        assert git(root, "diff", "--cached", "--name-only") == ""
        fetched = [
            await command.ainvoke({"command": text})
            for text in ("git fetch --prune origin", "git fetch --deepen=1 origin")
        ]
        assert all("[exit code 0]" in result for result in fetched), "\n".join(fetched)
        assert git(root, "for-each-ref", "--format=%(refname)", "refs/remotes/origin/obsolete") == ""
        assert git(root, "rev-list", "--count", "origin/main") == "2"
        # Git's own helpers, such as upload-pack, run inside the helper's captured boundary,
        # which a folder granted only after this helper was created does not widen.
        later = tmp_path / "later-grant"
        later.mkdir()
        (later / "readable.txt").write_text("Synthetic later data")
        ws.allow_folder(str(later))
        upload.write_text(
            "#!/bin/sh\n"
            + "".join(
                f"if printf corrupted > {shlex.quote(str(path))}; then exit 42; fi\n" for path in protected
            )
            + f"if cat {shlex.quote(str(later / 'readable.txt'))}; then exit 43; fi\n"
            + f'printf checked > {shlex.quote(str(fetch_marker))}\nexec git-upload-pack "$@"\n'
        )
        upload.chmod(0o755)
        git(source, "config", "remote.origin.uploadpack", str(upload))
        result = await command.ainvoke({"command": "git fetch origin"})
        assert "[exit code 0]" in result, result
        assert fetch_marker.read_text() == "checked"
        assert git(source, "rev-parse", "HEAD") == parent_head
        assert all(path.read_bytes() == value for path, value in before.items())
        services.repository.set_setting("folder_grants", [])
        with pytest.raises(PermissionError):
            await command.ainvoke({"command": "printf forbidden > revoked.txt"})
        assert not (root / "revoked.txt").exists()
    finally:
        services.repository.set_setting("folder_grants", grants)
        hook.unlink(missing_ok=True)
        upload.unlink(missing_ok=True)


@pytest.mark.sandbox
async def test_owned_worktree_gitfile_cannot_redirect_backend_or_captured_access(coding, helper, tmp_path):
    client, ws, _ = coding
    _, child, captured = helper
    root = Path(child["folder"])
    unrelated_parent = tmp_path / "unrelated"
    unrelated_parent.mkdir()
    unrelated = repository(unrelated_parent)
    protected = {
        path: path.read_bytes()
        for path in (unrelated / "a.txt", unrelated / ".git/index", unrelated / ".git/HEAD")
    }
    gitfile = root / ".git"
    original = gitfile.read_bytes()
    try:
        gitfile.write_text(f"gitdir: {unrelated / '.git'}\n")
        assert (await client.get("/api/coding/workspace")).status_code == 200
        response = await client.get(f"/api/coding/threads/{child['id']}/git/state")
        assert response.status_code == 422 and "Git registration no longer matches" in response.text
        with pytest.raises(ValueError, match="Git registration no longer matches"):
            captured.local_grants()
        assert all(path.read_bytes() == value for path, value in protected.items())
    finally:
        gitfile.write_bytes(original)
    assert captured.local_grants() and ws.root(child["id"]) == root


@pytest.mark.sandbox
async def test_helper_stopped_while_its_checkout_is_prepared_still_expires(coding, tmp_path, monkeypatch):
    client, ws, _ = coding
    source = repository(tmp_path)
    parent = await create(client, folder=str(source))
    child = ws.create_thread(
        ThreadInput(folder=str(source)), parent_thread_id=parent["id"], parent_run_id="synthetic-parent"
    )
    preparing = asyncio.Event()

    async def slow_copy(*_args, **_kwargs):
        # Copying the parent's uncommitted work into the new checkout: slow in a large repository.
        preparing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ws.recovery, "apply", slow_copy)
    setup = asyncio.create_task(ws.checkpoints.branch(parent["id"], child["id"], live=True))
    await asyncio.wait_for(preparing.wait(), 15)
    setup.cancel()  # Stop lands here: the helper never runs.
    with pytest.raises(asyncio.CancelledError):
        await setup
    with ws.db() as db:
        db.execute(
            "UPDATE coding_worktrees SET last_used_at = ? WHERE thread_id = ?",
            ((datetime.now(UTC) - timedelta(days=2)).isoformat(), child["id"]),
        )
    removals = []
    check = coding_cleanup.file_operation

    async def without_removal(runner, request, **kwargs):
        if request["action"] != "worktree_cleanup_remove":
            return await check(runner, request, **kwargs)
        removals.append(request)
        raise RuntimeError("Removal runs only in the destructive tests")

    monkeypatch.setattr(coding_cleanup, "file_operation", without_removal)
    await ws.cleanup.sweep()
    # Its checkout goes once it is still exactly what it was prepared from.
    assert [request["tree"] for request in removals] == [ws.delivery.worktree(child["id"])["base_tree"]]
