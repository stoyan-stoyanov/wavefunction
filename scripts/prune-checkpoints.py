"""Drop the saved agent states no chat can read, once, then shrink checkpoints.sqlite.

Builds before per-run pruning kept every step's full state. With Wavefunction quit:

    uv run --no-sync --project backend python scripts/prune-checkpoints.py [DATA_DIR]

DATA_DIR defaults to the installed app's, ~/Library/Application Support/app.harness.coding.
Each conversation keeps its latest checkpoint and those its message points recorded.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from wavefunction.coding_checkpoints import PRUNE_MODELS, RECORDED_MODELS

DEFAULT_DATA_DIR = Path.home() / "Library" / "Application Support" / "app.harness.coding"


def backend_running(data_dir: Path) -> bool:
    """Whether a backend has this data folder open, as a running app's sidecar would."""
    listed = subprocess.run(
        ["ps", "-axww", "-o", "pid=,command="], capture_output=True, text=True, check=True
    )
    for line in listed.stdout.splitlines():
        pid, _, command = line.strip().partition(" ")
        if (
            pid.isdigit()
            and int(pid) not in {os.getpid(), os.getppid()}
            and f"--data-dir {data_dir}" in command
        ):
            return True
    return False


data_dir = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else DEFAULT_DATA_DIR
path = data_dir / "Agent" / "checkpoints.sqlite"
if not path.is_file():
    raise SystemExit(f"There is no checkpoint database at {path}.")
if backend_running(data_dir):
    raise SystemExit("Wavefunction is running with this data folder. Quit it and run this again.")

before = path.stat().st_size
workspace = sqlite3.connect((data_dir / "harness-workspace.sqlite").as_uri() + "?mode=ro", uri=True)
saved = sqlite3.connect(path)
try:
    count = saved.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    for (conversation,) in saved.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall():
        recorded = json.dumps([row[0] for row in workspace.execute(RECORDED_MODELS, (conversation,))])
        for statement in PRUNE_MODELS:
            saved.execute(statement, {"conversation": conversation, "recorded": recorded})
    saved.commit()
    kept = saved.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    saved.execute("VACUUM")
    saved.execute("PRAGMA wal_checkpoint(TRUNCATE)")
finally:
    saved.close()
    workspace.close()
print(
    f"Kept {kept} of {count} checkpoints; {path.name} went from {before / 2**30:.2f} GB "
    f"to {path.stat().st_size / 2**30:.2f} GB."
)
