from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import httpx
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from . import notifications
from . import responses as api
from .anthropic_provider import AnthropicCatalog
from .codex import AUTH_FILE_NAME, CodexAuth, CodexAuthError, CodexAuthStore
from .codex import list_models as list_codex_models
from .coding_workspace import CodingWorkspace, register_coding_workspace
from .computer import DesktopManager, DesktopUnavailable, screenshots_root
from .computer_routes import register_computer_control
from .config import Settings, get_settings
from .connectors import (
    ConnectorRegistry,
    McpServer,
    SignInRequired,
    probe_server,
    secret_key,
    secret_names,
    secret_value,
    suggested_name,
)
from .files import ProjectFileStore
from .gemini_provider import GeminiCatalog
from .github_cli import GitHubSignIn
from .github_proxy import GitHubProxy
from .integration_setup import EmailSetup, GoogleSetup
from .local_integrations import Connectable as ConnectableIntegration
from .local_integrations import Kind as LocalIntegrationKind
from .local_integrations import LocalAccount
from .microsoft_graph import MicrosoftSetup, MicrosoftSignIn
from .model_registry import ModelRegistry
from .models import ModelGateway
from .ollama_provider import OllamaConnection, discover_models
from .openrouter import OpenRouterCatalog
from .repository import INTERRUPTED, Repository
from .runtime import RuntimeManager, stream_finished
from .sandbox import GRANTS_SETTING, Grant, holds_app_data, load_grants, scratch_for, self_test
from .schemas import (
    ApprovalDecision,
    ConnectionUpdate,
    EnabledModelsUpdate,
    GrantCreate,
    NotificationSettings,
    SecretUpdate,
    ZeroDataRetentionUpdate,
)
from .secrets import ENV_KEYS, SECRET_FILE_NAME, EnvFileSecretStore
from .spec_common import ModelSpec
from .vercel_gateway import VercelGatewayCatalog

logger = logging.getLogger(__name__)
# What each provider's chats import on first use, warmed at startup for the configured providers.
PROVIDER_MODULES = {
    "openai": "wavefunction.provider_chat",
    "codex": "wavefunction.provider_chat",
    "openrouter": "wavefunction.gateway_chat",
    "vercel": "wavefunction.gateway_chat",
    "openai-compatible": "langchain_openai",
    "anthropic": "langchain_anthropic",
    "gemini": "langchain_google_genai",
    "ollama": "langchain_ollama",
}


class Services:
    """The supported coding application and its shared provider/tool services."""

    def grants(self) -> list[Grant]:
        stored = self.repository.get_setting(GRANTS_SETTING, [])
        return load_grants(stored if isinstance(stored, list) else [])

    def default_model(self) -> ModelSpec:
        return ModelSpec.model_validate(default_model_setting(self.repository))

    def __init__(self, settings: Settings):
        self.settings = settings
        self.workspace_ready = asyncio.Event()
        self.optional_warmup: asyncio.Task[None] | None = None
        self.repository = Repository(settings.database_path, settings.agent_dir)
        self.task_news = notifications.TaskNews()
        self.repository.on_run_event = self.task_news.ring
        self.secrets = EnvFileSecretStore(settings.agent_dir / SECRET_FILE_NAME)
        self.files = ProjectFileStore(settings.data_dir, self.repository)
        self.codex = CodexAuth(CodexAuthStore(settings.agent_dir / AUTH_FILE_NAME))
        self.model_registry = ModelRegistry(settings.data_dir / "models-dev.json")
        self.models = ModelGateway(
            self.secrets,
            codex=self.codex,
            ollama_config=lambda: self.repository.get_setting("ollama", {}),
            capabilities=lambda: self.repository.get_setting("model_capabilities", {}),
            context_lengths=self.model_registry.context_length,
            zero_data_retention=lambda: self.repository.get_setting("require_zdr", False) is True,
        )
        self.openrouter = OpenRouterCatalog(self.secrets)
        self.vercel = VercelGatewayCatalog(self.secrets)
        self.anthropic = AnthropicCatalog(self.secrets)
        self.gemini = GeminiCatalog(self.secrets)
        self.connectors = ConnectorRegistry(self.repository, self.secrets)
        from .catalog_connections import CatalogConnections

        self.catalog_connections = CatalogConnections(self.connectors)
        self.microsoft_graph = MicrosoftSignIn(settings.data_dir / "integrations")
        self.github_sign_in = GitHubSignIn(settings.data_dir / "integrations")
        self.github_proxy = GitHubProxy()
        self.skills = self.repository.skills
        self.computer = DesktopManager(data_dir=settings.data_dir)
        self.coding_workspace = CodingWorkspace(self)
        self.runtime = RuntimeManager(
            self.repository,
            self.models,
            self.files,
            connectors=self.connectors,
            skills=self.skills,
            computer=self.computer,
            coding_workspace=self.coding_workspace,
        )

    async def start_optional_warmup(self) -> None:
        """Called after the first workspace response has been sent, once per service stack."""
        if self.optional_warmup is None:
            # Imports construct SDK schemas and hold the GIL intermittently. Keep them off
            # the API event loop so history and settings requests remain responsive.
            self.optional_warmup = asyncio.create_task(asyncio.to_thread(self._warm_optional_code))
        self.workspace_ready.set()

    def _warm_optional_code(self) -> None:
        # Import only: no clients, credentials, network discovery, or model calls. Ordinary
        # local imports at each use site also work if a user gets there before this finishes,
        # or uses a provider enabled later. Each provider SDK costs tens of MB, so only those
        # of the providers the model picker offers; PDFs load their reader when one is read.
        stored = self.repository.get_settings({"enabled_models": None, "default_model": DEMO_MODEL})
        enabled = stored["enabled_models"] if isinstance(stored["enabled_models"], list) else []
        providers: set[str | None] = {str(key).partition("::")[0] for key in enabled}
        if isinstance(stored["default_model"], dict):
            providers.add(stored["default_model"].get("provider"))
        for module in (
            *sorted(
                {
                    PROVIDER_MODULES[provider]
                    for provider in providers
                    if provider is not None and provider in PROVIDER_MODULES
                }
            ),
            "wavefunction.connector_oauth",
            "langchain_mcp_adapters.client",
        ):
            try:
                import_module(module)
            except Exception:
                logger.exception("Could not warm optional module %s", module)

    async def finish_optional_warmup(self) -> None:
        if self.optional_warmup is not None:
            # Cancelling to_thread would abandon a still-running importer. Join the finite
            # import job during shutdown instead of leaving an unowned worker behind.
            await asyncio.shield(self.optional_warmup)


services = Services(get_settings())


def agent_copies() -> list[str]:
    """Desktop homes to keep: the agent's own and one per chat."""
    copies = services.repository.agent_ids()
    with services.coding_workspace.db() as db:
        copies.extend(row["id"] for row in db.execute("SELECT id FROM coding_threads"))
    return copies


