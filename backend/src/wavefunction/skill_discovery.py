"""Discover Agent Skills without changing the application library or executing files."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from .sandbox import Grant, private_folders
from .skills import BUILTIN_SKILLS_DIR, SKILLS_DIR, UNREADABLE, Skill, load_skills, validate_skill

MAX_SKILL_BYTES = 256 * 1024
MAX_SKILLS = 256
LOCAL_ROOTS = (".agents/skills", ".claude/skills", ".opencode/skills", ".harness/skills")
GLOBAL_ROOTS = (*LOCAL_ROOTS, ".codex/skills", ".config/opencode/skills", ".config/harness/skills")
# Bundled guidance offered automatically with a connector rather than to every task.
CONNECTOR_SKILLS = {
    "email": "Offered when a mailbox is connected",
    "microsoft-workspace": "Offered when Microsoft 365 is connected",
}


def _iso(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, UTC).isoformat() if timestamp else None


def _file_times(path: Path) -> tuple[float | None, float | None]:
    try:
        stat = path.stat()
    except OSError:
        return None, None
    return getattr(stat, "st_birthtime", stat.st_ctime), stat.st_mtime


def discover(
    folder: str | None, library, *, grants: list[Grant], home: Path | None = None, shared: bool = True
) -> dict:
    """Nearest repository wins, then app library, then global, then built-in. Duplicates remain visible.

    Walk ancestors only as far as the checkout root. Only SKILL.md files at the standard
    skill/package levels are read; links outside a skills directory and oversized files
    are rejected. Discovery returns the exact text used for a later immutable capture.
    """
    home = home or Path.home()
    roots = []
    if folder:
        current = Path(folder).resolve()
        boundary = next((p for p in (current, *current.parents) if (p / ".git").exists()), current)
        for parent in (current, *current.parents):
            roots.extend((parent / name, "repository") for name in LOCAL_ROOTS)
            if parent == boundary:
                break
    rows, warnings, names, visited = [], [], set(), set()

    def append(skill, source, path, text=None):
        active = skill.name not in names
        names.add(skill.name)
        created, modified = (
            library.times(skill.name)
            if source == "library"
            else (None, None)
            if source == "builtin"
            else _file_times(path)
        )
        rows.append(
            {
                "name": skill.name,
                "description": skill.description,
                "body": skill.body,
                "source": source,
                "path": str(path) if path else None,
                "active": active,
                "version": skill.version if source == "library" else None,
                "digest": hashlib.sha256(text.encode()).hexdigest() if text is not None else None,
                "created_at": _iso(created),
                "modified_at": _iso(modified),
                "_text": text,
            }
        )

    def scan(root, source):
        if root in visited or not root.is_dir():
            return
        resolved = root.resolve()
        if source == "repository" and not any(resolved.is_relative_to(grant.path) for grant in grants):
            warnings.append(f"{root}: repository skills require folder access")
            return
        if source == "repository" and not resolved.is_relative_to(root.parent.parent.resolve()):
            warnings.append(f"{root}: skill directories must stay inside the repository")
            return
        # Marked only once read: a home folder that is itself a repository (dotfiles) offers
        # ~/.claude/skills to the repository walk, which refuses it without folder access, and
        # marked then, the global pass below would skip it too.
        visited.add(root)
        if any(resolved.is_relative_to(p) for p in [*private_folders(), library.root.parent.resolve()]):
            warnings.append(
                f"{root}: private application or credential folders cannot supply external skills"
            )
            return
        # Hidden folders hold another tool's own skills, such as Codex's `.system`, not the user's.
        paths = sorted(
            path
            for path in {*root.glob("*/SKILL.md"), *root.glob("*/*/SKILL.md")}
            if not any(part.startswith(".") for part in path.relative_to(root).parts)
        )
        for path in paths:
            if len(rows) >= MAX_SKILLS:
                warnings.append(f"Skill discovery is limited to {MAX_SKILLS} entries.")
                break
            try:
                if not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Skill links must stay inside their skills directory")
                if path.stat().st_size > MAX_SKILL_BYTES:
                    raise ValueError("Skill exceeds 256 KiB")
                text = path.read_text(encoding="utf-8")
                skill = Skill.parse(text, str(path))
                validate_skill(skill.name, skill.description)
                append(skill, source, path, text)
            except (OSError, UnicodeError, ValueError) as error:
                warnings.append(f"{path}: {error}")

    for root, source in roots:
        scan(root, source)
    if not shared:
        # A project's own skills only, for listing several projects without rereading the rest.
        return {"skills": rows, "warnings": warnings}
    for skill in library.list():
        unreadable = skill.description == UNREADABLE
        named = skill.name in names
        append(skill, "library", library.path_for(skill.name))
        if unreadable:
            # Listed for Settings to open and fix, but there is nothing in it for a task: it is not
            # chosen, and a skill of that name further down still is.
            rows[-1]["active"] = False
            if not named:
                names.discard(skill.name)
            warnings.append(f"{library.path_for(skill.name)}: this skill could not be read")
    for name in GLOBAL_ROOTS:
        scan(home / name, "global")
    for path in sorted(BUILTIN_SKILLS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        append(Skill.parse(text, path.name), "builtin", path, text)
    return {"skills": rows, "warnings": warnings}


SCOPES = {"repository": "project", "library": "global", "global": "global", "builtin": "builtin"}


def catalog(library, projects: list[dict], *, grants: list[Grant], home: Path | None = None) -> dict:
    """Every skill on this machine with its scope, for the Settings page.

    Built-in, Global and connector skills are listed once; each project adds its repository's
    skills. `active` says whether a skill wins over others with its name where it applies.
    """
    base = discover(None, library, grants=grants, home=home)
    rows = [
        {**row, "scope": SCOPES[row["source"]], "editable": row["source"] == "library"}
        for row in base["skills"]
    ]
    # Connector guidance is skipped when a chosen skill has its name, so it ranks last.
    rows += [
        {
            "name": skill.name,
            "description": skill.description,
            "body": skill.body,
            "source": "builtin",
            "scope": "builtin",
            "path": None,
            "active": True,
            "editable": False,
            "version": None,
            "digest": None,
            "created_at": None,
            "modified_at": None,
            "condition": CONNECTOR_SKILLS.get(skill.name, "Offered with its connector"),
        }
        for skill in load_skills(SKILLS_DIR)
    ]
    winners = {}
    for row in rows:
        row["active"] = row["active"] and row["name"] not in winners
        if row["active"]:
            winners[row["name"]] = row
        elif row["name"] in winners:
            row["overridden_by"] = winners[row["name"]]["scope"]
    warnings = list(base["warnings"])
    for project in projects:
        if not project.get("folder"):
            continue
        found = discover(project["folder"], library, grants=grants, home=home, shared=False)
        warnings.extend(found["warnings"])
        for row in found["skills"]:
            rows.append(
                {
                    **row,
                    "scope": "project",
                    "editable": True,
                    "project_id": project["id"],
                    "project_name": project["name"],
                    "overrides": row["active"] and row["name"] in winners,
                    **({} if row["active"] else {"overridden_by": "project"}),
                }
            )
    return {"skills": rows, "warnings": list(dict.fromkeys(warnings))}


def public_inventory(inventory):
    return {
        **inventory,
        "skills": [{k: v for k, v in row.items() if k != "_text"} for row in inventory["skills"]],
    }
