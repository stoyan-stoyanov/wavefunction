"""Repository and execution context for tasks with local tools."""

import platform
from pathlib import Path

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from .coding_access import full_access
from .hands import command_access, file_operation
from .sandbox import Grant, Sandbox

# The folder each chat chose. Coding tasks also pin their working folder in the spec.
REPOSITORY_FOLDERS_SETTING = "repository_folders"

EXPLORATION_GUIDANCE = (
    "Read applicable nested AGENTS.md before editing. Use find_files/search_files to locate "
    "symbols or sections. For a specific definition, request context_lines=3; read further "
    "only if that context is insufficient or before editing. Follow relevant callers/tests. "
    "Read large/generated files in bounded ranges, expanding when needed rather than taking "
    "many tiny reads. Batch independent searches/reads and compatible edits; order dependent "
    "work. File/search tools need no command permission; shell rg uses run_command."
)


def runner_for(access: str, grants: list[Grant], scratch: Path) -> Sandbox:
    """The runner an agent's command access asks for."""
    return Sandbox(grants, scratch=scratch, network=access == "network")


# Said once in the system prompt of a task with local tools; what changes is in its environment.
LOCAL_GUIDANCE = (
    "\n\nThe macOS sandbox allows the project, granted folders, system/toolchain support, "
    "Git config/caches and scratch. request_folder_access grants other folders; request "
    "write access only when needed. Other restrictions: use "
    "run_command(outside_sandbox=true, reason=...) under this chat's access mode. "
    "Irreversible commands need approval in every mode. workdir affects one command. "
    "Manage permissions in Settings → Access. " + EXPLORATION_GUIDANCE
)


def environment_context(
    folder: Path | None, scratch: Path, access: str, grants: list[Grant], *, read_only=False, choose=False
) -> str:
    """Where the model's local tools run and with what access; `folder` is None for a chat with
    no repository folder, and `choose` says whether it can ask the user for one."""
    # The sandbox keeps a chat-specific tool shim on PATH. Advertising that internal path
    # makes otherwise identical repository context differ for every new chat.
    if folder is None:
        place = (
            "No repository folder has been chosen for this chat. "
            + (
                "Call choose_repository_folder when the task needs one; otherwise give "
                if choose
                else "Give "
            )
            + "file tools absolute paths, since relative ones are refused; commands start in "
            f"{scratch}. "
        )
    else:
        place = f"Local repository folder: {folder}. Relative file paths and commands use this folder. "
    return (
        f"Local environment: {platform.system()} {platform.machine()}; shell /bin/sh. "
        + place
        + ("Network is enabled. " if access == "network" else "Network is disabled. ")
        + (" This is a read-only task: local writes and commands are disabled." if read_only else "")
    )


def github_context(access: str, signed_in: bool) -> str:
    """Whether git and gh in commands can reach GitHub, and as whom."""
    if access != "network":
        return "Commands have no network access, so git and gh cannot reach GitHub."
    if signed_in:
        return (
            "git and gh in commands are signed in to GitHub with the user's GitHub CLI sign-in. git reaches "
            "github.com remotes, SSH ones included, through the app, so it shows them as http://github.com/...; "
            "they still go to GitHub over HTTPS."
        )
    return "gh is not signed in to GitHub. Call configure_github before pushing or using gh."


def repository_folder(repository, spec, copy_id: str | None) -> str | None:
    """The folder one copy of an agent works in: the one chosen for it on this Mac, else the
    spec's working directory, else none."""
    chosen = repository.get_setting(REPOSITORY_FOLDERS_SETTING, {})
    return (chosen.get(copy_id) if isinstance(chosen, dict) else None) or spec.working_directory


# Environment messages are user-role messages with this source, holding the sections they say.
ENVIRONMENT_SOURCE = "environment"
SECTIONS = ("date", "local", "github", "repository_instructions")


def told_environment(messages) -> dict[str, str]:
    """What the conversation has been told about its environment, section by section."""
    told: dict[str, str] = {}
    for message in messages:
        if (
            isinstance(message, HumanMessage)
            and message.additional_kwargs.get("lc_source") == ENVIRONMENT_SOURCE
        ):
            told.update(message.additional_kwargs.get("environment", {}))
    return told


def environment_message(sections: dict[str, str], *, first: bool) -> HumanMessage:
    lead = (
        "Where and how your tools run. A later <environment> message replaces what it repeats."
        if first
        else "This changed since earlier in this conversation and replaces what was said then:"
    )
    body = "\n".join(f"<{name}>\n{sections[name]}\n</{name}>" for name in SECTIONS if name in sections)
    return HumanMessage(
        content=f"<environment>\n{lead}\n{body}\n</environment>",
        additional_kwargs={"lc_source": ENVIRONMENT_SOURCE, "environment": sections},
    )