async def secret_is_available(key: str) -> bool:
    """Read the local credential file without blocking the API event loop."""

    return bool(await asyncio.to_thread(services.secrets.get, key))


@asynccontextmanager
async def lifespan(_: FastAPI):
    stale_runs = services.repository.settle_stale_runs()
    if stale_runs:
        logger.info("Settled %d run(s) interrupted by a previous shutdown", stale_runs)

    async def discover_computer() -> None:
        await services.computer.refresh_status()
        await services.computer.sweep_orphans()
        await services.computer.remove_homes_except(agent_copies)

    async def optional_setup(name: str, operation: Any) -> None:
        try:
            await services.workspace_ready.wait()
            await services.finish_optional_warmup()
            await operation()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Optional %s discovery failed", name)

    async def resume_connector_setups() -> None:
        await services.workspace_ready.wait()
        await services.finish_optional_warmup()
        while True:
            try:
                await services.runtime.resume_connected()
            except Exception:
                logger.exception("Could not resume connector setup")
            await asyncio.sleep(2)

    discovery = [
        asyncio.create_task(optional_setup("connector", services.connectors.refresh_all)),
        asyncio.create_task(optional_setup("computer", discover_computer)),
        asyncio.create_task(optional_setup("model registry", services.model_registry.refresh)),
    ]
    connector_resume = asyncio.create_task(resume_connector_setups())
    worktree_cleanup = asyncio.create_task(services.coding_workspace.cleanup.run())
    routines = asyncio.create_task(services.coding_workspace.routines.run())
    try:
        yield
    finally:
        # Stop sources of new work before cancelling runs. The manager's cancel path also
        # settles tasks cancelled before their first step and reaps their command processes.
        for task in (*discovery, connector_resume, worktree_cleanup, routines):
            task.cancel()
        await asyncio.gather(*discovery, connector_resume, worktree_cleanup, routines, return_exceptions=True)
        await services.finish_optional_warmup()
        for run_id, task in list(services.runtime.tasks.items()):
            if not task.done():
                try:
                    await services.runtime.cancel(run_id, reason=INTERRUPTED)
                except Exception:
                    logger.exception("Could not stop %s while shutting down", run_id)
        from .integration_setup import cancel_google

        await cancel_google()
        await services.microsoft_graph.cancel()
        await services.github_sign_in.cancel()
        await services.github_proxy.close()
        await services.codex.cancel_login()
        await services.catalog_connections.close()
        await services.computer.shutdown()
        await services.coding_workspace.terminals.shutdown()
        await services.runtime.aclose()


def _plain_validation_error(error: Exception) -> str:
    """A 422 body a person can read.

    FastAPI's default is a list of `{loc, msg, type}` objects, which the side panel showed raw:
    a wrong middleware option arrived as a screenful of JSON around the one line that mattered.
    """
    parts = []
    for item in getattr(error, "errors", lambda: [])():
        loc = " › ".join(str(piece) for piece in item.get("loc", ()) if piece not in ("body",))
        msg = str(item.get("msg", "")).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "The request was not valid."


