from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Protocol

SECRET_FILE_NAME = ".wavefunction.env"
ENV_KEYS = {
    "openai_api_key": "OPENAI_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    # Vercel's own name for it, so an existing shell environment is picked up unchanged.
    "vercel_api_key": "AI_GATEWAY_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "tavily_api_key": "TAVILY_API_KEY",
}

# A connector's OAuth tokens and its registered client, one pair per connector. These cannot be
# listed ahead of time the way the model keys can — the set is whatever the user has connected —
# so they are allowed by prefix instead. Still an allowlist: anything not named this way is
# refused, which is what keeps arbitrary values out of the credentials file.
CONNECTOR_SECRET_PREFIX = "mcp_oauth_"
# The other half: a token the user typed, for a server that wants one in a header or in its
# environment. `$GITHUB_TOKEN` in a connector's settings is stored as `mcp_secret_github_token`,
# which is what keeps the token itself out of the settings file.
USER_SECRET_PREFIX = "mcp_secret_"
# Underscores only, so the name maps to an environment variable and back without ambiguity: a
# dash would become an underscore on the way out and could not be told from one on the way home.
CONNECTOR_KEY = re.compile(r"^mcp_(oauth|secret)_[a-z0-9_]+$")


class SecretBackend(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class EnvFileSecretStore:
    """App-local dotenv storage for developer credentials.

    The file sits beside the agent project directories, never inside an agent,
    and is written with user-only permissions. Environment variables still win
    so development and CI can inject credentials without changing the file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def get(self, key: str) -> str | None:
        environment = self._environment_name(key)
        if value := os.getenv(environment):
            return value
        with self._lock:
            return self._read().get(key)

    def set(self, key: str, value: str) -> None:
        self._environment_name(key)
        with self._lock:
            values = self._read()
            values[key] = value
            self._write(values)

    def delete(self, key: str) -> None:
        self._environment_name(key)
        with self._lock:
            values = self._read()
            values.pop(key, None)
            self._write(values)

    @staticmethod
    def _environment_name(key: str) -> str:
        if key in ENV_KEYS:
            return ENV_KEYS[key]
        if key.startswith((CONNECTOR_SECRET_PREFIX, USER_SECRET_PREFIX)) and CONNECTOR_KEY.match(key):
            return key.upper()
        raise ValueError(f"unsupported secret key: {key}")

    def _read(self) -> dict[str, str]:
        try:
            content = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise RuntimeError(f"Could not read {SECRET_FILE_NAME}") from error

        by_environment = {environment: key for key, environment in ENV_KEYS.items()}
        values: dict[str, str] = {}
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            environment, separator, raw_value = line.partition("=")
            name = environment.strip()
            # A connector's own keys are uppercase of themselves, so they map straight back.
            key = by_environment.get(name)
            if key is None and CONNECTOR_KEY.match(name.lower()):
                key = name.lower()
            if not separator or key is None:
                continue
            values[key] = self._decode(raw_value.strip())
        return values

    @staticmethod
    def _decode(raw_value: str) -> str:
        if raw_value.startswith('"'):
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError:
                return raw_value.strip('"')
            return value if isinstance(value, str) else str(value)
        if len(raw_value) >= 2 and raw_value.startswith("'") and raw_value.endswith("'"):
            return raw_value[1:-1]
        return raw_value

    def _write(self, values: dict[str, str]) -> None:
        if not values:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as error:
                raise RuntimeError(f"Could not update {SECRET_FILE_NAME}") from error
            return

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Everything held, not only the keys named in ENV_KEYS: a connector's OAuth tokens are
        # allowed by prefix, and writing only the enumerated ones dropped them on save. The model
        # keys keep their order, so an existing file does not churn.
        ordered = [key for key in ENV_KEYS if key in values]
        ordered += sorted(key for key in values if key not in ENV_KEYS)
        lines = [
            "# Wavefunction local credentials. Keep this file private and out of Git.",
            *(f"{self._environment_name(key)}={json.dumps(values[key])}" for key in ordered),
            "",
        ]
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise RuntimeError(f"Could not update {SECRET_FILE_NAME}") from error
        finally:
            temporary.unlink(missing_ok=True)


class MemorySecretStore:
    """Test-only secret backend."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class StagedSecretStore:
    """Hold a reconnect attempt privately; successful tool sessions then use live storage."""

    def __init__(self, backing: SecretBackend):
        self.backing = backing
        self.changes: dict[str, str | None] = {}
        self.committed = False

    def get(self, key: str) -> str | None:
        if not self.committed and key in self.changes:
            return self.changes[key]
        return self.backing.get(key)

    def set(self, key: str, value: str) -> None:
        if self.committed:
            self.backing.set(key, value)
        else:
            self.changes[key] = value

    def delete(self, key: str) -> None:
        if self.committed:
            self.backing.delete(key)
        else:
            self.changes[key] = None

    def commit(self) -> None:
        for key, value in self.changes.items():
            if value is None:
                self.backing.delete(key)
            else:
                self.backing.set(key, value)
        self.changes.clear()
        self.committed = True
