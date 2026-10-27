# Testing

## Budget

| | Cases | One test | All tests together |
|---|---|---|---|
| Backend (Python, plus native Rust and launcher tests) | 240 | 30 s | 240 s |
| Frontend (Vitest) | 230 | 5 s (Vitest's timeout) | 90 s |

- **Enforcement.** `backend/tests/conftest.py` and the reporter in `frontend/vite.config.ts` enforce these.
- **What counts.** Count collected cases, including parameterizations and skipped or opt-in ones. The backend count also covers `#[test]` functions in `src-tauri/src` and `test(...)` calls in `scripts/*.test.mjs`.
- **Time.** "All tests together" is the sum of test durations across workers, so a subset can only exceed it if the whole suite would.
- **Output.** A whole-suite run prints its count and test time; for the backend, `cd backend && uv run --no-sync pytest --collect-only -q` prints the count.
- **Over the limit.** Replace weaker coverage rather than adding near the limit. Never merge unrelated scenarios into one test to lower the count, and never hide tests with exclusions.

## What earns a place

A test earns its place by catching a concrete failure, for example:
- wrong ownership;
- overwritten bytes;
- lost history or drafts;
- unsafe commands;
- rejected or duplicated actions;
- broken provider or connector protocol;
- unusable controls;
- leaked resources.

Use real isolated repositories, actual tool loops and protocol fixtures where you can.

- **A bug fix's regression test fails on the code before the fix.** Check that it does.
- **Test behavior,** meaning what the user sees and what the system does: not markup, CSS strings, private helpers, or the source text itself.
- **Seams.** Don't add a seam to the product just so a test can reach it. Mock at real boundaries: the model, the network, the clock.
- **Removed features.** Don't add checks that a removed feature stays absent.
- **Negative assertions.** A useful one checks a current rejection or safety boundary, such as a stale save or a denied approval.
- **Distinct failures.** Keep failure modes that need different behavior apart, and avoid matrices of equivalent labels, formats or modes. Related steps of one workflow can share a test, for example rewind conflict handling, or attachment ownership through a fork.

## Markers (backend)

| Marker | Meaning | Runs |
|---|---|---|
| `sandbox` | Runs commands through the app's tools (Git, edits, terminals, discovery), so it needs macOS Seatbelt | macOS; skipped elsewhere |
| `destructive` | Runs a real deletion command in a temporary repository | CI only (the macOS job). Never locally. |
| `live` | Talks to real external services | Only with `-m live` |

A test that runs a deletion command, even in a temporary repository, must carry `destructive`. If the deletion is only cleanup of a temporary folder, drop it instead: pytest discards `tmp_path` itself.

## Running

```sh
npm run check                # what CI would fail on, for the files your branch changed
npm test                     # both suites, as CI runs them (destructive and live excluded)
npm run test:launcher        # the dev launcher's integration test
cd backend && uv run --no-sync pytest tests/test_x.py -k name
npm --prefix frontend test -- src/coding/x.test.tsx
```

Run pytest from `backend/`. Its node IDs (`tests/...`) and `--deselect` paths are relative to that folder; from the repository root a `--deselect` path silently matches nothing.

The backend tests drive the HTTP API with `ScriptedModel` and real tools on synthetic repositories. Each run gets a temporary data folder and a signed-out `gh`, and every API response a test receives is checked against its route's response model.

`test_prompt_surface.py` renders a default chat's first model request (system prompt, environment message, tools) through the real app and compares it with `tests/fixtures/prompt_surface/default_chat.md`. It is marked `sandbox`, because reading `AGENTS.md` runs the sandboxed file helper.

## Flaky tests

A flaky test is a bug. Don't retry it, lengthen a timeout, or add a sleep. Fix it in the change that found it; if that isn't possible, skip it with `@pytest.mark.skip(reason="flaky: <issue link>")` and open an issue. A skipped flaky test is fixed or deleted within two weeks.

## What the suites cover

| Area | Failure modes covered |
|---|---|
| Chats, projects and streams | Captured models, folders and attachments; independent checkpoints; queued follow-ups; stale snapshot and stream races; cancellation; fork, search, archive, delete and rename; project order; restart |
| File and command safety | Exact bytes and encodings, atomic failure, concurrent saves, traversal, symlinks and private storage, grants, native Seatbelt, timeouts, output replay, child-process cleanup |
| Changes and Git | Staged, unstaged, renamed and copied diffs; per-chat change records; worktrees; captured context, prompts and questions; the GitHub proxy's sign-in and refusals |
| Guidance and execution | Discovery precedence, immutable skill versions, slash invocation, approvals and Plan restrictions, compaction, truncation, retries, no-progress detection, helpers |
| Providers and connectors | Sign-in, refresh and storage; streamed tool calls; capabilities; real stdio and HTTP MCP fixtures; OAuth ports and refresh; scoped secrets; prompt-cache behavior |
| Computer use and terminals | Private takeover, queued input, lifecycle and readiness, PTY isolation, replay, search, resize and close, preserved user terminals |
| Frontend | Chat ownership, drafts, context and slash menus, file conflicts, preferences, providers, connector setup, editable skills, notifications |
| Native and launcher | Engine startup, quit, independent development sessions, occupied ports, signal and resource cleanup |

## Opt-in checks outside the suites

These use synthetic data, clean up after themselves, and are not needed for docs-only or tests-only changes.

- `scripts/verify-coding-features.py --backend <frozen engine>`: a frozen build's tools, terminals, history, helpers and restart, with a scripted model.
- `scripts/verify-coding-live.py [--provider openrouter] --cache-check`: real model calls through an existing sign-in or a key file; measures provider-reported cached tokens. Report misses as well as hits. Cross-chat cache hits are reported, not required.
- `scripts/verify-orchestration-live.py`: real parallel delegation with a cost stop.
- `scripts/verify-computer.py --build`: real computer-use input in Docker.
- `scripts/verify-microsoft-sdk.ps1`: offline compatibility of the pinned Microsoft Graph module.
