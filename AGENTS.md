# Wavefunction repository instructions

Wavefunction is one built-in coding agent with many independent chats. Projects are collections of chats. Keep this product model: no agent creation, team management or user-facing delegation. (The agent's own helpers, `coding_agents.py`, are part of a chat.)

The code is the source of truth; the docs describe intent and how to work.

## Commands

| Task | Command |
|---|---|
| Install everything | `npm run setup` |
| Check a change before review | `npm run check` (only what your branch touched; `-- --all` for everything) |
| Format and apply lint fixes | `npm run fix` |
| See the app with no account | `npm run dev:fixture` (prints its URL; send `fixture: edit`, see [the fixture skill](.agents/skills/drive-fixture/SKILL.md)) |
| One backend test file | `cd backend && uv run --no-sync pytest tests/test_x.py -k name` |
| One frontend test file | `npm --prefix frontend test -- src/coding/x.test.tsx` |
| After changing a route's response | `npm run generate:api`, then commit `backend/openapi.json` and `frontend/src/lib/api.gen.ts` |
| After changing prompts, guidance or tool schemas | `npm run generate:prompt-surface`, then review its diff like code |

Run pytest from `backend/`: the IDs it prints, and `--deselect` paths, are relative to it. [Verifying a change](.agents/skills/verify-change/SKILL.md) says which checks fit which change.

## Where things are

| Area | Read |
|---|---|
| Engine: API, chats, tasks, tools, sandbox, providers, connectors | [backend/AGENTS.md](backend/AGENTS.md) |
| Interface: app shell, chat, composer, workbench, settings | [frontend/AGENTS.md](frontend/AGENTS.md) |
| Desktop shell: windows, engine lifecycle, quit, notifications | [src-tauri/AGENTS.md](src-tauri/AGENTS.md) |
| Landing page | [website/AGENTS.md](website/AGENTS.md) |
| How a task flows through the system | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Tests: budget, what earns a place, markers | [docs/TESTING.md](docs/TESTING.md), [writing tests](.agents/skills/write-tests/SKILL.md) |
| Accepted limitations | [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) |
| Rebuild, package or release the desktop app | [rebuild-wavefunction](.agents/skills/rebuild-wavefunction/SKILL.md), [package-wavefunction-install-kit](.agents/skills/package-wavefunction-install-kit/SKILL.md), [release-wavefunction-dmg](.agents/skills/release-wavefunction-dmg/SKILL.md) |

## Rules

- **Ownership.** Keep per-chat ownership through async responses, streams, checkpoints, approvals, files and computer sessions. A task captures its model, tools, skills and working directory when it starts.
- **Performance, caching and tokens** are requirements of every change. Keep prompt and tool prefixes stable and cache keys scoped to a conversation; send only necessary context and bounded or incremental tool results; avoid redundant model calls, workspace reads and repeated serialization.
- **Caches** stay bounded, owned by the right conversation or workspace, and invalidated when their inputs change. Never trade correctness or isolation for a cache hit. Prefer measured fixes, using the existing usage counters (cached vs total input, and output tokens).
- **Provider-cache changes** need protocol regressions for request headers, streamed history, retries and turn/account isolation; a stable cache key is not evidence of reuse. Measure with the opt-in `scripts/verify-coding-live.py --cache-check` on synthetic data, and report misses as well as hits.
- **Types.** A route's JSON is its response model in `responses.py`; undeclared keys are dropped, and the backend tests fail if a route returns one. Never edit `api.gen.ts` by hand. Backend files you touch must pass pyright (`npm run typecheck:backend`, part of `npm run check`).
- **Size.** In `backend/src` and `frontend/src`, files stay within 1,500 lines and functions within 150; a larger one may shrink, never grow. `npm run check` names what to split.
- **Files.** Protect edits with file versions. Preserve drafts and unrelated repository changes. Never load binary or truncated content into the editor as editable text.
- **Data.** Keep data and credentials out of Git. Test with isolated synthetic workspaces; exercise storage changes with nonempty data, success and rejection.
- **Deletions.** Tests that run real deletion commands carry `@pytest.mark.destructive` and are excluded by default; CI runs them on macOS. Never run them locally (`-m destructive`), never execute deletion command examples any other way, and mark any new test that runs one.
- **Cleanup.** Stop task-owned servers, watchers, browsers, processes, containers and synthetic data, failures included; track exact identities and report the outcome. Never stop pre-existing resources or prune Docker globally.
- **Other agents.** Several agents may work in parallel worktrees. The git stash is shared, so don't use it: commit work in progress instead. Use your own ports (the fixture picks free ones). Never use the owner's data folder, `~/Library/Application Support/app.harness.coding`; `npm run desktop:dev` uses it.
- **Pull requests.** Ready for review unless asked for a draft; merge only when authorized. Describe them as [CONTRIBUTING.md](CONTRIBUTING.md#opening-a-pull-request) says: for a fix, the bug, how to reproduce it, the cause and fix, how to verify it and the fixed behaviour; for a feature, the accepted proposal, steps someone else can run, your evidence and the regressions you checked.
- **Tests** stay within the budget in [docs/TESTING.md](docs/TESTING.md): delete obsolete or duplicate tests rather than hiding them or merging unrelated scenarios; replace weaker coverage before adding near the limit.
- **Learnings.** When you find something non-obvious about an area, add one line to the nearest `AGENTS.md`.

## Scope and implementation

- The original task and explicitly agreed changes are the scope. Finish that outcome before proposing adjacent improvements; ask before expanding it.
- Choose the simplest complete fix for the root cause. Reuse existing patterns and dependencies.
- Reject overengineering: no abstractions, frameworks, options, fallbacks or dependencies for hypothetical needs. Added complexity must serve a demonstrated requirement of the task.
- Keep unrelated cleanup, refactors, dependency upgrades and features out of the change.
- Keep verification proportional to the behavior and risk; prefer meaningful regression tests over assertions that mirror the implementation.
- Stop once the agreed outcome and its validation are done. Later findings follow the review rules below.

## Code review

- Focus on consequential bugs the PR introduces. Explain the failing workflow and how to reproduce it.
- Respect intentional behavior described in the PR. Don't ask for unrelated refactors, features, or style that lint covers.
- A suggestion alone does not authorize a product change or scope expansion; check it against the original scope and observable behavior.
- Reject complexity without a demonstrated bug or unmet agreed requirement, and prefer the simplest adequate fix. Architectural preferences and speculative scenarios are not merge blockers.
- Read `docs/KNOWN_ISSUES.md` before reporting a limitation; reopen an accepted one only if the PR makes it materially worse.
- On your own PR, fix only findings with a concrete failing workflow in normal use. Decline the rest (hardening for hostile or extreme inputs, sub-second races, bounded bookkeeping) with a one-line reason, and record real limitations in `docs/KNOWN_ISSUES.md`. Stop once CI passes and no such finding remains.

## Names

"Harness", the app's earlier name, survives only in storage identifiers that must not change: the bundle and data folder `app.harness.coding`, `harness-workspace.sqlite`, `harness_*` settings, `harness.*` browser storage keys, `.harness-worktrees`, and the `.harness/skills` folders skills are discovered in. Use "Wavefunction" everywhere else.
