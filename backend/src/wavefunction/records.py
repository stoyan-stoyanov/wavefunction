"""Turning SQLite rows into the records the rest of the app passes around."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any, TypedDict

# Which JSON-encoded columns each table carries, and the attribute each is exposed under.
# Declaring them here means a new column is described once, rather than being unpacked by hand
# in every getter that touches the table — which is how `runs` ended up with two copies of the
# same four lines that had to be kept in step.
JSON_COLUMNS: dict[str, dict[str, str]] = {
    "runs": {"input_json": "input", "output_json": "output", "usage_json": "usage", "spec_json": "spec"},
    "run_events": {"payload_json": "payload"},
}

# NULL in one of these means "not yet", not "empty": a run that has not finished has no output.
# Any other column being NULL is a bug, and json.loads raising is the right way to hear about it.
NULLABLE_JSON_COLUMNS = frozenset({"output_json", "spec_json"})


def as_dicts(cursor: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    """Every read goes through here, so callers always get plain dicts, never sqlite3.Row."""
    return [dict(row) for row in cursor]


def hydrate(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Decode a row's selected JSON columns in place, renaming each to its attribute name."""
    for column, attribute in JSON_COLUMNS[table].items():
        if column not in row:
            continue
        raw = row.pop(column)
        row[attribute] = None if raw is None and column in NULLABLE_JSON_COLUMNS else json.loads(raw)
    return row


def hydrate_all(table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [hydrate(table, row) for row in rows]


# The records that cross the most module boundaries — runtime, the coding workspace and the
# route handlers all index into these. The repository hands back plain dicts, so these describe
# the shape rather than enforce it. Ordinary API responses omit the internal spec.
class Run(TypedDict):
    id: str
    project_id: str
    kind: str  # user | fork | subagent
    # Execution snapshot captured when the run was accepted. Only single-run reads include it.
    spec: dict[str, Any] | None
    conversation_id: str | None
    status: str  # queued | running | waiting_approval | waiting_connector | waiting_computer | completed | failed | cancelled
    error: str | None
    started_at: str
    completed_at: str | None
    duration_ms: int | None
    input: dict[str, Any]
    output: dict[str, Any] | None  # None until the run reaches a terminal state
    usage: dict[str, int]


class RunEvent(TypedDict):
    id: str
    run_id: str
    sequence: int  # dense and per-run, so a reconnecting client can resume from a cursor
    event_type: str
    node_id: str | None
    payload: dict[str, Any]
    created_at: str
