from __future__ import annotations

import asyncio
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from wavefunction.config import get_settings
from wavefunction.sandbox import Grant, Sandbox, build_profile, scratch_for

macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS only")
# These cases run real deletion commands inside the sandbox; CI runs them, never a local check.
pytestmark = pytest.mark.destructive


@pytest.fixture
def workspace(system_tmp: Path) -> tuple[Path, Path, Path]:
    # Outside the per-user temporary folder, which sandboxed commands may always write.
    root = system_tmp
    granted = root / "granted"
    granted.mkdir()
    (granted / "data.csv").write_text("a,b\n1,2\n")
    private = root / "private"
    private.mkdir()
    (private / "keys.txt").write_text("TOPSECRET")
    return root, granted, private


def test_commands_that_cannot_be_undone_are_caught_and_routine_ones_are_not(tmp_path, monkeypatch) -> None:
    """Classify command text only: no example command is ever executed. Exact captured files
    and deliberate generated-folder cleanup are safe; unknown deletion targets need approval."""
    from wavefunction.irreversible import command_danger, irreversible

    # Real directory metadata, but command text only. Keep this fixture outside the
    # classifier's temporary-root exemption so missing recovery bytes are exercised.
    project, scratch, temporary = (tmp_path / name for name in ("repo", "scratch", "temporary"))
    for directory in ("node_modules", "dist", ".next", "build", "frontend/dist"):
        (project / directory).mkdir(parents=True)
    monkeypatch.setattr("wavefunction.irreversible.TEMPORARY", (temporary,))
    captured = {project / path for path in ("src/old.ts", "data/saved.txt", ".gitignore")}
    routine = [
        "rm -rf node_modules dist .next",
        "gh search issues delete",
        "gh pr list --search delete",
        "gh repo view delete",
        "rm src/old.ts",
        "rm data/saved.txt",
        "rm .gitignore",
        f"rm -rf {project}/build",
        f"rm -rf {scratch}/x",
        f"rm -rf {temporary}/wf-x",
        "cd frontend && rm -rf dist",
        "find build -name '*.pyc' -delete",
        "cd frontend && rm -rf dist >/dev/null",
        "cd frontend >/dev/null && rm -rf dist >>/dev/null",
        "cd frontend 2>/dev/null && rm -rf dist >>/dev/null",
        "rm src/old.ts 2>/dev/null",
        "rm src/old.ts 2>>/dev/null",
        "rm src/old.ts 2>&1",
        "2>/dev/null rm src/old.ts",
        "cd frontend && rm -rf dist; echo done",
        "echo ready; cd frontend && rm -rf dist",
        "cd frontend && rm -rf dist\necho done",
        "echo ready\ncd frontend && rm -rf dist",
        "git checkout feature",
        "git restore --staged a.ts",
        "git clean -n",
        "git push -u origin feat",
        "docker compose down",
        "docker system prune -af",
        "supabase stop",
        "npx prisma migrate dev",
        'git commit -m "git reset --hard and drop table notes"',
        'echo "rm -rf ~"',
        "chmod -R 755 scripts",
        "dd if=/dev/zero of=disk.img bs=1m count=1",
        "kubectl delete -f x.yaml --dry-run=client",
        "terraform plan -destroy",
        "kubectl delete ns sample --dry-run none",
        "kubectl delete ns sample --dry-run=none --dry-run=server",
        "kubectl delete ns sample --dry-run=true",
        "kubectl delete ns sample --dry-run=1",
        "git --git-dir ../other/.git --work-tree ../other status",
        "pnpm --dir /tmp exec git status",
        "uv run --project /tmp python --version",
        "pnpm --loglevel warn exec git status",
        "pnpm exec --loglevel warn git status",
        "pnpm --color never exec git status",
        "pnpm exec --color git status",
        "uv run --color never python --version",
        "uvx --color never python --version",
        "gh release list",
        "gh issue view 3",
        "gh api repos/example/repo",
        "gh api -X GET repos/example/repo",
        "gh api repos/example/repo -q DELETE",
        "gh api -X DELETE -X GET repos/example/repo",
        "gh api repos/example/repo -H 'X-Method: DELETE'",
        "gh api -X DELETE -iX GET repos/example/repo",
        "gh api -iH 'X-Method: DELETE' repos/example/repo",
        "find build -name '*.tmp' -exec rm -f {} +",
        "find build -type f -execdir rm -f {} \\;",
        "find build -name '*.tmp' -exec env LC_ALL=C rm -f {} +",
        "find build \\( -name '*.tmp' -o -name '*.log' \\) -delete",
        'echo ";"',
        "npm rm lodash",
        "pnpm rm lodash",
        "bun rm lodash",
        # A here-document is input to the command that reads it, not commands of its own.
        "cat > migrations/002.sql <<'EOF'\nDROP TABLE legacy;\nEOF",
        "cat > scripts/clean.sh <<'EOF'\nrm -rf out\nEOF",
        "git commit -F - <<'EOF'\nDrop table legacy\nEOF",
    ]
    worst = [
        "rm -rf ~",
        "rm -rf /",
        "rm -rf .",
        "rm -rf *",
        "rm -rf ../other",
        "sudo rm -rf /usr/local/lib",
        "rm .env",
        "find ~ -name x -delete",
        "ls | xargs rm",
        "cd / && rm -rf usr",
        'bash -lc "rm -rf ~/old"',
        "echo $(rm -rf ~)",
        "git reset --hard",
        "git -C ../x checkout .",
        "git restore .",
        "git clean -fdx",
        "git stash clear",
        "git branch -D old",
        "git push -f origin main",
        "git push origin +main",
        "git push origin --delete old",
        'psql -c "DROP TABLE users"',
        'echo "TRUNCATE users;" | psql',
        "psql <<EOF\nDROP DATABASE app;\nEOF",
        "cat <<EOF | psql\nDROP DATABASE app;\nEOF",
        "docker exec -i db psql <<EOF\nDROP TABLE users;\nEOF",
        "bash <<'EOF'\nrm -rf ~/old\nEOF",
        "cat <<'EOF' | sh\nrm -rf ~/old\nEOF",
        "npm exec rm -rf ~/old",
        "npx supabase db reset",
        "pnpm prisma migrate reset --force",
        "redis-cli FLUSHALL",
        "docker volume prune -f",
        "docker compose down -v",
        "supabase stop --no-backup",
        "terraform destroy",
        "kubectl delete ns prod",
        "aws s3 rm s3://b --recursive",
        "gh repo delete me/x --yes",
        "gh release delete v1 --yes",
        "gh secret delete TOKEN",
        "gh -R me/x secret delete TOKEN",
        "gh --repo me/x release delete v1 --yes",
        "gh repo deploy-key delete 1234 --yes",
        "gh repo autolink --repo=me/x delete 1 --yes",
        "gh project item-delete 1 --id PVTI_x",
        "dd if=x.img of=/dev/disk4",
        "cat x.img > /dev/rdisk4",
        "diskutil eraseDisk APFS X disk4",
        "sudo chown -R me ~",
        "rm -rf .git",
        "find . -delete",
        "git checkout -f main",
        "git switch --discard-changes main",
        "docker volume remove data",
        "docker rm -v db",
        "psql -c 'DELETE FROM users RETURNING id'",
        "sudo --user root rm -rf /usr/local/data",
        "timeout --signal KILL 5 rm -rf /usr/local/data",
        "xargs --max-args 1 rm",
        "find -L /usr/local/data -delete",
        "rm -rf .git*",
        "rm -rf .gi?",
        "rm -rf .[g]it",
        "rm -rf .GIT*",
        "rm -rf node_modules/.git*",
        "rm data/local.sqlite",
        "rm -rf data",
        "rm -rf src/*",
        "rm -f *.log",
        "find . -name '*.pyc' -delete",
        "pnpm --dir /tmp exec supabase db reset",
        "pnpm -C /tmp exec supabase db reset",
        "pnpm --loglevel warn exec dropdb production",
        "pnpm exec --loglevel warn dropdb production",
        "pnpm --filter-prod app --changed-files-ignore-pattern '*.md' --test-pattern '*.test.ts' exec dropdb production",
        "pnpm --reporter append-only --resume-from app exec dropdb production",
        "pnpm --prefix /tmp --pm-on-fail warn exec dropdb production",
        "pnpm --color never exec dropdb production",
        "pnpm exec --color dropdb production",
        "npx --package supabase supabase db reset",
        "npm exec --package supabase -- supabase db reset",
        "uv run --project /tmp --with tool dropdb example",
        "uvx --from tool dropdb example",
        "uv run --group db pgcli -c 'DROP TABLE users'",
        "uv run --extra postgres pgcli -c 'TRUNCATE users'",
        "uv run --color never dropdb production",
        "uv --color never run dropdb production",
        "uvx --color never dropdb production",
        "uv run --only-group db --resolution highest dropdb production",
        "uvx --constraints deps.txt --prerelease allow dropdb production",
        "uv run -C key=value -w helper dropdb production",
        "kubectl delete ns sample --dry-run=client --dry-run=none",
        "kubectl delete ns sample --dry-run=false",
        "kubectl delete ns sample --dry-run=0",
        "kubectl delete ns sample -- --dry-run=client",
        "git worktree remove -f ../other",
        "git worktree remove --force ../other",
        "git worktree remove ../clean",
        "git --git-dir ../other/.git --work-tree ../other reset --hard",
        "git --namespace sample reset --hard",
        "gh api -X DELETE repos/example/repo/git/refs/heads/feature",
        "gh api --method=DELETE repos/example/repo",
        "gh api -XDELETE repos/example/repo",
        "gh --repo example/repo api repos/example/repo -X GET -X DELETE",
        "gh api -iX DELETE repos/example/repo",
        "gh api -iXDELETE repos/example/repo",
        "find build -name '*.tmp' -exec rm -f data/local.sqlite {} +",
        "find build -type f -exec rm -rf /Users/synthetic/important {} \\;",
        "find build -type f -execdir rm -f data/saved.txt {} \\;",
        "find . -exec env LC_ALL=C rm -rf /Users/synthetic/important {} \\;",
        "find build -exec env LC_ALL=C rm -f /Users/synthetic/important {} +",
        "find build -execdir env LC_ALL=C rm -f data/saved.txt {} \\;",
        "(cd /tmp); rm data/local.sqlite",
        "echo $(cd /tmp); rm data/local.sqlite",
        "find -L build -name '*.sqlite' -delete",
        "find -H build -delete",
        "find build -follow -delete",
        "find . \\( -name '*.sqlite' -o -name '*.db' \\) -delete",
        "find . '(' -name '*.sqlite' -o -name '*.db' ')' -delete",
        "find build -exec rm -f {} \\; -exec rm -rf /Users/synthetic/important \\;",
        "if test -f data/local.sqlite; then rm data/local.sqlite; fi",
        'for database in data/*.sqlite; do rm "$database"; done',
        "cd /tmp | cat; rm data/local.sqlite",
        "cd /tmp & wait; rm data/local.sqlite",
        "cd /tmp/does-not-exist; rm data/local.sqlite",
        "cd /tmp && false; rm data/local.sqlite",
        "cd build && cd 2>/dev/null /Users/synthetic/outside && rm local.sqlite",
        f"echo ready > cd {scratch} && rm local.sqlite",
        "rm src/old.ts 2 >/dev/null",
        "rm src/old.ts '2'>/dev/null",
        'rm src/old.ts "2">/dev/null',
        r"rm src/old.ts \2>/dev/null",
        r"rm src/old.ts unknown\ 2>/dev/null",
    ]

    def check(command):
        return irreversible(command, cwd=project, undoable={project: captured, scratch: None})

    assert [command for command in routine if check(command)] == []
    assert [command for command in worst if not check(command)] == []
    assert check("rm -rf ~/old") == "Deletes files that undo can't bring back."
    assert check("git push --force") == "Overwrites branches on the remote."
    assert (
        irreversible(
            "git push origin --delete old", cwd=project, undoable={}, proxy_protects_remote_delete=True
        )
        is None
    )
    # A mirror push also force-updates every branch, which the proxy does not stop.
    assert (
        irreversible("git push --mirror origin", cwd=project, undoable={}, proxy_protects_remote_delete=True)
        == "Overwrites branches on the remote."
    )
    for command in (
        "git push origin --delete old; git reset --hard",
        'sh -c "git push origin --delete old; git reset --hard"',
        "git push origin --delete old; gh api -X DELETE repos/example/repo",
    ):
        assert irreversible(command, cwd=project, undoable={}, proxy_protects_remote_delete=True), command
    # A Git repository alone does not mean ignored/large/omitted data can be restored.
    (tmp_path / ".git").mkdir()
    (tmp_path / "build").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "uploads").mkdir()
    (tmp_path / "dist").write_text("uncaptured ignored file")
    (tmp_path / "target").write_bytes(b"x" * 600_000)
    (tmp_path / "venv").symlink_to(tmp_path / "uploads", target_is_directory=True)
    (tmp_path / "build/link").symlink_to(tmp_path / "uploads", target_is_directory=True)
    monkeypatch.setattr("wavefunction.irreversible.TEMPORARY", ())
    capture = {"saved.txt": {"data": "c2F2ZWQ="}, "large.bin": {"kind": "large"}, "target": {"kind": "large"}}

    def classify(command):
        return command_danger(
            command,
            cwd=tmp_path,
            project=tmp_path,
            scratch=tmp_path / "scratch",
            outside=False,
            captured=capture,
        )

    assert classify("rm saved.txt") is None
    assert classify("rm -rf build node_modules") is None
    for command in (
        "rm large.bin",
        "rm local.sqlite",
        "rm dist",
        "rm target",
        "unlink venv",
        "rm -rf venv",
        "rm -rf uploads",
        "rm -rf .git*",
        "find build/link/ -delete",
        "find build/link/. -delete",
        "rm -rf build/link/../local.sqlite",
        "rm -rf build/link/*",
        "rm -rf build/link/*.sqlite",
        "rm -rf build/*/*.sqlite",
        "rm -rf build/{link/*,cache}",
        "rm -rf build/{link,cache}/*",
        "rm -rf build/{.git,cache}",
        "rm -rf build/$folder/*",
        "rm -rf build/${folder}/*",
    ):
        assert classify(command), command


