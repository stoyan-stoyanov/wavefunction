"""Exact source snapshots in Git objects, executed inside the filesystem sandbox.

The real index and branch are never changed. A small stat cache avoids rereading
unchanged files; Git stores each distinct blob only once.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

from .coding_git_ops import git, text

MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_FILES = 10000
TOO_LARGE = "This source snapshot exceeds the code history limit (16 MB per file, 128 MB or 10,000 files)."
# Secrets stay out of code history even when tracked; build output and dependencies only
# while untracked.
PRIVATE = {".git", ".ssh", ".aws", ".gnupg"}
EXCLUDED = {*PRIVATE, "node_modules", ".venv", "venv", "__pycache__", "target", "dist", "build", ".next"}


def eligible(name):
    parts = Path(name).parts
    return not any(p in EXCLUDED or p.startswith(".env") for p in parts)


def private(name):
    return any(p in PRIVATE or p.startswith(".env") for p in Path(name).parts)


def entries(root, ref):
    result = {}
    for row in git(root, "ls-tree", "-rz", ref).split(b"\0"):
        if not row:
            continue
        meta, name = row.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        name = os.fsdecode(name)
        if kind == "blob" and mode in {"100644", "100755"}:
            result[name] = (mode, oid)
        elif eligible(name):
            raise ValueError(f"Code history cannot copy linked files or submodules: {name}")
    return result


IDENTITY = {
    "GIT_AUTHOR_NAME": "Wavefunction",
    "GIT_AUTHOR_EMAIL": "checkpoint@localhost",
    "GIT_COMMITTER_NAME": "Wavefunction",
    "GIT_COMMITTER_EMAIL": "checkpoint@localhost",
}


def write_tree(root, records):
    with tempfile.TemporaryDirectory(prefix="wavefunction-index-") as temporary:
        env = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
        git(root, "update-index", "-z", "--index-info", data=records, env=env)
        return text(root, "write-tree", env=env)


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def capture(root, owner, point, matching=None):
    """A snapshot of this checkout, or `{"matches": True}` and no ref if it equals `matching`."""
    if not all(re.fullmatch(r"[a-zA-Z0-9-]{1,80}", key) for key in (owner, point)):
        raise ValueError("Invalid checkpoint identity.")
    top, cache_name, head = text(
        root, "rev-parse", "--show-toplevel", "--git-path", f"wavefunction/{owner}.json", "--verify", "HEAD"
    ).rsplit("\n", 2)
    if Path(top).resolve() != root:
        raise ValueError("Choose the Git repository root to use code history.")
    cache_path = Path(cache_name)
    if not cache_path.is_absolute():
        cache_path = root / cache_path
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = read_json(cache_path)
    # A tracked file is source, whatever its folder is called: only untracked build output,
    # dependencies and secrets are left out by name.
    tracked = {os.fsdecode(n) for n in git(root, "ls-files", "-z", "--cached").split(b"\0") if n}
    # Every message tries again, so a repository over the limit stops here, before Git walks the
    # folder for untracked files and each file is checked.
    source = {name for name in tracked if eligible(name)}
    if len(source) > MAX_FILES:
        # Tracked but not here doesn't count: deleted (R), or left out of a sparse checkout (S).
        rows = git(root, "ls-files", "-z", "-t", "--cached", "--deleted").split(b"\0")
        absent = {os.fsdecode(row[2:]) for row in rows if row[:2] in (b"R ", b"S ")}
        if len(source - absent) > MAX_FILES:
            raise ValueError(TOO_LARGE)
    names = {
        os.fsdecode(n)
        for n in git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
        if n
    }
    # Build output and dependencies kept on disk but not in this snapshot: untracked, or tracked
    # but not copyable (a committed node_modules' links, a bundle past the limits). Recorded by
    # their folder, as ignored folders are, so a restore leaves them as they are instead of taking
    # them for absent. Source first, so output never takes the limits from it.
    saved, changed, total, skipped = {}, [], 0, set()

    def leave(name):
        parts = Path(name).parts
        folder = next(index for index, part in enumerate(parts) if part in EXCLUDED)
        skipped.add("/".join(parts[: folder + 1]) + ("/" if folder < len(parts) - 1 else ""))

    for name in sorted(names, key=lambda name: (not eligible(name), name)):
        output = not eligible(name)
        if private(name):
            continue
        if output and name not in tracked:
            leave(name)
            continue
        path = root / name
        linked = path.is_symlink() or not path.resolve().is_relative_to(root)
        info = None if linked or not path.exists() else path.stat()
        special = info is not None and not stat.S_ISREG(info.st_mode)
        over = info is not None and (
            info.st_size > MAX_FILE or total + info.st_size > MAX_TOTAL or len(saved) >= MAX_FILES
        )
        if output and (linked or special or over):
            leave(name)
            continue
        if linked:
            raise ValueError(f"Code history cannot copy a linked file: {name}")
        if info is None:
            continue
        if special:
            raise ValueError(f"Code history cannot copy a submodule or special file: {name}")
        if over:
            raise ValueError(TOO_LARGE)
        total += info.st_size
        signature = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode]
        previous = cache.get(name)
        saved[name] = {"stat": signature, "mode": "100755" if info.st_mode & 0o111 else "100644"}
        if previous and previous.get("stat") == signature:
            saved[name]["oid"] = previous["oid"]
        else:
            changed.append(name)
    if changed:
        paths = "".join(json.dumps(str(root / name), ensure_ascii=False) + "\n" for name in changed)
        hashes = (
            git(root, "hash-object", "-w", "--no-filters", "--stdin-paths", data=paths.encode())
            .decode()
            .splitlines()
        )
        for name, oid in zip(changed, hashes, strict=True):
            info = (root / name).stat()
            if saved[name]["stat"] != [
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                info.st_mode,
            ]:
                raise ValueError(f"{name} changed while its code checkpoint was being saved.")
            saved[name]["oid"] = oid
    records = b"".join(
        f"{row['mode']} {row['oid']}\t".encode() + os.fsencode(name) + b"\0" for name, row in saved.items()
    )
    # Most snapshots find nothing changed since the last, on the same commit, so they need no new
    # tree or commit: only a ref of their own.
    last_path = cache_path.with_suffix(".last.json")
    digest = hashlib.sha256(head.encode() + b"\0" + records).hexdigest()
    last = read_json(last_path)
    same = last.get("digest") == digest
    tree = last["tree"] if same else write_tree(root, records)
    modes = {name: stat.S_IMODE(row["stat"][-1]) for name, row in saved.items()}
    if matching == {"tree": tree, "modes": modes}:
        cache_path.write_text(json.dumps(saved))
        return {"matches": True}
    ref = f"refs/wavefunction/checkpoints/{point}"
    commit = last.get("commit") if same else None
    try:
        if commit:
            git(root, "update-ref", ref, commit)
    except ValueError:  # Nothing kept it, and Git has since pruned it.
        commit = None
    if not commit:
        commit = text(
            root, "commit-tree", tree, "-p", head, "-m", "Wavefunction code checkpoint", env=IDENTITY
        )
        git(root, "update-ref", ref, commit)
    last_path.write_text(json.dumps({"digest": digest, "tree": tree, "commit": commit}))
    cache_path.write_text(json.dumps(saved))
    # Ignored files are not code history, but they are on disk: a later restore must not take
    # one missing from a snapshot for a file that was absent. A folder is listed as "dir/".
    ignored = sorted(
        {
            *skipped,
            *(
                os.fsdecode(n)
                for n in git(
                    root, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", "--directory"
                ).split(b"\0")
                if n
            ),
        }
    )
    # "tracked": this snapshot keeps tracked files under excluded names; older ones did not.
    return {
        "tree": tree,
        "commit": commit,
        "head": head,
        "ref": ref,
        "ignored": ignored,
        "tracked": True,
        "modes": modes,
    }


def _ignored(name, ignored):
    return any(name == entry or entry.endswith("/") and name.startswith(entry) for entry in ignored or ())


def restore_plan(
    root,
    before,
    after,
    before_modes=None,
    after_modes=None,
    before_ignored=None,
    after_ignored=None,
    before_tracked=False,
    after_tracked=False,
):
    """Files changed between two exact snapshots; unrelated disk paths are omitted.

    A file one snapshot lacks only because it was ignored then is left as it is: ignored files
    are not code history, so its absence there says nothing about the disk."""
    old, new = entries(root, before), entries(root, after)
    files, total = [], 0

    def mode(row, modes, name):
        return (modes or {}).get(name, 0o755 if row and row[0] == "100755" else 0o644) if row else None

    for name in sorted(old.keys() | new.keys()):
        if old.get(name) == new.get(name) and mode(old.get(name), before_modes, name) == mode(
            new.get(name), after_modes, name
        ):
            continue
        if (
            name not in new
            and _ignored(name, after_ignored)
            or name not in old
            and _ignored(name, before_ignored)
        ):
            continue
        if not eligible(name) and (
            name not in old and not before_tracked or name not in new and not after_tracked
        ):
            # A snapshot from before tracked files under these names were kept: its missing
            # the name says nothing about the disk.
            continue
        record = {"path": name}
        for side, row in (("before", old.get(name)), ("after", new.get(name))):
            raw = git(root, "cat-file", "blob", row[1]) if row else None
            if raw is not None:
                total += len(raw)
                if len(raw) > MAX_FILE or total > MAX_TOTAL:
                    raise ValueError("The code restore is too large to apply atomically.")
            record.update(
                {
                    f"{side}_data": base64.b64encode(raw).decode() if raw is not None else None,
                    f"{side}_version": hashlib.sha256(raw).hexdigest() if raw is not None else "absent",
                    f"{side}_mode": mode(row, before_modes if side == "before" else after_modes, name),
                }
            )
        files.append(record)
    return {"files": files}


def operate(root, request):
    action = request["action"]
    if action == "checkpoint_capture":
        return capture(root, request["owner"], request["point"], request.get("matching"))
    if action == "checkpoint_plan":
        return restore_plan(
            root,
            request["before"],
            request["after"],
            request.get("before_modes"),
            request.get("after_modes"),
            request.get("before_ignored"),
            request.get("after_ignored"),
            request.get("before_tracked", False),
            request.get("after_tracked", False),
        )
    if action == "checkpoint_staging":
        return {
            "paths": [
                os.fsdecode(p)
                for p in git(root, "diff", "--cached", "--name-only", "-z", "--").split(b"\0")
                if p
            ]
        }
    if action == "checkpoint_diff":
        patch = git(root, "diff", "--no-ext-diff", "--no-textconv", request["base"], request["tip"], "--")
        return {
            "diff": patch[:180000].decode(errors="replace"),
            "truncated": len(patch) > 180000,
            "files": text(root, "diff", "--name-only", request["base"], request["tip"], "--").splitlines(),
        }
    if action == "checkpoint_forget":
        for ref in request["refs"]:
            if not re.fullmatch(r"refs/wavefunction/checkpoints/[a-zA-Z0-9-]{1,80}", ref):
                raise ValueError("Invalid checkpoint reference.")
            git(root, "update-ref", "-d", ref)
        return {"removed": len(request["refs"])}
    raise ValueError("Unknown checkpoint operation.")
