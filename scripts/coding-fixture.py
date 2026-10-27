"""Disposable Wavefunction browser/API fixture; no user accounts or live model calls."""
# The engine reads its settings at import, so it is imported after they are set below.
# ruff: noqa: E402

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=8787)
args = parser.parse_args()
root = Path(os.environ["WAVEFUNCTION_DATA_DIR"])
folder = root / "synthetic-repository"
folder.mkdir()
(folder / "README.md").write_text(
    "# Sandbox project\n\nA disposable repository for Wavefunction verification.\n"
)
(folder / "app.py").write_text('def greet(name):\n    return f"Hello, {name}!"\n')
(folder / "AGENTS.md").write_text("Use focused edits and verify with python3 -m unittest.\n")
(folder / ".gitignore").write_text("__pycache__/\n*.pyc\n")
subprocess.run(["git", "init", "-q", str(folder)], check=True)
subprocess.run(["git", "-C", str(folder), "config", "user.name", "Wavefunction Fixture"], check=True)
subprocess.run(["git", "-C", str(folder), "config", "user.email", "fixture@example.invalid"], check=True)
(folder / ".agents/skills/fixture-coding").mkdir(parents=True)
(folder / ".agents/skills/fixture-coding/SKILL.md").write_text(
    "---\nname: fixture-coding\ndescription: Guidance for the synthetic coding fixture.\n---\n\nUse focused edits and compile app.py.\n"
)
subprocess.run(["git", "-C", str(folder), "add", "."], check=True)
subprocess.run(
    [
        "git",
        "-C",
        str(folder),
        "-c",
        "user.name=Wavefunction Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "Synthetic baseline",
    ],
    check=True,
)
# Keep the synthetic repository outside the private application workspace.
os.environ["WAVEFUNCTION_DATA_DIR"] = str(root / "app-data")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/tests"))
from conftest import ScriptedModel, tool_call_message
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from wavefunction import coding_workspace as coding_surface
from wavefunction import main
from wavefunction.coding_workspace import ThreadInput
from wavefunction.execution_policy import _is_user_message
from wavefunction.skill_discovery import discover

thread_delay = float(os.environ.get("WAVEFUNCTION_FIXTURE_THREAD_DELAY", "0"))
if thread_delay:
    delayed_threads = set()

    @main.app.middleware("http")
    async def delay_history(request, call_next):
        path = request.url.path
        if (
            request.method == "GET"
            and path.startswith("/api/coding/threads/")
            and path.count("/") == 4
            and path not in delayed_threads
        ):
            delayed_threads.add(path)
            await asyncio.sleep(thread_delay)
        return await call_next(request)


fixture_home = root / "synthetic-home"
global_skill = fixture_home / ".agents/skills/fixture-global/SKILL.md"
global_skill.parent.mkdir(parents=True)
global_skill.write_text(
    "---\nname: fixture-global\ndescription: Synthetic global coding guidance.\n---\n\nPreserve unrelated changes.\n"
)
coding_surface.discover = lambda selected_folder, library, **kwargs: discover(
    selected_folder, library, home=fixture_home, **kwargs
)


