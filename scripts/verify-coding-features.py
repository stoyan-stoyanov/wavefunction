"""Exercise a frozen Harness sidecar with disposable data and scripted model calls.

Does not launch, quit, or read the user's desktop app. No provider account is used.
"""

import argparse
import json
import os
import re
import secrets
import shlex
import signal
import socket
import sqlite3
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

BOOTSTRAP = """
import asyncio
import json
import os
import shlex
import sys
from pathlib import Path
import uvicorn
from pydantic import PrivateAttr
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from wavefunction import main, coding_workspace
from wavefunction.execution_policy import _is_user_message
from wavefunction.skill_discovery import discover

home = Path(os.environ["HARNESS_FEATURES_HOME"])
coding_workspace.discover = lambda folder, library, **kwargs: discover(folder, library, home=home, **kwargs)
ws = main.services.coding_workspace
ws.allow_folder(str(home.parent / "remote.git"))

async def verify_runtime_reuse():
    from wavefunction.sandbox import Sandbox
    runner = Sandbox([], scratch=home.parent / "runtime-check", restrict_reads=True)
    command = shlex.join([sys.executable, "--python", "-I", "-c", "import sys; print(sys._MEIPASS)"])
    result = await runner.run(command, scan_detached=False)
    assert result.exit_code == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == Path(sys._MEIPASS).resolve()

asyncio.run(verify_runtime_reuse())

class SmokeModel(BaseChatModel):
    _cursor: int = PrivateAttr(default=0)
    @property
    def _llm_type(self): return "synthetic-coding-smoke"
    def bind_tools(self, tools, **kwargs): return self
    def _generate(self, messages, **kwargs): raise NotImplementedError
    async def _agenerate(self, messages, **kwargs):
        # The user's message, not the environment note the app adds after it.
        prompt = next((str(m.content) for m in reversed(messages) if _is_user_message(m)), "")
        calls = [
            ("run_command", {"command": "git fetch origin"}),
            ("read_skill", {"name": "fixture-coding"}),
            ("read_file", {"path": "app.py"}),
            ("edit_file", {"path": "app.py", "old_text": "Hello,", "new_text": "Hi,"}),
            ("run_command", {"command": "python3 -m compileall -q app.py"}),
            ("start_terminal", {"command": "read answer; printf '%s\\\\n' \\"$answer\\" > result.txt; sleep 60", "title": "Agent check"}),
        ]
        if prompt.startswith("Smoke history"):
            before, after = ("Hi,", "Checkpoint,") if "first" in prompt else ("Checkpoint,", "Future,")
            history_calls = [("read_file", {"path": "app.py"}), ("edit_file", {"path": "app.py", "old_text": before, "new_text": after})]
            if self._cursor < len(history_calls):
                name, args = history_calls[self._cursor]
                message = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "history-" + str(self._cursor), "type": "tool_call"}])
            else:
                message = AIMessage(content="Historical reply: " + after)
        elif any(isinstance(m, HumanMessage) and str(m.content) == "Smoke steering" for m in messages):
            message = AIMessage(content="", tool_calls=[{"name": "run_command", "args": {"command": "sleep 1.5"}, "id": "slow-steering", "type": "tool_call"}]) if self._cursor == 0 else AIMessage(content="Frozen steering applied" if prompt.startswith("Use the captured steering instruction") else "Steering not received")
        elif prompt == "Smoke helper":
            if self._cursor == 0:
                # Isolated: helpers share the checkout by default, and only an isolated one has a diff to report.
                name, args = "spawn_agent", {"task": "Frozen child change", "title": "Frozen helper", "isolated": True}
            else:
                observed = json.loads(next(m.content for m in reversed(messages) if isinstance(m, ToolMessage)))
                child = observed["agents"][0] if "agents" in observed else observed
                if child["status"] == "completed":
                    assert child["files"] == ["helper.txt"] and child["base_commit"] and "Frozen child change" in child["diff"]
                    name, args = None, None
                else:
                    name, args = "wait_agents", {"agent_ids": [child["agent_id"]], "timeout_seconds": 30}
            message = AIMessage(content="Frozen helper complete") if name is None else AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "helper-" + str(self._cursor), "type": "tool_call"}])
        elif prompt == "Frozen child change":
            if self._cursor == 0:
                message = AIMessage(content="", tool_calls=[{"name": "write_file", "args": {"path": "helper.txt", "content": "Frozen child change\\n"}, "id": "child-write", "type": "tool_call"}])
            elif self._cursor == 1:
                message = AIMessage(content="", tool_calls=[{"name": "run_command", "args": {"command": "git add -- helper.txt && git -c user.name=Synthetic -c user.email=fixture@example.invalid -c commit.gpgsign=false commit -m 'Frozen helper change'"}, "id": "child-commit", "type": "tool_call"}])
            else:
                committed = next(m for m in messages if isinstance(m, ToolMessage) and m.tool_call_id == "child-commit")
                assert "[exit code 0]" in committed.content, committed.content
                message = AIMessage(content="Frozen child finished")
        elif prompt.startswith("Smoke question"):
            if self._cursor == 0 and not any(getattr(m, "name", None) == "ask_user" for m in messages):
                message = AIMessage(content="", tool_calls=[{"name": "ask_user", "args": {"questions": [{"id": "delivery", "title": "Choose delivery", "options": [{"label": "Commit", "description": "Save locally"}]}]}, "id": "question", "type": "tool_call"}])
            else:
                message = AIMessage(content="Synthetic question answered")
        elif prompt.startswith("Use the thorough-bug-review skill for this repository."):
            message = AIMessage(content="", tool_calls=[{"name": "read_skill", "args": {"name": "thorough-bug-review"}, "id": "slash-skill", "type": "tool_call"}]) if self._cursor == 0 else AIMessage(content="Synthetic slash skill loaded")
        elif prompt != "Smoke edit":
            message = AIMessage(content="Synthetic review received")
        elif self._cursor < len(calls):
            name, args = calls[self._cursor]
            message = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": str(self._cursor), "type": "tool_call"}])
        elif self._cursor == len(calls):
            terminal = next(s for s in ws.terminals.sessions.values() if s.owner == "agent")
            message = AIMessage(content="", tool_calls=[{"name": "write_terminal", "args": {"terminal_id": terminal.id, "text": "FROZEN-PTY-PASS\\n"}, "id": "input", "type": "tool_call"}])
        elif self._cursor == len(calls) + 1:
            terminal = next(s for s in ws.terminals.sessions.values() if s.owner == "agent")
            message = AIMessage(content="", tool_calls=[{"name": "read_terminal", "args": {"terminal_id": terminal.id, "wait_seconds": 1}, "id": "output", "type": "tool_call"}])
        else:
            message = AIMessage(content="Synthetic coding complete")
        self._cursor += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

main.services.models.chat_model = lambda *args, **kwargs: SmokeModel()
uvicorn.run(main.app, host="127.0.0.1", port=int(os.environ["WAVEFUNCTION_PORT"]), log_level="warning", timeout_graceful_shutdown=1)
"""


