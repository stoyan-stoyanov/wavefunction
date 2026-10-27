# Chat workspaces, history, skills, and tools

Wavefunction keeps one built-in agent and independent conversations. These features belong to the selected conversation and its captured repository.

## Worktrees, forks, and time travel

Before the first message, **New worktree** starts from the branch the repository has checked out, or its current commit when the checkout is detached, without asking. To start from another base, pick it in **Base branch** beside the checkbox; the choice applies to that chat. The compact picker supports search and puts common base branches (`main`, `develop`, `master`, `development`, `dev`, and `trunk`) first. The menu includes local branches and remote branches already known to Git. Sending the first message creates a new chat branch from the selected base, without fetching remote updates. The original checkout keeps its uncommitted changes. Worktrees require a Git repository with a commit.

Use the fork icon beneath a message, or **Fork chat** in the conversation menu. Message forks always use a new worktree with the code and history at that boundary. A conversation-menu fork can share the current folder instead. Forking requires a settled source task.

Hover over a message to reveal **Rewind**. Confirm to restore before a user message or through an assistant reply. A user message returns to the composer with its context. Later history and an unsent draft are preserved in an archived chat. Rewind stops owned execution, preserves unrelated edits and staging, and refuses conflicting affected files. Old messages without a complete saved code checkpoint cannot be rewound.

Snapshots retain eligible regular source files with exact bytes and permissions. They exclude private folders, `.env*`, ignored untracked files, and common dependency/build outputs. Linked files/submodules or snapshots beyond 16 MiB per file, 128 MiB total, or 10,000 files report an unavailable checkpoint. Branch pointers and commits are never rewound.

## Context

The ring at the bottom left of the message box fills as the chat's context grows toward the point where it is compacted: 70% of the model's window, at most 350,000 tokens. Its numbers come from the chat's last model request (the `context_budget` event), split into the system prompt with the environment notice, tool descriptions, attached documents (document tool results, uploaded images and attached repository excerpts) and the rest of the conversation. Click it for the breakdown and **Compact now**, which runs a `compact` run: the whole conversation becomes its summary without a model answer, as automatic compaction does when the chat fills up. It is refused while a task is running or waiting.

## Routines

A routine is a project chat that is sent its instruction on a schedule while the app is open: every so many minutes, hours or days, at a time on chosen days, on a day of the month, or once. A chat creates one with the on-demand `create_routine` tool (`list_routines` and `cancel_routine` manage them). The routine chat uses the creating chat's model and access and works in the project's folder. Each run is a new message in that same chat, so it remembers earlier runs, and compaction keeps its history bounded. With `report_back`, each finished run is sent to the creating chat as a new message, which starts a turn there; otherwise the routine reports nowhere. The routine outlives the chat that made it.

Routines are listed first in their project, with a clock. **Routine settings** in the chat's menu change the instruction, the schedule, where it reports (another chat in the project, or nobody) and whether it runs, or stop it, which leaves an ordinary chat. A time missed while the app was closed runs once when it opens. A routine whose chat is busy stays due and runs once the chat is free. Archiving the chat pauses it, and so does removing its project, which hides the chat until the project is made again; deleting the chat deletes it.

## Projects from a chat

Ask a chat to manage projects and it uses the on-demand project tools, which do what the sidebar's menus do:

- `create_project` adds a project for a folder, creating the folder when it doesn't exist. A folder the agent can't already write to asks on the folder access card first. To start from a repository, the agent creates the project, then clones into its folder with `run_command`, inside the sandbox.
- `archive_chats` archives a project's chats with no message for a given number of days. Pinned chats, chats with a task running or waiting, routines and the asking chat stay.
- `remove_project` takes a project out of the sidebar. Its folder, chats and their history stay, as with **Remove project** in the sidebar.
- `list_projects` lists them.

Everything but a new folder can be taken back from the sidebar, so these don't ask. The sidebar updates as each one finishes. They are offered only in a chat's own task, not to helpers, and not in Plan mode.

## Steering and helpers

Send a message while the agent is working to steer the same task; **Queue follow-up** explicitly starts it afterward instead. Questions and approvals retain their own answer controls. Steering messages show whether the agent has applied them and preserve selected files, skills, slash commands, and attachments. They stay where they were sent in the conversation; the next assistant reply appears separately, and the existing plan updates in place.

Attachments belong to the message that sends them. Drop files anywhere in the chat or use the paperclip; the draft shows previews, and sending moves them onto that message, where the previews stay. Later messages in the conversation can still read earlier attachments. Editing a message again through rewind or fork re-attaches its files.

The agent links files it creates as markdown links to their paths. Clicking one opens a preview over the conversation, above the composer, which stays usable: PDFs, images, CSV/TSV tables and text. **Open in** shows the file in Finder or opens it in its default app; files that would run when opened are only shown in Finder. Only files in the chat's folders (its repository and granted folders) can be previewed or opened.

