from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import OrderedDict
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from .database import database_connection
from .packages import AgentPackage
from .records import Run, RunEvent, as_dicts, hydrate, hydrate_all
from .spec_v2 import load_spec


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


# A field `update_run` was not asked to change.
_UNCHANGED = object()
# Why a run paused for private sign-in ends when the app restarts: the desktop it was waiting
# on ran in the process that is gone.
DESKTOP_CLOSED = "The desktop closed when Wavefunction restarted — send the request again."
INTERRUPTED = "Interrupted when the app closed."
# What run reads return. Lists leave out the captured spec: only an executing or paused task
# needs it, and decoding it for every row of a transcript would be wasted work.
RUN_FIELDS = (
    "id, project_id, conversation_id, status, input_json, output_json, error, started_at, "
    "completed_at, duration_ms, usage_json, kind, parent_run_id"
)

CATALOG_SCHEMA = """
-- The built-in coding agent. Its run history lives in its package.
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _serialized(method):
    """Keep resource validation and removal atomic with work creation."""

    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return locked


class Repository:
    """Settings, immutable execution specs, run history, events, and attached files.

    Coding project/chat metadata lives in CodingWorkspace.
    """

    # How many runs' owners are remembered: a transcript page or two of many chats.
    RUN_OWNERS = 4096

    def __init__(self, database_path: Path, agent_root: Path | None = None):
        from .skills import SkillLibrary

        self.skills = SkillLibrary(database_path.parent)
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.agent_root = agent_root or database_path.parent / "Agent"
        self.agent_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Which agent's history holds each run this process saw lately; a run forgotten here, as a
        # deleted one is eventually, is found again by looking through the histories.
        self._run_owners: OrderedDict[str, str] = OrderedDict()
        # History files whose schema this process has already brought up to date.
        self._ensured: set[Path] = set()
        # Told the type of each run event once it is saved, from whichever thread saved it.
        self.on_run_event: Callable[[str], None] | None = None
        with self._connect() as connection:
            connection.executescript(CATALOG_SCHEMA)

    def _connect(self) -> AbstractContextManager[sqlite3.Connection]:
        return database_connection(self.database_path)

    def agent_ids(self) -> list[str]:
        with self._connect() as connection:
            return [row[0] for row in connection.execute("SELECT id FROM agents")]

    def package(self, project_id: str) -> AgentPackage:
        return AgentPackage(self.agent_root, project_id)

    def package_path(self, project_id: str) -> Path:
        """The directory holding an agent's files, including uploaded documents."""
        return self._ensured_package(project_id).root

    def _history(self, project_id: str) -> AbstractContextManager[sqlite3.Connection]:
        return self._ensured_package(project_id).connect()

    def _ensured_package(self, project_id: str) -> AgentPackage:
        """The agent's package, its schema checked once per process rather than on every read.

        `ensure` runs the whole schema script; run on every call, it was most of the cost of
        each of the many reads a run makes. A history file that has gone since is ensured again.
        """
        package = self.package(project_id)
        if package.history_file not in self._ensured or not package.history_file.is_file():
            self.get_agent(project_id)
            package.ensure()
            self._ensured.add(package.history_file)
        return package

    def _owned(self, project_id: str, rows: list) -> list:
        """Rows read from one agent's history: each run's owner is known from then on, so no
        later lookup by id scans every history for it."""
        for row in rows:
            self._remember_owner(row["id"], project_id)
        return rows

    def _remember_owner(self, run_id: str, project_id: str) -> None:
        with self._lock:
            self._run_owners[run_id] = project_id
            self._run_owners.move_to_end(run_id)
            while len(self._run_owners) > self.RUN_OWNERS:
                self._run_owners.popitem(last=False)

    def _run_owner(self, run_id: str) -> str:
        """The agent whose history holds this run."""
        if owner := self._run_owners.get(run_id):
            return owner
        for agent_id in self.agent_ids():
            with self._history(agent_id) as connection:
                if connection.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
                    self._remember_owner(run_id, agent_id)
                    return agent_id
        raise KeyError("run not found")

    def create_agent(self, name: str, description: str) -> dict[str, Any]:
        timestamp = now_iso()
        agent = {
            "id": new_id(),
            "name": name,
            "description": description,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO agents (id, name, description, created_at, updated_at) "
                "VALUES (:id, :name, :description, :created_at, :updated_at)",
                agent,
            )
            self.package(agent["id"]).ensure()
        return self.get_agent(agent["id"])

    def get_agent(self, project_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM agents WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError("project not found")
        return dict(row)

    @_serialized
    def has_active_work(self, owner_id: str) -> bool:
        with self._history(owner_id) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM runs WHERE status IN ('queued', 'running', 'waiting_approval', 'waiting_connector', 'waiting_computer') LIMIT 1"
                ).fetchone()
                is not None
            )

    def save_run_connector_setup(self, run_id: str, pending: dict | None) -> None:
        with self._lock:
            run = self.get_run(run_id)
            payload = {**run["input"]}
            if pending is None:
                payload.pop("connector_setup", None)
            else:
                payload["connector_setup"] = pending
            with self._history(run["project_id"]) as connection:
                connection.execute(
                    "UPDATE runs SET input_json = ? WHERE id = ?", (json.dumps(payload), run_id)
                )

    def capture_run_files(self, run_id: str, files: list[dict[str, Any]]) -> None:
        """Keep references accepted with steering alive for this run and its saved history."""
        with self._lock:
            run = self.get_run(run_id)
            references = {row["id"]: row for row in run["input"].get("reference_files", [])}
            references.update({row["id"]: row for row in files})
            # The first message's own files, which the transcript shows on that message.
            initial = run["input"].get("initial_reference_files", run["input"].get("reference_files", []))
            payload = {
                **run["input"],
                "reference_files": list(references.values()),
                "initial_reference_files": initial,
            }
            with self._history(run["project_id"]) as connection:
                connection.execute(
                    "UPDATE runs SET input_json = ? WHERE id = ?", (json.dumps(payload), run_id)
                )

    def add_run_folder_grant(self, run_id: str, grant: dict[str, Any]) -> None:
        """A folder the user allowed for a helper, kept with its captured access for resumes."""
        with self._lock:
            run = self.get_run(run_id)
            payload = {**run["input"], "folder_grants": [*run["input"].get("folder_grants", []), grant]}
            with self._history(run["project_id"]) as connection:
                connection.execute(
                    "UPDATE runs SET input_json = ? WHERE id = ?", (json.dumps(payload), run_id)
                )

    def assign_run_connector_tools(self, run_id: str, tool_names: list[str]) -> None:
        """Attach a selected connection only to the waiting run's captured spec."""
        from .spec_v2 import ToolBinding

        with self._lock:
            run = self.get_run(run_id)
            if run["status"] != "waiting_connector":
                raise ValueError("This task is no longer waiting for account setup.")
            spec = load_spec(run["spec"])
            bindings = {binding.name: binding for binding in spec.tools}
            for name in tool_names:
                bindings.setdefault(name, ToolBinding(name=name))
            updated = spec.model_copy(update={"tools": list(bindings.values())})
            with self._history(run["project_id"]) as connection:
                connection.execute(
                    "UPDATE runs SET spec_json = ? WHERE id = ?", (updated.model_dump_json(), run_id)
                )

    def add_project_file(self, record: dict[str, Any]) -> dict[str, Any]:
        project_id = str(record["project_id"])
        with self._lock, self._history(project_id) as connection:
            connection.execute(
                """INSERT INTO project_files
                (id, project_id, name, media_type, relative_path, extracted_text, size_bytes, created_at)
                VALUES (:id, :project_id, :name, :media_type, :relative_path, :extracted_text, :size_bytes, :created_at)""",
                record,
            )
        return record

    def delete_project_file(self, project_id: str, file_id: str) -> None:
        with self._lock, self._history(project_id) as connection:
            row = connection.execute(
                "SELECT relative_path FROM project_files WHERE id = ?", (file_id,)
            ).fetchone()
            if row is None:
                raise KeyError("file not found")
            root = self.package_path(project_id).resolve()
            path = (root / row["relative_path"]).resolve()
            path.relative_to(root / "sources")
            path.unlink(missing_ok=True)
            connection.execute("DELETE FROM project_files WHERE id = ?", (file_id,))

    def run_references_file(self, project_id: str, file_id: str) -> bool:
        """Accepted inputs own their uploads independently of the current composer."""
        with self._history(project_id) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM runs, json_each(runs.input_json, '$.reference_files') AS file "
                    "WHERE json_extract(file.value, '$.id') = ? LIMIT 1",
                    (file_id,),
                ).fetchone()
                is not None
            )

    def conversation_file(self, project_id: str, conversation_id: str, file_id: str) -> dict[str, Any] | None:
        """One upload a run of this conversation could read, found without loading its transcript."""
        with self._history(project_id) as connection:
            found = connection.execute(
                "SELECT 1 FROM runs, json_each(runs.input_json, '$.reference_files') AS file "
                "WHERE runs.conversation_id = ? AND json_extract(file.value, '$.id') = ? LIMIT 1",
                (conversation_id, file_id),
            ).fetchone()
        return self.get_project_file(project_id, file_id) if found else None

    def get_project_file(self, project_id: str, file_id: str) -> dict[str, Any] | None:
        with self._history(project_id) as connection:
            row = connection.execute(
                "SELECT id, project_id, name, media_type, relative_path, size_bytes, created_at FROM project_files WHERE id = ?",
                (file_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_project_files(self, project_id: str) -> list[dict[str, Any]]:
        with self._history(project_id) as connection:
            return as_dicts(
                connection.execute(
                    "SELECT id, project_id, name, media_type, relative_path, size_bytes, created_at FROM project_files ORDER BY created_at DESC"
                )
            )

    def get_project_file_texts(self, project_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        """These uploads' text, oldest first; other uploads' text is never read."""
        with self._history(project_id) as connection:
            return as_dicts(
                connection.execute(
                    "SELECT id, name, media_type, extracted_text FROM project_files "
                    "WHERE id IN (SELECT value FROM json_each(?)) ORDER BY created_at",
                    (json.dumps(file_ids),),
                )
            )

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO app_settings (key, value_json, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at""",
                (key, json.dumps(value), now_iso()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._connect() as connection:
            row = connection.execute("SELECT value_json FROM app_settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def get_settings(self, defaults: dict[str, Any]) -> dict[str, Any]:
        """Several settings in one read, each one's default where it was never saved."""
        with self._connect() as connection:
            stored = {
                row[0]: row[1]
                for row in connection.execute(
                    f"SELECT key, value_json FROM app_settings WHERE key IN ({','.join('?' for _ in defaults)})",
                    list(defaults),
                )
            }
        return {
            key: json.loads(stored[key]) if key in stored else default for key, default in defaults.items()
        }

    # ---- conversations -------------------------------------------------
    #
    # A conversation is the unit of memory: one LangGraph thread, and the runs inside it see
    # each other's messages. Ending one is how the user starts from a blank slate.

    def current_conversation(self, project_id: str, scope_id: str | None = None) -> dict[str, Any]:
        """The open conversation for this owner and scope, opening one if none is.

        `scope_id` is who the conversation belongs to inside the agent's history: each coding
        chat passes its own id, so chats never share a thread.
        """
        scope = scope_id or project_id
        with self._lock, self._history(project_id) as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE project_id = ? AND ended_at IS NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (scope,),
            ).fetchone()
            if row is not None:
                return dict(row)
            record = {"id": new_id(), "project_id": scope, "created_at": now_iso(), "ended_at": None}
            connection.execute(
                "INSERT INTO conversations (id, project_id, created_at, ended_at) "
                "VALUES (:id, :project_id, :created_at, :ended_at)",
                record,
            )
        return record

    def list_conversation_runs(self, project_id: str, conversation_id: str) -> list[Run]:
        """Every run in one conversation, oldest first — the transcript order."""
        with self._history(project_id) as connection:
            rows = as_dicts(
                connection.execute(
                    f"SELECT {RUN_FIELDS} FROM runs WHERE conversation_id = ? ORDER BY started_at",
                    (conversation_id,),
                )
            )
        return hydrate_all("runs", self._owned(project_id, rows))

    def conversation_reference_files(self, project_id: str, conversation_id: str) -> list[dict[str, Any]]:
        """Every file a run of this conversation accepted, oldest first, without loading transcripts."""
        with self._history(project_id) as connection:
            rows = connection.execute(
                "SELECT file.value FROM runs, json_each(runs.input_json, '$.reference_files') AS file "
                "WHERE runs.conversation_id = ? ORDER BY runs.rowid, file.key",
                (conversation_id,),
            ).fetchall()
        files = {}
        for row in rows:
            file = json.loads(row["value"])
            files.setdefault(file["id"], file)
        return list(files.values())

    def conversation_summary(self, project_id: str, conversation_id: str) -> dict:
        """Sidebar state without decoding message bodies, results or event history."""
        return self.conversation_summaries(project_id, [conversation_id])[conversation_id]

    def conversation_summaries(self, project_id: str, conversation_ids: list[str]) -> dict[str, dict]:
        """Each conversation's sidebar state, read together in one connection: a few queries for
        all of them rather than four per conversation."""
        # One parameter however many conversations there are.
        scope = "conversation_id IN (SELECT value FROM json_each(?))"
        wanted = json.dumps(list(conversation_ids))

        def first(order: str, statuses: str) -> str:
            return (
                "SELECT conversation_id, id, status FROM (SELECT conversation_id, id, status, ROW_NUMBER() OVER "
                f"(PARTITION BY conversation_id ORDER BY {order}) AS n FROM runs WHERE {scope} "
                f"AND status IN ({statuses})) WHERE n = 1"
            )

        with self._history(project_id) as connection:
            latest = {
                row[0]: row[1]
                for row in connection.execute(
                    f"SELECT conversation_id, id FROM runs WHERE rowid IN (SELECT MAX(rowid) FROM runs WHERE {scope} "
                    "GROUP BY conversation_id)",
                    (wanted,),
                )
            }
            active = {
                row[0]: row
                for row in connection.execute(
                    first(
                        "status = 'queued', rowid",
                        "'queued', 'running', 'waiting_approval', 'waiting_connector', 'waiting_computer'",
                    ),
                    (wanted,),
                )
            }
            finished = {
                row[0]: row[1]
                for row in connection.execute(
                    first("completed_at DESC, rowid DESC", "'completed', 'failed', 'cancelled'"), (wanted,)
                )
            }
            # The pause each waits on. Questions rather than an approval card: a helper's are its main agent's.
            paused = [row["id"] for row in active.values() if row["status"] not in ("queued", "running")]
            pauses = (
                {
                    row[0]: row
                    for row in connection.execute(
                        "SELECT run_id, event_type, sequence FROM (SELECT run_id, event_type, sequence, ROW_NUMBER() OVER "
                        "(PARTITION BY run_id ORDER BY sequence DESC) AS n FROM run_events WHERE run_id IN "
                        "(SELECT value FROM json_each(?)) AND event_type IN ('run_questions', 'run_waiting_for_approval', "
                        "'run_connector_setup_required', 'run_computer_takeover')) WHERE n = 1",
                        (json.dumps(paused),),
                    )
                }
                if paused
                else {}
            )
        summaries = {}
        for conversation_id in conversation_ids:
            run = active.get(conversation_id)
            pause = pauses.get(run["id"]) if run else None
            summaries[conversation_id] = {
                "status": run["status"] if run else "idle",
                "active_run_id": run["id"] if run else None,
                "last_run_id": latest.get(conversation_id),
                "last_finished_run_id": finished.get(conversation_id),
                "asks_questions": bool(pause) and pause["event_type"] == "run_questions",
                # One pause can follow another without the status changing; this tells them apart.
                "pause_sequence": pause["sequence"] if pause else None,
            }
        return summaries

    def pending_conversation_runs(
        self, project_id: str, conversation_id: str, *, settling: tuple[str, ...] = ()
    ) -> list[dict[str, str]]:
        """Outstanding runs, including tasks still releasing their checkpoint writer.

        Admission and steering need identities/statuses, not saved messages or tool output.
        """
        extra = " OR id IN (" + ",".join("?" for _ in settling) + ")" if settling else ""
        with self._history(project_id) as connection:
            return self._owned(
                project_id,
                as_dicts(
                    connection.execute(
                        "SELECT id, status FROM runs WHERE conversation_id = ? AND (status IN "
                        "('queued', 'running', 'waiting_approval', 'waiting_connector', 'waiting_computer')"
                        + extra
                        + ") ORDER BY rowid",
                        (conversation_id, *settling),
                    )
                ),
            )

    def helper_runs(self, project_id: str, conversations: list[str]) -> list[dict]:
        """Latest run of each helper conversation, without decoding transcripts, specs or tool
        results. Which task owns a helper is its chat's, not its runs': a later task can take it over."""
        if not conversations:
            return []
        placeholders = ",".join("?" for _ in conversations)
        with self._history(project_id) as connection:
            return as_dicts(
                connection.execute(
                    "SELECT id, conversation_id, status FROM runs WHERE rowid IN "
                    f"(SELECT MAX(rowid) FROM runs WHERE conversation_id IN ({placeholders}) "
                    "AND parent_run_id IS NOT NULL GROUP BY conversation_id)",
                    conversations,
                )
            )

    def task_endings(
        self, project_id: str, after: str, event_types: tuple[str, ...], limit: int
    ) -> list[dict]:
        """Events since `after` that end or pause a user's own task, oldest first: not a helper's
        or a compaction's. Only the opening of a reply or error is read."""
        placeholders = ",".join("?" for _ in event_types)
        with self._history(project_id) as connection:
            return as_dicts(
                connection.execute(
                    "SELECT e.id, e.event_type, e.created_at, r.conversation_id, "
                    "substr(json_extract(e.payload_json, '$.text'), 1, 400) AS text, "
                    "substr(json_extract(e.payload_json, '$.message'), 1, 400) AS message "
                    f"FROM run_events e JOIN runs r ON r.id = e.run_id WHERE e.event_type IN ({placeholders}) "
                    "AND e.created_at > ? AND r.kind = 'user' AND r.parent_run_id IS NULL "
                    "ORDER BY e.created_at LIMIT ?",
                    (*event_types, after, limit),
                )
            )

    def previous_user_run(self, project_id: str, conversation_id: str, run_id: str) -> dict[str, str] | None:
        """The user task before this one in a conversation: its id and status, nothing decoded."""
        with self._history(project_id) as connection:
            row = connection.execute(
                # Earlier than this run: a follow-up queued after it is newer, not previous.
                "SELECT id, status FROM runs WHERE conversation_id = ? AND kind = 'user' "
                "AND rowid < (SELECT rowid FROM runs WHERE id = ?) ORDER BY rowid DESC LIMIT 1",
                (conversation_id, run_id),
            ).fetchone()
        return {"id": row[0], "status": row[1]} if row else None

    def helper_usage(self, project_id: str, run_ids: list[str]) -> dict[str, dict[str, Any]]:
        """What the helpers each of these runs started spent, follow-ups included, by run."""
        from .models import merge_usage

        if not run_ids:
            return {}
        totals: dict[str, dict[str, Any]] = {}
        with self._history(project_id) as connection:
            for parent, usage in connection.execute(
                f"SELECT parent_run_id, usage_json FROM runs WHERE parent_run_id IN ({','.join('?' for _ in run_ids)})",
                run_ids,
            ):
                totals[parent] = merge_usage(totals.get(parent), json.loads(usage or "{}"))
        return totals

    def conversation_page(
        self, project_id: str, conversation_id: str, *, before: str | None = None, limit: int = 50
    ) -> dict:
        with self._history(project_id) as connection:
            boundary = None
            if before:
                boundary = connection.execute(
                    "SELECT rowid FROM runs WHERE id = ? AND conversation_id = ?", (before, conversation_id)
                ).fetchone()
                if boundary is None:
                    raise KeyError("History cursor does not belong to this conversation.")
            rows = as_dicts(
                connection.execute(
                    f"SELECT rowid AS history_order, {RUN_FIELDS} FROM runs WHERE conversation_id = ? AND (? IS NULL OR rowid < ?) ORDER BY rowid DESC LIMIT ?",
                    (
                        conversation_id,
                        boundary[0] if boundary else None,
                        boundary[0] if boundary else None,
                        limit + 1,
                    ),
                )
            )
            more = len(rows) > limit
            rows = rows[:limit]
            cursor = rows[-1]["id"] if more else None
            rows.reverse()
            if not before:
                # Many queued turns must never push the executing/paused owner off screen.
                owners = as_dicts(
                    connection.execute(
                        f"SELECT rowid AS history_order, {RUN_FIELDS} FROM runs WHERE conversation_id = ? AND status IN "
                        "('running', 'waiting_approval', 'waiting_connector', 'waiting_computer') ORDER BY rowid",
                        (conversation_id,),
                    )
                )
                ids = {row["id"] for row in rows}
                rows = [row for row in owners if row["id"] not in ids] + rows
        return {"runs": hydrate_all("runs", self._owned(project_id, rows)), "older_cursor": cursor}

    def create_run(
        self,
        project_id: str,
        input_payload: dict[str, Any],
        conversation_id: str,
        *,
        spec: dict[str, Any] | None,
        kind: str = "user",
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": new_id(),
            "project_id": project_id,
            "conversation_id": conversation_id,
            "status": "queued",
            "input_json": json.dumps(input_payload),
            "output_json": None,
            "error": None,
            "started_at": now_iso(),
            "completed_at": None,
            "duration_ms": None,
            "usage_json": "{}",
            "kind": kind,
            # The configuration the task executes, captured when it was accepted. A queued,
            # steered or resumed task keeps it even after its chat's settings change.
            "spec_json": json.dumps(spec) if spec is not None else None,
            "parent_run_id": parent_run_id,
        }
        with self._lock, self._history(project_id) as connection:
            connection.execute(
                """INSERT INTO runs
                (id, project_id, revision_id, conversation_id, status, input_json, output_json,
                 error, started_at, completed_at, duration_ms, usage_json, kind, spec_json,
                 parent_run_id)
                VALUES (:id, :project_id, '', :conversation_id, :status, :input_json,
                        :output_json, :error, :started_at, :completed_at, :duration_ms,
                        :usage_json, :kind, :spec_json, :parent_run_id)""",
                record,
            )
        self._remember_owner(record["id"], project_id)
        return self.get_run(record["id"])

    def get_run(self, run_id: str) -> Run:
        project_id = self._run_owner(run_id)
        with self._history(project_id) as connection:
            row = connection.execute(
                f"SELECT {RUN_FIELDS}, spec_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError("run not found")
        return hydrate("runs", dict(row))

    def active_runs(self, project_id: str) -> list[Run]:
        with self._history(project_id) as connection:
            rows = connection.execute(
                f"SELECT {RUN_FIELDS}, spec_json FROM runs WHERE status IN ('queued', 'running', 'waiting_approval', 'waiting_connector', 'waiting_computer')"
            ).fetchall()
        return [hydrate("runs", dict(row)) for row in self._owned(project_id, rows)]

    def run_analytics(self, project_id: str) -> dict:
        from .coding_analytics import run_analytics

        with self._history(project_id) as connection:
            return run_analytics(connection, project_id, now_iso())

    def keep_run_times(self, run_id: str, started_at: str, completed_at: str | None) -> None:
        """A copied run keeps when the original started and finished."""
        project_id = self._run_owner(run_id)
        with self._lock, self._history(project_id) as connection:
            connection.execute(
                "UPDATE runs SET started_at = ?, completed_at = ? WHERE id = ?",
                (started_at, completed_at, run_id),
            )

    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        output: dict[str, Any] | None | object = _UNCHANGED,
        error: str | None | object = _UNCHANGED,
        duration_ms: int | None | object = _UNCHANGED,
        usage: dict[str, Any] | None | object = _UNCHANGED,
        expected_status: str | None = None,
    ) -> dict[str, Any]:
        """Set a run's status and whichever results are given; the others keep their values.

        Every field used to be written on every call, so a plain status change — a resume, a
        claim, the start of each execution — wiped the usage, output and duration recorded at
        the pause before it. `expected_status` makes a claim atomic: the row changes only if it
        is still in that status, and a ValueError says it was not.
        """
        project_id = self._run_owner(run_id)
        terminal = status in {"completed", "failed", "cancelled"}
        fields: dict[str, Any] = {"status": status, "completed_at": now_iso() if terminal else None}
        if output is not _UNCHANGED:
            fields["output_json"] = json.dumps(output) if output is not None else None
        if error is not _UNCHANGED:
            fields["error"] = error
        if duration_ms is not _UNCHANGED:
            fields["duration_ms"] = duration_ms
        if usage is not _UNCHANGED:
            fields["usage_json"] = json.dumps(usage or {})
        guard = " AND status = ?" if expected_status is not None else ""
        with self._lock, self._history(project_id) as connection:
            changed = connection.execute(
                f"UPDATE runs SET {', '.join(f'{name} = ?' for name in fields)} WHERE id = ?{guard}",
                (*fields.values(), run_id, *([expected_status] if expected_status is not None else [])),
            ).rowcount
        if expected_status is not None and not changed:
            raise ValueError(f"This run is no longer {expected_status.replace('_', ' ')}.")
        return self.get_run(run_id)

    def add_run_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        node_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        project_id = self._run_owner(run_id)
        with self._lock, self._history(project_id) as connection:
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            record = {
                "id": new_id(),
                "run_id": run_id,
                "sequence": sequence,
                "event_type": event_type,
                "node_id": node_id,
                "payload_json": json.dumps(payload),
                # A copied history keeps when each event originally happened.
                "created_at": created_at or now_iso(),
            }
            connection.execute(
                """INSERT INTO run_events
                (id, run_id, sequence, event_type, node_id, payload_json, created_at)
                VALUES (:id, :run_id, :sequence, :event_type, :node_id, :payload_json, :created_at)""",
                record,
            )
            if event_type == "tool_output" and payload.get("id"):
                # Live snapshots replace the prior tail for this call. A new sequence keeps
                # reconnect cursors monotonic, and the last tail survives interrupted runs.
                connection.execute(
                    "DELETE FROM run_events WHERE run_id = ? AND event_type = 'tool_output' "
                    "AND json_extract(payload_json, '$.id') = ? AND id != ?",
                    (run_id, payload["id"], record["id"]),
                )
        if self.on_run_event is not None:
            self.on_run_event(event_type)
        return hydrate("run_events", record)

    def preserve_assistant_start(self, run_id: str, event_id: str) -> None:
        """Keep a compacted reply's position without retaining its token text twice."""
        project_id = self._run_owner(run_id)
        with self._lock, self._history(project_id) as connection:
            connection.execute(
                "UPDATE run_events SET event_type = 'assistant_started', "
                "payload_json = json_object('message_id', json_extract(payload_json, '$.message_id')) "
                "WHERE run_id = ? AND id = ? AND event_type = 'assistant_delta'",
                (run_id, event_id),
            )

    def delete_run_events(self, run_id: str, event_type: str) -> None:
        """Remove one kind of event from a run's trace.

        Exists for streaming deltas: they are replayed live so a reconnecting client misses
        nothing, and deleted once the run settles because the finished run stores the same
        text properly — keeping both would double every reply in the database.
        """
        project_id = self._run_owner(run_id)
        with self._lock, self._history(project_id) as connection:
            connection.execute(
                "DELETE FROM run_events WHERE run_id = ? AND event_type = ?", (run_id, event_type)
            )

    def list_run_events(
        self,
        run_id: str,
        after: int = 0,
        *,
        limit: int | None = None,
        tail: bool = False,
        event_types: tuple[str, ...] | None = None,
    ) -> list[RunEvent]:
        project_id = self._run_owner(run_id)
        kinds = " AND event_type IN (" + ",".join("?" for _ in event_types) + ")" if event_types else ""
        with self._history(project_id) as connection:
            rows = as_dicts(
                connection.execute(
                    "SELECT * FROM run_events WHERE run_id = ? AND sequence > ?"
                    + kinds
                    + " ORDER BY sequence "
                    + ("DESC" if tail else "ASC")
                    + " LIMIT ?",
                    (run_id, after, *(event_types or ()), -1 if limit is None else limit),
                )
            )
        return hydrate_all("run_events", list(reversed(rows)) if tail else rows)

    def settle_stale_runs(self) -> int:
        """Mark runs a previous process could not finish as failed. Called once, at startup.

        The one-run-per-conversation guard blocks on stored nonterminal runs, so a leftover
        `queued`, `running` or unavailable desktop would prevent the conversation from continuing. `waiting_approval` and `waiting_connector` survive: those resume from
        their checkpoints. Streamed deltas nobody will finish are dropped from every run.
        """
        settled = 0
        for project_id in self.agent_ids():
            with self._lock, self._history(project_id) as connection:
                stale = connection.execute(
                    "SELECT id, status FROM runs WHERE status IN ('queued', 'running', 'waiting_computer')"
                ).fetchall()
                connection.execute("DELETE FROM run_events WHERE event_type = 'assistant_delta'")
            # Through update_run rather than raw SQL, so a settled run gets the same terminal
            # bookkeeping every other failure gets — timestamps and the location index.
            for run_id, status in stale:
                self.update_run(
                    run_id,
                    status="failed",
                    error=DESKTOP_CLOSED
                    if status == "waiting_computer"
                    else "Interrupted when the app closed.",
                )
                settled += 1
        return settled

    @_serialized
    def waiting_connector_runs(self) -> list[str]:
        """Recover setup pauses once when the runtime starts; idle polls use its set."""
        ids = []
        for owner in self.agent_ids():
            with self._history(owner) as connection:
                ids.extend(
                    row[0]
                    for row in connection.execute("SELECT id FROM runs WHERE status = 'waiting_connector'")
                )
        return ids
