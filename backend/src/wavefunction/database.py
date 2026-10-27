"""Short-lived SQLite transactions with deterministic connection cleanup."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path


@contextmanager
def database_connection(path: Path) -> Iterator[sqlite3.Connection]:
    # sqlite3's own context manager only commits/rolls back. The outer closing
    # also releases file handles if setup, the operation, or commit fails.
    with closing(sqlite3.connect(path, timeout=30, check_same_thread=False)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        with connection:
            yield connection