app = FastAPI(
    title="Wavefunction Local API",
    description="Local desktop coding agent with independent conversations",
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def plain_validation_errors(_: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": _plain_validation_error(error)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=services.settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def authorize(authorization: Annotated[str | None, Header()] = None) -> None:
    if services.settings.development_auth_bypass:
        return
    expected = services.settings.desktop_token
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid desktop session")


Auth = Annotated[None, Depends(authorize)]

register_computer_control(app, lambda: services, authorize)


def not_found(error: KeyError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error).strip("'"))


@app.get("/api/activity", response_model=api.Activity)
def activity(_: Auth) -> dict[str, bool]:
    # A sync endpoint keeps the package scan off the agent event loop.
    return {"active": services.repository.has_active_work(services.coding_workspace.agent_id)}


@app.get("/health", response_model=api.Health)
async def health() -> dict[str, Any]:
    return {
        "status": "healthy",
        "service": "Wavefunction Local API",
        "version": app.version,
        "data_dir": str(services.settings.data_dir),
    }


DEMO_MODEL = {
    "provider": "demo",
    "model": "demo-reliable",
    "base_url": None,
    "temperature": 0.1,
    "max_tokens": None,
}


def default_model_setting(repository: Repository) -> dict[str, Any]:
    """The model new chats start on, repaired to the first enabled model when it was disabled."""
    stored = repository.get_setting("default_model", DEMO_MODEL)
    enabled = repository.get_setting("enabled_models")
    if not isinstance(enabled, list):
        return stored
    replacement = dict(DEMO_MODEL)
    # The model as saved in Settings, with its own server address and options.
    saved = {
        f"{spec.get('provider')}::{spec.get('model')}": spec
        for spec in repository.get_setting("coding_models", []) or []
        if isinstance(spec, dict)
    }
    # Demo is a first-run placeholder: prefer a valid real model when repairing the default.
    for candidate in sorted(enabled, key=lambda key: str(key).startswith("demo::")):
        provider, _, model = str(candidate).partition("::")
        proposed = (
            dict(saved[candidate])
            if candidate in saved
            else {
                **stored,
                "provider": provider,
                "model": model,
                # A base URL belongs to one provider; carrying it across would point a different
                # provider at the wrong host.
                "base_url": stored.get("base_url") if provider == stored.get("provider") else None,
                "reasoning_effort": stored.get("reasoning_effort")
                if (provider, model) == (stored.get("provider"), stored.get("model"))
                else None,
            }
        )
        if provider == "openai-compatible" and not proposed.get("base_url"):
            continue  # Without its server's address it would be sent to OpenAI.
        try:
            ModelSpec.model_validate(proposed)
        except ValueError:
            continue
        replacement = proposed
        break
    if f"{stored['provider']}::{stored['model']}" in enabled and (
        stored["provider"] != "demo" or replacement["provider"] == "demo"
    ):
        return stored
    repository.set_setting("default_model", replacement)
    return replacement


def skill_row(skill: Any) -> dict[str, Any]:
    return {
        "name": skill.name,
        "description": skill.description,
        "body": skill.body,
        "version": skill.version,
    }


@app.get("/api/skills", response_model=list[api.SkillRecord])
async def list_skills(_: Auth) -> list[dict[str, Any]]:
    """Every skill on this machine."""
    return [skill_row(skill) for skill in services.skills.list()]


@app.post("/api/skills", status_code=201, response_model=api.SkillRecord)
async def create_skill(payload: dict[str, Any], _: Auth) -> dict[str, Any]:
    """Write a new skill.

    The name is derived from the title unless one is given, because a title is what someone
    types and the spec's name rules are not something they should have to know.
    """
    from .skills import normalise_name

    title = str(payload.get("name", "")).strip()
    name = normalise_name(str(payload.get("slug") or title))
    if services.skills.get(name):
        raise HTTPException(status_code=409, detail=f"there is already a skill called {name}")
    try:
        saved = services.skills.save(name, str(payload.get("description", "")), str(payload.get("body", "")))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return skill_row(saved)


@app.put("/api/skills/{name}", response_model=api.SkillRecord)
async def update_skill(name: str, payload: dict[str, Any], _: Auth) -> dict[str, Any]:
    """Change a skill. Renaming is refused: tasks capture a skill by its name."""
    if services.skills.get(name) is None:
        raise HTTPException(status_code=404, detail="skill not found")
    try:
        saved = services.skills.save(name, str(payload.get("description", "")), str(payload.get("body", "")))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return skill_row(saved)


@app.delete("/api/skills/{name}", status_code=204)
async def remove_skill(name: str, _: Auth) -> None:
    """Remove a skill from the library."""
    if services.skills.get(name) is None:
        raise HTTPException(status_code=404, detail="skill not found")
    services.skills.delete(name)


@app.post("/api/local-integrations/{kind}/install", response_model=api.Installed)
async def install_local_integration(kind: LocalIntegrationKind, _: Auth):
    from .integration_setup import install

    try:
        await install(kind, services.settings.data_dir / "integrations")
    except Exception as error:
        raise HTTPException(
            400, "Could not install the tool. Check your internet connection and try again."
        ) from error
    return {"installed": True}


@app.post("/api/local-integrations/himalaya/setup", response_model=api.EmailAccount)
async def setup_local_email(payload: EmailSetup, _: Auth):
    from .integration_setup import setup_email

    try:
        await setup_email(payload, services.settings.data_dir / "integrations")
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error
    return {"account": payload.email}


@app.post("/api/local-integrations/microsoft/sign-in", response_model=api.MicrosoftAttempt)
async def start_microsoft_graph(payload: MicrosoftSetup, _: Auth):
    try:
        return services.microsoft_graph.start(payload)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.get("/api/local-integrations/microsoft/sign-in/{attempt}", response_model=api.MicrosoftSignInState)
async def microsoft_graph_status(attempt: str, _: Auth):
    try:
        return services.microsoft_graph.status(attempt)
    except KeyError:
        raise HTTPException(404, "This Microsoft sign-in is no longer active. Try again.") from None


@app.delete("/api/local-integrations/microsoft/sign-in/{attempt}", response_model=api.Cancelled)
async def cancel_microsoft_graph(attempt: str, _: Auth):
    try:
        await services.microsoft_graph.cancel(attempt)
    except KeyError:
        raise HTTPException(404, "This Microsoft sign-in is no longer active.") from None
    return {"cancelled": True}


@app.post("/api/local-integrations/gh/sign-in", response_model=api.GitHubSignInState)
async def start_github_sign_in(_: Auth):
    """`gh auth login` with GitHub's device flow; the card polls for its one-time code."""
    return services.github_sign_in.start()


@app.get("/api/local-integrations/gh/sign-in", response_model=api.GitHubSignInState)
async def github_sign_in_status(_: Auth):
    return services.github_sign_in.status()


@app.post("/api/local-integrations/gog/sign-in", response_model=api.SignInRunning)
async def start_local_google(payload: GoogleSetup, _: Auth):
    from .integration_setup import start_google

    try:

        def protected_clients():
            return {
                server.args[server.args.index("--client") + 1]
                for server in services.connectors.servers()
                if server.env.get("WAVEFUNCTION_LOCAL_INTEGRATION") == "gog"
                and "--client" in server.args
                and server.args.index("--client") + 1 < len(server.args)
            }

        await start_google(payload, services.settings.data_dir / "integrations", protected_clients)
    except (ValueError, OSError) as error:
        raise HTTPException(400, str(error)) from error
    return {"running": True}


@app.get("/api/local-integrations/gog/credentials", response_model=api.FoundCredentials)
async def downloaded_google_credentials(_: Auth, project_id: str = "") -> dict[str, Any]:
    """Whether Google's console has just saved a Desktop client file to Downloads.

    Its name, never its contents: the form offers it so nobody has to find it in a file chooser.
    """
    from .integration_setup import downloaded_credentials

    found = await asyncio.to_thread(downloaded_credentials, project_id)
    return {"found": found}


@app.get("/api/local-integrations/gog/sign-in", response_model=api.GoogleSignInState)
async def local_google_status(_: Auth):
    from .integration_setup import google_status

    return google_status()


@app.delete("/api/local-integrations/gog/sign-in", response_model=api.SignInRunning)
async def cancel_local_google(_: Auth):
    from .integration_setup import cancel_google

    await cancel_google()
    return {"running": False}


@app.get("/api/local-integrations/{kind}", response_model=api.LocalIntegrationStatus)
async def local_integration_status(kind: LocalIntegrationKind, _: Auth) -> dict[str, Any]:
    from .local_integrations import inspect

    return await inspect(kind, services.settings.data_dir / "integrations")


@app.post("/api/local-integrations/{kind}/connect", status_code=201, response_model=api.SavedConnector)
async def connect_local_integration(
    kind: ConnectableIntegration, payload: LocalAccount, _: Auth
) -> dict[str, Any]:
    from .local_integrations import prepare

    try:
        server = await prepare(kind, payload, services.settings.data_dir / "integrations")
    except (ValueError, OSError, SignInRequired) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if kind in ("himalaya", "microsoft", "gog"):
        existing = next((item for item in services.connectors.servers() if item.name == server.name), None)
        if existing:
            if existing.env.get("WAVEFUNCTION_LOCAL_INTEGRATION") != kind:
                raise HTTPException(409, "This connector name is already in use by another integration.")
            # Reconnecting a saved mailbox should repair it rather than fail as a duplicate.
            services.connectors.save(
                [server if item.name == server.name else item for item in services.connectors.servers()]
            )
            found = await services.connectors.refresh(server)
            return {
                **server.model_dump(mode="json"),
                "connected": found.connected,
                "error": found.error,
                "tools": [{"name": tool.name} for tool in found.tools],
            }
    return await add_connector(server, None)


@app.get("/api/connector-catalog", response_model=list[api.CatalogEntry])
async def connector_catalog(_: Auth):
    from .connector_catalog import CATALOG

    return CATALOG


@app.post("/api/connector-catalog/{entry_id}/connect", response_model=api.CatalogConnecting)
async def connect_catalog_entry(
    entry_id: str, _: Auth, payload: Annotated[dict[str, str] | None, Body()] = None
):
    from .connector_catalog import BY_ID

    payload = payload or {}
    entry = BY_ID.get(entry_id)
    if entry is None:
        raise HTTPException(404, "Connector is not in the catalog")
    if entry_id == "n8n":
        import sys
        from urllib.parse import urlparse

        # n8n shows its API address as the instance URL plus /api/v1; the adapter adds that itself.
        url = payload.get("url", "").strip().rstrip("/").removesuffix("/api/v1")
        key = payload.get("api_key", "").strip()
        existing_n8n = next((item for item in services.connectors.servers() if item.name == "n8n"), None)
        if existing_n8n and existing_n8n.args != ["-m", "wavefunction.n8n_mcp"]:
            raise HTTPException(409, "A custom n8n connector already exists. Open its settings to continue.")
        if existing_n8n and not key and url in {"", existing_n8n.env.get("N8N_BASE_URL", "")}:
            if not existing_n8n.enabled:
                raise HTTPException(409, "Enable the n8n connector in its settings before reconnecting.")
            services.catalog_connections.start(existing_n8n)
            return {"name": "n8n", "auth": "none"}
        if existing_n8n and not key:
            # A new address with the key already saved: checked with that key and saved, rather
            # than reconnecting the old address and calling it connected.
            key = services.secrets.get(secret_key("MCP_N8N_API_KEY")) or ""
        parsed = urlparse(url)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.scheme not in {"http", "https"}
        ):
            raise HTTPException(422, "Enter the base URL of your n8n instance.")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(422, "Use HTTPS for a remote n8n instance.")
        if not key or len(key) > 8192 or any(c in key for c in "\r\n"):
            raise HTTPException(422, "Enter your n8n API key.")
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                response = await client.get(
                    f"{url}/api/v1/workflows", headers={"X-N8N-API-KEY": key}, params={"limit": 1}
                )
                response.raise_for_status()
                if not isinstance(response.json().get("data"), list):
                    raise ValueError("Unexpected response")
        except (httpx.HTTPError, ValueError, AttributeError) as error:
            raise HTTPException(400, "Could not verify n8n. Check the instance URL and API key.") from error
        await services.catalog_connections.cancel("n8n")
        services.secrets.set(secret_key("MCP_N8N_API_KEY"), key)
        server = McpServer(
            name="n8n",
            command=sys.executable,
            args=["-m", "wavefunction.n8n_mcp"],
            env={"N8N_BASE_URL": url, "N8N_API_KEY": "$MCP_N8N_API_KEY"},
        )
        services.connectors.save(
            [item for item in services.connectors.servers() if item.name != "n8n"] + [server]
        )
        services.catalog_connections.start(server)
        return {"name": server.name, "auth": "none"}
    if entry.get("setup") == "api_key":
        existing = next((item for item in services.connectors.servers() if item.name == entry_id), None)
        header = {"Authorization": "$MCP_RENDER_API_KEY"}
        if existing and (
            existing.url != entry["url"]
            or existing.headers != header
            or existing.auth != "none"
            or not existing.enabled
        ):
            raise HTTPException(
                409, "A custom Render connector already exists. Open its settings to continue."
            )
        key = payload.get("api_key", "").strip()
        if not key and not (existing and services.secrets.get(secret_key("MCP_RENDER_API_KEY"))):
            raise HTTPException(422, "Enter your Render API key.")
        if len(key) > 8192 or any(char in key for char in "\r\n"):
            raise HTTPException(422, "Enter a valid single-line Render API key.")
        await services.catalog_connections.cancel(entry_id)
        server = McpServer(name=entry_id, transport="http", url=entry["url"], headers=header)
        if existing and key:
            services.catalog_connections.replace(server, {secret_key("MCP_RENDER_API_KEY"): f"Bearer {key}"})
        else:
            if key:
                services.secrets.set(secret_key("MCP_RENDER_API_KEY"), f"Bearer {key}")
            services.connectors.save(
                [item for item in services.connectors.servers() if item.name != entry_id] + [server]
            )
            services.catalog_connections.start(server)
        return {"name": server.name, "auth": "api_key"}
    if entry.get("setup"):
        from .catalog_setup import prepare, save_setup

        existing = next((item for item in services.connectors.servers() if item.name == entry_id), None)
        if existing and (existing.transport != "http" or existing.auth != "oauth" or not existing.enabled):
            raise HTTPException(
                409,
                "A connector with this name already exists with different settings. Open its settings to continue.",
            )
        try:
            server, setup = prepare(entry, payload, existing, services.secrets)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        await services.catalog_connections.cancel(entry_id)
        if existing:
            from .connector_oauth import setup_key

            changes = {setup_key(entry_id): json.dumps(setup)} if setup is not None else {}
            services.catalog_connections.replace(server, changes)
        else:
            if setup is not None:
                save_setup(entry_id, setup, services.secrets)
            services.connectors.save(
                [item for item in services.connectors.servers() if item.name != entry_id] + [server]
            )
            services.catalog_connections.start(server)
        return {"name": server.name, "auth": server.auth}
    server = McpServer(name=entry_id, transport=entry["transport"], url=entry["url"], auth=entry["auth"])
    existing = next((item for item in services.connectors.servers() if item.name == entry_id), None)
    if existing:
        if existing.url != server.url or existing.auth != server.auth or not existing.enabled:
            raise HTTPException(
                409,
                "A connector with this name already exists with different settings. Open its settings to continue.",
            )
        server = existing
    else:
        services.connectors.save([*services.connectors.servers(), server])
    services.catalog_connections.start(server)
    return {"name": server.name, "auth": server.auth}


