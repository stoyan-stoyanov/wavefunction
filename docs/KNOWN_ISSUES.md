# Known issues

Accepted limitations. A review reports one of these again only if a change makes it materially worse (see Code review in [AGENTS.md](../AGENTS.md)).

## Commands that can't be undone

- The check in `backend/src/wavefunction/irreversible.py` reads the command's text, as Codex's and Hermes' rules do. A program that deletes on its own (`python -c "shutil.rmtree(...)"`), an obfuscated command, or a directory change it can't see (`env --chdir`) passes. It catches an overeager agent, not a determined one.
- Inside the sandbox, `git push --delete` doesn't ask: pushes to GitHub go through the app's proxy, which refuses branch deletes. A non-GitHub remote the sandbox can reach, such as a bare repository inside the project or a remote with a token in its URL, isn't covered. Codex and Hermes don't flag remote branch deletes at all.
- `git checkout <file>` without `--` doesn't ask. Like any other edit in the repository, a per-file discard is restorable from the chat's code history, and telling a file from a branch would take a guess from the filesystem. Whole-tree discards (`git checkout .`, `git restore .`, `git reset --hard`) ask.
- Recursive `chmod` and `chown` ask only for `/`, `~` and the top-level system folders, not for paths under them. Checking descendants would flag ordinary work under `/Users` and `/private`, and `sudo chown -R $(whoami) /usr/local` is a routine Homebrew fix.
- `start_terminal` refuses these commands and points to `run_command`, but text typed into a running terminal with `write_terminal` isn't checked.

## Sandbox network

