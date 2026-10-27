# Wavefunction desktop shell (src-tauri)

Tauri v2 in Rust, plus one Objective-C file for notifications. Read the root [AGENTS.md](../AGENTS.md) first.

## Where things are

| Area | Files |
|---|---|
| Startup, window, starting and supervising the engine | `src/lib.rs` |
| Quit and restart, with the active-work check | `src/quit.rs` |
| macOS notifications (long poll to the engine) | `src/notifications.rs`, `src/notifications.m`, `build.rs` |
| Configuration and bundled resources | `tauri.conf.json`, `Cargo.toml` |

## Checking a change

- `npm run check` runs clippy and the Rust tests when this folder changed.
- They compile against the bundled engine's folder but never run it. Without a built engine, an empty folder stands in: `mkdir -p backend/dist/wavefunction-backend`. `npm run check` creates it.
- Behavior in the real app (quit dialog, notifications, Keychain, signing) needs a build: use [rebuild-wavefunction](../.agents/skills/rebuild-wavefunction/SKILL.md) when asked to rebuild.

## Rules here

- Never print the engine's process arguments: they hold a session token.
- `npm run desktop:dev` and the installed app share the owner's data folder (`app.harness.coding`). Set `WAVEFUNCTION_DEV_DATA_DIR` to a scratch folder to keep `desktop:dev` off it.
- Keep the `app.harness.coding` identity and the signing setup as they are.
- Distinguish a successful build from a verified launch.
