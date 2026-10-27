from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    if os.getenv("WAVEFUNCTION_DEVELOPMENT_AUTH_BYPASS", "true").lower() == "true":
        return Path.cwd() / ".wavefunction-data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Harness"
    if sys.platform == "win32":
        return Path(os.getenv("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Harness"
    return Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "harness"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WAVEFUNCTION_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=_default_data_dir)
    development_auth_bypass: bool = True
    desktop_token: str | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173", "tauri://localhost"]
    )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "wavefunction.db"

    @property
    def agent_dir(self) -> Path:
        return self.data_dir / "Agent"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