The agent can launch direct helpers without a fixed concurrency cap, observe or redirect them, and stop them. Helpers appear as indented chats below their parent. By default an editing helper works in the parent's checkout, so its edits appear there directly; the agent gives each helper its own files. For work that could interfere, the agent can start an isolated helper with its own worktree, whose code is retained for review and integrated by the parent rather than merged automatically. Read-only helpers inspect the parent's checkout. Open a child to inspect its transcript or stop it, and fork it for an independent conversation. A parent collects its helpers' results before finishing; stopping it stops them.

## Skills

**Settings → Skills** lists every skill with its scope, location, created and last-edited dates, and whether a same-name skill takes precedence. Filter by scope or search.

- **Built-in** skills (coding workflow, debugging, code review, and `skill-creator`) come with the app, are read-only, and update with it. **Customise** copies one to Global, and that copy is used instead. Connector guidance is listed here too and is offered only with its connector.
- **Global** skills apply in every project. They live in the app's versioned library; editing saves a new version and running tasks keep theirs. Skills found in global folders outside the app are listed read-only.
- **Project** skills live in a repository's `.agents/skills/<name>/SKILL.md` (or the other repository directories below) and apply only there. They are edited in place; a save is refused if the file changed since it was opened. Delete removes the file.

**New skill** asks for the name, when to use it, the instructions, and where to save it, and shows the exact destination. You can also ask the agent: it follows the built-in `skill-creator` guidance, writing project skills as repository files and Global skills with its `save_skill` tool.

Repository directories: `.agents/skills`, `.claude/skills`, `.opencode/skills`, and `.harness/skills`. Discovery walks from the selected folder to its checkout root. Global discovery checks those directories under your home, plus `.codex/skills`, `.config/opencode/skills`, and `.config/harness/skills`. Both `<skill>/SKILL.md` and `<package>/<skill>/SKILL.md` are recognized.

Precedence is nearest repository, app library, global folders, then built-in. The model receives the active descriptions and can request a body with `read_skill`. Each task pins library versions and exact external texts; changing a source does not rewrite a running task's guidance. New tasks pick up refreshed sources automatically. Discovery rejects malformed frontmatter, paths into private app/credential folders, links outside a skills directory, and files larger than 256 KiB. External discovery is bounded to 256 entries and shows warnings for omissions.

## Review changes

Open the workbench and choose **Changes** to see the repository's changed files. Select a file to open its unified diff against HEAD, with removed and added lines together. Staged and unstaged edits are combined into the current file preview; untracked files appear as additions. Use Back to changed files to return to the list. Refresh reads external changes; completed agent tool activity also refreshes the view.

Changes is read-only. Ask Wavefunction in the chat to self-review, edit, stage, commit, or undo specific changes. Repository changes may include work from other conversations sharing that folder. Binary, large, and unsupported files display Git metadata or a warning instead of an editable preview.

## Terminal

Open **Terminal**, then **New Terminal**. Each tab is a real PTY shell in this conversation's repository. It supports ANSI colors, interactive programs, shell history, input, resizing, multiple tabs, renaming, interruption, scrollback, and search. Use the magnifier to search output; Shift+Cmd/Ctrl+F also opens search. Switching tabs, hiding the workbench, or reloading the frontend reconnects to the same process and replays retained output.

The default terminal uses the same macOS sandbox and command-access settings as the coding tools. Plan mode withholds terminals. Changing the folder, mode, grants, or access setting stops existing sessions so old permissions cannot remain live. There are at most 16 sessions per conversation and 64 per backend; close tabs when finished. The server retains the latest 1 MiB of decoded output and the UI keeps 10,000 scrollback lines; old trimmed output is explicitly marked on replay.

The agent can use `start_terminal`, `list_terminals`, `read_terminal`, `write_terminal`, and `close_terminal`. Its command keeps running across replies, approval pauses, and completed, cancelled, or failed tasks. Later tasks can list, read, write to, and close agent terminals in the same chat; they cannot type into or close your shell or access another chat's sessions. Terminal changes are recorded when the session exits. The agent should explicitly close temporary test servers after verification and leave servers requested for your use running.

Closing a tab stops its owned processes, including interactive background jobs. Cleanup continues if the close request disconnects. Quitting Wavefunction closes all sessions; live shell state is not restored after a backend/app restart. Keep durable changes in files. Native quit allows up to 15 seconds for the engine to finish cleanup.

## Reproduce verification

`npm run dev:fixture -- --port 5187 --api-port 8787` starts a disposable repository with synthetic repository/global skills and scripted replies. Send `fixture: edit` to exercise real skill reads, file edits, a Python compile command, and the Changes tab's diff. Stop the launcher to remove its data and servers.

After `npm run desktop:build`, run:

```sh
uv run --no-sync --project backend python scripts/verify-coding-features.py \
  --backend backend/dist/wavefunction-backend/wavefunction-backend
```

This runs the frozen binary with synthetic storage, a scripted coding model, real tools, PTYs, change records and the Changes tab, restart persistence, private-storage rejection, cross-conversation rejection, and background-job cleanup. It checks its own processes and ports and removes its temporary repository/data. It never launches or quits the user's desktop app and does not use a model account.
