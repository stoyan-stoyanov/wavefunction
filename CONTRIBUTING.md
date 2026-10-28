# Contributing to Wavefunction

Thanks for taking the time to help.

Wavefunction is a small coding agent for macOS. We want it to stay small, so we're careful about what goes in. Bug fixes are always welcome. New features need a short proposal first, so you don't spend a weekend on something we can't merge.

Every pull request also has to show that it works, with reproduction steps, verification steps and evidence. We ask for these to make agentic coding smooth: a coding agent can follow the steps to check a change end to end, and the evidence shows what you saw. It also keeps the code in `main` verified.

This page covers:

- [Before you start](#before-you-start)
- [What we accept](#what-we-accept)
- [Proposing a feature](#proposing-a-feature)
- [Setting up](#setting-up)
- [Where things are](#where-things-are)
- [Opening a pull request](#opening-a-pull-request)
- [How your pull request is reviewed](#how-your-pull-request-is-reviewed)
- [Tests](#tests)
- [Using a coding agent](#using-a-coding-agent)

## Before you start

- Read the [README](README.md) to get a feel for what Wavefunction is and isn't.
- Search the [issues](https://github.com/stoyan-stoyanov/wavefunction/issues) and the [known issues](docs/KNOWN_ISSUES.md). Your bug or idea may already be there.
- Found a security problem? Don't open an issue. Report it privately as described in [SECURITY.md](SECURITY.md).
- Not sure whether something is a bug or a missing feature? Open a bug report and say so.

## What we accept

| You want to send | What to do |
|---|---|
| A bug fix | Open a pull request. For anything that isn't obvious, open a [bug report](https://github.com/stoyan-stoyanov/wavefunction/issues/new?template=bug_report.yml) first. |
| Docs, tests or polish that don't change how the app behaves | Open a pull request. |
| A new feature, or a change to how something works | Open a [feature proposal](https://github.com/stoyan-stoyanov/wavefunction/issues/new?template=feature_request.yml) and wait for it to be accepted before you start. |

## Proposing a feature

1. Open a feature proposal. Start with the problem, then describe the smallest change that would solve it.
2. A maintainer replies on the issue. If it's a fit, they add the `accepted` label. If not, they close it and explain why.
3. Once it's accepted, comment that you're working on it, then open a pull request that links the proposal.

We close feature pull requests that don't have an accepted proposal, even when the code is good. It's the only way to avoid people building things we then can't take.

### What fits

When you're deciding whether to propose something, these are the questions we'll ask:

- **Does it keep one agent with many chats?** Agent creation, team management and user-facing delegation are out of scope.
- **Is it worth it for people who don't need it?** Every setting, panel and mode makes the app a little harder to use for everyone. Improving what's there usually beats adding something new.
- **Could it be a skill, a connector or a line in `AGENTS.md`?** Wavefunction already loads `SKILL.md` skills, MCP connectors and `AGENTS.md`. If your idea works as one of those, it should be one.
- **Does it work with every provider?** We don't add features that only work with one provider's models or plans, unless they degrade cleanly elsewhere.
- **Does it keep data local?** No telemetry, no accounts of our own, nothing sent anywhere the user didn't choose.
- **Does it stay fast and cheap?** Startup time, responsiveness and token use matter. A change that makes every model request bigger needs a very good reason.

Things we're likely to accept: fixes, performance work, accessibility, support for another model provider, and smoothing rough edges in existing features. Things we're likely to decline: built-in apps (a browser, an editor, dashboards), features for one provider or one workflow, and options that only change appearance.

## Setting up

You need macOS, Node.js 22+ and [uv](https://docs.astral.sh/uv/), which installs Python 3.12.

```sh
npm run setup         # frontend packages and the Python environment, from their lockfiles
npm run dev:fixture   # the interface and engine in your browser; it prints the address
```

`dev:fixture` needs no account or model provider. It runs against a throwaway workspace with a synthetic repository, and a scripted model answers each chat while the commands, edits and Git it asks for run for real. Send `fixture: edit` for an edit and a check; [driving the fixture](.agents/skills/drive-fixture/SKILL.md) lists the other prompts, such as approval cards, terminals and helpers. The workspace is removed when you stop it.

While you work, run the tests near your change:

```sh
cd backend && uv run --no-sync pytest tests/test_chats.py -q -k <name>
npm --prefix frontend test -- src/coding/composer.test.tsx
```

### The desktop app

Changes to the Rust shell (`src-tauri`) or the packaged app also need Rust and the Xcode Command Line Tools. `npm run check` runs clippy and the Rust tests; they need only an empty `backend/dist/wavefunction-backend` folder in place of the frozen engine, which it creates.

`npm run desktop:dev` builds the frozen engine and runs the app with the interface reloading as you edit. It uses the same data folder as an installed Wavefunction unless `WAVEFUNCTION_DEV_DATA_DIR` names another. `npm run desktop:build` builds the app into `src-tauri/target/release/bundle/macos/`. [Development](docs/DEVELOPMENT.md) covers the rest, and the [architecture](docs/ARCHITECTURE.md) explains how the pieces fit together.

## Where things are

| Path | What's there |
|---|---|
| `frontend/` | The interface: React, Vite and zustand. `src/coding` is the app; `src/views` holds the settings, connector and skill pages. |
| `backend/` | The engine: FastAPI and LangGraph, in `src/wavefunction`. `coding_workspace.py` is the API the interface talks to. |
| `src-tauri/` | The Rust shell: the window, starting and stopping the engine, notifications and updates. |
| `scripts/` | The dev launcher and fixture, the engine and release builds, and verification scripts. |
| `docs/` | Architecture, development, the test policy, known issues and releasing. |
| `website/` | The landing page, a separate Next.js app. |
| `.agents/skills/` | Skills for coding agents: verifying a change, writing tests, driving the fixture, and rebuilding, packaging and releasing the app. |

## Opening a pull request

Keep each pull request to one change. Unrelated cleanup, refactors and dependency upgrades go in separate pull requests. Use synthetic repositories and data in tests, examples and screenshots, and never commit credentials, databases or personal data.

Fill in the [pull request template](.github/pull_request_template.md). What it asks for depends on the kind of change.

If your change is one users will notice, add a line for it under `Unreleased` in [CHANGELOG.md](CHANGELOG.md), written for them. [Releasing](docs/RELEASING.md#the-changelog) says how.

### Bug fixes

- **The bug.** What goes wrong, and who it affects.
- **How to reproduce it.** Steps that show the bug on `main`, ideally on a fresh install with a synthetic repository.
- **The cause and the fix.** What was wrong in the code, and what you changed.
- **How to verify the fix.** Steps a reviewer, or a coding agent, can follow to confirm the bug is gone.
- **The fixed behaviour.** What it looks like now: a screenshot, a recording, logs or command output.
- **A regression test,** if a test would have caught the bug. See [Tests](#tests).

### Features

- **The proposal.** A link to the accepted proposal.
- **How to verify it.** Steps a reviewer, or a coding agent, can run end to end, with the result to expect at each step. Write them so someone who didn't build the feature can follow them.
- **Your evidence.** What you ran and what you saw: screenshots, a recording, logs or test output from your own run of those steps.
- **No regressions.** The existing behaviour around your change that you checked still works, as well as the checks below.
- **Tests.** The cases that cover the new behaviour. See [Tests](#tests).

### Checks

Run this before you ask for a review, and paste its summary into the pull request:

```sh
npm run check
```

It runs what CI would fail on for the files you changed (`npm run check -- --all` runs everything) and ends with what failed and how to rerun it. `npm run fix` formats your code and applies the lint fixes it can.

The default tests use synthetic data and no model accounts. Tests marked `sandbox` run real commands through the macOS sandbox, so they only run on a Mac.

Once it's ready, open the pull request as ready for review, not as a draft. If you couldn't check something, say so; we'd rather know.

## How your pull request is reviewed

1. **CI.** Every pull request runs the frontend (lint, tests and build), the backend on Linux (Ruff and tests), the backend on macOS (the whole suite, sandbox and destructive cases included, and the launcher test), and the native shell (clippy and the Rust tests). The macOS jobs skip docs-only changes. `all-checks` passes when they all have.
2. **An automated review** may comment with findings. Fix the ones that describe a real bug in normal use; for the rest, a one-line reply saying why is enough.
3. **A maintainer reviews it.** They check that it's one change, that a feature has an accepted proposal, and that the tests earn their place. They, or their coding agent, follow your verification steps, so write them to be followed. The review looks for bugs your change introduces, not for style that lint already covers or refactors it didn't need. [AGENTS.md](AGENTS.md#code-review) has the rules reviews follow.
4. **A maintainer merges it** once checks pass and the review is done.

## Tests

The test suites have a hard limit: 240 backend cases and 230 frontend cases, and a time budget. The test runner fails if they go over, and a whole-suite run prints the current count.

The limit exists because coding agents, left to themselves, add tests for the sake of it. A test that restates the code doesn't catch bugs. It slows the suite down and makes every later change harder. So:

- Add a test when it guards against a real failure: a bug that happened, or behaviour people rely on.
- Test what the user sees or the system does, not how the code is written.
- If the suite is near the limit, replace a weaker test rather than squeezing in another.
- Don't merge unrelated scenarios into one test to get around the limit.

The [test policy](docs/TESTING.md) has the details.

## Using a coding agent

The repository comes with what an agent needs. [AGENTS.md](AGENTS.md) has the rules on scope, verification, tests and cleanup, and most coding agents read it on their own. [`.agents/skills`](.agents/skills) has skills for common jobs: [verifying a change](.agents/skills/verify-change/SKILL.md), [writing tests](.agents/skills/write-tests/SKILL.md), [driving the fixture](.agents/skills/drive-fixture/SKILL.md), and rebuilding and relaunching the app.

## License

Wavefunction is [MIT licensed](LICENSE). By submitting a contribution, you agree that it's licensed under the same MIT License and that you have the right to submit it.