class FixtureModel(ScriptedModel):
    async def _astream(self, messages, **kwargs):
        result = await self._agenerate(messages, **kwargs)
        message = result.generations[0].message
        prompt = next((str(m.content) for m in reversed(messages) if _is_user_message(m)), "")
        if prompt.startswith("fixture: smooth"):
            for offset in range(0, len(message.content), 24):
                await asyncio.sleep(0.12)
                yield ChatGenerationChunk(
                    message=AIMessageChunk(content=message.content[offset : offset + 24])
                )
        else:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content=message.content, tool_calls=message.tool_calls)
            )
        # Responses providers replace the temporary LangChain id at completion.
        yield ChatGenerationChunk(message=AIMessageChunk(content="", id=f"resp_fixture_{uuid.uuid4().hex}"))

    async def _agenerate(self, messages, **kwargs):
        latest = next((str(m.content) for m in reversed(messages) if _is_user_message(m)), "")
        if not self.script:
            prompt = next((str(m.content) for m in reversed(messages) if _is_user_message(m)), "")
            recent = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
            if prompt.startswith("fixture: smooth"):
                self.script = [
                    AIMessage(
                        content="## A smoother live reply\n\n"
                        + " ".join(
                            "New words fade in gently while previous replies stay still. Scroll up to read the saved conversation without being pulled back to the end."
                            for _ in range(8)
                        )
                    )
                ]
            elif prompt.startswith("fixture: steering"):
                plan = tool_call_message(
                    "write_todos",
                    {
                        "todos": [
                            {"content": "Check the original request", "status": "in_progress"},
                            {"content": "Apply any new direction", "status": "pending"},
                        ]
                    },
                    "fixture-steering-plan",
                )
                plan.content = "I’m checking the original request."
                self.script = [
                    plan,
                    tool_call_message(
                        "run_command", {"command": "sleep 12", "timeout": 30}, "fixture-steering-wait"
                    ),
                    AIMessage(content="The original request is finished."),
                ]
            elif prompt.startswith("fixture: slow"):
                self.script = [
                    tool_call_message("run_command", {"command": "sleep 12", "timeout": 30}, "fixture-slow"),
                    AIMessage(
                        content="Synthetic slow task finished; steering reaches this same running task."
                    ),
                ]
            elif prompt.startswith("fixture: helpers"):
                if recent and recent.name == "spawn_agent":
                    child = json.loads(recent.content)
                    self.script = [
                        tool_call_message(
                            "send_message",
                            {
                                "agent_id": child["agent_id"],
                                "message": "fixture: redirect Verify the isolated helper change",
                            },
                            "fixture-steer-child",
                        )
                    ]
                elif recent and recent.name == "send_message":
                    child = json.loads(recent.content)
                    self.script = [
                        tool_call_message(
                            "wait_agents",
                            {"agent_ids": [child["agent_id"]], "timeout_seconds": 30},
                            "fixture-wait",
                        )
                    ]
                elif recent and recent.name == "wait_agents":
                    child = json.loads(recent.content)["agents"][0]
                    assert child["status"] not in {"failed", "cancelled"}, child.get("error")
                    self.script = (
                        [
                            AIMessage(
                                content="Synthetic helper finished in its own worktree. Its code remains available in the indented child chat."
                            )
                        ]
                        if child["status"] == "completed"
                        else [
                            tool_call_message(
                                "wait_agents",
                                {"agent_ids": [child["agent_id"]], "timeout_seconds": 30},
                                "fixture-wait-again",
                            )
                        ]
                    )
                else:
                    self.script = [
                        tool_call_message(
                            "spawn_agent",
                            {"task": "fixture: child", "title": "Greeting helper"},
                            "fixture-spawn",
                        )
                    ]
            elif any(isinstance(m, HumanMessage) and str(m.content) == "fixture: child" for m in messages):
                self.script = [
                    tool_call_message("run_command", {"command": "sleep 8"}, "fixture-child-wait"),
                    tool_call_message(
                        "write_file",
                        {"path": "helper.txt", "content": "Independent helper work\n"},
                        "fixture-child-write",
                    ),
                    AIMessage(content="Created helper.txt in my isolated worktree."),
                ]
            elif prompt.startswith("fixture: edit"):
                self.script = [
                    tool_call_message("read_skill", {"name": "fixture-coding"}, "fixture-skill"),
                    tool_call_message("read_file", {"path": "app.py"}, "fixture-read"),
                    tool_call_message(
                        "edit_file",
                        {"path": "app.py", "old_text": "Hello,", "new_text": "Hi,"},
                        "fixture-edit",
                    ),
                    tool_call_message(
                        "run_command", {"command": "python3 -m compileall -q app.py"}, "fixture-check"
                    ),
                    AIMessage(
                        content="Synthetic task: used repository guidance, changed the greeting, and compiled app.py through the real tools. Open Changes to see the diff."
                    ),
                ]
            elif prompt.startswith("fixture: question"):
                self.script = [
                    tool_call_message(
                        "ask_user",
                        {
                            "questions": [
                                {
                                    "id": "next_step",
                                    "title": "The app starts, but sign-in needs a local database. How should I continue?",
                                    "options": [
                                        {
                                            "label": "The database is running",
                                            "description": "Start the stack and verify sign-in end to end.",
                                        },
                                        {
                                            "label": "Help me install it",
                                            "description": "Set up the missing runtime first.",
                                        },
                                        {
                                            "label": "Stop here for now",
                                            "description": "Explain the startup commands and what remains blocked.",
                                        },
                                    ],
                                }
                            ]
                        },
                        "fixture-question",
                    ),
                    AIMessage(content="Synthetic task: continued with your answer."),
                ]
            elif prompt.startswith("fixture: table"):
                self.script = [
                    AIMessage(
                        content="## App comparison\n\n"
                        "| App | What it does | Comparison | Platform | Notes |\n"
                        "| --- | --- | --- | --- | --- |\n"
                        + "\n".join(
                            f"| [Example app {index}](https://example.com/) | A synthetic app with a readable description. | Compare behavior without cramped columns. | Desktop and mobile | A long note that wraps normally within its column. |"
                            for index in range(1, 25)
                        )
                    )
                ]
            elif prompt.startswith("fixture: files"):
                # Generated files, linked as the agent links what it creates for the user.
                from PIL import Image, ImageDraw

                out = folder / "out"
                out.mkdir(exist_ok=True)
                pages = []
                for number in range(1, 4):
                    page = Image.new("RGB", (612, 792), "white")
                    ImageDraw.Draw(page).text(
                        (72, 72), f"Synthetic quarterly report, page {number}", fill="black"
                    )
                    pages.append(page)
                pages[0].save(out / "report.pdf", save_all=True, append_images=pages[1:])
                (out / "summary.md").write_text(
                    "# Summary\n\n- **North** led Q1.\n- See the table below.\n\n| Region | Total |\n| --- | --- |\n| North | 1200 |\n"
                )
                (out / "totals.csv").write_text(
                    'region,quarter,total\nNorth,Q1,1200\nSouth,Q1,"1,050"\nEast,Q2,980\n'
                )
                self.script = [
                    AIMessage(
                        content="Created [report.pdf](out/report.pdf), [totals.csv](out/totals.csv) and "
                        "[summary.md](out/summary.md), and see [the app](app.py)."
                    )
                ]
            elif prompt.startswith("fixture: transcript"):
                self.script = [
                    AIMessage(
                        content="\n\n".join(
                            f"## System {index}: scrolling verification\n\n"
                            "This synthetic response checks the conversation boundary. Text, headings, "
                            "and code should fade within the history without crossing the toolbar divider.\n\n"
                            "- Repository files remain scoped to the selected project.\n"
                            "- Independent chats preserve their own drafts and tool context.\n\n"
                            "```python\ndef verify_boundary():\n    return 'The divider stays clear'\n```"
                            for index in range(1, 19)
                        )
                    )
                ]
            elif prompt.startswith("fixture: approval"):
                # A command outside the sandbox waits on its approval card. Allowing it only echoes.
                self.script = [
                    tool_call_message(
                        "run_command",
                        {
                            "command": "echo Ran outside the sandbox",
                            "outside_sandbox": True,
                            "reason": "Synthetic check of the approval card.",
                        },
                        "fixture-approval",
                    ),
                    AIMessage(content="Synthetic task: the command ran after your approval."),
                ]
            elif prompt.startswith("fixture: irreversible"):
                # A force push is flagged as irreversible. Allowing it only adds a branch to the fixture's own remote.
                self.script = [
                    tool_call_message(
                        "run_command",
                        {"command": "git push --force origin HEAD:refs/heads/fixture-force-demo"},
                        "fixture-irreversible",
                    ),
                    AIMessage(content="Synthetic task: the flagged command ran after your approval."),
                ]
            elif prompt.startswith("fixture: terminal"):
                self.script = [
                    tool_call_message(
                        "start_terminal",
                        {
                            "command": "for i in 1 2 3; do echo tick $i; sleep 1; done; read line",
                            "title": "Fixture terminal",
                        },
                        "fixture-terminal",
                    ),
                    AIMessage(content="Synthetic task: a terminal is open in the workbench."),
                ]
            elif prompt.startswith("fixture: fail"):
                raise RuntimeError("Synthetic failure: the fixture's model failed on purpose.")
            elif prompt.startswith("Use the thorough-bug-review skill for this repository."):
                self.script = [
                    tool_call_message("read_skill", {"name": "thorough-bug-review"}, "fixture-slash-skill"),
                    AIMessage(
                        content="Synthetic verification: the slash command loaded the captured thorough-bug-review skill. No repository review was performed by this fixture."
                    ),
                ]
            else:
                self.script = [
                    AIMessage(
                        content="Synthetic reply: your message reached the real coding runtime. This disposable fixture uses no model account. Send `fixture: edit` to exercise real skill loading, edits and verification."
                    )
                ]
        if latest.startswith("fixture: redirect"):
            self.script[-1] = AIMessage(content="Steering applied in the running task: " + latest)
        result = await super()._agenerate(messages, **kwargs)
        if latest.startswith("fixture: helpers"):
            # The parent chooses its next tool from the helper's actual saved result.
            self.script = []
            self._cursor = 0
        return result


