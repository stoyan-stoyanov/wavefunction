# Git, worktrees, and composer context

Wavefunction keeps one built-in coding agent and independent conversations. Git through the agent, optional managed worktrees, composer context, structured questions, and prompt commands support the coding workflow. Projects remain collections of conversations.

## Repository changes

Open **Changes** in the workbench to browse the repository's changed files and their unified diffs. The view compares current file content with HEAD, including staged and unstaged edits, and displays untracked files as additions. It is read-only. Ask the agent in the conversation to review, edit, stage, commit, push, create a PR, or undo a specific change.

The agent uses `git` and `gh` through its coding tools and terminals. GitHub access uses the GitHub CLI sign-in in **Settings → Access** and requires command network access. When gh is not signed in, the agent asks for the sign-in card and continues afterward. Sandboxed processes receive a placeholder credential; the app adds the real token only on the GitHub proxy's outgoing request. Repository permissions and irreversible-command safeguards still apply.

Start a chat with **New worktree** for an independent checkout. See [worktrees and history](CODING_WORKSPACE_FEATURES.md#worktrees-forks-and-time-travel) for the current controls.

## Composer context and questions

Type `@` followed by a filename, or select `/files` from the slash menu, to attach a repository file and optional line range. Wavefunction captures its folder, path, version, and excerpt at task start; changed, binary, oversized, and invalid-range inputs are rejected. Selecting a skill from `/` explicitly requests captured skill guidance. Context chips show the references and skills used. The repository button sits above the input; model and attachment controls sit together on the right.

The slash menu selects prompt commands. `/review`, `/commit`, `/pr`, and `/test` are built in. `$ARGUMENTS` expands from the rest of the message. Prompts saved by earlier versions still apply to their project, or to their chat outside a project. Queued/running tasks retain the workflow and context captured at start.

Typing `/` opens the combined file/skill/prompt menu; continue typing to filter it. Skills can be invoked as `/<skill-name>` with optional arguments, and the selected version is captured. Typing a command shared by a project prompt and a skill uses the prompt; the menu shows distinct Prompt and Skill choices so either remains selectable. Shared library skills are available across all projects and General chats.

The agent can ask structured questions with choices and free-text answers. Choose an option or write an answer for each, then continue; **Cancel** closes the questions unanswered and the task carries on without assuming one. A message sent from the composer while questions are open answers them, and each answer appears in the conversation where it was given. Questions persist through navigation and restart. Checkpoint identity and event-sequence checks reject stale or cross-conversation answers. Folder access approvals remain separate: in-project actions run without approval, while new outside folders require a read-only or writable grant saved until revoked in Settings → Access.

## Prompt commands and verification

Invoke prompt commands from the composer with `/`.

Use `/test` or ask the agent to run relevant tests and formatters. The built-in prompt asks it to discover commands from repository instructions and project scripts. Execution uses normal task tools, so output, cancellation, and change records follow the conversation. Commands can also run in the terminal. There is no separate saved-check runner, automatic Git hook runner, or native LSP client.

## Verification

Current suite counts and commands are maintained in [the test policy](TESTING.md).

Real sandbox tests cover the Changes tab's staged, unstaged, renamed and copied diffs, change records, worktrees and restart, rewind byte/mode restores and conflicts, context, and question ownership/restart. A mocked GitHub covers the proxy's sign-in handling and credential redaction; tests never reach real GitHub.
