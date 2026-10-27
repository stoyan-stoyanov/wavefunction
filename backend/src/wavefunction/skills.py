"""Progressive disclosure of written guidance for an agent.

A skill is a markdown document with a name and a description. The agent always sees the name and
description; it reads the body only when it decides the skill applies. That keeps a long document
out of every request while still making it reachable.

LangChain ships this for `create_deep_agent` via its `skills=` argument, but not for
`create_agent`, which is what Wavefunction builds on. Adopting Deep Agents for this alone would pull
`langchain-anthropic` and `langchain-google-genai` into the frozen sidecar for no other purpose,
so the mechanism is implemented here instead. It is roughly forty lines.

Skills are documents, not code. Nothing in a skill is executed, so this does not widen the trust
boundary the way a user-authored tool would.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.tools import BaseTool, tool

SKILLS_DIR = Path(__file__).parent / "skills"
# Built-in skills every task is offered, read-only and updated with the app. A Global skill with
# the same name takes precedence, which is how one is customised.
BUILTIN_SKILLS_DIR = Path(__file__).parent / "coding_skills"
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)
# What Settings shows for a library skill whose file does not parse, until it is fixed.
UNREADABLE = "This skill could not be read. Open it to fix the file."


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str
    version: int = 1
    source: str | None = None

    @classmethod
    def parse(cls, text: str, source: str) -> Skill:
        match = FRONTMATTER.match(text.removeprefix("\ufeff"))  # Some editors start UTF-8 with a mark.
        if match is None:
            raise ValueError(f"{source}: a skill must start with a --- frontmatter block")
        header, body = match.groups()
        fields = cls._frontmatter_fields(header)
        missing = {"name", "description"} - set(fields)
        if missing:
            raise ValueError(f"{source}: frontmatter is missing {', '.join(sorted(missing))}")
        return cls(name=fields["name"], description=fields["description"], body=body.strip())

    @staticmethod
    def _frontmatter_fields(header: str) -> dict[str, str]:
        """Read `key: value`, plus YAML's folded (`>`) and literal (`|`) block scalars.

        Block scalars matter for interoperability: skills published elsewhere commonly write a
        long description as `description: >` followed by indented lines. Reading only the first
        line yields a description of `>`, and since the description is what the model matches on,
        the skill would never be selected.
        """
        fields: dict[str, str] = {}
        key: str | None = None
        block: list[str] | None = None
        folded = True

        def flush() -> None:
            if key is not None and block is not None:
                joined = (
                    " ".join(part.strip() for part in block if part.strip()) if folded else "\n".join(block)
                )
                fields[key] = joined.strip()

        for raw in header.splitlines():
            indented = raw.startswith((" ", "\t"))
            if block is not None and (indented or not raw.strip()):
                block.append(raw.strip() if folded else raw)
                continue
            flush()
            key, block, folded = None, None, True
            name, separator, value = raw.partition(":")
            if not separator:
                continue
            key, value = name.strip(), value.strip()
            if value in {">", "|", ">-", "|-"}:
                block, folded = [], value.startswith(">")
            else:
                fields[key] = _unquote(value)
                key = None
        flush()
        return fields


def _unquote(value: str) -> str:
    """A YAML scalar's value without its quotes.

    Quoting is how a description containing a colon stays one field, so it is how skills are
    written here — and skills written elsewhere quote too. Reading the quotes as part of the
    text would put them in front of the model, which matches on the description.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        if value[0] == '"':
            try:
                decoded = json.loads(value)
            except ValueError:
                return value[1:-1]
            return decoded if isinstance(decoded, str) else value[1:-1]
        return value[1:-1].replace("''", "'")
    return value


def load_skills(directory: Path = SKILLS_DIR) -> list[Skill]:
    """Read every skill in a directory, including vendored subdirectories.

    Vendored skills live under `vendor/<source>/` beside their licence, so their provenance
    stays visible in the tree rather than only in a commit message. README.md files are skipped;
    they document the vendoring, they are not skills.
    """
    if not directory.exists():
        return []
    paths = [*directory.glob("*.md"), *directory.glob("vendor/*/*.md")]
    return [
        Skill.parse(path.read_text(encoding="utf-8"), path.name)
        for path in sorted(paths)
        if path.name != "README.md"
    ]