def test_a_folder_name_cannot_inject_policy(tmp_path: Path) -> None:
    """Paths are user data spliced into a policy. A quote must not end the string early and let
    the rest of the folder name be read as rules."""
    hostile = Path('/tmp/x")(allow file-read* (subpath "/')
    benign = Path("/tmp/x")

    def rules(path: Path) -> int:
        return sum(
            line.startswith("(allow") or line.startswith("(deny")
            for line in build_profile([Grant(path=path, writable=True)], scratch=tmp_path).splitlines()
        )

    # A hostile name buys no extra rules, because its quotes never close the string.
    assert rules(hostile) == rules(benign)
    assert '\\"' in build_profile([Grant(path=hostile, writable=True)], scratch=tmp_path)


@macos_only
@pytest.mark.asyncio
async def test_reads_reach_the_disk_except_private_folders(workspace, monkeypatch) -> None:
    """Commands read what they need to run — any folder — but never keys or the app's data,
    not even through a writable grant that contains them."""
    root, _, private = workspace
    home = root / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "id_synthetic").write_text("SSH-SECRET")
    monkeypatch.setenv("HOME", str(home))
    data = get_settings().data_dir / "Agent"
    data.mkdir(parents=True, exist_ok=True)
    (data / "synthetic.env").write_text("DATA-SECRET")
    box = Sandbox([Grant(path=home, writable=True)], scratch=scratch_for(root))

    assert "TOPSECRET" in (await box.run(f"cat {private}/keys.txt", timeout=30)).stdout
    for secret, word in (
        (home / ".ssh" / "id_synthetic", "SSH-SECRET"),
        (data / "synthetic.env", "DATA-SECRET"),
    ):
        denied = await box.run(f"cat '{secret}'; echo more >> '{secret}'", timeout=30)
        assert word not in denied.stdout
        assert secret.read_text() == word
    assert (await box.run(f"touch {home}/allowed.txt", timeout=30)).exit_code == 0


