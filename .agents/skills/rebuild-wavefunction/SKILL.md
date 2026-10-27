---
name: rebuild-wavefunction
description: Rebuild and relaunch the local Wavefunction macOS app with its bundled Python sidecar while preserving conversations, projects, skills, and credentials. Use when the user requests an app rebuild or restart after changes.
---

# Rebuild and relaunch Wavefunction

Wavefunction uses a self-contained frozen Python sidecar and a signed app/ZIP.

## Identify the checkout and app

- Check the branch and working tree. Build the requested checkout, including intended local changes; preserve unrelated edits.
- Follow the requested app path. Otherwise identify an already-running Wavefunction executable with `ps -axo pid=,comm=` and check `/Applications/Wavefunction.app` and `~/Applications/Wavefunction.app`. If no installation exists, launch the built bundle from this checkout.
- Never print full backend arguments: they contain a per-launch bearer token. Keep any token needed for a read-only API check in memory.
- Preserve `~/Library/Application Support/app.harness.coding`, including conversation/checkpoint databases, the skills library, files, settings, and credentials. Do not reset it or read credential contents for a rebuild.

For a launch-only request, use the existing app when it contains the requested changes. Do not rebuild solely because a skill or other guidance changed, unless the user asks to rebuild.

## Close an existing Wavefunction app

- Check for active work immediately before quitting. Prefer the app's authenticated `/api/activity` endpoint when session access is available; otherwise read only run statuses from its saved history. Queued, running, or waiting tasks count as active; an open, idle chat does not. If work is active, or the status cannot be established, ask the user before quitting.
- When idle, a rebuild/restart request authorizes quitting without another confirmation. Use the normal Quit action through native app controls, or AppleScript (`osascript`) targeting the exact running Wavefunction bundle when those controls are unavailable. Preserve the app's own active-work confirmation in case a task starts after the check; do not dismiss that warning without user approval.
- Wait for the app and its sidecar to exit before replacing the bundle. If normal Quit does not complete, report the blocker; do not force-kill the app or bypass an unresolved dialog. Honor cancellation, and leave unrelated processes untouched.

## Build and package

From the repository root, run:

```sh
npm run desktop:build
```

This freezes the Python backend, bundles the built-in skills and computer tools, builds the React/Tauri desktop, then seals the complete app with an ad-hoc signature and creates the ZIP/checksum. Outputs:

- `src-tauri/target/release/bundle/macos/Wavefunction.app`
- `dist/desktop/Wavefunction-0.1.0-macos-<arch>.zip`
- The ZIP's adjacent `.sha256` file

Use `npm run setup` only when development dependencies are missing. Rust and Xcode Command Line Tools are also needed to build. If Cargo is absent from PATH but installed in `~/.cargo/bin`, add that directory for the build command. Keep large build logs in a task-specific temporary file. Stop on failure and preserve any existing installation.

Do not provision a managed runtime: the Wavefunction app contains Python, its libraries, and ripgrep. Developer ID signing, notarization, and automatic updates are separate work; do not claim an ad-hoc build has them.

If the user requested installation or updating an existing installed Wavefunction, copy the successful signed bundle to the selected path while it is closed, then verify the copied signature. Otherwise launch the built bundle in place; creating an installation is optional.

## Verify and launch

- Verify the app with `codesign --verify --deep --strict <app-path>`, check the ZIP with `unzip -tq <zip-path>`, and run `shasum -a 256 -c <zip-name>.sha256` from `dist/desktop`.
- For a synthetic native regression check, `uv run --no-sync --project backend python scripts/verify-native.py --app <app-path>` verifies the frozen tools and restart persistence and cleans up afterward. It refuses existing Wavefunction data. Never remove or rename user data to make it run; use a disposable OS account when needed.
- Launch the requested signed app through the platform's application launcher. Confirm its executable and bundled sidecar remain running, and poll its own loopback `/health` for `status: healthy` and the Wavefunction data path. Use read-only `/api/coding/workspace` and `/api/skills` checks when session access is available; do not submit a coding task as a launch check.
- Inspect the native window when native UI tools are available. Report visual verification as unavailable when they are not; a launch request alone does not prove startup succeeded.
- For existing data, compare read-only project/thread/skill IDs or counts before and after. Do not reset the workspace or replace user-authored skills.
- Clean up synthetic test processes, workspaces, disposable build caches, and temporary verification resources. Keep the requested app running for the user, along with source, build deliverables, and useful evidence.
- Report the launched app path, build/startup checks, cleanup outcome, and material limitations. Copying or rebuilding does not itself authorize a PR merge, release upload, or repository visibility change.