class SkillsMiddleware(AgentMiddleware):
    """Advertise skills in the system prompt and serve their bodies through a tool."""

    def __init__(self, skills: list[Skill]):
        super().__init__()
        self._skills = {skill.name: skill for skill in skills}
        self.tools: list[BaseTool] = [self._read_skill_tool()] if skills else []

    def _catalogue(self) -> str:
        lines = ["", "<available_skills>", "Read relevant skills with read_skill; skip others."]
        for skill in self._skills.values():
            lines.append(f"- {skill.name!r}: {skill.description}")
        lines.append("</available_skills>")
        return "\n".join(lines)

    def _read_skill_tool(self) -> BaseTool:
        skills = self._skills

        @tool("read_skill")
        def read_skill(name: str, offset: int = 0, limit: int = 12000) -> str:
            """Read a listed skill by exact name; offset/limit continue pages."""
            skill = skills.get(name)
            if skill is None:
                available = ", ".join(skills) or "none"
                raise ValueError(f"No skill named {name!r}. Available skills: {available}")
            if offset < 0 or not 1 <= limit <= 100000:
                raise ValueError("Use a non-negative offset and a page size from 1 to 100000.")
            end = min(len(skill.body), offset + limit)
            footer = (
                f"\n[Continue with read_skill(name={name!r}, offset={end}).]" if end < len(skill.body) else ""
            )
            origin = (
                f"Source: {skill.source}\nResolve relative references from {Path(skill.source).parent}.\n\n"
                if skill.source
                else ""
            )
            return origin + skill.body[offset:end] + footer

        return read_skill

    def _with_catalogue(self, request: ModelRequest) -> ModelRequest:
        if not self._skills:
            return request
        return request.override(system_prompt=(request.system_prompt or "") + self._catalogue())

    async def awrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        return await handler(self._with_catalogue(request))


# ---- the skills someone writes themselves ---------------------------------------------------
#
# The skills above are bundled with the app and come with the connectors they explain. These are
# the other kind: written by the user, or by the agent on their behalf, and given to tasks by
# name. They are kept in the Agent Skills layout — a directory per skill holding a
# SKILL.md — so a skill written here is a skill anything else can read, and one written elsewhere
# can be dropped in.

SKILLS_PACKAGE = "Skills"
# The spec's own rule: lowercase letters, digits and single hyphens, never leading or trailing.
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
NAME_LIMIT = 64
DESCRIPTION_LIMIT = 1024
# Reserved by the Agent Skills spec, so a skill named this way is not portable.
RESERVED_WORDS = ("anthropic", "claude")


def normalise_name(name: str) -> str:
    """A title someone typed, as a skill name the spec accepts.

    Repairing rather than refusing: "Refund Policy" is a reasonable thing to type and there is
    exactly one thing it can sensibly mean.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:NAME_LIMIT].strip("-")


def validate_skill(name: str, description: str) -> None:
    """Check a skill against the Agent Skills spec, and say which rule it broke."""
    if not name:
        raise ValueError("a skill needs a name")
    if len(name) > NAME_LIMIT:
        raise ValueError(f"a skill name has to be {NAME_LIMIT} characters or fewer")
    if not SKILL_NAME.match(name):
        raise ValueError("a skill name can use lowercase letters, digits and single hyphens between them")
    if any(word in name for word in RESERVED_WORDS):
        raise ValueError("a skill name cannot contain 'anthropic' or 'claude'")
    if not description.strip() or description.strip() == UNREADABLE:
        raise ValueError("a skill needs a description — it is what the agent matches on")
    if len(description) > DESCRIPTION_LIMIT:
        raise ValueError(f"a description has to be {DESCRIPTION_LIMIT} characters or fewer")
    if "<" in name or "<" in description:
        raise ValueError("a name or description cannot contain XML tags")


def write_skill_markdown(name: str, description: str, body: str) -> str:
    """A SKILL.md, with the description quoted so a colon in it cannot break the frontmatter."""
    quoted = json.dumps(description.strip())
    return f"---\nname: {name}\ndescription: {quoted}\n---\n\n{body.strip()}\n"


def _instructions(path: Path) -> str:
    """What an unreadable SKILL.md says after its broken frontmatter: the instructions to keep
    when it is fixed in Settings, which writes the name and description back itself. Nothing
    for a file that is not UTF-8: saving a lossy decoding would replace the original."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return ""
    if lines and lines[0].strip().startswith("--"):
        # A frontmatter that closes: whatever it holds, the instructions follow it.
        closing = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
        if closing is not None:
            return "".join(lines[closing + 1 :]).strip()
    header = 0
    # Without a closing fence, the opening one and the spec's frontmatter fields that follow it:
    # `Steps:` in the instructions is not one of them.
    field = r"(name|description|license|compatibility|allowed-tools|metadata)\s*:.*|\s+[\w-]+\s*:.*"
    while header < len(lines) and re.fullmatch(rf"\s*(-{{2,}}\s*|{field})?\n?", lines[header]):
        header += 1
    fences = [index for index in range(header) if lines[index].strip().startswith("--")]
    start = fences[-1] + 1 if fences and fences[-1] > 0 else header if fences else 0
    return "".join(lines[start:]).strip()


