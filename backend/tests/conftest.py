from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from wavefunction.files import ProjectFileStore
from wavefunction.models import ModelGateway
from wavefunction.repository import Repository
from wavefunction.runtime import RuntimeManager
from wavefunction.secrets import EnvFileSecretStore
from wavefunction.spec_v2 import AgentSpecV2, ToolBinding


def pytest_configure(config):
    """Isolate import-time services before test modules are collected in each worker."""
    from wavefunction.config import get_settings

    directory = TemporaryDirectory(prefix="wavefunction-pytest-")
    environment = pytest.MonkeyPatch()
    environment.setenv("WAVEFUNCTION_DATA_DIR", directory.name)
    get_settings.cache_clear()
    _check_response_models(environment)
    config.add_cleanup(directory.cleanup)
    config.add_cleanup(environment.undo)


def _check_response_models(environment: pytest.MonkeyPatch) -> None:
    """A route's `response_model` must describe what it returns, not change it.

    FastAPI drops keys a response model does not declare and converts values to its types, so a
    key added to a route but not to `wavefunction/responses.py` would silently never reach the
    app. Every response a test receives is checked against what the route returned.
    """
    import json

    from fastapi import routing
    from fastapi.encoders import jsonable_encoder

    serialize = routing.serialize_response

    def difference(sent: Any, returned: Any, at: str = "$") -> str | None:
        if isinstance(sent, dict) and isinstance(returned, dict):
            for key in returned.keys() - sent.keys():
                return f"{at}.{key} was dropped: declare it in the response model"
            for key in sent.keys() - returned.keys():
                return f"{at}.{key} was added"
            return next(
                filter(None, (difference(sent[key], returned[key], f"{at}.{key}") for key in sent)), None
            )
        if isinstance(sent, list) and isinstance(returned, list) and len(sent) == len(returned):
            return next(
                filter(
                    None,
                    (
                        difference(a, b, f"{at}[{i}]")
                        for i, (a, b) in enumerate(zip(sent, returned, strict=True))
                    ),
                ),
                None,
            )
        same = json.dumps(sent, sort_keys=True) == json.dumps(returned, sort_keys=True)
        return None if same else f"{at} changed from {returned!r} to {sent!r}"

    async def checked(**kwargs: Any) -> Any:
        sent = await serialize(**kwargs)
        if kwargs.get("field") is not None:
            changed = difference(
                json.loads(sent) if isinstance(sent, bytes) else sent,
                jsonable_encoder(kwargs["response_content"]),
            )
            if changed:
                where = kwargs.get("endpoint_ctx") or {}
                raise AssertionError(
                    f"The response model of {where.get('function')} "
                    f"({where.get('file')}:{where.get('line')}) changed its JSON: {changed}"
                )
        return sent

    environment.setattr(routing, "serialize_response", checked)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Include native/launcher cases and count optional cases before deselection."""
    root = Path(__file__).resolve().parents[2]
    native = sum(
        len(re.findall(r"^\s*#\[test\]", file.read_text(), re.MULTILINE))
        for file in (root / "src-tauri/src").rglob("*.rs")
    )
    launcher = sum(
        len(re.findall(r"^test\(", file.read_text(), re.MULTILINE))
        for file in (root / "scripts").glob("*.test.mjs")
    )
    total = len(items) + native + launcher
    if total > CASES:
        raise pytest.UsageError(
            f"Backend test budget exceeded: {total}/{CASES} cases "
            f"({len(items)} Python, {native} native, {launcher} launcher). "
            "Replace redundant coverage before adding cases."
        )
    if config.args_source == pytest.Config.ArgsSource.TESTPATHS:  # The whole suite, not chosen files.
        _budget_report.append(
            (f"Cases: {total} of {CASES} ({len(items)} Python, {native} native, {launcher} launcher).", False)
        )
    # The app runs no command outside the macOS sandbox, so where it is missing these cases cannot
    # run. CI runs the whole suite on macOS too.
    if sys.platform != "darwin":
        missing = pytest.mark.skip(reason="Needs the macOS sandbox")
        for item in items:
            if "sandbox" in item.keywords:
                item.add_marker(missing)


# The test budget (docs/TESTING.md): how many cases, and how long one test and all of them may take
# (summed across workers; a subset can only exceed the total if the whole suite would). Times depend
# on the machine, and a shared CI runner's vary by a third between runs of the same code, so CI
# reports the time limits and a local run fails on them. The case count fails everywhere.
CASES = 240
TEST_SECONDS = 30
SUITE_SECONDS = 240
ENFORCE_TIME = not os.environ.get("CI")
TIME_NOTE = "" if ENFORCE_TIME else " CI reports this; a local run fails on it."
_durations: dict[str, float] = {}
_budget_report: list[tuple[str, bool]] = []


def pytest_runtest_logreport(report):
    _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput") or not _durations:
        return  # xdist workers report to the controller, which applies the budget once.
    total = sum(_durations.values())
    _budget_report.append((f"Test time: {total:.0f} s of the {SUITE_SECONDS} s budget.", False))
    for seconds, nodeid in sorted(((s, n) for n, s in _durations.items() if s > TEST_SECONDS), reverse=True):
        _budget_report.append(
            (
                f"{nodeid} took {seconds:.1f} s, over the {TEST_SECONDS} s budget for one test. "
                f"Speed it up or split it.{TIME_NOTE}",
                ENFORCE_TIME,
            )
        )
    if total > SUITE_SECONDS:
        _budget_report.append(
            (
                f"The tests took {total:.0f} s together, over the {SUITE_SECONDS} s budget. "
                f"Speed up the slowest (--durations=15) before adding more.{TIME_NOTE}",
                ENFORCE_TIME,
            )
        )
    if any(failed for _, failed in _budget_report) and session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    for line, failed in _budget_report:
        terminalreporter.write_line(line, red=failed)


@dataclass
class ServiceStack:
    repository: Repository
    files: ProjectFileStore
    secrets: EnvFileSecretStore
    models: ModelGateway
    runtime: RuntimeManager
    services: Any
    # The one coding agent every task runs as.
    agent: dict


class ScriptedModel(BaseChatModel):
    """Replays fixed assistant messages so a real `create_agent` loop can be driven in tests.

    `GenericFakeChatModel` cannot be used: it streams by splitting message *content*, and a
    tool-calling turn has empty content, which makes it raise "No generations found in stream".
    The last message repeats, so scripts must end with a message that has no tool calls.
    """

    script: list[AIMessage] = Field(default_factory=list)
    # When given, each call's messages are added to it: what a provider would have been sent.
    # Any, not list: validating a list field would copy it.
    seen: Any = None
    _cursor: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _next(self) -> AIMessage:
        message = self.script[min(self._cursor, len(self.script) - 1)]
        self._cursor += 1
        # A new message per call, as a provider sends: LangChain gives each one its call's id.
        return message.model_copy()

    def _record(self, messages) -> None:
        if self.seen is not None:
            self.seen.append(list(messages))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._record(messages)
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._record(messages)
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def bind_tools(self, tools, **kwargs):  # noqa: ARG002 - the script decides, not the tools
        return self


def agent_spec(*tools: ToolBinding, **fields) -> AgentSpecV2:
    """A minimal task configuration with only the given tools."""
    return AgentSpecV2(
        name="Test agent", description="Synthetic", system_prompt="Help.", tools=list(tools), **fields
    )


def tool_call_message(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}]
    )


def script_gateway(gateway: ModelGateway, *scripts: list[AIMessage], seen: list | None = None) -> None:
    """Make `gateway.chat_model` hand out each script in turn, then a plain reply; `seen` collects
    the messages of every model call."""
    remaining = list(scripts)

    def chat_model(_model, **_kwargs):
        script = remaining.pop(0) if remaining else [AIMessage(content="done")]
        return ScriptedModel(script=script, seen=seen)

    gateway.chat_model = chat_model  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _signed_out_of_github(monkeypatch):
    """No test asks the real gh on this Mac for its token or account: commands get GitHub
    credentials only from a test that hands them a synthetic one."""
    from wavefunction import github_cli

    async def signed_out(root=None):
        raise github_cli.SignInRequired("not signed in to GitHub in tests")

    async def no_account(root=None):
        return ""

    monkeypatch.setattr(github_cli, "token", signed_out)
    monkeypatch.setattr(github_cli, "signed_in_account", no_account)
    monkeypatch.setattr(github_cli, "_command_token", None)


@pytest.fixture
async def stack(tmp_path, monkeypatch):
    """The app's own services in an isolated data folder, with its coding workspace."""
    from wavefunction import main
    from wavefunction.config import Settings

    services = main.Services(Settings(data_dir=tmp_path / "data", development_auth_bypass=True))
    monkeypatch.setattr(main, "services", services)
    # What this fixture made is what it closes, whatever a test patches in meanwhile.
    runtime, computer = services.runtime, services.computer
    try:
        yield ServiceStack(
            repository=services.repository,
            files=services.files,
            secrets=services.secrets,
            models=services.models,
            runtime=runtime,
            services=services,
            agent=services.repository.get_agent(services.coding_workspace.agent_id),
        )
    finally:
        await services.finish_optional_warmup()
        tasks = list(runtime.tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.aclose()
        await computer.shutdown()
        await services.coding_workspace.terminals.shutdown()
        await services.github_proxy.close()


@pytest.fixture
def system_tmp():
    """A disposable folder outside the per-user temporary folder, which pytest's `tmp_path` is
    in and sandboxed commands may write. Where a test needs a write to be refused."""
    folder = Path(
        mkdtemp(prefix="wavefunction-test-", dir="/private/tmp" if sys.platform == "darwin" else None)
    )
    yield folder.resolve()
    shutil.rmtree(folder, ignore_errors=True)


# The coding API over isolated services, for the coding test modules. Their plain helpers
# (create, git, repository) are in support.py.


@pytest.fixture
async def coding(tmp_path, monkeypatch):
    import httpx

    from wavefunction import main
    from wavefunction.config import Settings
    from wavefunction.skill_discovery import catalog, discover

    # Global skills come from a synthetic home, never the developer's own.
    monkeypatch.setattr(
        "wavefunction.coding_workspace.discover",
        lambda folder, library, **kwargs: discover(folder, library, home=tmp_path / "home", **kwargs),
    )
    monkeypatch.setattr(
        "wavefunction.coding_workspace.catalog",
        lambda library, projects, **kwargs: catalog(library, projects, home=tmp_path / "home", **kwargs),
    )
    services = main.Services(Settings(data_dir=tmp_path / "data", development_auth_bypass=True))
    monkeypatch.setattr(main, "services", services)
    workspace = services.coding_workspace
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
    yield client, workspace, services
    await client.aclose()
    await services.finish_optional_warmup()
    for run_id, task in list(services.runtime.tasks.items()):
        if not task.done():
            await services.runtime.cancel(run_id)
    await services.runtime.aclose()
    await services.computer.shutdown()
    await workspace.terminals.shutdown()
    await services.github_proxy.close()