@app.get("/api/connector-catalog/connections", response_model=dict[str, api.CatalogConnectionState])
async def catalog_connection_status(_: Auth):
    return services.catalog_connections.results


@app.get("/api/connector-catalog/{entry_id}/setup", response_model=api.CatalogSetup)
async def catalog_setup(entry_id: str, _: Auth):
    from .catalog_setup import DEFAULT_CALLBACK
    from .connector_catalog import BY_ID
    from .connector_oauth import registered_setup

    entry = BY_ID.get(entry_id)
    if entry is None:
        raise HTTPException(404, "Connector is not in the catalog")
    setup = registered_setup(entry_id, services.secrets)
    existing = next((item for item in services.connectors.servers() if item.name == entry_id), None)
    setup_url = existing.url if existing else entry["url"]
    if entry_id == "asana" and setup_url == "https://mcp.asana.com/sse":
        setup_url = entry["url"]
    return {
        "url": setup_url,
        "redirect_uri": setup.get("redirect_uri", entry.get("oauth_callback", DEFAULT_CALLBACK)),
        "client_id": setup.get("client_id", ""),
        "scope": setup.get("scope", entry.get("oauth_default_scope", "")),
        "auth_method": setup.get(
            "auth_method", entry.get("oauth_default_auth_method", "client_secret_basic")
        ),
        "secret_saved": "true"
        if setup.get("client_secret")
        or (entry_id == "render" and services.secrets.get(secret_key("MCP_RENDER_API_KEY")))
        else "false",
    }


@app.delete("/api/connector-catalog/connections/{name}", response_model=api.Cancelled)
async def cancel_catalog_connection(name: str, _: Auth):
    await services.catalog_connections.cancel(name)
    return {"cancelled": True}


