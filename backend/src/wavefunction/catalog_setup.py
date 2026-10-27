"""Validated catalog setup. OAuth client secrets never enter connector settings."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from .connector_oauth import registered_setup, setup_key
from .connectors import McpServer

DEFAULT_CALLBACK = "http://127.0.0.1:43827/callback"


def prepare(entry: dict, payload: dict[str, str], existing: McpServer | None, secrets):
    url = payload.get("url", existing.url if existing else entry["url"]).strip().rstrip("/")
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Enter a complete HTTPS MCP server URL without credentials, query parameters, or a fragment."
        )
    if entry["id"] == "hex" and (not parsed.hostname.endswith(".hex.tech") or parsed.path != "/mcp"):
        raise ValueError("Enter your Hex MCP URL, for example https://eu.hex.tech/mcp.")
    setup = None
    if entry.get("setup") == "registered_oauth":
        if entry["id"] == "snowflake" and not re.fullmatch(
            r"/api/v2/databases/[^/]+/schemas/[^/]+/mcp-servers/[^/]+", parsed.path
        ):
            raise ValueError(
                "Enter the full Snowflake MCP server URL, including database, schema, and server name."
            )
        if entry["id"] in {"slack", "hubspot", "asana"} and url != entry["url"].rstrip("/"):
            raise ValueError(f"Use the official {entry['title']} MCP server URL shown in the catalog.")
        if entry["id"] == "salesforce" and (
            parsed.hostname != "api.salesforce.com"
            or not re.fullmatch(r"/platform/mcp/v1/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+){0,2}", parsed.path)
        ):
            raise ValueError(
                "Enter the full Salesforce hosted MCP URL ending in /platform/mcp/v1/SERVER-NAME."
            )
        previous = registered_setup(entry["id"], secrets)
        setup = {
            key: payload.get(key, previous.get(key, default)).strip()
            for key, default in {
                "client_id": "",
                "redirect_uri": entry.get("oauth_callback", DEFAULT_CALLBACK),
                "scope": entry.get("oauth_default_scope", ""),
                "auth_method": entry.get("oauth_default_auth_method", "client_secret_basic"),
            }.items()
        }
        # Blank secret means keep it only for the same client and endpoint.
        setup["client_secret"] = payload.get("client_secret", "").strip() or (
            previous.get("client_secret", "")
            if existing
            and existing.url == url
            and previous.get("server_url") == url
            and previous.get("client_id") == setup["client_id"]
            else ""
        )
        methods = entry.get("oauth_auth_methods", ["client_secret_basic", "client_secret_post"])
        if setup["auth_method"] not in methods:
            raise ValueError("Choose a supported client authentication method.")
        if setup["auth_method"] == "none":
            setup["client_secret"] = ""
        if not setup["client_id"]:
            raise ValueError("Enter the OAuth client ID from your app registration.")
        if setup["auth_method"] != "none" and not setup["client_secret"]:
            raise ValueError("Enter the OAuth client secret from your app registration.")
        if any(len(value) > 8192 or "\n" in value or "\r" in value for value in setup.values()):
            raise ValueError("OAuth settings must be single-line values shorter than 8193 characters.")
        callback = urlparse(setup["redirect_uri"])
        if (
            callback.scheme != "http"
            or callback.hostname not in {"127.0.0.1", "localhost"}
            or callback.username
            or callback.password
            or not callback.port
            or callback.port < 1024
            or callback.path != "/callback"
            or callback.query
            or callback.fragment
        ):
            raise ValueError(
                "Use a callback URL of the form http://127.0.0.1:43827/callback with a port above 1023."
            )
        if setup["redirect_uri"] != f"http://{callback.hostname}:{callback.port}/callback":
            raise ValueError(
                "Use the exact callback format http://127.0.0.1:43827/callback with your chosen port."
            )
        if entry["id"] == "slack" and callback.hostname != "localhost":
            raise ValueError("Use http://localhost:43828/callback (or another port) for Slack desktop PKCE.")
        setup["server_url"] = url
    server = McpServer(name=entry["id"], transport="http", auth="oauth", url=url)
    return server, setup


def save_setup(name: str, setup: dict[str, str], secrets):
    secrets.set(setup_key(name), json.dumps(setup))