# Plain sockets on the loopback addresses, IPv4 and IPv6: http.server looks up the host's
# name, which on some machines waits on DNS far longer than the test does.
LOOPBACK = (
    "import socket, threading\n"
    "for family, host in ((socket.AF_INET, '127.0.0.1'), (socket.AF_INET6, '::1')):\n"
    "    server = socket.socket(family)\n"
    "    server.bind((host, 0))\n"
    "    server.listen()\n"
    "    threading.Thread(target=lambda s=server: s.accept()[0].sendall(b'200'), daemon=True).start()\n"
    "    client = socket.create_connection((host, server.getsockname()[1]), timeout=5)\n"
    "    print(host, client.recv(3).decode(), flush=True)\n"
)


@macos_only
@pytest.mark.asyncio
async def test_network_access_reaches_loopback_but_not_other_local_sockets(workspace) -> None:
    """With network access a command can serve and fetch on loopback. The only local socket it
    reaches is the DNS resolver's: others, like Docker's, lead outside the sandbox."""
    root, granted, _ = workspace
    (granted / "serve.py").write_text(LOOPBACK)
    listener = socket.socket(socket.AF_UNIX)
    path = root / "service.sock"
    listener.bind(str(path))
    listener.listen()
    connect = (
        f"python3 -c \"import socket; socket.socket(socket.AF_UNIX).connect('{path}'); print('CONNECTED')\""
    )
    try:
        for network in (False, True):
            box = Sandbox([], scratch=scratch_for(root), network=network)
            served = await box.run(f"python3 {granted / 'serve.py'}", timeout=30)
            reached = "127.0.0.1 200" in served.stdout and "::1 200" in served.stdout
            assert reached is network, served.stdout + served.stderr
            assert "CONNECTED" not in (await box.run(connect, timeout=30)).stdout
    finally:
        listener.close()