def until(operation, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = operation()
            if value:
                return value
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(0.05)
    raise AssertionError("Synthetic coding feature check timed out")


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def main(args):
    result = {}
    process, client = None, None
    children, sidecars, ports = set(), set(), []
    with TemporaryDirectory(prefix="harness-features-frozen-", dir="/private/tmp") as temporary:
        root = Path(temporary)
        folder, data = root / "repository", root / "app-data"
        folder.mkdir()
        (folder / "app.py").write_text('def greet(name):\n    return f"Hello, {name}!"\n')
        (folder / ".gitignore").write_text("__pycache__/\n*.pyc\n.env\n")
        (folder / ".env").write_text("DO_NOT_REVIEW=SYNTHETIC-SECRET\n")
        skill = folder / ".agents/skills/fixture-coding/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "---\nname: fixture-coding\ndescription: Synthetic native guidance.\n---\n\nPreserve other files.\n"
        )
        home_skill = root / "home/.claude/skills/global-helper/SKILL.md"
        home_skill.parent.mkdir(parents=True)
        home_skill.write_text(
            "---\nname: global-helper\ndescription: Synthetic global guidance.\n---\n\nVerify your edits.\n"
        )
        (folder / "unrelated.txt").write_text("Original unrelated file\n")

        def git(*args):
            return (
                subprocess.check_output(["git", "-C", str(folder), *args], stderr=subprocess.STDOUT)
                .decode()
                .strip()
            )

        git("init", "-b", "main")
        git("config", "user.name", "Synthetic native verifier")
        git("config", "user.email", "fixture@example.invalid")
        git("add", ".")
        git("commit", "-m", "Synthetic baseline")
        (folder / "unrelated.txt").write_text("Pre-existing dirty work\n")
        remote = root / "remote.git"
        subprocess.check_call(["git", "init", "--bare", "-q", str(remote)])
        git("remote", "add", "origin", str(remote))
        bootstrap = root / "bootstrap.py"
        bootstrap.write_text(BOOTSTRAP)
        log = (root / "sidecar.log").open("w+")

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json() if response.content else None

        def start():
            nonlocal process, client
            with socket.socket() as available:
                available.bind(("127.0.0.1", 0))
                port = available.getsockname()[1]
            ports.append(port)
            token = secrets.token_hex(32)
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("WAVEFUNCTION_", "MCP_"))
                and key
                not in {
                    "OPENAI_API_KEY",
                    "OPENROUTER_API_KEY",
                    "AI_GATEWAY_API_KEY",
                    "ANTHROPIC_API_KEY",
                    "GEMINI_API_KEY",
                    "GOOGLE_API_KEY",
                    "TAVILY_API_KEY",
                }
            }
            env.update(
                WAVEFUNCTION_DATA_DIR=str(data),
                WAVEFUNCTION_PORT=str(port),
                WAVEFUNCTION_DESKTOP_TOKEN=token,
                WAVEFUNCTION_DEVELOPMENT_AUTH_BYPASS="false",
                HARNESS_FEATURES_HOME=str(root / "home"),
            )
            process = subprocess.Popen(
                [str(args.backend.resolve()), "--python", "-I", str(bootstrap)],
                env=env,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
            client = httpx.Client(
                base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"}, timeout=30
            )
            until(lambda: client.get("/health").is_success, 60)
            assert httpx.get(f"http://127.0.0.1:{port}/api/coding/workspace").status_code == 401

        def stop():
            nonlocal process, client
            if client:
                client.close()
                client = None
            if process and process.poll() is None:
                rows = subprocess.run(
                    ["/bin/ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True
                ).stdout
                pairs = [tuple(map(int, row.split())) for row in rows.splitlines()]
                owned = {process.pid}
                while True:
                    descendants = {pid for pid, parent in pairs if parent in owned}
                    if descendants <= owned:
                        break
                    owned.update(descendants)
                sidecars.update(owned)
                # Match the desktop's normal quit: signal its backend supervisor,
                # allowing the engine to close PTYs before terminating their wrappers.
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(5)
                until(lambda: not any(alive(pid) for pid in owned), 5)
            process = None

        def completed(run):
            row = until(
                lambda: (
                    r
                    if (r := request("GET", f"/api/runs/{run['id']}"))["status"]
                    in {"completed", "failed", "cancelled"}
                    else None
                )
            )
            assert row["status"] == "completed", row.get("error")
            return row

        try:
            start()
            first = request(
                "POST", "/api/coding/threads", json={"title": "Frozen check", "folder": str(folder)}
            )
            second = request(
                "POST", "/api/coding/threads", json={"title": "Isolation", "folder": str(folder)}
            )
            base = f"/api/coding/threads/{first['id']}"
            assert any(entry["name"] == "app.py" for entry in request("GET", base + "/tree"))
            inventory = request("GET", "/api/coding/skills", params={"thread_id": first["id"]})
            assert {s["name"] for s in inventory["skills"] if s["active"]} >= {
                "fixture-coding",
                "global-helper",
            }
            edit_run = completed(request("POST", base + "/messages", json={"content": "Smoke edit"}))
            assert any(
                event["event_type"] == "tool_completed"
                and event["payload"].get("tool") == "run_command"
                and event["payload"]["result"].startswith("[exit code 0]")
                for event in edit_run["events"]
            )
            # Completion and the monitor both leave managed terminals available.
            time.sleep(0.6)
            agent_terminal = next(t for t in request("GET", base + "/terminals") if t["owner"] == "agent")
            assert agent_terminal["status"] == "running"
            request("DELETE", base + "/terminals/" + agent_terminal["id"])
            assert (folder / "result.txt").read_text() == "FROZEN-PTY-PASS\n"

            # The chat's own edits and its terminal's file are recorded as its changes; .env is not.
            def recorded(thread_id):
                with sqlite3.connect(f"file:{data / 'harness-workspace.sqlite'}?mode=ro", uri=True) as db:
                    return {
                        row[0]: row[1]
                        for row in db.execute(
                            "SELECT path, source FROM coding_changes WHERE thread_id = ?", (thread_id,)
                        )
                    }

            def terminal_recorded():
                found = recorded(first["id"])
                return found if "result.txt" in found else None

            changes = until(terminal_recorded)
            assert {"app.py", "result.txt"} <= changes.keys() and ".env" not in changes, changes
            assert not recorded(second["id"])
            # The Changes tab: the repository's changed files and a file's diff against HEAD.
            assert {"app.py", "result.txt", "unrelated.txt"} <= {
                f["path"] for f in request("GET", base + "/git/state")["files"]
            }
            diff = request("GET", base + "/git/diff", params={"path": "app.py", "combined": True})[
                "diff"
            ].splitlines()
            assert any(line.startswith("-") and "Hello," in line for line in diff), diff
            assert any(line.startswith("+") and "Hi," in line for line in diff), diff
            private = data / "synthetic-private.txt"
            private.write_text("PRIVATE-NATIVE-MARKER")
            for closure in ["close", "exit"]:
                terminal = request(
                    "POST", base + "/terminals", json={"title": closure, "cols": 93, "rows": 35}
                )
                url = base + "/terminals/" + terminal["id"]
                request(
                    "POST",
                    url + "/input",
                    json={
                        "text": f"cat {shlex.quote(str(private))}; stty size; sleep 60 & echo SMOKE_CHILD=$!\n"
                    },
                )
                text, cursor = "", 0

                def output(url=url):
                    nonlocal text, cursor
                    chunk = request("GET", url + "/output", params={"cursor": cursor, "wait": 0.2})
                    text += chunk["text"]
                    cursor = chunk["cursor"]
                    return re.search(r"SMOKE_CHILD=(\d+)", text)

                match = until(output)
                child = int(match[1])
                children.add(child)
                assert "PRIVATE-NATIVE-MARKER" not in text
                assert "Operation not permitted" in text or "Permission denied" in text
                assert "35 93" in text, text
                assert request("GET", url + "/output", params={"cursor": 0})["text"]
                assert (
                    client.get(
                        f"/api/coding/threads/{second['id']}/terminals/{terminal['id']}/output"
                    ).status_code
                    == 404
                )
                if closure == "close":
                    request("DELETE", url)
                else:
                    request("POST", url + "/input", json={"text": "exit\nexit\n"})
                    until(lambda url=url: request("GET", url + "/output")["status"] != "running")
                until(lambda child=child: not alive(child), 5)
            terminal = request("POST", base + "/terminals", json={"title": "Shutdown check"})
            url = base + "/terminals/" + terminal["id"]
            request(
                "POST",
                url + "/input",
                json={"text": "echo SHUTDOWN_SHELL=$$; sleep 60 & echo SHUTDOWN_CHILD=$!\n"},
            )
            text, cursor = "", 0

            def shutdown_output():
                nonlocal text, cursor
                chunk = request("GET", url + "/output", params={"cursor": cursor, "wait": 0.2})
                text += chunk["text"]
                cursor = chunk["cursor"]
                return re.search(r"SHUTDOWN_CHILD=(\d+)", text)

            match = until(shutdown_output)
            shutdown_pids = {int(match[1]), int(re.search(r"SHUTDOWN_SHELL=(\d+)", text)[1])}
            children.update(shutdown_pids)
            stop()
            until(lambda: not any(alive(pid) for pid in shutdown_pids), 5)
            start()
            assert recorded(first["id"]) == changes
            assert request("GET", base + "/terminals") == []
            # Commit the edit, as the agent would through git, so new worktrees start from it.
            git("add", "app.py")
            git("commit", "-m", "Update the greeting")
            assert git("show", "HEAD:unrelated.txt") == "Original unrelated file"
            captured = request("GET", base + "/file", params={"path": "app.py"})
            question = request(
                "POST",
                base + "/messages",
                json={
                    "content": "Smoke question",
                    "context": {
                        "files": [
                            {"path": "app.py", "version": captured["version"], "start_line": 2, "end_line": 2}
                        ],
                        "skills": ["fixture-coding"],
                    },
                },
            )
            until(lambda: request("GET", f"/api/runs/{question['id']}")["status"] == "waiting_approval")
            event = next(
                e for e in request("GET", base)["runs"][-1]["events"] if e["event_type"] == "run_questions"
            )
            group = event["payload"]["groups"][0]
            stop()
            start()
            answer = {"sequence": event["sequence"], "answers": {group["id"] + ":delivery": "Commit"}}
            assert (
                client.post(
                    f"/api/coding/threads/{second['id']}/runs/{question['id']}/answers", json=answer
                ).status_code
                == 404
            )
            request("POST", base + f"/runs/{question['id']}/answers", json=answer)
            completed(question)
            from wavefunction.skills import Skill

            review_skill = Skill.parse(
                (Path(__file__).resolve().parents[1] / "skills/thorough-bug-review/SKILL.md").read_text(),
                "thorough-bug-review",
            )
            request(
                "POST",
                "/api/skills",
                json={
                    "name": review_skill.name,
                    "description": review_skill.description,
                    "body": review_skill.body,
                },
            )
            slash = request("POST", base + "/messages", json={"content": "/thorough-bug-review"})
            completed(slash)
            slash_run = request("GET", f"/api/runs/{slash['id']}")
            assert slash_run["input"]["coding_context"]["skills"] == ["thorough-bug-review"]
            assert slash_run["input"]["display_text"] == "/thorough-bug-review"
            assert slash_run["output"]["text"] == "Synthetic slash skill loaded"
            stop()
            start()
            for thread_id in [first["id"], second["id"]]:
                inventory = request("GET", "/api/coding/skills", params={"thread_id": thread_id})
                assert any(
                    s["name"] == "thorough-bug-review" and s["active"] and s["source"] == "library"
                    for s in inventory["skills"]
                )

            advanced = request(
                "POST",
                "/api/coding/threads",
                json={"title": "Frozen history", "folder": str(folder), "worktree": True},
            )
            advanced_base = f"/api/coding/threads/{advanced['id']}"
            owned = Path(advanced["folder"])
            assert owned != folder and (owned / "app.py").read_bytes() == (folder / "app.py").read_bytes()
            history_first = completed(
                request("POST", advanced_base + "/messages", json={"content": "Smoke history first"})
            )
            points = request("GET", advanced_base)["runs"][-1]["checkpoints"]
            after_first = next(point for point in points if point["kind"] == "after_assistant")
            assert after_first["available"]
            history_later = completed(
                request("POST", advanced_base + "/messages", json={"content": "Smoke history later"})
            )
            before_later = next(
                point
                for point in request("GET", advanced_base)["runs"][-1]["checkpoints"]
                if point["kind"] == "before_user"
            )
            fork = request("POST", advanced_base + "/fork", json={"checkpoint_id": after_first["id"]})
            assert "Checkpoint," in (Path(fork["folder"]) / "app.py").read_text()
            assert "Future," in (owned / "app.py").read_text() and "Hi," in (folder / "app.py").read_text()
            assert len(request("GET", f"/api/coding/threads/{fork['id']}")["runs"]) == 1
            restored = request("POST", advanced_base + "/rewind", json={"checkpoint_id": before_later["id"]})
            assert (
                restored["draft"]["content"] == "Smoke history later"
                and "Checkpoint," in (owned / "app.py").read_text()
            )
            assert [row["input"]["forked_from"] for row in request("GET", advanced_base)["runs"]] == [
                history_first["id"]
            ]
            assert (
                request("GET", f"/api/coding/threads/{restored['archived_thread_id']}")["runs"][-1]["id"]
                == history_later["id"]
            )
            helper = completed(request("POST", advanced_base + "/messages", json={"content": "Smoke helper"}))
            assert helper["output"]["text"] == "Frozen helper complete"
            children_threads = [
                row
                for row in request("GET", "/api/coding/workspace")["threads"]
                if row.get("parent_run_id") == helper["id"]
            ]
            assert len(children_threads) == 1
            assert (Path(children_threads[0]["folder"]) / "helper.txt").read_text() == "Frozen child change\n"
            assert not (owned / "helper.txt").exists()
            steering = request("POST", advanced_base + "/messages", json={"content": "Smoke steering"})
            until(
                lambda: any(
                    event["event_type"] == "tool_started" and event["payload"].get("tool") == "run_command"
                    for event in request("GET", f"/api/runs/{steering['id']}")["events"]
                )
            )
            redirected = request(
                "POST", advanced_base + "/messages", json={"content": "Use the captured steering instruction"}
            )
            assert redirected["id"] == steering["id"]
            steered = completed(steering)
            assert steered["output"]["text"] == "Frozen steering applied"
            assert (
                len([event for event in steered["events"] if event["event_type"] == "steering_applied"]) == 1
            )
            stop()
            start()
            assert request("GET", advanced_base)["folder"] == str(owned)
            assert request("GET", advanced_base + "/worktree")["folder"] == str(owned)
            assert request("GET", f"/api/coding/threads/{fork['id']}")["runs"][0]["checkpoints"]
            result.update(
                frozen_backend=True,
                frozen_runtime_reused=True,
                bearer_auth=True,
                repository_global_skills=True,
                agent_repository_fetch=True,
                real_agent_tools=True,
                agent_pty=True,
                task_changes=True,
                changes_tab=True,
                change_record_restart=True,
                interactive_pty=True,
                output_replay=True,
                private_storage_denied=True,
                conversation_isolation=True,
                background_jobs_stopped=True,
                shutdown_stops_live_pty_and_jobs=True,
                explicit_file_skill_context=True,
                structured_question_restart=True,
                global_slash_skill=True,
                slash_skill_restart=True,
                new_chat_worktree=True,
                selected_message_fork=True,
                code_history_rewind=True,
                busy_message_steering=True,
                isolated_helper_tools=True,
                checkpoint_restart=True,
            )
        except BaseException:
            log.flush()
            log.seek(0)
            print(log.read()[-12000:])
            raise
        finally:
            stop()
            log.close()
            for child in children:
                if alive(child):
                    # These PIDs came from the synthetic shell's own $! output.
                    os.kill(child, signal.SIGKILL)
            until(lambda: not any(alive(child) for child in children), 5)
            for port in ports:
                with socket.socket() as check:
                    assert check.connect_ex(("127.0.0.1", port)) != 0
    result["cleanup"] = {
        "sidecar_stopped": process is None and not any(alive(pid) for pid in sidecars),
        "ports_closed": True,
        "terminal_children_stopped": True,
        "synthetic_data_removed": not root.exists(),
    }
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    main(parser.parse_args())
