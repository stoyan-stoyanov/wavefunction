"""Opt-in real-model check of each capability a release ships, through the product API.

Runs one scenario per capability in a throwaway data folder: conversation memory, coding, web
search and page reading, uploaded documents, computer use, helpers, and the approval for a
command that can't be undone. Each scenario plants a random code the model can only report if
the tool really worked. Uses a current ChatGPT sign-in or a provider's API key, both read-only,
and sends only synthetic files and prompts. Prints a pass/fail table; exits 1 unless every
scenario that ran passed.
"""

import argparse
import asyncio
import base64
import io
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

SCENARIOS = ("conversation", "coding", "web", "documents", "computer", "helpers", "approval")
RUN_TIMEOUT = 360


def code(prefix):
    return f"{prefix}-{secrets.token_hex(3).upper()}"


def minimal_pdf(text):
    """A one-page PDF whose text layer is `text`, built by hand: no PDF writer is installed."""
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(bodies) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(bodies) + 1, xref)
    return bytes(out)


def office_files(codes):
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation
    from pptx.util import Inches

    files = {"brief.pdf": minimal_pdf(f"Release code: {codes['pdf']}")}
    document = Document()
    document.add_paragraph(f"Release code: {codes['docx']}")
    files["notes.docx"] = document
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])  # Blank
    slide.shapes.add_textbox(
        Inches(1), Inches(1), Inches(6), Inches(1)
    ).text_frame.text = f"Release code: {codes['pptx']}"
    files["deck.pptx"] = deck
    book = Workbook()
    book.active.append(["Field", "Value"])
    book.active.append(["Release code", codes["xlsx"]])
    files["sheet.xlsx"] = book
    for name, value in list(files.items()):
        if not isinstance(value, bytes):
            buffer = io.BytesIO()
            value.save(buffer)
            files[name] = buffer.getvalue()
    return files