class SkillLibrary:
    """Editable library heads backed by immutable, numbered guidance snapshots.

    Removing a library entry archives it: a queued or paused task can still read its pin.
    """

    _lock = threading.RLock()

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / SKILLS_PACKAGE
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, name: str) -> Path:
        validate_skill(name, "Guidance")
        return self.root / name / "SKILL.md"

    def _versions(self, name: str) -> list[Path]:
        return sorted(self.path_for(name).parent.glob("versions/*.md"), key=lambda p: int(p.stem))

    def _snapshot(self, name: str, text: str) -> Skill:
        skill = Skill.parse(text, name)
        if skill.name != name:
            raise ValueError("skill name must match its directory")
        versions = self._versions(name)
        if versions and versions[-1].read_text(encoding="utf-8") == text:
            version = int(versions[-1].stem)
        else:
            version = int(versions[-1].stem) + 1 if versions else 1
            path = self.path_for(name).parent / "versions" / f"{version}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as out:
                out.write(text)
        return Skill(skill.name, skill.description, skill.body, version)

    def list(self) -> list[Skill]:
        found = []
        for path in sorted(self.root.glob("*/SKILL.md")):
            try:
                if skill := self.get(path.parent.name):
                    found.append(skill)
            except (ValueError, OSError):
                found.append(Skill(path.parent.name, UNREADABLE, _instructions(path)))
        return found

    def get(self, name: str, version: int | None = None) -> Skill | None:
        with self._lock:
            try:
                head = self.path_for(name)
            except ValueError:
                return None
            if head.is_file():
                try:
                    current = self._snapshot(name, head.read_text(encoding="utf-8"))
                except ValueError:
                    # The file as it is, so Settings opens on what is there to fix rather than on
                    # an empty editor whose save would replace it.
                    current = Skill(name, UNREADABLE, _instructions(head))
                if version is None:
                    return current
            if version is None:
                return None
            if not isinstance(version, int) or version < 1:
                raise ValueError("skill version must be a positive integer")
            path = head.parent / "versions" / f"{version}.md"
            if not path.is_file():
                return None
            skill = Skill.parse(path.read_text(encoding="utf-8"), name)
            return Skill(skill.name, skill.description, skill.body, version)

    def save(self, name: str, description: str, body: str) -> Skill:
        validate_skill(name, description)
        with self._lock:
            path = self.path_for(name)
            if path.is_file():
                try:
                    head = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    # Nothing in it could be shown for editing, and nothing of it would be kept.
                    raise ValueError(
                        f"{path} is not UTF-8 text. Convert it in an editor before changing it here."
                    ) from None
                # Repair an invalid head without losing its valid history.
                with suppress(ValueError):
                    self._snapshot(name, head)
            text = write_skill_markdown(name, description, body)
            saved = self._snapshot(name, text)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
            return saved

    def times(self, name: str) -> tuple[float | None, float | None]:
        """When a library skill was first saved and last edited, from its files."""
        head = self.path_for(name)
        versions = self._versions(name)
        created = (
            versions[0].stat().st_mtime if versions else (head.stat().st_mtime if head.is_file() else None)
        )
        return created, head.stat().st_mtime if head.is_file() else None

    def delete(self, name: str) -> None:
        with self._lock:
            self.get(name)  # snapshot an unversioned entry before archiving
            self.path_for(name).unlink(missing_ok=True)

    def capture(self, text: str) -> str:
        """Keep external guidance without adding it to the user's editable library."""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            path = self.root / ".discovered" / f"{digest}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                with path.open("x", encoding="utf-8") as out:
                    out.write(text)
        return digest

    def captured(self, name: str, digest: str, source: str | None = None) -> Skill:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid skill snapshot")
        path = self.root / ".discovered" / f"{digest}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            raise ValueError(f"Skill {name!r} snapshot is unavailable") from None
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
            raise ValueError(f"Skill {name!r} snapshot has changed")
        skill = Skill.parse(text, str(path))
        if skill.name != name:
            raise ValueError("Skill snapshot name does not match")
        return Skill(skill.name, skill.description, skill.body, source=source)

    def select(
        self,
        names: list[str],
        versions: dict[str, int] | None = None,
        sources: dict[str, str] | None = None,
        paths: dict[str, str] | None = None,
    ) -> list[Skill]:
        selected = []
        for name in names:
            if name in (sources or {}):
                selected.append(self.captured(name, sources[name], (paths or {}).get(name)))
                continue
            version = (versions or {}).get(name)
            skill = self.get(name, version)
            if skill:
                selected.append(skill)
            elif version is not None:
                raise ValueError(f"Skill {name!r} version {version} is unavailable")
        return selected

    def pin_spec(self, spec):
        """Pin unpinned library skills to their current version; verify captured guidance."""
        pins = {}
        for name in spec.skills:
            if name in spec.skill_sources:
                self.captured(name, spec.skill_sources[name])
                continue
            version = spec.skill_versions.get(name)
            if version is None:
                skill = self.get(name)
                version = skill.version if skill else 1
            if self.get(name, version) is None:
                raise ValueError(f"Skill {name!r} version {version} is unavailable")
            pins[name] = version
        return spec.model_copy(update={"skill_versions": pins})


