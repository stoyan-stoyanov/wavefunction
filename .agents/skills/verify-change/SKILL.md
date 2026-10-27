---
name: verify-change
description: Decide and run the checks a Wavefunction change needs before review, from npm run check to the browser fixture, the native app or a frozen build. Use before saying a change is done, or when unsure what to run.
---

# Verify a change

## 1. Always

```sh
npm run check
```

It runs what CI would fail on for the files your branch changed: typecheck, lint and related tests for the frontend; Ruff and the backend tests for the engine; clippy and the Rust tests for the shell; the launcher test for the dev scripts. It ends with a summary and the command to rerun each failed step. Fix failures your change caused; if one isn't yours, say so in the PR with its output.

While iterating, run the narrowest test:
- one backend file: `cd backend && uv run --no-sync pytest tests/test_x.py -k name`;
- one frontend file: `npm --prefix frontend test -- src/coding/x.test.tsx`.

## 2. Then, by what you changed

| You changed | Also do |
|---|---|
| Anything a user sees: interface, transcript, cards, settings | Look at it in the browser fixture ([drive-fixture](../drive-fixture/SKILL.md)). Check the states you touched, and both themes if you changed styling. |
| Engine behavior behind a route | A regression test that fails before your fix ([write-tests](../write-tests/SKILL.md)). The fixture if the change shows in the UI. If the response shape changed: `npm run generate:api` and commit the two generated files. |
| Prompts, tools, the environment message, caching | `npm run generate:prompt-surface` and review the diff of what the model reads. Keep prefixes stable (`AGENTS.md`). For provider-cache changes, the opt-in `scripts/verify-coding-live.py --cache-check` ([TESTING.md](../../../docs/TESTING.md)). |
| The sandbox, irreversible-command checks, deletion safeguards | Tests by inspection, classification and mocks only. Tests that must run a real deletion are marked `destructive` and run in CI, never locally. |
| The desktop shell (`src-tauri/`) | `npm run check` covers compile and tests. Behavior in the real app needs a rebuild: ask before using [rebuild-wavefunction](../rebuild-wavefunction/SKILL.md). |
| Packaging, the frozen engine's spec, bundled resources | A frozen check: `npm run sidecar:build`, then `uv run --no-sync --project backend python scripts/verify-coding-features.py --backend backend/dist/wavefunction-backend/wavefunction-backend`. |
| Storage: tables, settings, files on disk | Exercise it with existing data and with a fresh folder, both success and rejection. |
| Docs only | Check that the links resolve and the commands you name exist. |

## 3. Report

Put the following in the PR (CONTRIBUTING has the template):
- what you ran and its result, with the summary `npm run check` printed;
- what you looked at in the fixture or the app;
- anything you couldn't verify, and why.

Stop the servers and processes you started, and say that you did.