@macos_only
@pytest.mark.asyncio
async def test_commands_cannot_reach_apps_the_pasteboard_or_launchd(workspace) -> None:
    """Blocked at the services, not by binary name: LaunchServices (which `open` and every app
    launch go through), the pasteboard and job submission. A re-signed copy of a LaunchServices
    client is refused the same way. Only queries without side effects are made."""
    root, _, _ = workspace
    box = Sandbox([], scratch=scratch_for(root))
    query = "info -only bundleid Finder"
    direct = subprocess.run(f"/usr/bin/lsappinfo {query}", shell=True, capture_output=True, text=True)
    copy = scratch_for(root) / "renamed-client"
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy("/usr/bin/lsappinfo", copy)
    subprocess.run(["/usr/bin/codesign", "-f", "-s", "-", str(copy)], capture_output=True, check=True)
    for client in ("/usr/bin/lsappinfo", str(copy)):
        answered = await box.run(f"{client} {query}", timeout=30)
        # Unsandboxed, the same query names the running Finder.
        assert "com.apple.finder" not in answered.stdout, (client, direct.stdout)
    assert (await box.run("pbpaste >/dev/null", timeout=30)).exit_code != 0
    label = f"com.wavefunction.sbtest.{uuid.uuid4().hex[:8]}"
    try:
        assert (await box.run(f"launchctl submit -l {label} -- /usr/bin/true", timeout=30)).exit_code != 0
    finally:
        subprocess.run(["/bin/launchctl", "remove", label], capture_output=True)