main.services.models.chat_model = lambda *_args, **_kwargs: FixtureModel()
# A connected model, so the window opens past setup. Nothing calls its address: the fixture's
# model answers every chat.
fixture_model = {
    "provider": "openai-compatible",
    "model": "fixture-model",
    "base_url": "http://127.0.0.1:9/v1",
    "temperature": 0.1,
    "max_tokens": None,
}
main.services.repository.set_setting("coding_models", [fixture_model])
main.services.repository.set_setting("enabled_models", ["openai-compatible::fixture-model"])
main.services.repository.set_setting("default_model", fixture_model)
ws = main.services.coding_workspace
project = {
    "id": "synthetic-project",
    "name": "Sandbox project",
    "folder": str(folder),
    "created_at": "2026-10-02T00:00:00Z",
}
with ws.db() as db:
    db.execute(
        """INSERT INTO coding_projects (id, name, folder, created_at)
        VALUES (:id, :name, :folder, :created_at)""",
        project,
    )
ws.allow_folder(str(folder))
thread = ws.create_thread(ThreadInput(title="Welcome to Wavefunction", project_id=project["id"]))
remote = root / "synthetic-remote.git"
subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
subprocess.run(["git", "-C", str(folder), "remote", "add", "origin", str(remote)], check=True)
ws.allow_folder(str(remote))
# A second project, so switching projects and chats can be checked.
second_folder = root / "second-repository"
second_folder.mkdir()
(second_folder / "README.md").write_text("# Second project\n")
subprocess.run(["git", "init", "-q", str(second_folder)], check=True)
with ws.db() as db:
    db.execute(
        """INSERT INTO coding_projects (id, name, folder, created_at)
        VALUES ('synthetic-second-project', 'Second project', ?, '2026-10-03T00:00:00Z')""",
        (str(second_folder),),
    )
ws.allow_folder(str(second_folder))
ws.create_thread(ThreadInput(title="Second project chat", project_id="synthetic-second-project"))
print(f"Synthetic repository: {folder}", flush=True)
uvicorn.run(main.app, host="127.0.0.1", port=args.port, log_level="warning")
