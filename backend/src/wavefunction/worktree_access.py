"""Sandbox access to a linked worktree's shared Git metadata, in the backend before a command runs."""

from __future__ import annotations

import unicodedata
from pathlib import Path

from .sandbox import Grant


def linked_worktree_grants(folder: Path, grants: list[Grant], source: Path | None) -> list[Grant]:
    """Grant a linked worktree's shared Git metadata: reads, and writes while it is writable.

    Worktrees share objects and refs, but keep their own HEAD/index. Anchor discovery to the
    reciprocal Git registration, and for a managed worktree (`source` saved) to its source, never
    just the child-controlled .git file. The shared directory must already be a Git directory, so
    a registration forged in a writable folder cannot name an ordinary parent folder. A managed
    worktree is granted only while writable. No subprocess or persisted extra grant.
    """
    if source and (folder.resolve() != folder or source.resolve() != source):
        raise ValueError("The managed checkout was replaced by a link.")

    def pointer(path, base, prefix=""):
        if path.is_symlink():
            raise ValueError("Linked Git metadata cannot be granted.")
        value = path.read_text().strip()
        if not value.startswith(prefix) or not value.removeprefix(prefix):
            raise ValueError("Invalid worktree Git metadata.")
        return (base / value.removeprefix(prefix)).resolve()

    def nfc(path):
        # Git on macOS records these paths precomposed, the volume keeps the folder's spelling.
        return unicodedata.normalize("NFC", str(path))

    try:
        admin = pointer(folder / ".git", folder, "gitdir: ")
        # The user's own worktree (`git worktree add`): the Git directory that registered it.
        common = admin.parent.parent
        if source:
            original = source / ".git"
            common = original if original.is_dir() else pointer(original, source, "gitdir: ")
            if (common / "commondir").exists():
                common = pointer(common / "commondir", common)
        if (
            common.resolve() != common
            or not ((common / "HEAD").is_file() and (common / "objects").is_dir())
            or nfc(admin.parent) != nfc(common / "worktrees")
            or nfc(pointer(admin / "commondir", admin)) != nfc(common)
            or nfc(pointer(admin / "gitdir", admin)) != nfc(folder / ".git")
        ):
            raise ValueError("The worktree's Git registration no longer matches its saved source.")
        paths = [
            admin,
            *(
                common / name
                for name in (
                    "objects",
                    "refs",
                    "logs/refs",
                    "lfs",
                    "packed-refs",
                    "packed-refs.lock",
                    "packed-refs.new",
                    "shallow",
                    "shallow.lock",
                )
            ),
        ]
        if any(path.resolve() != path for path in paths):
            raise ValueError("Linked Git metadata cannot be granted.")
    except (OSError, ValueError) as error:
        # Any other folder (a repository, a submodule, an unregistered checkout) is left to Git.
        if not source:
            return grants
        if isinstance(error, ValueError):
            raise
        raise ValueError("The managed worktree's Git registration is unavailable.") from error
    writable = any(g.writable and folder.is_relative_to(g.path) for g in grants)
    if not writable and (source or not any(folder.is_relative_to(g.path) for g in grants)):
        return grants
    # Avoid redundant paths in both the sandbox and the model's access context.
    additions = [Grant(common), *(Grant(path, True) for path in paths if writable)]
    return [
        *grants,
        *(
            g
            for g in additions
            if not any(
                g.path.is_relative_to(existing.path) and (not g.writable or existing.writable)
                for existing in grants
            )
        ),
    ]