@macos_only
@pytest.mark.asyncio
async def test_git_and_the_shell_s_own_redirects_work(workspace, tmp_path) -> None:
    """`/usr/bin/git` is an xcrun shim that caches in the per-user temporary folder, and the
    shell points `cmd &` at /dev/null: both failed in the sandbox."""
    root, granted, _ = workspace
    box = Sandbox([Grant(path=granted, writable=True)], scratch=scratch_for(root), restrict_reads=True)
    (granted / "hello.c").write_text("int main(void) { return 0; }\n")
    cold = await box.run("xcrun_nocache=1 /usr/bin/git --version", cwd=granted, timeout=60)
    assert cold.exit_code == 0, cold.stderr
    script = (
        f"export GIT_CONFIG_GLOBAL=/dev/null XDG_CONFIG_HOME={shlex.quote(str(box.scratch / 'config'))}; "
        'git() { /usr/bin/git "$@"; }; '
        "git init -q && git config user.email synthetic@example.com && git config user.name Synthetic"
        " && git add data.csv && git -c commit.gpgsign=false commit -qm first && echo 3,4 >> data.csv"
        " && git status --short && git diff --stat && git -c commit.gpgsign=false commit -qam second"
        " && git log --oneline"
        " && /usr/bin/xcrun --find clang && /usr/bin/clang hello.c -o hello && ./hello"
        " && cat /dev/null && (sleep 0.1 &) && echo done > /dev/null && echo ALL-OK"
    )
    result = await box.run(script, cwd=granted, timeout=60)
    assert result.exit_code == 0, result.stderr
    assert "Operation not permitted" not in result.stderr
    assert "M data.csv" in result.stdout and "second" in result.stdout and "ALL-OK" in result.stdout
    private_temp = tmp_path / "unrelated-temp.txt"
    private_temp.write_text("PRIVATE_TEMP_DATA")
    denied = await box.run(f"cat {shlex.quote(str(private_temp))}", cwd=granted)
    assert denied.exit_code != 0 and "PRIVATE_TEMP_DATA" not in denied.stdout


