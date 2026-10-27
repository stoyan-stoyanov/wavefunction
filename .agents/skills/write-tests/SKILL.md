---
name: write-tests
description: Write or change Wavefunction tests that earn their place under the case and time budget, using the existing fixtures. Use when adding a regression test, testing a new behavior, or a test fails for reasons you need to understand.
---

# Write tests

The policy is in [docs/TESTING.md](../../../docs/TESTING.md): a budget of 240 backend and 230 frontend cases, 30 s per backend test, and a time limit for each suite.

## Before adding a test

- **Find the existing test for this behavior.** Tests drive the HTTP API, so search by route or behavior: `rg -l "/fork" backend/tests/`, `rg -l "composer" frontend/src`. Extending a test of the same workflow is often better than a new case.
- **Make a bug fix's test fail first.** Run it against the code without your fix and see it fail for the reason you claim. A test that passes either way is not a regression test.
- **Decide what to assert.** What the user sees, what the API returns, what reaches disk or the model. Not private helpers, markup, CSS or source text.
- **Watch the budget.** Near it, replace a weaker test instead of adding one. Never merge unrelated scenarios to save a case.

## Backend (`backend/tests/`)

- **Fixtures** (`conftest.py`): `coding` gives `(client, workspace, services)` on a temporary data folder. Helpers (`support.py`): `create(client, ...)` makes a chat; `repository(tmp_path)` and `git(...)` make a synthetic Git repository. Import helpers from `support`, never from another test module.
- **Scripted model.** `script_gateway(services.models, [...])` with `tool_call_message(name, args, id)` and `AIMessage(...)` drives a real tool loop without a model. Then `ws.send(thread_id, text)` and `services.runtime.wait(run_id)`.
- **Markers.**
  - `@pytest.mark.sandbox` for anything that runs a command through the app's tools: Git, edits, terminals, discovery.
  - `@pytest.mark.destructive` if it runs a real deletion command, even in a temporary repository. That test then runs only in CI.
  - Cleanup that only deletes temporary folders isn't needed: pytest discards `tmp_path`.
- **No sleeps.** Wait for state, not time: the existing tests poll a condition with a deadline. Keep each test under 30 s.
- **Run from `backend/`.** `uv run --no-sync pytest tests/test_x.py -k name`. IDs and `--deselect` paths are relative to it.

## Frontend (`frontend/src/`)

- **Placement.** Tests sit next to the code (`x.test.tsx`), with Testing Library and `userEvent`.
- **Queries.** Find elements by role and accessible name, the way a user or browser automation would.
- **Mocks.** Mock the API at `coding/api.ts` or `lib/api.ts` and assert where requests go and what the user sees.
- **Time.** Each test has 5 s.

## After

Run `npm run check`. If a test is flaky, fix it; don't retry or add a sleep. If you can't fix it now, skip it with `reason="flaky: <issue link>"` and open an issue.