class EnvironmentMiddleware(AgentMiddleware):
    """The date, where and how local tools run, GitHub sign-in and the repository's AGENTS.md
    files, said in the conversation rather than the system prompt, as Codex, Pi and Hermes do.

    A conversation is told all of it at its first model call, then only what changed, before
    the model call where it changed — such as a folder chosen mid-turn, whose AGENTS.md then
    applies — or all of it again where compaction left none. The system prompt stays the same
    for the whole conversation, which keeps the provider's cache, and a change comes after what
    the conversation said before it: an agent that found GitHub unreachable earlier reads that
    git and gh are signed in now, after that failure. A notice is not the user's message, so it
    never starts a turn (execution_policy._is_user_message).

    The instructions are read once per run, at its first model call: reading
    them is a sandboxed process, and instructions that change mid-run, when the agent edits
    AGENTS.md itself, are not ones it was working from.
    """

    def __init__(self, context, *, local: bool = True):
        self.context = context
        self.local = local
        self.instructions: str | None = None

    async def abefore_model(self, state, runtime):
        messages = state.get("messages", [])
        told = told_environment(messages)
        sections = await self.sections()
        changed = {name: text for name, text in sections.items() if told.get(name) != text}
        if not changed:
            return None
        return {"messages": [environment_message(changed, first=not told)]}

    async def sections(self) -> dict[str, str]:
        from .agent_factory import today_line
        from .github_cli import commands_signed_in

        sections = {"date": today_line().strip()}
        if not self.local:
            return sections
        access = command_access(self.context.repository, self.context.agent_id)
        grants = self.context.local_grants()
        scratch = self.context.local_scratch
        folder = Path(self.context.working_directory).expanduser() if self.context.working_directory else None
        # Only a run the user started can stop to ask where to work.
        local = environment_context(
            folder,
            scratch,
            access,
            grants,
            read_only=self.context.local_read_only,
            choose=self.context.choose_folder,
        )
        source = self.context.coding_workspace.worktree_source(folder) if folder else None
        if source:
            local += (
                f" This folder is a Git worktree of {source}. Gitignored files such as virtualenvs, "
                "node_modules and .env stay there and are not copied here; reuse them from there or set up your own."
            )
        isolated = self.context.folder_grants is not None and bool(
            self.context.coding_workspace.delivery.worktree(self.context.desktop_copy)
        )
        if isolated:
            local += " You are a helper: write only in your own worktree, its granted Git metadata, and scratch. Ask the parent to handle other paths or new permissions."
        elif self.context.folder_grants is not None:
            # As Codex tells its sub-agents: they are not alone in the checkout.
            local += (
                " You are a helper working in the parent's checkout alongside other agents: edit only your "
                "assigned files, never revert others' changes, and do not commit, switch branches, stash or reset. "
                "Ask the parent to handle other paths or new permissions."
            )
        elif self.context.choose_folder and full_access(
            self.context.coding_workspace, self.context.desktop_copy
        ):
            # As Codex states its approval policy, so the agent need not hold back from asking.
            local += (
                " This chat has full access: run_command(outside_sandbox=true) runs without asking the user, "
                "unless the command can't be undone."
            )
        sections["local"] = local
        # Offline, no command can use a sign-in, so gh is not asked for one.
        sections["github"] = github_context(access, access == "network" and await commands_signed_in())
        # Always said, so a chat whose folder went away is not left with the old instructions.
        sections["repository_instructions"] = (
            await self._instructions(folder, grants, scratch)
            if self.context.repository_instructions and folder is not None
            else "No repository instructions apply."
        )
        return sections

    async def _instructions(self, folder: Path, grants: list[Grant], scratch: Path) -> str:
        if self.instructions is not None:
            return self.instructions
        runner = Sandbox(
            grants,
            scratch=scratch,
            restrict_reads=True,
            private_roots=[self.context.coding_workspace.services.settings.data_dir],
        )
        try:
            result = await file_operation(runner, {"op": "instructions", "path": str(folder)})
        except (OSError, ValueError, RuntimeError) as error:
            return f"Repository instructions could not be read: {error}"
        text = result["text"].strip() or "No AGENTS.md files apply to this folder."
        if result.get("complete", True):
            self.instructions = text
        return text