@macos_only
@pytest.mark.asyncio
async def test_background_processes_end_when_the_command_returns(tmp_path: Path) -> None:
    """A child left running by `&` is stopped with its command, and holding the output pipe does
    not keep the command waiting until its timeout."""
    marker = tmp_path / "late"
    started = time.monotonic()
    result = await Sandbox([], scratch=tmp_path / "scratch").run(
        f"(sleep 1; touch '{marker}') & sleep 30 & echo started", timeout=20
    )
    assert not result.timed_out and "started" in result.stdout
    assert time.monotonic() - started < 10
    await asyncio.sleep(1.5)
    assert not marker.exists()


@macos_only
@pytest.mark.asyncio
async def test_detached_processes_end_when_the_command_returns(tmp_path: Path) -> None:
    """A child in a session of its own escapes the process-group kill; it is found by the tag
    in its environment and stopped before it can write."""
    marker = tmp_path / "late"
    child = f"import time; time.sleep(1); open({str(marker)!r}, 'w').close()"
    parent = (
        f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True)"
    )
    result = await Sandbox([], scratch=tmp_path / "scratch").run(
        f"python3 -c {shlex.quote(parent)}; echo started", timeout=20
    )
    assert not result.timed_out and "started" in result.stdout, result.stderr
    await asyncio.sleep(2)
    assert not marker.exists()


@macos_only
@pytest.mark.asyncio
async def test_the_app_token_and_provider_keys_do_not_leak(workspace, monkeypatch) -> None:
    """The sidecar's environment unlocks the local API and holds provider keys. It is built for
    the child; frozen runtime reuse must not add credentials or arbitrary folder access."""
    monkeypatch.setenv("WAVEFUNCTION_DESKTOP_TOKEN", "tok-should-not-appear")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-appear")
    monkeypatch.setenv("_PYI_UNRELATED", "also-should-not-appear")
    root, _, private = workspace
    runtime = root / "bundled-runtime"
    runtime.mkdir()
    library = runtime / "runtime.txt"
    library.write_text("BUNDLED-RUNTIME")
    outside = root / "outside.txt"
    outside.write_text("OUTSIDE-SECRET")
    bookkeeping = {
        "_PYI_ARCHIVE_FILE": sys.executable,
        "_PYI_APPLICATION_HOME_DIR": str(runtime),
        "_PYI_PARENT_PROCESS_LEVEL": "1",
    }
    for key, value in bookkeeping.items():
        monkeypatch.setenv(key, value)
    box = Sandbox([], scratch=scratch_for(root), restrict_reads=True, private_roots=[private])
    result = await box.run("env", timeout=30)
    assert "should-not-appear" not in result.stdout
    assert "_PYI_" not in result.stdout
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(runtime), raising=False)
    result = await box.run("env", timeout=30)
    assert "should-not-appear" not in result.stdout
    assert all(f"{key}={value}" in result.stdout for key, value in bookkeeping.items())
    assert (await box.run("cat " + shlex.quote(str(library)))).stdout.strip() == "BUNDLED-RUNTIME"
    denied = await box.run("echo changed >> " + shlex.quote(str(library)))
    assert denied.exit_code != 0 and library.read_text() == "BUNDLED-RUNTIME"
    # Even a spoofed bootloader environment path does not become a filesystem grant.
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", str(root))
    for path in [outside, private / "keys.txt"]:
        denied = await box.run("cat " + shlex.quote(str(path)))
        assert denied.exit_code != 0 and "SECRET" not in denied.stdout


