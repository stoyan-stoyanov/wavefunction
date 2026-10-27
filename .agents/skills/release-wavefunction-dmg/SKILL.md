---
name: release-wavefunction-dmg
description: Build, verify and hand over a Developer ID-signed, notarized Wavefunction DMG on this Mac with npm run release:local, then relaunch the local app. Use when the user asks for a new DMG, a signed or notarized build, or a release build to install on another Mac or send to someone.
---

# Build a notarized Wavefunction DMG

`npm run release:local` runs the Release workflow's steps on this Mac, so it costs no GitHub Actions minutes. It installs dependencies from the lockfiles, builds the backend and app, and collects the notices. Then it signs with the Developer ID in the keychains, notarizes and staples the app and the DMG, and writes the release files to `dist/release`. It tags, pushes and uploads nothing.

## Prepare

- **Commit.** Build the commit the user asked for. After merges that is usually the tip of `main`. Check the branch, commit and working tree, and preserve unrelated edits. The script says `with uncommitted changes` when the tree is dirty, untracked files included; report that instead of naming a clean commit.
- **Settings file.** `~/.config/wavefunction/release.env` names the notarization key's file, its Issuer ID, and optionally the key id and updater key path. It holds paths and ids only. If a setting or file is missing, the script stops and names it.
- **Credentials stay the user's.** Tell the user what belongs in the settings file. Never ask for, print, copy or upload key material: not the `.p8`, the `.p12`, its password or the updater key.
- **Several signers.** With more than one Developer ID in the keychains, the script stops. Ask the user which one, then set `APPLE_SIGNING_IDENTITY`.

## Close the app

The build writes `src-tauri/target/release/bundle/macos/Wavefunction.app`, which the local app usually runs from. Close it first, exactly as [rebuild-wavefunction](../rebuild-wavefunction/SKILL.md) describes:

- check `/api/activity` immediately before quitting, and ask if work is active or unknown;
- quit through the normal Quit action or AppleScript;
- wait for the app and its sidecar to exit;
- never force-kill.

Before quitting, note project, thread and skill counts from the read-only API, so you can compare them after the relaunch.

## Build

From the repository root, run it in the background with its log in a task-owned temporary file:

```sh
npm run release:local
```

- **Duration.** It takes roughly 15 to 25 minutes. Most of that is the two notarization submissions, which wait on Apple.
- **Keychain prompt.** The first signing can raise a macOS prompt to let `codesign` use the Developer ID key. Tell the user to choose **Always Allow**; do not click it for them. The build waits while the prompt is open.
- **When it finishes.** On success the log ends with `Notarized, stapled and accepted by Gatekeeper: <dmg>`. On failure, report the step and its message. A rejected notarization names the `xcrun notarytool log <id>` command that explains it.

## Verify

```sh
backend/.venv/bin/python scripts/verify-release.py dist/release
```

It checks:

- every file against `SHA256SUMS`;
- that Gatekeeper accepts the DMG, and the app inside it, as notarized Developer ID, and both staples validate;
- `syspolicy_check distribution`;
- that the bundled third-party notices are present;
- the app's identifier and version;
- that the updater archive holds only the app, with no macOS `._` entries, and its signature, against the public key in `tauri.conf.json`, and that `latest.json` matches it.

It mounts the DMG read-only in a temporary folder and detaches it. Report a failure as the release not verified, and do not hand it over.

## Hand over

- **Replace the Desktop copy.** If `~/Desktop/Wavefunction-<version>-macos-<arch>.dmg` exists, move it to `~/.Trash/` under a unique name, such as one with the commit or time. Never delete it.
- **Copy and confirm.** Copy the new DMG to the Desktop and confirm its SHA-256 matches `dist/release/SHA256SUMS`.
- **Installing.** To install, the user opens the DMG and drags Wavefunction to Applications, replacing an older copy.

## Relaunch

- **Launch.** Open the rebuilt `src-tauri/target/release/bundle/macos/Wavefunction.app`. It is now Developer ID-signed and notarized.
- **Check health.** Poll `/health`. Compare the project, thread and skill counts with those taken before quitting, using the verify steps in [rebuild-wavefunction](../rebuild-wavefunction/SKILL.md). Never print backend arguments or tokens.

## Report

Tell the user:

- the DMG path on the Desktop;
- the commit it was built from, and whether the tree was clean;
- the verification result;
- that the app was relaunched, and whether the counts matched.

The build does not authorize tagging, uploading to a GitHub release, publishing a release, or changing repository visibility. In-app updates read `latest.json` from a published GitHub release. Until the user asks for these files to be uploaded and published, installed apps will not see this build as an update.
