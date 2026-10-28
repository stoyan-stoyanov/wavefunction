"""Sandbox access to a managed worktree's shared Git metadata, in the backend before a command runs."""

from __future__ import annotations

import unicodedata
from pathlib import Path

from .sandbox import Grant


def linked_worktree_grants(folder: Path, grants: list[Grant], source: Path) -> list[Grant]:
    """Grant registered worktree metadata only while its checkout is writable.

    Worktrees share objects and refs, but keep their own HEAD/index. Anchor discovery to the
    saved source and reciprocal Git registration, never just the child-controlled .git file:
    a worktree made outside the app has no saved source, and its .git file could name any
    registration Git still keeps, so it gets nothing here. No subprocess or persisted extra grant.
    """
    if folder.resolve() != folder or source.resolve() != source:
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
        original = source / ".git"
        common = original if original.is_dir() else pointer(original, source, "gitdir: ")
        if (common / "commondir").exists():
            common = pointer(common / "commondir", common)
        admin = pointer(folder / ".git", folder, "gitdir: ")
        if (
            common.resolve() != common
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
    except OSError as error:
        raise ValueError("The managed worktree's Git registration is unavailable.") from error
    if not any(g.writable and folder.is_relative_to(g.path) for g in grants):
        return grants
    # Avoid redundant paths in both the sandbox and the model's access context.
    additions = [Grant(common), *(Grant(path, True) for path in paths)]
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