@macos_only
@pytest.mark.asyncio
async def test_python3_is_the_project_s_environment_or_imports_like_python(workspace) -> None:
    """`python3 -m unittest` and a script importing its neighbour work with the app's
    interpreter; a project's own `.venv` is used instead when the command runs beside it."""
    root, granted, _ = workspace
    (granted / "helper.py").write_text("ANSWER = 42\n")
    (granted / "main.py").write_text("from helper import ANSWER\nprint('script', ANSWER)\n")
    (granted / "test_helper.py").write_text(
        "import unittest\nfrom helper import ANSWER\n"
        "class T(unittest.TestCase):\n    def test_answer(self):\n        self.assertEqual(ANSWER, 42)\n"
    )
    box = Sandbox([], scratch=scratch_for(root))
    ran = await box.run(
        "python3 main.py && python3 -m unittest -q 2>&1 && python3 -c 'import helper'",
        cwd=granted,
        timeout=60,
    )
    assert ran.exit_code == 0, ran.stdout + ran.stderr
    assert "script 42" in ran.stdout and "OK" in ran.stdout
    # The packaged app's shim runs `wavefunction-backend --python "$@"`: it takes the usual
    # interpreter options and a program on standard input, as python3 does.
    import subprocess

    def interpreter(*args, stdin=None):
        return subprocess.run(
            [sys.executable, "-m", "wavefunction", "--python", *args],
            cwd=granted,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert interpreter("--version").stdout.startswith("Python 3")
    assert interpreter("-u", "main.py").stdout == "script 42\n"
    assert interpreter("-", stdin="print(6 * 7)\n").stdout == "42\n"
    # Grouped flags end at the program, and the program is `__main__`: its classes pickle.
    assert interpreter("-uc", "print(5)").stdout == "5\n"
    assert (
        interpreter("-", stdin="import pickle\nclass P: pass\nprint(bool(pickle.dumps(P())))\n").stdout
        == "True\n"
    )
    # Started as its own `sys.executable` (no flag), it is the interpreter again, not the server;
    # and a script's `__file__` is absolute, as in Python 3.9 and later.
    again = subprocess.run(
        [sys.executable, "-m", "wavefunction", "-c", "print(7)"],
        cwd=granted,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert again.stdout == "7\n", again.stderr
    (granted / "where.py").write_text("print(__file__)\n")
    assert interpreter("where.py").stdout == f"{(granted / 'where.py').resolve()}\n"

    venv = granted / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text('#!/bin/sh\necho "project venv $*"\n')
    (venv / "python").chmod(0o755)
    assert "project venv -m pytest" in (await box.run("python3 -m pytest", cwd=granted, timeout=30)).stdout


@macos_only
async def test_private_python_environment_runs_document_libraries(tmp_path):
    box = Sandbox([], scratch=tmp_path / "scratch")
    result = await box.run("python3 -c 'import docx, openpyxl, pptx; print(\"PRIVATE_PYTHON_OK\")'")
    assert result.exit_code == 0, result.stderr
    assert "PRIVATE_PYTHON_OK" in result.stdout


@macos_only
async def test_user_toolchains_are_readable_but_unrelated_home_files_remain_private(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    bins = [home / ".nvm/versions/node/v22/bin", home / ".cargo/bin", home / ".local/bin"]
    node_lib = home / ".nvm/versions/node/v22/lib"
    rust_lib = home / ".rustup/toolchains/stable/lib"
    for path in [*bins, node_lib, rust_lib]:
        path.mkdir(parents=True, exist_ok=True)
    (node_lib / "npm.txt").write_text("NPM_RUNTIME_OK\n")
    (rust_lib / "rust.txt").write_text("RUST_RUNTIME_OK\n")
    (home / ".rustup/settings.toml").write_text("RUST_SETTINGS_OK\n")
    scripts = {
        bins[0] / "npm": f"cat {shlex.quote(str(node_lib / 'npm.txt'))}",
        bins[1]
        / "cargo": f"cat {shlex.quote(str(home / '.rustup/settings.toml'))} {shlex.quote(str(rust_lib / 'rust.txt'))}",
        bins[2] / "uv": "echo UV_RUNTIME_OK",
    }
    for path, body in scripts.items():
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    package = home / "custom-node/lib/node_modules/npm"
    (package / "bin").mkdir(parents=True)
    (package / "package.json").write_text("NODE_PACKAGE_OK\n")
    # npm can be a PATH symlink into a package outside known manager locations.
    scripts_path = package / "bin/npm-cli.js"
    scripts_path.write_text(
        "#!/bin/sh\ncat " + shlex.quote(str(package / "package.json")) + "\n" + scripts[bins[0] / "npm"]
    )
    scripts_path.chmod(0o755)
    (bins[0] / "npm").unlink()
    (bins[0] / "npm").symlink_to(scripts_path)
    (home / "personal.txt").write_text("UNGRANTED_PERSONAL_DATA")
    (home / ".cargo/credentials.toml").write_text("UNGRANTED_REGISTRY_TOKEN")
    # With no gh of the user's own, the one the app installed for its sign-in, in its private
    # data, runs; the rest of that data stays private.
    from wavefunction.config import get_settings

    managed = get_settings().data_dir / "integrations/bin/gh"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_text("#!/bin/sh\necho MANAGED_GH_OK\n")
    managed.chmod(0o755)
    (managed.parent.parent / "other.txt").write_text("PRIVATE_INTEGRATION_DATA")
    # A HOME entry on PATH must not become a blanket read grant.
    monkeypatch.setattr(
        "wavefunction.sandbox.command_path", lambda: [str(home), *(str(p) for p in bins), "/usr/bin", "/bin"]
    )
    box = Sandbox([Grant(project, writable=True)], scratch=tmp_path / "scratch", restrict_reads=True)
    result = await box.run("npm && cargo && uv && gh", cwd=project)
    assert result.exit_code == 0, result.stderr
    assert all(
        marker in result.stdout
        for marker in (
            "NODE_PACKAGE_OK",
            "NPM_RUNTIME_OK",
            "RUST_RUNTIME_OK",
            "RUST_SETTINGS_OK",
            "UV_RUNTIME_OK",
            "MANAGED_GH_OK",
        )
    )
    for path in [
        home / "personal.txt",
        home / ".cargo/credentials.toml",
        managed.parent.parent / "other.txt",
    ]:
        denied = await box.run("cat " + shlex.quote(str(path)), cwd=project)
        assert denied.exit_code != 0 and "UNGRANTED" not in denied.stdout
    denied = await box.run("echo changed >> " + shlex.quote(str(node_lib / "npm.txt")), cwd=project)
    assert denied.exit_code != 0 and (node_lib / "npm.txt").read_text() == "NPM_RUNTIME_OK\n"
    # Swift and Objective-C build their modules (`@import Foundation`) in the per-user cache.
    (project / "m.m").write_text("@import Foundation;\nint main(void) { return 0; }\n")
    built = await box.run("/usr/bin/clang -fmodules -c m.m -o m.o", cwd=project, timeout=120)
    assert built.exit_code == 0 and (project / "m.o").exists(), built.stderr

    # Git reads the user's global configuration as in Terminal: a dotfile manager's symlinked
    # ~/.gitconfig, the files it includes, and its global ignore file.
    dotfiles = home / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "gitconfig").write_text(
        "[include]\n\tpath = ~/.gitconfig.local # machine settings\n[core]\n\texcludesfile = ~/dotfiles/gitignore_global\n"
        "\thooksPath = ~/dotfiles/hooks\n"
    )
    (dotfiles / "hooks").mkdir()
    (dotfiles / "hooks" / "pre-commit").write_text("#!/bin/sh\necho GLOBAL_HOOK_RAN\n")
    (dotfiles / "hooks" / "pre-commit").chmod(0o755)
    (home / ".gitconfig").symlink_to(dotfiles / "gitconfig")
    (home / ".gitconfig.local").write_text("[user]\n\tname = Synthetic Included\n")
    (dotfiles / "gitignore_global").write_text("*.local.env\n")
    (project / "app.py").write_text("print(1)\n")
    (project / "secrets.local.env").write_text("SYNTHETIC=1\n")
    status = await box.run(
        "/usr/bin/git init -q && /usr/bin/git config user.name && /usr/bin/git status --porcelain",
        cwd=project,
        timeout=60,
    )
    assert status.exit_code == 0 and "Operation not permitted" not in status.stderr, status.stderr
    assert (
        "Synthetic Included" in status.stdout
        and "app.py" in status.stdout
        and "secrets.local.env" not in status.stdout
    )
    committed = await box.run(
        "/usr/bin/git add app.py && /usr/bin/git -c user.email=s@example.com commit -qm one",
        cwd=project,
        timeout=60,
    )
    assert committed.exit_code == 0 and "GLOBAL_HOOK_RAN" in committed.stdout + committed.stderr, (
        committed.stderr
    )
    # The system's time zone and resolver files, and a UTF-8 locale when the app was opened
    # from the Dock without one.
    for name in ("LANG", "LC_ALL", "LC_CTYPE"):
        monkeypatch.delenv(name, raising=False)
    system = await box.run(
        "head -c 4 /etc/localtime; head -c 4 /usr/share/zoneinfo/UTC; test -r /etc/resolv.conf "
        '&& echo; echo "$LANG"',
        cwd=project,
    )
    assert system.stdout == "TZifTZif\nen_US.UTF-8\n", system.stderr
    denied = await box.run("cat " + shlex.quote(str(home / "personal.txt")), cwd=project)
    assert denied.exit_code != 0