- With network access on, a server a command starts can be reached from other devices on the network if it listens on all interfaces (`0.0.0.0`, `--host`, or `python3 -m http.server`'s default), just as when it is started in Terminal. The sandbox can't limit incoming connections to this Mac: Seatbelt checks `listen`, not each connection, names hosts only as `localhost` or `*`, and its `localhost` covers every address of this Mac, including its Wi-Fi address. Refusing inbound entirely would stop every local dev server. Servers that listen on `127.0.0.1` (the default for Vite, Flask, Django, Jupyter and Node's inspector) are not reachable from elsewhere, and the agent's instructions tell it to start servers there unless asked; that is guidance, not enforcement. A server started by `run_command` stops when the command returns; one in an agent terminal runs until the terminal closes.

## Terminals

- Closing a terminal, or its shell exiting, stops every process in the terminal's session or below its shell, and those whose environment carries the terminal's tag. A process that starts a session of its own and loses its parent before the terminal next records its processes, every 5 s, is found only by that tag, which `ps` cannot read for some system programs; such a process can outlive the terminal. Recording every half second cost a few percent of a core per open terminal.

## Worktrees made outside Wavefunction

- A project folder that is a worktree made with `git worktree add`, or by another tool, keeps its objects and refs in its main repository, which the sandbox doesn't grant with it. Git there fails with "not a git repository", so the Changes tab, code history and the agent's Git commands don't work until the main repository is allowed in Settings › Access. Granting it automatically would trust the folder's `.git` file, which the agent can rewrite to name another repository's registration, such as one Git kept for a deleted folder.

## GitHub through the proxy

- Commands act on GitHub as the user through `git` and `gh`, as in other coding agents' terminals. The proxy refuses only pushes that delete branches or push tags; there is no API blocklist.

## Sidebar

- Projects are reordered by dragging with a pointer. There is no keyboard or menu action to move a project; the agreed change was drag-and-drop.
- A new project takes back the chats that removed projects left in its folder, with one set of their slash commands: the most recently saved. If two projects for the same folder had different custom commands and both were removed, the chats from the other one switch to that set.

## Composer

- A message sent while a chat's history loads waits for that history. If you leave the chat first, it stays in the box and goes when you open the chat again, not while you're away, because how it starts depends on that chat's message box. Editing it first cancels the send.
- A sent message leaves the box, and its saved draft, as soon as it is sent. If the window reloads or the app stops before the backend accepts it, usually within a second or two, the message is gone: it is neither a run nor a draft. Keeping the saved draft until acceptance would put the sent message back in a composer that remounts while it is sent.

## Full access

- In a chat with full access, what the agent asks to run outside the sandbox runs without a card, with the user's environment minus the app's own variables. Only commands that can't be undone still ask.
- Its helpers follow that setting. A command a helper runs outside the sandbox isn't limited to the helper's folders, so with full access an isolated helper can change the main checkout that way. An isolated worktree keeps helpers from getting in each other's way; it isn't a boundary against commands the user lets run outside the sandbox.

## Helpers

- A main task's turn held open for a helper waiting on the user (connector sign-in or computer takeover) lives in memory. If the app quits or crashes during that wait, startup fails the main task like any other run left running, including one waiting in `wait_agents`, and the helper is then cancelled; ask again after relaunching. Codex keeps no durable parent-child wait either.
- Shared helpers editing the same file are kept apart only by the instruction to give each helper its own files and by edits refusing to overwrite a file that changed since it was read. Use `isolated=true` for work that could interfere.
- Deleting a chat deletes the chat first, then its helpers. If a helper's checkout can't be removed (another chat uses a folder inside it, or its branch no longer holds its checkout), the chat is still deleted and that helper stays behind as a chat of its own, to delete separately.
- A fork of a chat shows each copied task's own usage: what its helpers spent stays with the original chat's tasks.
- While a task runs, its token count follows its own model calls live. What its helpers spend joins that count when a helper settles and the chat reloads, not call by call: helper calls stream only to the helper's own chat.
- A helper's checkout is kept while a chat, run or terminal uses that checkout's own folder, not one inside it. A chat pointed at a subfolder of a helper's checkout does not stop its removal.
- A helper's checkout removed after a day takes its ignored files with it: dependencies, build output and anything else the repository ignores. Private files (`.env`, `.ssh`, …) keep it from being removed.

## Skills

- Installs from before built-in skills keep the starter skills they were given (code review, coding workflow, debugging) as Global skills, which take precedence over the updating built-ins. There is no startup migration; delete an unedited copy in Settings → Skills to use the built-in.
- Settings → Skills lists repository skills for project folders only. A conversation with its own folder outside a project, or one that overrides its project's folder, still uses that repository's skills, but they are not listed there.
- If the app stops in the moment between accepting a message and clearing its attachments from the draft, those files reappear in the composer after restart. Remove them before sending again, or they are attached to the next message too.

## File previews

- A linked file is checked (inside the chat's folders, not Git metadata or private data, not runnable for Default app) and then read or opened by its path. A process that swaps the file for a symlink in that instant could redirect the read or the open; opening refuses a file that is a symlink at the moment of opening, which narrows but does not close that window.
- Relative links resolve against the chat's current folder. If the chat's repository changes, links in older replies resolve against the new one.
- Previews have no limits beyond the 25 MB file size: an extremely large markdown document, a very wide CSV, an image with enormous pixel dimensions or a PDF with thousands of pages can make the preview slow. Use Open in instead.

## Computer use

- Start Docker and Restart Docker reopen a hung Docker Desktop only when the `docker` the app found is Docker Desktop's own, a link into its app such as `/usr/local/bin/docker`. If a standalone CLI is found first, such as Homebrew's `/opt/homebrew/bin/docker`, a hung Docker Desktop keeps the earlier behavior: the engine error stays until Docker Desktop is quit and opened again by hand.

## Claude on an Anthropic key

- Claude checks that a reasoning block sent back is still in the conversation it was written in. A tool loaded mid-run, the plan reminder sent with one request, or a compaction that keeps recent turns changes that history, so the request asks the API to drop those blocks (`drop_block`) instead of refusing it. The turn goes through; the model loses that earlier reasoning.
- A refusal (`stop_reason: refusal`) ends the turn like an empty reply. There is no separate message for it.

## Gemini on a Google AI Studio key

- Google's Models API says whether a model thinks, not which thinking levels it takes. Those come from the model profiles LangChain ships, so a model newer than them offers Low, Medium and High, and Google refuses a level that model lacks. Choose Default for it.

## Model checkpoints

- A finished run keeps only its conversation's latest LangGraph checkpoint and those message points recorded, and drops the other checkpoints' writes with them. That is safe for `langgraph-checkpoint-sqlite` 3.x, whose `aget_tuple` reads only the returned checkpoint's own writes, and for the agent's plain-reducer channels. A saver that rebuilds state from ancestors' writes (`DeltaChannel`, or pending sends read from a parent) would need those ancestors kept; check this before adopting either.

- Chats saved before checkpoints kept each message once stay the size they were: their rows are never rewritten, so an old long chat frees space only when it's deleted. `checkpoints.sqlite` doesn't shrink on its own, but new writes reuse the free space inside it.
- A release that writes shared messages can't be followed by one that only reads rows saved in full. Keep the reader, and revert only by shipping forward.

## API responses

- A route answers 500 rather than passing through saved data in a shape its response model doesn't allow, for example a provider removed from the model list. Migrate such data in the change that invalidates it.
- `POST …/runs/{id}/answers` returns the run's captured spec; the other run routes leave it out.
- The prompt-surface test runs only where the macOS sandbox does.
