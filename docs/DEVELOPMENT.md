# Developing Wavefunction

Read [AGENTS.md](../AGENTS.md) first. Where a document and the code disagree, the code is right.

## Setup

You need macOS, Node.js 22 (`.nvmrc`) and [uv](https://docs.astral.sh/uv/), which installs Python 3.12. Native work also needs Rust and the Xcode Command Line Tools.

```sh
npm run setup
```

## Running it

| Command | What it is | Data |
|---|---|---|
| `npm run dev:fixture` | Interface and engine in the browser, with a scripted model. No account needed. See [driving the fixture](../.agents/skills/drive-fixture/SKILL.md). | A temporary folder, removed when it stops |
| `npm run dev` | Interface and engine in the browser, with your real providers | `.wavefunction-data/` in this checkout |
| `npm run desktop:dev` | The native app, with the interface reloading as you edit | **The installed app's folder** (`app.harness.coding`), unless `WAVEFUNCTION_DEV_DATA_DIR` names another |

- **Ports.** Both browser launchers take `--port` and `--api-port`. The fixture picks free ports by default, so several can run at once. They refuse a port that's in use, and Ctrl+C stops everything they started.
- **The native app.** It needs the frozen engine; `npm run desktop:dev` builds it first. To rebuild and relaunch an installed app, use [rebuild-wavefunction](../.agents/skills/rebuild-wavefunction/SKILL.md).

## Checks

`npm run check` runs what CI would fail on for the files your branch changed, and ends with what failed and how to rerun it. Use `npm run check -- --all` for everything. `npm run fix` formats the code (Ruff and Prettier at 110 characters, rustfmt) and applies lint fixes. `npm run check:health` keeps product files within 1,500 lines and functions within 150; a larger one may shrink, never grow. Generated files: `npm run generate:api` (the OpenAPI schema and the frontend's API types) and `npm run generate:prompt-surface` (what a default chat sends the model); `npm run typecheck:backend` runs pyright on the backend files your branch touched. [Verifying a change](../.agents/skills/verify-change/SKILL.md) covers when the browser, the native app or a frozen build is needed, and [docs/TESTING.md](TESTING.md) has the test rules.

CI runs these jobs on every pull request and push to `main`, and all must pass:

| Job | Runs on | What it checks |
|---|---|---|
| frontend | Linux | lint, the frontend tests, and the build (which typechecks) |
| backend | Linux | Ruff and the backend tests; `sandbox` tests skip here |
| backend-macos | macOS | the whole backend suite, the `destructive` tests, and the launcher test |
| native | macOS | clippy and the Rust tests |

A newer push to a pull request cancels its older run. On `main` a started run finishes; when several merges queue up behind it, GitHub keeps only the newest waiting one.

## Working alongside other agents

Several agents can work at once, each in its own git worktree:

- **The stash.** It's shared by every worktree, so don't use it: commit work in progress instead.
- **Ports.** Give each running server its own; the fixture does this itself.
- **Data.** Keep `npm run desktop:dev` off the owner's data with `WAVEFUNCTION_DEV_DATA_DIR`.
- **Engine builds.** `npm run sidecar:build` keeps PyInstaller's cache inside the checkout (`backend/build/`), so parallel builds don't collide; uv's shared cache is safe to share.
- **Rust without a build.** `cargo check` and `cargo test` need only an empty `backend/dist/wavefunction-backend` folder, not a built engine.

## The frozen engine

`npm run sidecar:build` freezes the engine with PyInstaller into `backend/dist/wavefunction-backend`: an executable plus its `_internal` runtime. Tauri bundles that folder into `Wavefunction.app/Contents/Resources/backend` and starts it from there, so Python, its libraries, the built-in skills and ripgrep load from the app without extracting. Keep the folder together when copying or measuring it.