@app.get("/api/connectors", response_model=list[api.Connector])
async def list_connectors(_: Auth) -> list[dict[str, Any]]:
    """The MCP servers you have added, and what each is offering.

    Connected services supply tools to the next coding task.
    """
    rows = []
    for server in services.connectors.servers():
        cached = services.connectors.cached(server.name)
        rows.append(
            {
                **server.model_dump(mode="json"),
                "secrets": [
                    {"name": name, "set": bool(secret_value(services.secrets, name))}
                    for name in secret_names(server)
                ],
                "connected": bool(cached and cached.connected),
                "checking": server.name in services.connectors.checking,
                "error": cached.error if cached else "",
                "tools": [
                    {"name": tool.name, "description": (tool.description or "").strip()[:300]}
                    for tool in (cached.tools if cached and cached.connected else [])
                ],
            }
        )
    return rows


@app.post("/api/connectors", status_code=201, response_model=api.SavedConnector)
async def add_connector(payload: McpServer, _: Auth) -> dict[str, Any]:
    """Add a server and ask it what it offers, so a failure is visible now rather than at run."""
    servers = services.connectors.servers()
    if any(existing.name == payload.name for existing in servers):
        raise HTTPException(status_code=409, detail=f"{payload.name} is already connected")
    services.connectors.save([*servers, payload])
    found = await services.connectors.refresh(payload)
    return {
        **payload.model_dump(mode="json"),
        "connected": found.connected,
        "error": found.error,
        "tools": [{"name": tool.name} for tool in found.tools],
    }


@app.post("/api/connectors/probe", response_model=api.ConnectorProbe)
async def probe_connector(payload: dict[str, Any], _: Auth) -> dict[str, Any]:
    """Ask a URL what it is and what signing in to it takes.

    Someone adding a connector knows a URL. Whether it speaks streamable HTTP or SSE, whether the
    path is /mcp or /sse, and whether it wants a token or a browser round trip are all things the
    server will say if it is asked — so they are asked here rather than put to the user as
    settings they would have to look up.
    """
    url = str(payload.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=422, detail="type the server's address first")
    found = await probe_server(url)
    taken = {server.name for server in services.connectors.servers()}
    name = suggested_name(found["url"])
    # Names are how tools are addressed, so a second Notion has to be told from the first.
    if name in taken:
        name = next(f"{name}-{n}" for n in range(2, 100) if f"{name}-{n}" not in taken)
    return {**found, "name": name}


@app.put("/api/connectors/secrets/{name}", status_code=204)
async def set_connector_secret(name: str, payload: dict[str, Any], _: Auth) -> None:
    """Save the value behind a `$NAME` written in a connector's settings.

    Kept apart from the settings themselves so the token lives in the credentials file rather
    than in the connector list, which is plain JSON in the app's own settings.
    """
    value = str(payload.get("value", "")).strip()
    if not value:
        raise HTTPException(status_code=422, detail="a key needs a value")
    services.secrets.set(secret_key(name), value)
    await refresh_connectors_using(name)


@app.delete("/api/connectors/secrets/{name}", status_code=204)
async def clear_connector_secret(name: str, _: Auth) -> None:
    services.secrets.delete(secret_key(name))
    await refresh_connectors_using(name)


async def refresh_connectors_using(name: str) -> None:
    """Connectors whose settings name this key were connected with its old value, which their
    tools keep: connect them again, replacing those tools as a settings change does, so a
    cleared key stops working (also for a task already holding them) and a new one is used."""
    connectors = services.connectors
    await asyncio.gather(
        *(
            connectors.refresh(server, replace=True)
            for server in connectors.servers()
            if server.enabled and name in secret_names(server)
        )
    )


@app.put("/api/connectors/{name}", response_model=api.SavedConnector)
async def update_connector(name: str, payload: McpServer, _: Auth) -> dict[str, Any]:
    """Change a server's settings and try it again straight away.

    Renaming is refused rather than handled: agents bind a tool by `server__tool`, and a rename
    would quietly break every one of them. Remove and re-add is the honest way to do that.
    """
    servers = services.connectors.servers()
    if not any(server.name == name for server in servers):
        raise HTTPException(status_code=404, detail="connector not found")
    if payload.name != name:
        raise HTTPException(status_code=409, detail="a connector cannot be renamed")
    await services.catalog_connections.cancel(name)
    services.catalog_connections.results.pop(name, None)
    services.connectors.save([payload if s.name == name else s for s in servers])
    found = await services.connectors.refresh(payload)
    return {
        **payload.model_dump(mode="json"),
        "connected": found.connected,
        "error": found.error,
        "tools": [{"name": tool.name} for tool in found.tools],
    }


@app.post("/api/connectors/{name}/refresh", response_model=api.ConnectorCheck)
async def refresh_connector(name: str, _: Auth) -> dict[str, Any]:
    """Ask a server what it offers now. Its tools change when the server does."""
    server = next((s for s in services.connectors.servers() if s.name == name), None)
    if server is None:
        raise HTTPException(status_code=404, detail="connector not found")
    found = await services.connectors.refresh(server)
    return {
        "connected": found.connected,
        "error": found.error,
        "tools": [{"name": tool.name} for tool in found.tools],
    }


@app.post("/api/connectors/{name}/sign-in", response_model=api.ConnectorCheck)
async def sign_in_connector(name: str, _: Auth) -> dict[str, Any]:
    """Open a browser and complete this server's OAuth flow.

    Blocks while the user is signing in — the round trip ends when they come back, which is the
    honest shape for something that waits on a person. Tokens land in the secret store.
    """
    server = next((s for s in services.connectors.servers() if s.name == name), None)
    if server is None:
        raise HTTPException(status_code=404, detail="connector not found")
    if not server.remote:
        raise HTTPException(status_code=409, detail="only a URL connector can sign in")
    found = await services.connectors.sign_in(server)
    return {
        "connected": found.connected,
        "error": found.error,
        "tools": [{"name": tool.name} for tool in found.tools],
    }


@app.post("/api/connectors/{name}/sign-out", status_code=204)
async def sign_out_connector(name: str, _: Auth) -> None:
    """Forget this server's tokens. The next sign-in starts the flow again."""
    server = next((s for s in services.connectors.servers() if s.name == name), None)
    if server is None:
        raise HTTPException(status_code=404, detail="connector not found")
    await services.catalog_connections.cancel(name)
    services.catalog_connections.results.pop(name, None)
    services.connectors.sign_out(server)


