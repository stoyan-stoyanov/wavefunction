---
name: drive-fixture
description: Run Wavefunction's disposable browser fixture (npm run dev:fixture) and use its scripted prompts to see interface changes, with no account or model. Use to check any change a user would see, such as the transcript, cards, composer, workbench or sidebar.
---

# Drive the fixture

`npm run dev:fixture` starts the real engine and interface on a throwaway workspace. A scripted model answers each chat; the commands, edits, Git and terminals it asks for run for real, in synthetic repositories.

## Start and stop

```sh
npm run dev:fixture
```

- **Startup.** It picks free ports and prints, in order: the workspace folder, `Launcher PID: <pid>`, then `UI: http://127.0.0.1:<port>`. Open the UI address in the browser.
- **Fixed ports.** Pass `-- --port <ui> --api-port <api>` for fixed ones.
- **Stopping.** Stop it with Ctrl+C or `kill -INT <launcher pid>`. It stops everything it started and removes the workspace; check that it says so. A hard kill skips that cleanup.
- **Seeded data.** Two projects: **Sandbox project**, with the chat **Welcome to Wavefunction**, a Git repository and a local remote; and **Second project**, with one chat. The model is `fixture-model`, so setup is already done.
- **Slow history.** Set `WAVEFUNCTION_FIXTURE_THREAD_DELAY=10` to delay each chat's first history load, to check loading placeholders.

## Prompts

Send these as chat messages. Anything else gets a one-line "Synthetic reply".

| Message | Shows |
|---|---|
| `fixture: edit` | A skill read, a file read, an edit to `app.py` and a compile command. The Changes tab then shows the diff. |
| `fixture: question` | The agent's question card, with choices and free text. |
| `fixture: approval` | The card for a command outside the sandbox. Allowing it only runs `echo`. |
| `fixture: irreversible` | The card for a command that can't be undone (a force push). Allowing it only adds a branch to the fixture's own remote. |
| `fixture: terminal` | An agent terminal in the workbench. |
| `fixture: fail` | A failed task and its error. |
| `fixture: steering`, then `fixture: redirect` while it runs | A plan, a slow command, and a steering message applied to the running task. |
| `fixture: slow` | A task that runs for about 12 s. Send another message to see queueing or steering. |
| `fixture: helpers` | A helper chat started, steered and waited on, nested under its parent. |
| `fixture: smooth` | A reply streamed in small pieces, to check word fades and scroll position. |
| `fixture: transcript` | A long reply with headings, lists and code, to check scrolling. |
| `fixture: table` | A wide Markdown table. |
| `fixture: files` | Links to generated files (PDF, CSV, Markdown), to check previews. |
| `/thorough-bug-review` | A slash skill captured and read. |

## Looking at it

- **Text and structure.** Prefer reading the page's text or accessibility tree to screenshots; use a screenshot for layout and styling.
- **Themes.** Check both themes when you change styling.
- **Isolation.** Each chat is independent: use a new chat per scenario so earlier messages don't change what the scripted model does.
- **Not covered.** Native-only behavior (the quit dialog, notifications, Keychain) needs the desktop app, not the fixture.