def git(folder, *args):
    return subprocess.run(
        [
            "git",
            "-C",
            str(folder),
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Harness:
    def __init__(self, services, client, root, model):
        self.services, self.client, self.root, self.model = services, client, root, model

    async def thread(self, name):
        folder = self.root / name
        folder.mkdir(exist_ok=True)
        response = await self.client.post(
            "/api/coding/threads", json={"folder": str(folder), "model": self.model}
        )
        response.raise_for_status()
        return response.json(), folder

    async def send(self, thread, content):
        response = await self.client.post(
            f"/api/coding/threads/{thread['id']}/messages", json={"content": content}
        )
        response.raise_for_status()
        return await self.services.runtime.wait(response.json()["id"], timeout=RUN_TIMEOUT)

    def completed(self, run):
        assert run["status"] == "completed", f"task ended {run['status']}: {run.get('error')}"
        return run["output"]["text"]

    def events(self, run, kind):
        return [
            e["payload"]
            for e in self.services.repository.list_run_events(run["id"])
            if e["event_type"] == kind
        ]

    def used(self, run, tool):
        """The tool's successful results in this task; a failed call is an assertion error."""
        failed = [e["result"][:200] for e in self.events(run, "tool_failed") if e["tool"] == tool]
        assert not failed, f"{tool} failed: {failed}"
        results = [e["result"] for e in self.events(run, "tool_completed") if e["tool"] == tool]
        assert results, f"the model never called {tool}"
        return results

    async def conversation(self):
        thread, _ = await self.thread("conversation")
        word = code("WORD")
        self.completed(await self.send(thread, f"Remember this code word: {word}. Reply only with OK."))
        assert "391" in self.completed(
            await self.send(thread, "What is 17 times 23? Reply with only the number.")
        )
        answer = self.completed(
            await self.send(thread, "What was the code word I gave you? Reply with only it.")
        )
        assert word in answer, f"forgot the code word two turns later: {answer[:200]}"

    async def coding(self):
        thread, folder = await self.thread("coding")
        (folder / "pricing.py").write_text("def total(price, count):\n    return price + count\n")
        test = (
            "import unittest\nfrom pricing import total\nclass PricingTests(unittest.TestCase):\n"
            "    def test_total(self): self.assertEqual(total(5, 3), 15)\n"
            "    def test_zero(self): self.assertEqual(total(8, 0), 0)\n"
        )
        (folder / "test_pricing.py").write_text(test)
        run = await self.send(
            thread,
            "The pricing tests fail. Fix pricing.py without changing the tests, then run "
            "python3 -m unittest -v to confirm.",
        )
        self.completed(run)
        self.used(run, "run_command")
        after = await self.services.coding_workspace.runner().run("python3 -m unittest -v", cwd=folder)
        assert after.exit_code == 0, f"tests still fail: {after.stderr[-300:]}"
        assert (folder / "test_pricing.py").read_text() == test, "the model changed the tests"

    async def web(self):
        from wavefunction.spec_common import ModelSpec

        thread, _ = await self.thread("web")
        searchable = self.services.models.search_provider(ModelSpec(**self.model)) != "none"
        if searchable:
            run = await self.send(
                thread,
                "Use the web_search tool to find the official website of the Python programming language. "
                "Reply with its URL.",
            )
            assert "python.org" in self.completed(run).lower()
            assert any("http" in result for result in self.used(run, "web_search")), (
                "search returned no links"
            )
        run = await self.send(
            thread, "Use fetch_page to read https://example.com and reply with the page's main heading."
        )
        answer = self.completed(run)
        assert any("Example Domain" in result for result in self.used(run, "fetch_page"))
        assert "Example Domain" in answer, answer[:200]
        if not searchable:
            return "skipped search: this provider searches only with a Tavily key; fetch_page passed"

    async def documents(self):
        thread, _ = await self.thread("documents")
        codes = {kind: code(kind.upper()) for kind in ("pdf", "docx", "pptx", "xlsx")}
        for name, data in office_files(codes).items():
            response = await self.client.post(
                f"/api/coding/threads/{thread['id']}/attachments", files={"file": (name, data)}
            )
            assert response.status_code == 201, f"{name} upload: {response.status_code} {response.text[:200]}"
        answer = self.completed(
            await self.send(
                thread,
                "I attached four documents. Each contains a release code. "
                "List every file name with its release code.",
            )
        )
        missing = [kind for kind, value in codes.items() if value not in answer]
        assert not missing, f"codes missing for {missing}: {answer[:300]}"

    async def computer(self):
        manager = self.services.computer
        status = await manager.refresh_status()
        if not (status.docker_available and status.image_ready) or status.docker_problem:
            return "skipped: start Docker and build the computer image in Settings › Computer first"
        thread, _ = await self.thread("computer")
        # The page draws its own random code, so the prompt holds no answer to repeat.
        page = (
            "data:text/html,<script>c='PX-'+Math.random().toString(36).slice(2,8).toUpperCase();"
            "document.title=c;document.write('<h1 style=font-size:72px>'+c+'</h1>')</script>"
        )
        try:
            answer = self.completed(
                await self.send(
                    thread,
                    f"Use the computer. In Firefox, open this address: {page} — the page shows a code "
                    "starting with PX-. Reply with only that code.",
                )
            )
            session = manager.running(thread["conversation_id"], thread["id"])
            assert session, "the model never started the computer"
            title = await manager._run(
                manager.docker(), "exec", session.container_id, "xdotool", "getactivewindow", "getwindowname"
            )
            shown = re.search(r"PX-[A-Z0-9]+", title.stdout)
            assert shown, f"the page never opened: window is {title.stdout.strip()!r}"
            assert shown.group() in answer, f"screen shows {shown.group()}, model said {answer[:200]}"
        finally:
            await manager.forget([thread["id"]])
            volume = manager.home_volume(thread["id"])
            volumes = await manager._run(manager.docker(), "volume", "ls", "-q", "--filter", "name=" + volume)
            assert volume not in volumes.stdout.split(), "the computer's home volume was left behind"

    async def helpers(self):
        thread, folder = await self.thread("helpers")
        secret = code("HELPER")
        (folder / "secret.txt").write_text(f"The code is {secret}.\n")
        run = await self.send(
            thread,
            "Start one helper agent and have it read secret.txt and report the code inside. "
            "Don't read the file yourself. When it reports back, reply with only the code.",
        )
        assert secret in self.completed(run), "the main chat never got the helper's code"
        self.used(run, "spawn_agent")
        workspace = (await self.client.get("/api/coding/workspace")).json()
        children = [row for row in workspace["threads"] if row.get("parent_run_id") == run["id"]]
        assert children, "no helper chat was recorded"
        read = []
        for child in children:
            for child_run in (await self.client.get(f"/api/coding/threads/{child['id']}")).json()["runs"]:
                read += [r for r in self.events(child_run, "tool_completed") if secret in r["result"]]
        assert read, "no helper read the file"

    async def approval(self):
        thread, folder = await self.thread("approval")
        remote = folder / "remote.git"
        git(folder, "init", "-q", "-b", "main")
        (folder / "notes.txt").write_text("first\n")
        git(folder, "add", "notes.txt")
        git(folder, "commit", "-q", "-m", "First")
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        git(folder, "remote", "add", "origin", str(remote))
        git(folder, "push", "-q", "origin", "main")
        published = git(remote, "rev-parse", "main")
        git(folder, "commit", "-q", "--amend", "-m", "Rewritten")
        run = await self.send(
            thread, "Run exactly this command with run_command: git push --force origin HEAD:main"
        )
        asked = 0
        while run["status"] == "waiting_approval":
            requests = self.events(run, "run_waiting_for_approval")[-1]["requests"]
            asked += any(request.get("kind") == "irreversible" for request in requests)
            response = await self.client.post(f"/api/runs/{run['id']}/approval", json={"decision": "reject"})
            response.raise_for_status()
            run = await self.services.runtime.wait(run["id"], timeout=RUN_TIMEOUT)
            assert asked <= 3, "the model kept asking after being declined"
        self.completed(run)
        assert asked, "a force push ran without asking first"
        assert git(remote, "rev-parse", "main") == published, "the declined force push changed the remote"


async def verify(args):
    rows, failures = [], 0
    with TemporaryDirectory(prefix="wavefunction-harness-live-") as temporary:
        root = Path(temporary).resolve()
        os.environ["WAVEFUNCTION_DATA_DIR"] = str(root / "data")
        from langchain.agents.middleware import AgentMiddleware
        from wavefunction import agent_factory, coding_workspace, github_cli, main
        from wavefunction.codex import CodexAuth, CodexAuthError, CodexAuthStore, CodexCredentials
        from wavefunction.config import Settings
        from wavefunction.models import ModelGateway
        from wavefunction.secrets import EnvFileSecretStore, MemorySecretStore

        services = main.Services(Settings(data_dir=root / "data", development_auth_bypass=True))
        main.services = services
        services.secrets = MemorySecretStore()
        secret_values = []
        if args.provider == "codex":

            class ReadOnlyAuth(CodexAuth):
                def access_token(self):
                    credentials = self.credentials()
                    if credentials is None or credentials.expiring():
                        raise CodexAuthError(
                            "A current sign-in is needed. This check never refreshes credentials."
                        )
                    return credentials.access

            class ReadOnlyStore(CodexAuthStore):
                """The app's sign-in file, or the Codex CLI's `~/.codex/auth.json`; never written."""

                def load(self):
                    tokens = json.loads(self.path.read_text(encoding="utf-8")).get("tokens")
                    if not tokens:
                        return super().load()
                    claims = tokens["access_token"].split(".")[1]
                    claims = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
                    return CodexCredentials(
                        tokens["access_token"],
                        tokens["refresh_token"],
                        float(claims["exp"]),
                        tokens["account_id"],
                    )

                def save(self, credentials):
                    raise CodexAuthError("This check never writes the sign-in")

            auth = ReadOnlyAuth(ReadOnlyStore(args.auth_file.expanduser()))
            secret_values.append(auth.access_token())
            services.codex = auth
            services.models = ModelGateway(services.secrets, codex=auth)
            services.runtime.models = services.models
        else:
            # Keys under the app's own names, from a file like its key file or from the environment.
            stored = EnvFileSecretStore((args.key_file or root / "none.env").expanduser())
            for name in (f"{args.provider}_api_key", "tavily_api_key"):
                if value := stored.get(name):
                    secret_values.append(value)
                    services.secrets.set(name, value)
            if not services.secrets.get(f"{args.provider}_api_key"):
                raise ValueError(
                    f"No {args.provider}_api_key: pass --key-file or set its environment variable"
                )
            services.models.secrets = services.secrets
        # Only this check's synthetic skills, and no GitHub account.
        discover = coding_workspace.discover
        coding_workspace.discover = lambda *a, **kw: discover(*a, **kw, home=root)

        async def signed_out():
            return False

        github_cli.commands_signed_in = signed_out
        sent = 0

        class Budget(AgentMiddleware):
            """Counts every model call, helpers' included, and stops past --max-requests."""

            async def awrap_model_call(self, request, handler):
                nonlocal sent
                sent += 1
                if sent > args.max_requests:
                    raise RuntimeError(f"Stopped at the {args.max_requests}-call budget (--max-requests)")
                return await handler(request)

        build_middleware = agent_factory.build_middleware
        agent_factory.build_middleware = lambda *a, **kw: [Budget(), *build_middleware(*a, **kw)]
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app), base_url="http://test"
            ) as client:
                harness = Harness(services, client, root, {"provider": args.provider, "model": args.model})
                for name in args.only:
                    started, before = time.monotonic(), sent
                    try:
                        skipped = await getattr(harness, name)()
                        result, detail = ("skipped", skipped) if skipped else ("pass", "")
                    except Exception as error:  # noqa: BLE001 — report every scenario, then fail
                        result, detail = "FAIL", f"{type(error).__name__}: {error}"
                        for value in secret_values:
                            detail = detail.replace(value, "[REDACTED]")
                    failures += result != "pass"
                    rows.append((name, result, time.monotonic() - started, sent - before, detail))
                    print(f"{name}: {result} {detail}".rstrip(), flush=True)
        finally:
            for run_id, task in list(services.runtime.tasks.items()):
                if not task.done():
                    await services.runtime.cancel(run_id)
            await services.runtime.aclose()
            await services.computer.shutdown()
            await services.coding_workspace.terminals.shutdown()
            await services.github_proxy.close()
    print(f"\n{'Scenario':<14}{'Result':<9}{'Seconds':>8}{'Model calls':>13}  Detail")
    for name, result, seconds, calls, detail in rows:
        print(f"{name:<14}{result:<9}{seconds:>8.1f}{calls:>13}  {detail[:160]}")
    print(f"\n{args.provider} {args.model}: {sent} model calls; temporary data removed: {not root.exists()}")
    print(json.dumps({"passed": failures == 0, "scenarios": {row[0]: row[1] for row in rows}}))
    return 1 if failures or root.exists() else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--provider",
        choices=("codex", "openrouter", "vercel", "anthropic", "gemini", "openai"),
        default="codex",
    )
    parser.add_argument(
        "--auth-file",
        type=Path,
        help="A ChatGPT sign-in, such as the Codex CLI's ~/.codex/auth.json; read-only",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        help="The app's key file (Agent/.wavefunction.env in its data folder) or one like it; read-only",
    )
    parser.add_argument("--model", help="Required except for codex, which defaults to gpt-6.1-sol")
    parser.add_argument("--only", help="Comma-separated scenarios to run: " + ", ".join(SCENARIOS))
    parser.add_argument("--max-requests", type=int, default=120, help="Stop after this many model calls")
    args = parser.parse_args()
    if args.provider == "codex" and not args.auth_file:
        parser.error("--auth-file is required for codex")
    if args.provider != "codex" and not args.model:
        parser.error("--model is required; computer use needs one that reads images")
    args.only = args.only.split(",") if args.only else list(SCENARIOS)
    if unknown := set(args.only) - set(SCENARIOS):
        parser.error(f"unknown scenarios: {', '.join(sorted(unknown))}")
    args.model = args.model or "gpt-6.1-sol"
    raise SystemExit(asyncio.run(verify(args)))