@app.delete("/api/connectors/{name}", status_code=204)
async def remove_connector(name: str, _: Auth) -> None:
    """Remove a server and its tools."""
    servers = services.connectors.servers()
    if not any(server.name == name for server in servers):
        raise HTTPException(status_code=404, detail="connector not found")
    await services.catalog_connections.cancel(name)
    services.catalog_connections.results.pop(name, None)
    from .connector_oauth import setup_key

    services.connectors.sign_out(next(server for server in servers if server.name == name))
    if services.secrets.get(setup_key(name)):
        services.secrets.delete(setup_key(name))
    managed_key = {"render": "MCP_RENDER_API_KEY", "n8n": "MCP_N8N_API_KEY"}.get(name)
    if managed_key and services.secrets.get(secret_key(managed_key)):
        services.secrets.delete(secret_key(managed_key))
    services.connectors.save([server for server in servers if server.name != name])
    removed = next(server for server in servers if server.name == name)
    if removed.env.get("WAVEFUNCTION_LOCAL_INTEGRATION") == "gog":
        from .integration_setup import google_clients, remove_google_client

        def argument(server, flag):
            index = server.args.index(flag) + 1 if flag in server.args else len(server.args)
            return server.args[index] if index < len(server.args) else ""

        client_name = argument(removed, "--client")
        root = services.settings.data_dir / "integrations"
        # The gog store is this app's own.
        if (
            client_name.startswith("wavefunction-")
            and client_name not in google_clients(root).values()
            and not any(
                argument(server, "--client") == client_name for server in services.connectors.servers()
            )
        ):
            await remove_google_client(root, client_name, argument(removed, "--account"))


def _run_response(run: dict[str, Any]) -> dict[str, Any]:
    """Saved execution specs are internal; ordinary run responses need metadata only."""
    return {key: value for key, value in run.items() if key != "spec"}


@app.get("/api/runs/{run_id}", response_model=api.RunWithEvents)
async def get_run(run_id: str, _: Auth) -> dict[str, Any]:
    try:
        run = cast(dict[str, Any], services.repository.get_run(run_id))
        run["events"] = services.repository.list_run_events(run_id)
        return _run_response(run)
    except KeyError as error:
        raise not_found(error) from error


@app.get("/api/runs/{run_id}/connectors", response_model=api.RunSetup)
async def run_connector_setup(run_id: str, _: Auth) -> dict[str, Any]:
    try:
        setup = services.runtime.connector_setup(run_id)
        for row in setup["connectors"]:
            row["signing_in"] = (
                row["auth"] == "oauth"
                and services.catalog_connections.results.get(row["id"], {}).get("state") == "connecting"
            )
        return setup
    except KeyError as error:
        raise not_found(error) from error


@app.post("/api/runs/{run_id}/connectors/github", response_model=api.GitHubAccount)
async def run_github_signed_in(run_id: str, _: Auth) -> dict[str, str]:
    """The GitHub card finished signing gh in: the task waiting on it carries on."""
    try:
        return {"account": await services.runtime.github_signed_in(run_id)}
    except KeyError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@app.post("/api/runs/{run_id}/connectors/folder", response_model=api.FolderChosen)
async def choose_run_folder(run_id: str, payload: dict[str, str], _: Auth) -> dict[str, bool]:
    """The folder the user picked on a folder card: where the agent works, or one a task needs."""
    try:
        path = payload.get("path", "")
        destination = str(Path(path).expanduser().resolve())
        async with services.coding_workspace.reviews.locks.setdefault(destination, asyncio.Lock()):
            if not Path(destination).is_dir():
                raise ValueError("That folder does not exist.")
            services.runtime.choose_folder(run_id, path, payload.get("setup_id"))
    except KeyError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    return {"chosen": True}


@app.get("/api/runs/{run_id}/events")
async def run_events(request: Request, run_id: str, _: Auth, after: int = 0) -> EventSourceResponse:
    try:
        services.repository.get_run(run_id)
    except KeyError as error:
        raise not_found(error) from error

    async def event_stream() -> AsyncGenerator[dict[str, str], None]:
        cursor = after
        while not await request.is_disconnected():
            events = services.repository.list_run_events(run_id, cursor)
            for event in events:
                cursor = event["sequence"]
                yield {"event": event["event_type"], "id": str(cursor), "data": json.dumps(event)}
            run = services.repository.get_run(run_id)
            if stream_finished(run["status"]):
                if not events:
                    break
                continue  # Read what is left, then close; nothing more is coming to wait for.
            await services.runtime.changed(run_id)

    return EventSourceResponse(event_stream(), ping=15)


@app.post("/api/runs/{run_id}/approval", status_code=202, response_model=api.Run)
async def decide_approval(run_id: str, payload: ApprovalDecision, _: Auth) -> dict[str, Any]:
    try:
        return _run_response(
            await services.runtime.resume(
                run_id, payload.decision, payload.feedback, expected_approval=payload.expected_approval
            )
        )
    except KeyError as error:
        raise not_found(error) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/runs/{run_id}/cancel", response_model=api.Run)
async def cancel_run(run_id: str, _: Auth) -> dict[str, Any]:
    try:
        return _run_response(await services.runtime.cancel(run_id))
    except KeyError as error:
        raise not_found(error) from error


def _stored_grants() -> list[dict[str, Any]]:
    stored = services.repository.get_setting(GRANTS_SETTING, [])
    return stored if isinstance(stored, list) else []


@app.get("/api/grants", response_model=api.GrantState)
async def list_grants(_: Auth) -> dict[str, Any]:
    reason = await self_test(scratch_for(services.settings.agent_dir))
    return {
        "grants": _stored_grants(),
        # Surfaced rather than logged: if the boundary is not working the user is entitled to
        # know before they hand anything access, and the app should say so plainly.
        "sandbox_ok": reason is None,
        "sandbox_problem": reason,
    }


@app.post("/api/grants", status_code=201, response_model=api.Grants)
async def add_grant(payload: GrantCreate, _: Auth) -> dict[str, Any]:
    resolved = Path(payload.path).expanduser().resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="that folder does not exist")
    # The data folder holds the chat history, skills, the databases and the credentials file.
    # Opening any of it up would let a command erase the work it is supposed to be helping with.
    if holds_app_data(resolved, services.settings.data_dir):
        raise HTTPException(
            status_code=400, detail="that folder holds the app's own data, so it cannot be granted"
        )

    grants = [item for item in _stored_grants() if item.get("path") != str(resolved)]
    grants.append({"path": str(resolved), "writable": payload.writable})
    services.repository.set_setting(GRANTS_SETTING, grants)
    return {"grants": grants}


@app.delete("/api/grants", status_code=200, response_model=api.Grants)
async def revoke_grant(path: str, _: Auth) -> dict[str, Any]:
    resolved = str(Path(path).expanduser().resolve())
    grants = [item for item in _stored_grants() if item.get("path") != resolved]
    services.repository.set_setting(GRANTS_SETTING, grants)
    return {"grants": grants}


@app.get("/api/computer", response_model=api.ComputerStatus)
async def computer_status(_: Auth, refresh: bool = False) -> dict[str, Any]:
    """Whether computer use can run: Docker installed, engine running, image built.

    `refresh=true` probes Docker; without it the cached status is returned, because the
    settings page polls this while a build streams and a probe per poll would be noise.
    """
    if refresh:
        await services.computer.refresh_status()
    return services.computer.status_cached().as_dict()


