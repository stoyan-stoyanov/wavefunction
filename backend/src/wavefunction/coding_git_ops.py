"""Structured Git operations. This module runs inside the native command sandbox."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path


def git(root, *args, env=None, data=None, check=True):
    environment = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_LITERAL_PATHSPECS": "1",
    }
    # A inherited alternate index must never redirect a product action.
    for key in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE"):
        environment.pop(key, None)
    environment.update(env or {})
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], input=data, capture_output=True, timeout=90, env=environment
        )
    except subprocess.TimeoutExpired:
        hint = (
            " Large folders it has to look through, such as data or a virtual environment, slow it down: "
            "add them to .gitignore."
            if args[:1] == ("status",)
            else ""
        )
        raise ValueError(f"Git did not finish `git {args[0]}` within 90 seconds.{hint}") from None
    if check and result.returncode:
        error = result.stderr.decode(errors="replace")[-5000:]
        # Remote URLs occasionally embed credentials; do not return them to the UI/logs.
        error = re.sub(r"https?://[^\s/@]+:[^\s/@]+@", "https://[redacted]@", error)
        raise ValueError(error.strip() or "Git refused this operation.")
    return result.stdout


def text(root, *args, **kw):
    return git(root, *args, **kw).decode(errors="replace").strip()


def path(root, value):
    candidate = Path(value)
    if candidate.is_absolute() or not candidate.parts or any(p in {"..", ".git"} for p in candidate.parts):
        raise ValueError("Choose a file inside this repository.")
    target = root / candidate
    if target.is_symlink() or not target.resolve().is_relative_to(root):
        raise ValueError("Linked or outside files cannot be shown.")
    return candidate.as_posix()


def branch(root, value):
    if not value or value.startswith("-") or len(value) > 200:
        raise ValueError("Choose a valid branch name.")
    git(root, "check-ref-format", "--branch", value)
    return value


def github_remote(url):
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?/?",
        url,
    )
    return {"owner": match[1], "repo": match[2]} if match else None


def branches(root):
    top = Path(text(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise ValueError("Choose the repository root to see its branches.")
    current = text(root, "symbolic-ref", "--quiet", "HEAD", check=False) or None
    head = text(root, "rev-parse", "--verify", "HEAD", check=False)
    choices, remote_heads = [], {}
    for line in text(
        root,
        "for-each-ref",
        "--format=%(refname)%00%(refname:short)%00%(symref)",
        "refs/heads",
        "refs/remotes",
    ).splitlines():
        ref, name, symbolic = line.split("\0")
        if ref.startswith("refs/remotes/") and symbolic:
            if ref.endswith("/HEAD"):
                remote_heads[ref] = symbolic
            continue
        choices.append({"name": name, "ref": ref})
    known = {choice["ref"] for choice in choices}
    defaults = {}
    for ref, target in remote_heads.items():
        prefix = ref.removesuffix("HEAD")
        if target not in known or not target.startswith(prefix):
            continue
        local = "refs/heads/" + target.removeprefix(prefix)
        defaults[ref] = local if local in known else target
    default = defaults.get("refs/remotes/origin/HEAD")
    candidates = set(defaults.values())
    if default is None and len(candidates) == 1:
        default = candidates.pop()
    return {"current": current, "head": head, "default": default, "branches": choices}


# Changes listed, and hashed, at most. A repository with more still shows and delivers these.
LISTED_FILES = 5000


def state(root):
    top = Path(text(root, "rev-parse", "--show-toplevel")).resolve()
    if top != root:
        raise ValueError("Choose the repository root to see its Git changes.")
    head = text(root, "rev-parse", "--verify", "HEAD", check=False)
    current = text(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    raw = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = raw.split(b"\0")
    files, i, total, conflicted = [], 0, 0, False
    while i < len(entries) and entries[i]:
        entry = entries[i]
        i += 1
        status, name = entry[:2].decode(), os.fsdecode(entry[3:])
        original = None
        if "R" in status or "C" in status:
            original = os.fsdecode(entries[i])
            i += 1
        total += 1
        # A conflict past the listed files still blocks staging, committing and publishing.
        conflicted = conflicted or "U" in status or status in {"AA", "DD"}
        if len(files) == LISTED_FILES:
            continue
        target = root / name
        digest = "absent"
        if target.is_symlink():
            digest = "link:" + os.readlink(target)
        elif target.is_file():
            with target.open("rb") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest() + ":" + str(target.stat().st_mode)
        files.append(
            {
                "path": name,
                "original_path": original,
                "index": status[0],
                "worktree": status[1],
                "digest": digest,
                "staged": status[0] not in {" ", "?"},
                "unstaged": status[1] != " ",
                "conflict": "U" in status or status in {"AA", "DD"},
            }
        )
    index = Path(text(root, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    index_hash = hashlib.sha256(index.read_bytes()).hexdigest() if index.exists() else "absent"
    # The whole status is in the version: a file past the listed ones that appears, goes or
    # changes state still counts, and the index's hash covers anything staged. Delivery stages
    # only listed files, so an edit to an unlisted, already changed file cannot reach a commit.
    version = hashlib.sha256(
        json.dumps(
            [head, current, index_hash, files, hashlib.sha256(raw).hexdigest()], sort_keys=True
        ).encode()
    ).hexdigest()
    upstream = text(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False)
    counts = text(root, "rev-list", "--left-right", "--count", "HEAD...@{upstream}", check=False).split()
    remotes = text(root, "remote").splitlines()
    remote = "origin" if "origin" in remotes else remotes[0] if len(remotes) == 1 else None
    url = text(root, "remote", "get-url", "--push", remote) if remote else ""
    default = (
        text(root, "symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD", check=False)
        if remote
        else ""
    )
    refs = text(root, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes").splitlines()
    default = (
        default.removeprefix(f"{remote}/")
        if default
        else next((b for b in ("main", "master") if b in refs or f"{remote}/{b}" in refs), "main")
    )
    history = []
    if head:
        for line in text(root, "log", "-12", "--format=%H%x09%s%x09%an%x09%aI").splitlines():
            sha, title, author, stamp = (line.split("\t", 3) + ["", "", ""])[:4]
            history.append({"sha": sha, "title": title, "author": author, "date": stamp})
    return {
        "version": version,
        "head": head,
        "branch": current,
        "files": files,
        "total_files": total,
        "conflicted": conflicted,
        "upstream": upstream,
        "ahead": int(counts[0]) if len(counts) == 2 else 0,
        "behind": int(counts[1]) if len(counts) == 2 else 0,
        "remote": remote,
        "github": github_remote(url),
        "default_base": default,
        "branches": refs,
        "history": history,
        "identity": {
            "name": text(root, "config", "user.name", check=False),
            "email": text(root, "config", "user.email", check=False),
        },
    }


def diff(root, name, staged=False, combined=False):
    name = path(root, name)
    if combined:
        head = text(root, "rev-parse", "--verify", "HEAD", check=False)
        indexed = bool(git(root, "ls-files", "--", name).strip())
        committed = bool(head and git(root, "ls-tree", "HEAD", "--", name).strip())
        recreated_warning = {
            "diff": "",
            "hunks": [],
            "warning": "This path has a staged move or removal and a new untracked file. Inspect it in a terminal.",
        }
        if not indexed and committed and (root / name).exists():
            return recreated_warning
        if not head or (not indexed and not committed):
            # Untracked files and unborn repositories have no committed baseline.
            result = git(
                root,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-index",
                "--",
                "/dev/null",
                name,
                check=False,
            )
        else:
            paths = [name]
            entries = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=no").split(b"\0")
            i = 0
            while i < len(entries) and entries[i]:
                entry = entries[i]
                i += 1
                if "R" in entry[:2].decode() or "C" in entry[:2].decode():
                    original = os.fsdecode(entries[i])
                    i += 1
                    if "R" in entry[:2].decode() and os.fsdecode(entry[3:]) == name:
                        original = path(root, original)
                        if (root / original).exists() and not git(root, "ls-files", "--", original).strip():
                            return recreated_warning
                        paths.append(original)
            result = git(root, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", *paths)
    elif not staged and not git(root, "ls-files", "--", name).strip():
        result = git(
            root, "diff", "--no-ext-diff", "--no-textconv", "--no-index", "--", "/dev/null", name, check=False
        )
    else:
        result = git(
            root, "diff", "--no-ext-diff", "--no-textconv", *(["--cached"] if staged else []), "--", name
        )
    if len(result) > 1024 * 1024:
        return {"diff": "", "hunks": [], "warning": "Diff exceeds 1 MiB. Inspect it in a terminal."}
    content = result.decode(errors="replace")
    # New/deleted/renamed/binary files are staged as complete entries, not partial patches.
    hunks = []
    if "new file mode" not in content and "deleted file mode" not in content and "rename from" not in content:
        parts = re.split(r"(?=^@@ )", content, flags=re.M)
        header = parts[0]
        for part in parts[1:]:
            patch = header + part
            hunks.append(
                {
                    "id": hashlib.sha256(patch.encode()).hexdigest(),
                    "header": part.splitlines()[0],
                    "patch": patch,
                }
            )
    return {"diff": content, "hunks": hunks, "warning": None}


def registered_worktree(root, destination, *, missing_ok=False):
    """Verify a linked checkout belongs to this repository before removing it."""
    folder = Path(destination)
    if folder.resolve() != folder or (
        not (folder / ".git").is_file() and (folder.exists() or not missing_ok)
    ):
        raise ValueError("The managed checkout is missing or linked.")
    entries = git(root, "worktree", "list", "--porcelain", "-z").split(b"\0\0")
    entry = next(
        (
            item.split(b"\0")
            for item in entries
            if item.startswith(b"worktree " + os.fsencode(folder) + b"\0")
        ),
        None,
    )
    if not entry and missing_ok and not folder.exists():
        return folder, None
    if not entry or any(
        item.startswith(b"locked") or (item.startswith(b"prunable") and not missing_ok) for item in entry
    ):
        raise ValueError("The managed checkout is not registered or is locked.")
    return folder, entry


def cleanup_check(root, destination, saved_tree=None, owner=None):
    """Only registered, unlocked checkouts with a retained branch and nothing unsaved in them:
    completely clean, or, for a helper, exactly its saved final state apart from build output."""
    folder, _ = registered_worktree(root, destination)
    if saved_tree is None:
        # Ignored files can include credentials or user work. Git's ordinary clean
        # check alone can remove those, so automatic cleanup must check them too.
        if git(folder, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching"):
            raise ValueError("The worktree contains unsaved or ignored files.")
    else:
        from .coding_snapshots import capture, private

        # A helper's last snapshot holds its source, tracked or not; what it leaves out by name
        # is dependencies and build output, and secrets, which are never disposable: a private
        # file that differs from the branch, tracked, untracked or ignored, keeps the checkout.
        # Status names changed tracked files; ls-files every untracked one, inside ignored folders too.
        changed = [
            entry[3:]
            for entry in git(folder, "status", "--porcelain=v1", "-z", "--untracked-files=no").split(b"\0")
            if len(entry) > 3
        ]
        untracked = git(folder, "ls-files", "-z", "--others").split(b"\0")
        if any(name and private(os.fsdecode(name)) for name in [*changed, *untracked]):
            raise ValueError("The worktree contains private files.")
        current = capture(folder, owner, f"{owner}-cleanup")
        git(folder, "update-ref", "-d", current["ref"])
        if current["tree"] != saved_tree:
            raise ValueError("The worktree changed after its helper's last snapshot.")
    # Status and Git's ordinary removal can miss local edits behind these index
    # flags. Retain the checkout instead of treating hidden changes as clean.
    if any(
        entry[:1].islower() or entry[:1] == b"S" for entry in git(folder, "ls-files", "-v", "-z").split(b"\0")
    ):
        raise ValueError("The worktree has assume-unchanged or skip-worktree files.")
    if any(line.startswith(b"160000 ") for line in git(folder, "ls-files", "--stage").splitlines()):
        raise ValueError("Worktrees with submodules are retained.")
    head = text(folder, "rev-parse", "--verify", "HEAD")
    name = text(folder, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if not name or text(root, "rev-parse", "--verify", "refs/heads/" + name) != head:
        raise ValueError("Keep a branch pointing to this checkout before cleanup.")
    return {"head": head, "branch": name}


def operate(request):
    root = Path(request["root"]).resolve()
    action = request.get("action", "state")
    if action == "worktree_delete":
        # Only the explicitly confirmed chat-deletion path uses forced removal.
        # Automatic retention continues to require a completely clean checkout.
        folder, entry = registered_worktree(root, request["destination"], missing_ok=True)
        if entry is None:
            return {"removed": str(folder)}
        head = (
            text(folder, "rev-parse", "--verify", "HEAD")
            if folder.exists()
            else next(
                (item.removeprefix(b"HEAD ").decode() for item in entry if item.startswith(b"HEAD ")), ""
            )
        )
        if not head:
            raise ValueError("The managed worktree's saved commit is unavailable.")
        if not text(root, "branch", "--contains", head, "--format=%(refname:short)"):
            raise ValueError("Keep a branch pointing to this work before deleting the worktree.")
        git(root, "worktree", "remove", "--force", str(folder))
        return {"removed": str(folder)}
    if action in {"worktree_cleanup_check", "worktree_cleanup_remove"}:
        saved = cleanup_check(root, request["destination"], request.get("tree"), request.get("owner"))
        if action == "worktree_cleanup_remove":
            if saved != {"head": request["head"], "branch": request["branch"]}:
                raise ValueError("The checkout changed before cleanup; it was retained.")
            # A helper's checkout holds its unsaved build output, which goes with it.
            git(
                root,
                "worktree",
                "remove",
                *(["--force"] if request.get("tree") else []),
                request["destination"],
            )
        return saved
    if action == "worktree_restore":
        folder = Path(request["destination"])
        if folder.exists() or folder.resolve() != folder:
            raise ValueError("The worktree destination is occupied or linked.")
        name = branch(root, request["branch"])
        if text(root, "rev-parse", "--verify", "refs/heads/" + name) != request["head"]:
            raise ValueError(
                "The retained branch changed. Restore its saved commit before continuing this chat."
            )
        # Git also refuses a branch checked out elsewhere. Never force or reset it.
        git(root, "worktree", "add", "--no-checkout", request["destination"], name)
        return {"head": request["head"], "branch": name}
    if action == "worktree_restore_checkout":
        if (
            text(root, "rev-parse", "--verify", "HEAD") != request["head"]
            or text(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False) != request["branch"]
        ):
            raise ValueError("The restoring checkout changed; its files were preserved.")
        if any(item.name != ".git" for item in root.iterdir()):
            if git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching"):
                raise ValueError("The restoring checkout contains changes. Its files were preserved.")
            # Population may have finished before a failed or interrupted hook.
            # Retry the hook without resetting the already restored files.
            head = request["head"]
            git(root, "hook", "run", "--ignore-missing", "post-checkout", "--", "0" * len(head), head, "1")
            return {"state": state(root)}
        action = "worktree_checkout"
    if action.startswith("checkpoint_"):
        from .coding_snapshots import operate as checkpoint_operation

        return checkpoint_operation(root, request)
    if action == "state":
        return state(root)
    if action == "branches":
        return branches(root)
    if action == "diff":
        return diff(root, request["path"], request.get("staged", False), request.get("combined", False))
    if action == "worktree_add":
        # A worktree starts from a commit, which uncommitted files here cannot reach, so it never
        # reads this checkout's status: in a large repository that can take minutes, and every
        # new chat with its own worktree would wait for it.
        top = Path(text(root, "rev-parse", "--show-toplevel")).resolve()
        if top != root:
            raise ValueError("Choose the repository root to use worktrees.")
        name = branch(root, request["branch"])
        base = request.get("base") or "HEAD"
        if base.startswith("-"):
            raise ValueError("Choose a valid base ref.")
        base = text(root, "rev-parse", "--verify", "--end-of-options", base + "^{commit}")
        git(root, "worktree", "add", "--no-checkout", "-b", name, request["destination"], base)
        return {"head": base, "branch": name}
    if action == "worktree_checkout":
        # Only populate the new empty checkout. Preserve any files that appeared
        # after metadata creation instead of overwriting a failed or edited start.
        if any(item.name != ".git" for item in root.iterdir()):
            raise ValueError("The new worktree already contains files. Its checkout was preserved.")
        git(root, "reset", "--hard", "--no-recurse-submodules", "HEAD")
        # Match worktree-add's single hook call, including its null previous HEAD.
        head = text(root, "rev-parse", "HEAD")
        git(root, "hook", "run", "--ignore-missing", "post-checkout", "--", "0" * len(head), head, "1")
        return {"state": state(root)}
    raise ValueError("Unknown Git action.")
