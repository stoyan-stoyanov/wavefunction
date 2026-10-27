# Common Wavefunction changes

Use [the architecture](ARCHITECTURE.md) to identify ownership and [the test policy](TESTING.md) to choose meaningful coverage. Preserve existing chats, provider settings, drafts, and unrelated repository edits.

## Chat or workspace behavior

Trace the component under `frontend/src/coding/` through `coding/store.ts` and `coding/api.ts` to `backend/src/wavefunction/coding_workspace.py`. Shared request handling belongs in `frontend/src/lib/transport.ts`. A route's response is the model it declares in `responses.py`; regenerate the TypeScript with `npm run generate:api` and derive frontend types from `lib/api.gen.ts`.

Capture the chat, run, and folder when an operation starts. A later selection, response, stream event, or project setting change must not redirect it. Verify the user-visible workflow and a relevant ownership or stale-state failure, rather than incidental component markup.

## Tools and agent execution

Follow `RuntimeManager` in `runtime.py`, `build_agent` in `agent_factory.py`, and `ToolContext` in `toolkit.py`. Extend the existing runtime and event stream. Pass coding services explicitly; do not discover application mode by adding attributes to the repository. Prompt, guidance or tool-schema changes alter `backend/tests/fixtures/prompt_surface/default_chat.md`: regenerate it with `npm run generate:prompt-surface` and review the diff.

Read callers before changing shared tools. Keep provider requests, skills, connectors, command execution, and computer sessions tied to the captured task. Use scripted models and isolated repositories for ordinary verification. Permission and command changes require the real macOS sandbox.

## Files, Git, and recovery

Start with `coding_workspace.py`, `change_review.py`, `coding_delivery.py`, `coding_git_ops.py`, and `coding_recovery.py`. Preserve expected file versions, captured folders, raw recovery bytes, and unrelated Git staging. Exercise a successful operation and its relevant stale/conflicting rejection against nonempty synthetic data.

## Settings, providers, connectors, and skills

Start from `coding/settings.tsx` or the current connector/skills view and follow the exact API it calls. Reuse the existing model gateway, connector registry, and versioned skill library. Keep stored compatibility keys when changing display names; do not introduce credential or data migrations for cosmetic consistency.

## Storage and packaging

Apply storage changes locally to populated synthetic data, including success, rejection, and ownership cases. Preserve existing application identity and data directories. For desktop changes, use the [rebuild skill](../.agents/skills/rebuild-wavefunction/SKILL.md), then verify the frozen runtime, signed bundle, and startup. A passing source test does not exercise PyInstaller's imports or bundled resources.

Stop and remove task-owned verification resources when finished. Keep source, reusable fixtures, build deliverables, and useful verification evidence.