@app.post("/api/computer/build", status_code=202, response_model=api.ComputerBuild)
async def build_computer_image(_: Auth) -> dict[str, Any]:
    """Start building the desktop image. Follow progress by polling GET /api/computer."""
    status = await services.computer.refresh_status()
    if status.docker_action:
        raise HTTPException(status_code=409, detail="Wait for Docker to finish starting or restarting.")
    if not status.docker_available:
        raise HTTPException(status_code=400, detail="Docker is not installed on this machine.")
    if status.docker_problem:
        raise HTTPException(status_code=400, detail=status.docker_problem)
    started = services.computer.start_build()
    return {"started": started, "building": True}


@app.post("/api/computer/docker/{action}", response_model=api.ComputerStatus)
async def manage_computer_docker(action: Literal["start", "restart"], _: Auth) -> dict[str, Any]:
    await services.computer.refresh_status()
    try:
        return (await services.computer.manage_docker(action)).as_dict()
    except DesktopUnavailable as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/agents/{agent_id}/screenshots/{conversation_id}/{filename}")
async def get_screenshot(agent_id: str, conversation_id: str, filename: str, _: Auth) -> FileResponse:
    """Serve one stored screenshot to the trace view.

    The webview's CSP allows only blob and data images, so the frontend fetches this with the
    bearer token and displays the blob — the file itself never gets a public URL.
    """
    root = screenshots_root(services.files, agent_id).resolve()
    path = (root / conversation_id / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="that is not a screenshot path") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no such screenshot")
    return FileResponse(path, media_type="image/png")


@app.get("/api/connections", response_model=api.Connections)
async def get_connections(_: Auth) -> dict[str, Any]:
    default_model = default_model_setting(services.repository)
    default_model_key = f"{default_model['provider']}::{default_model['model']}"
    enabled_models = services.repository.get_setting("enabled_models")
    if not isinstance(enabled_models, list):
        enabled_models = [default_model_key]
    openai_key, openrouter_key, vercel_key, anthropic_key, gemini_key, tavily_key = await asyncio.gather(
        secret_is_available("openai_api_key"),
        secret_is_available("openrouter_api_key"),
        secret_is_available("vercel_api_key"),
        secret_is_available("anthropic_api_key"),
        secret_is_available("gemini_api_key"),
        secret_is_available("tavily_api_key"),
    )
    codex_models = await _codex_catalogue()
    return {
        "model": default_model,
        "enabled_models": enabled_models,
        "coding_models": services.repository.get_setting("coding_models", []),
        "secrets": {
            "openai_api_key": openai_key,
            "openrouter_api_key": openrouter_key,
            "vercel_api_key": vercel_key,
            "anthropic_api_key": anthropic_key,
            "gemini_api_key": gemini_key,
            "tavily_api_key": tavily_key,
        },
        # Not a secret in the `secrets` sense — nothing was pasted — so it travels as its own
        # block with the account it belongs to.
        "codex": services.codex.status(),
        "codex_models": services.model_registry.dated("codex", codex_models),
        "ollama": services.repository.get_setting("ollama", None),
        "require_zdr": services.repository.get_setting("require_zdr", False) is True,
    }


@app.put("/api/connections/zdr", response_model=api.ZeroDataRetention)
async def set_zero_data_retention(payload: ZeroDataRetentionUpdate, _: Auth) -> dict[str, bool]:
    """Require zero data retention from OpenRouter and the Vercel AI Gateway, for chats started after."""
    services.repository.set_setting("require_zdr", payload.enabled)
    return {"require_zdr": payload.enabled}


@app.get("/api/notifications/settings", response_model=NotificationSettings)
async def get_notification_settings(_: Auth) -> dict[str, bool]:
    return notifications.settings(services.repository)


@app.put("/api/notifications/settings", response_model=NotificationSettings)
async def set_notification_settings(payload: NotificationSettings, _: Auth) -> dict[str, bool]:
    services.repository.set_setting("notifications", payload.model_dump())
    return notifications.settings(services.repository)


@app.get("/api/notifications/pending", response_model=api.PendingNotifications)
async def pending_notifications(
    _: Auth,
    after: str | None = None,
    visible: str | None = None,
    wait: Annotated[float, Query(ge=0, le=30)] = 0,
) -> dict[str, Any]:
    """What the desktop shell shows next. It asks again with the cursor this returned, waiting up to
    `wait` seconds for a task to end or pause when there is nothing yet."""
    return await notifications.wait_pending(
        services.coding_workspace, services.task_news, after, visible, wait
    )


async def _codex_catalogue() -> list[dict[str, Any]]:
    models = await list_codex_models(services.codex)
    if models:
        known = services.repository.get_setting("model_capabilities", {})
        services.repository.set_setting(
            "model_capabilities", {**known, "codex": {m["id"]: m for m in models}}
        )
    return models


@app.post("/api/models/reasoning-options", response_model=api.ReasoningOptions)
async def model_reasoning_options(payload: ModelSpec, _: Auth) -> dict[str, Any]:
    """Use the same capability information as model construction, without making a model call."""
    return {"efforts": services.models.reasoning_options(payload)}


@app.put("/api/connections/ollama", response_model=api.OllamaConfig)
async def connect_ollama(payload: OllamaConnection, _: Auth) -> dict[str, Any]:
    try:
        async with asyncio.timeout(40):
            models = await discover_models(payload.base_url)
    except (httpx.HTTPError, ValueError, TypeError, AttributeError, TimeoutError) as error:
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not read models from Ollama. Make sure Ollama is running and check the server address."
            ),
        ) from error
    config = {**payload.model_dump(), "models": models, "connected": True}
    services.repository.set_setting("ollama", config)
    return config


@app.get("/api/vercel/models", response_model=api.GatewayModels)
async def list_vercel_models(_: Auth) -> dict[str, Any]:
    """What the Vercel AI Gateway offers.

    Listed without a key, unlike OpenRouter's: the catalogue is public, so someone can see that
    the model they want is reachable before deciding to connect.
    """
    try:
        models = await services.vercel.list_models()
        known = services.repository.get_setting("model_capabilities", {})
        services.repository.set_setting(
            "model_capabilities", {**known, "vercel": {m["id"]: m for m in models}}
        )
        return {"models": services.model_registry.dated("vercel", models)}
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {401, 403}:
            raise HTTPException(status_code=409, detail="The Vercel AI Gateway key was rejected.") from error
        raise HTTPException(status_code=502, detail="The Vercel AI Gateway is unreachable.") from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="The Vercel AI Gateway is unreachable.") from error