def build_save_skill(library: SkillLibrary, pinned: dict[str, int]) -> BaseTool:
    """Let the agent write a Global skill, which lives in the app's library it cannot write to.

    `pinned` holds the library versions the task started with. A skill changed since then, or
    created meanwhile, is not replaced: the task never saw that version.
    """
    known = dict(pinned)

    @tool("save_skill")
    def save_skill(name: str, description: str, instructions: str) -> str:
        """Create or update one of the user's Global skills, offered to new tasks in every project.

        name: lowercase words joined by hyphens. description: what it covers and when to use it;
        the agent sees only this before reading the rest. instructions: the markdown body.
        Saving an existing name keeps its earlier versions. For a skill that belongs to one
        repository, write .agents/skills/<name>/SKILL.md there with file tools instead.
        """
        slug = normalise_name(name)
        current = library.get(slug)
        if current is None and slug in known:
            raise ValueError(
                f"The Global skill {slug!r} was deleted after this task started. "
                "Ask the user before recreating it."
            )
        # One that could not be read when this task started was offered to it in no version, so
        # there is no older one to overwrite; one it was given and broken since has changed.
        unreadable = current is not None and current.description == UNREADABLE
        if current is not None and (slug in known if unreadable else known.get(slug) != current.version):
            now = "" if unreadable else f" (now version {current.version})"
            raise ValueError(
                f"The Global skill {slug!r} changed after this task started{now}. "
                "Ask the user before replacing it."
            )
        saved = library.save(slug, description, instructions)
        known[slug] = saved.version
        return (
            f"Saved Global skill {slug!r} (version {saved.version}) at {library.path_for(slug)}. "
            "New tasks in every project can use it; running tasks keep the skills they started with."
        )

    return save_skill
