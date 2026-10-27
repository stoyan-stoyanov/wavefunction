from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from pathlib import Path

from .database import database_connection

HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_files (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    extracted_text TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    -- Unused; history files written by earlier builds require a value, so it is always empty.
    revision_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    duration_ms INTEGER,
    usage_json TEXT NOT NULL DEFAULT '{}',
    -- Why this run happened: 'user' for a chat message, 'fork' for copied history, 'subagent'
    -- for a helper task.
    kind TEXT NOT NULL DEFAULT 'user',
    -- The configuration the run executes, captured when it was accepted.
    spec_json TEXT,
    -- The run that delegated this one. A helper is a separate run rather than steps inside its
    -- parent's, so each keeps its own pinned revision, trace and cost — and this is what still
    -- ties them back together.
    parent_run_id TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    node_id TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_files_project ON project_files(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_conversation ON runs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON run_events(event_type, created_at);
"""


class AgentPackage:
    """The local directory holding the agent's run history and uploaded sources."""

    def __init__(self, agent_root: Path, project_id: str):
        if Path(project_id).name != project_id or project_id in {"", ".", ".."}:
            raise ValueError("invalid project id")
        self.root = agent_root / project_id
        self.history_file = self.root / "history.sqlite"

    def ensure(self) -> None:
        with self.connect() as connection:
            connection.executescript(HISTORY_SCHEMA)

    def connect(self) -> AbstractContextManager[sqlite3.Connection]:
        self.root.mkdir(parents=True, exist_ok=True)
        return database_connection(self.history_file)