@app.get("/api/anthropic/models", response_model=api.AnthropicModels)
async def list_anthropic_models(_: Auth) -> dict[str, Any]:
    """The Claude models the saved Anthropic key can use, with what each request may send."""
    from anthropic import APIError

    try:
        models = await services.anthropic.list_models()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except APIError as error:
        raise _anthropic_http_error(error) from error
    known = services.repository.get_setting("model_capabilities", {})
    services.repository.set_setting(
        "model_capabilities", {**known, "anthropic": {m["id"]: m for m in models}}
    )
    return {"models": services.model_registry.dated("anthropic", models)}


def _anthropic_http_error(error: Exception) -> HTTPException:
    from anthropic import APIStatusError

    if isinstance(error, APIStatusError) and error.status_code in {401, 403}:
        return HTTPException(status_code=401, detail="Anthropic rejected this API key")
    return HTTPException(status_code=502, detail="Could not reach Anthropic")


@app.get("/api/gemini/models", response_model=api.GeminiModels)
async def list_gemini_models(_: Auth) -> dict[str, Any]:
    """The Gemini models the saved key can use for chat."""
    from google.genai.errors import APIError

    try:
        models = await services.gemini.list_models()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (APIError, httpx.HTTPError) as error:
        raise _gemini_http_error(error) from error
    known = services.repository.get_setting("model_capabilities", {})
    services.repository.set_setting("model_capabilities", {**known, "gemini": {m["id"]: m for m in models}})
    return {"models": services.model_registry.dated("gemini", models)}


def _gemini_http_error(error: Exception) -> HTTPException:
    from google.genai.errors import APIError

    # Google answers a bad key with 400 API_KEY_INVALID, not 401.
    if isinstance(error, APIError) and (error.code in {401, 403} or "API_KEY_INVALID" in str(error)):
        return HTTPException(status_code=401, detail="Google rejected this Gemini API key")
    return HTTPException(status_code=502, detail="Could not reach the Gemini API")


@app.get("/api/openrouter/models", response_model=api.GatewayModels)
async def list_openrouter_models(_: Auth) -> dict[str, Any]:
    try:
        models = await services.openrouter.list_models()
        known = services.repository.get_setting("model_capabilities", {})
        services.repository.set_setting(
            "model_capabilities", {**known, "openrouter": {m["id"]: m for m in models}}
        )
        return {"models": services.model_registry.dated("openrouter", models)}
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {401, 403}:
            detail = "OpenRouter rejected this API key"
            status_code = 401
        else:
            detail = "OpenRouter could not return the available models"
            status_code = 502
        raise HTTPException(status_code=status_code, detail=detail) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="Could not connect to OpenRouter") from error


@app.post("/api/codex/login", status_code=202, response_model=api.CodexLogin)
async def codex_login(_: Auth) -> dict[str, Any]:
    """Begin a ChatGPT sign-in and return the URL to open.

    The listener is up before this answers, so the browser cannot beat it.
    """
    try:
        url = await services.codex.begin_login()
    except CodexAuthError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"url": url, "status": services.codex.status()}


@app.post("/api/codex/login/cancel", status_code=204)
async def codex_cancel_login(_: Auth) -> None:
    await services.codex.cancel_login()


@app.post("/api/codex/logout", status_code=204)
async def codex_logout(_: Auth) -> None:
    """Forget the sign-in. Chats on a `codex` model stop running until one is signed in again."""
    await services.codex.cancel_login()
    try:
        services.codex.logout()
    except CodexAuthError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _enable_model(model: dict[str, Any]) -> None:
    """A chosen model belongs in the enabled list, or the picker it was chosen from loses it.

    Before the list is first saved it exists only as the default's key, the way
    `get_connections` presents it; adding to that starts the stored list from the same place.
    """
    enabled_models = services.repository.get_setting("enabled_models")
    if not isinstance(enabled_models, list):
        default = default_model_setting(services.repository)
        enabled_models = [f"{default['provider']}::{default['model']}"]
    model_key = f"{model['provider']}::{model['model']}"
    if model_key not in enabled_models:
        services.repository.set_setting("enabled_models", [*enabled_models, model_key])


def _validate_model_reasoning(model: ModelSpec) -> None:
    try:
        services.models.validate_reasoning(model)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.put("/api/connections/default-model", response_model=api.DefaultModel)
async def update_default_model(payload: ConnectionUpdate, _: Auth) -> dict[str, Any]:
    """Set the model new chats start on."""
    _validate_model_reasoning(payload.model)
    model = payload.model.model_dump(mode="json")
    services.repository.set_setting("default_model", model)
    _enable_model(model)
    return {"model": model}


@app.put("/api/connections/enabled-models", response_model=api.EnabledModels)
async def update_enabled_models(payload: EnabledModelsUpdate, _: Auth) -> dict[str, Any]:
    models = list(dict.fromkeys(model.strip() for model in payload.models if model.strip() and "::" in model))
    services.repository.set_setting("enabled_models", models)
    # Turning off the default moves the default; the reply carries where it went so the
    # Settings page can show it without another round trip.
    return {"models": models, "model": default_model_setting(services.repository)}


@app.put("/api/connections/secrets/{secret_key}", status_code=204)
async def update_secret(secret_key: str, payload: SecretUpdate, _: Auth) -> None:
    try:
        if secret_key == "openrouter_api_key":
            await services.openrouter.list_models(api_key=payload.value)
        if secret_key == "anthropic_api_key":
            from anthropic import APIError

            try:
                await services.anthropic.list_models(api_key=payload.value)
            except APIError as error:
                raise _anthropic_http_error(error) from error
        if secret_key == "gemini_api_key":
            from google.genai.errors import APIError as GoogleAPIError

            try:
                await services.gemini.list_models(api_key=payload.value)
            except (GoogleAPIError, httpx.HTTPError) as error:
                raise _gemini_http_error(error) from error
        services.secrets.set(secret_key, payload.value)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {401, 403}:
            raise HTTPException(status_code=401, detail="OpenRouter rejected this API key") from error
        raise HTTPException(status_code=502, detail="OpenRouter could not validate this API key") from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail="Could not connect to OpenRouter") from error


@app.delete("/api/connections/secrets/{secret_key}", status_code=204)
async def delete_secret(secret_key: str, _: Auth) -> None:
    """Forget a provider or search key. As with signing out of ChatGPT, its models stay enabled
    and run again once a key is back; until then a chat on one says which key to connect."""
    if secret_key not in ENV_KEYS:
        raise HTTPException(status_code=404, detail="Unknown key")
    # The app's environment wins over the saved file, so a key set there would stay in use. Say so
    # and keep the saved one, which is what applies once the variable is gone.
    if os.getenv(ENV_KEYS[secret_key]):
        raise HTTPException(
            status_code=409,
            detail=f"{ENV_KEYS[secret_key]} is set in the environment "
            "Wavefunction was started from. Remove it there to disconnect.",
        )
    await asyncio.to_thread(services.secrets.delete, secret_key)


# Coding routes share the provider, connector, skill and run services above.
register_coding_workspace(app, lambda: services, authorize)
