"""Connector availability and authentication pauses for running tasks."""

from __future__ import annotations

import json
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import interrupt

from .connector_catalog import CATALOG
from .connectors import ConnectorRegistry, needs_sign_in
from .execution_policy import surface_tool_error

# The card a task shows while it waits for the user to sign gh in to GitHub; ready once the card
# has seen gh signed in (RuntimeManager.github_signed_in).
GITHUB_SETUP_ROW = {
    "id": "github",
    "title": "GitHub",
    "catalog_id": None,
    "local_kind": "github",
    "auth": "none",
    "state": "setup",
    "error": "",
    "tool_count": 0,
}


def build_configure_github():
    @tool
    async def configure_github() -> str:
        """Sign gh in to GitHub, for git and gh in commands, when gh is not signed in.

        Pauses the request for the GitHub sign-in card, then resumes. Never ask for a password or
        token in chat. Signing in does not change repositories.
        """
        from .github_cli import signed_in_account

        account = await signed_in_account()
        if not account:
            interrupt({"kind": "connector_setup", "connectors": [dict(GITHUB_SETUP_ROW)]})
            account = await signed_in_account()
        if not account:
            return json.dumps(
                {"github": "not signed in", "next": "Tell the user GitHub sign-in did not finish."}
            )
        return json.dumps(
            {
                "github": f"signed in as {account}",
                "next": "git and gh in commands use this sign-in now. Continue the original request with them.",
            }
        )

    configure_github.metadata = {"readOnlyHint": True}
    return configure_github


class ConnectorAuthMiddleware(AgentMiddleware):
    """Pause after recording an expired-auth result; never replay an action on resume."""

    def __init__(self, registry: ConnectorRegistry):
        self.registry = registry

    @staticmethod
    def _failure(error, request):
        server = getattr(request.tool, "server", "")
        if not server or not needs_sign_in(error):
            raise error
        return ToolMessage(
            content=surface_tool_error(error, request),
            status="error",
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            artifact={"connector_auth_required": server},
        )

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except Exception as error:
            return self._failure(error, request)

    async def abefore_model(self, state, runtime):
        failed = []
        for message in reversed(state.get("messages", [])):
            if isinstance(message, AIMessage):
                break
            if (
                isinstance(message, ToolMessage)
                and isinstance(message.artifact, dict)
                and message.artifact.get("connector_auth_required")
            ):
                failed.append(message)
        if not failed:
            return None
        names = {m.artifact["connector_auth_required"] for m in failed}
        interrupt(
            {
                "kind": "connector_setup",
                "connectors": [connector_status(self.registry, name) for name in sorted(names)],
            }
        )
        # Executed only after sign-in. Keep the failed call and all other successful work;
        # let the model verify uncertain actions before deciding whether a retry is safe.
        return {
            "messages": [
                m.model_copy(
                    update={
                        "artifact": None,
                        "content": str(m.content)
                        + "\nConnector setup is now complete. Continue the original request. Verify any uncertain action before retrying it.",
                    }
                )
                for m in failed
            ]
        }


def connector_status(registry: ConnectorRegistry | None, name: str) -> dict[str, Any]:
    entry = next((entry for entry in CATALOG if entry["id"] == name), None)
    server = (
        next((server for server in registry.servers() if server.name == name), None) if registry else None
    )
    found = registry.cached(name) if registry else None
    row = {
        "id": name,
        "title": entry["title"] if entry else name,
        "catalog_id": entry["id"] if entry else None,
        "auth": server.auth if server else (entry or {}).get("auth", "none"),
        "state": "setup",
        "error": "",
        "tool_count": 0,
    }
    if server and server.env.get("WAVEFUNCTION_LOCAL_INTEGRATION") == "himalaya":
        account = server.args[server.args.index("--account") + 1] if "--account" in server.args else ""
        row.update(
            local_kind="himalaya",
            account=account,
            allow_write="--allow-write" in server.args,
            title=f"Email · {account}",
        )
    if server and server.env.get("WAVEFUNCTION_LOCAL_INTEGRATION") == "microsoft":
        account = server.args[server.args.index("--account") + 1]
        mode = server.args[server.args.index("--mode") + 1]
        selected = (
            server.args[server.args.index("--services") + 1].split(",")
            if "--services" in server.args
            else (["mail"] if mode == "mail" else ["mail", "files", "calendar"])
        )
        row.update(
            services=selected,
            local_kind="microsoft",
            account=account,
            mode=mode,
            allow_write="--allow-write" in server.args,
            title=f"{'Outlook' if mode == 'mail' else 'Microsoft 365'} · {account}",
        )
    if server and server.env.get("WAVEFUNCTION_LOCAL_INTEGRATION") == "gog":
        account = server.args[server.args.index("--account") + 1] if "--account" in server.args else ""
        selected = (
            server.args[server.args.index("--allow-tool") + 1].split(",")
            if "--allow-tool" in server.args
            else []
        )
        row.update(
            services=selected,
            local_kind="gog",
            account=account,
            mode="workspace",
            allow_write="--allow-write" in server.args,
            title=f"Google · {account}",
        )
    if server and entry and not entry.get("setup") and not entry.get("local") and server.url != entry["url"]:
        row["catalog_id"] = None
    if server is None:
        return row
    if not server.enabled:
        return {**row, "state": "disabled", "error": "This connector is disabled."}
    if name in registry.checking:
        return {**row, "state": "connecting"}
    if found is None:
        return {**row, "state": "sign_in" if server.auth == "oauth" else "error"}
    if found.error:
        return {**row, "state": "sign_in" if found.needs_auth else "error", "error": found.error}
    if not found.tools:
        return {
            **row,
            "state": "error",
            "error": "The service returned no tools. Check its configuration and retry.",
        }
    return {**row, "state": "ready", "tool_count": len(found.tools)}
