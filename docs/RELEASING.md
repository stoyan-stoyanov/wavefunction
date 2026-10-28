# Releasing Wavefunction

A version tag builds a signed, notarized app on GitHub Actions and opens a **draft** release. Publishing the draft ships it: the website's download button (`releases/latest`) and every installed app's update check read the latest published release.

## Release

1. Choose the version (see [the changelog](#the-changelog)), set it everywhere it is recorded, then commit and merge it:

   ```sh
   node scripts/version.mjs 0.2.0
   ```

   It also dates the `Unreleased` section of [CHANGELOG.md](../CHANGELOG.md) as `0.2.0` and starts a fresh one, and refuses when `Unreleased` is empty. Read the section over in this pull request: it is what users will see.

2. Tag the merged commit on `main` and push the tag:

   ```sh
   git tag v0.2.0 && git push origin v0.2.0
   ```

3. The [Release workflow](../.github/workflows/release.yml) checks that the tag matches the app version and that the changelog has notes for it, then builds the backend and app. It signs every binary with Developer ID under the hardened runtime, notarizes and staples, and drafts a release containing:
   - `Wavefunction-<version>-macos-arm64.dmg` and `.zip`, to download
   - `Wavefunction-<version>-macos-arm64.app.tar.gz` and its `.sig`, which installed apps update from
   - `latest.json`, the update manifest, with the release's changelog section as its notes
   - `Wavefunction-<version>-macos-arm64-notices-and-sources.zip`: the third-party notices, also inside the app under `Contents/Resources/legal`, and the source archives of the bundled Python and Rust dependencies
   - `SHA256SUMS`
   The draft's description is the same changelog section.
4. Review the draft, optionally install the DMG, and publish it.

## The changelog

[CHANGELOG.md](../CHANGELOG.md) is written for people who use the app, not for reviewers. A pull request that changes what users see or can do adds one line under `## Unreleased`, in the group that fits (`Added`, `Changed`, `Fixed`, `Removed` or `Security`), ending with a link to the pull request. Say what changed for the user, not what changed in the code. Tests, refactors, CI, docs, dependency updates and website changes get no line.

A release's section becomes its GitHub release notes and the notes **Settings › About** shows next to **Install and restart**.

Before 1.0, a release that adds or changes behaviour raises the minor version (0.2.0); one with only fixes raises the patch (0.1.2).

## Updates

The app checks `https://github.com/stoyan-stoyanov/wavefunction/releases/latest/download/latest.json` shortly after launch and every six hours, but only in the packaged app, not in development builds. When a newer version is published, **Update to x.y.z** appears above Settings, and **Settings › About** offers **Install and restart** with the release's notes.

The download is verified against the updater public key in `src-tauri/tauri.conf.json`, then replaces the app. The restart goes through the same check as quitting: it asks first when tasks are running, and stops the engine before the new version starts. Declining applies the update on the next launch.

Releases must be on a public repository for the download link and update check to work.

## Signing material (GitHub secrets)

| Secret | What it is |
|---|---|
| `APPLE_CERTIFICATE` | The Developer ID Application certificate exported as `.p12`, base64-encoded (`base64 -i cert.p12 \| pbcopy`) |
| `APPLE_CERTIFICATE_PASSWORD` | The password chosen when exporting the `.p12` |
| `APPLE_API_KEY` | Contents of the App Store Connect API key `.p8` (Developer role), used for notarization |
| `APPLE_API_KEY_ID`, `APPLE_API_ISSUER` | That key's Key ID and the account's Issuer ID |
| `TAURI_SIGNING_PRIVATE_KEY` | Contents of the updater private key |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Its password; empty for the current key |

Two kinds of signing are involved:

- **Apple signing** lets macOS open the app. Changing the Developer ID certificate, for example from an individual to an organization membership with the same Team ID, only means replacing the first two secrets. Do not revoke an old certificate: apps already signed with it would stop opening.
- **The updater key** proves an update came from this project. It is kept outside the repository, in `~/.tauri/wavefunction-updater.key`, and must be backed up. **If it is lost, installed apps can never update again.** Its public half is in `tauri.conf.json`.

## Locally

### A release build on this Mac

`npm run release:local` runs the Release workflow's steps here instead of on GitHub Actions: dependencies installed from the lockfiles, backend, app, notices collected afresh, Developer ID signing, notarization and stapling, then a Gatekeeper check of the DMG. The files land in `dist/release`, the same set the workflow drafts. Nothing is tagged, pushed or uploaded, so no Actions minutes are used; upload them to a release yourself if it should ship.

It signs with the Developer ID Application identity in your keychains; with more than one, set `APPLE_SIGNING_IDENTITY` to choose. The first time, macOS may ask to let `codesign` use that key: choose **Always Allow**. The rest comes from `~/.config/wavefunction/release.env`, which names files and ids rather than holding keys:

```sh
APPLE_API_KEY_PATH=~/path/to/AuthKey_XXXXXXXXXX.p8
APPLE_API_ISSUER=<Issuer ID from App Store Connect, Users and Access, Integrations>
# Optional: APPLE_API_KEY_ID (default: from the .p8 file name),
# TAURI_SIGNING_PRIVATE_KEY_PATH (default: ~/.tauri/wavefunction-updater.key), APPLE_SIGNING_IDENTITY.
```

Environment variables of the same names win over the file. To check the result before installing or sending it:

```sh
backend/.venv/bin/python scripts/verify-release.py dist/release
```

It checks the checksums, that Gatekeeper accepts the DMG and the app inside it as notarized with valid staples, `syspolicy_check`, the bundled notices, the app's identifier and version, and the updater signature and `latest.json`. The [release-wavefunction-dmg](../.agents/skills/release-wavefunction-dmg/SKILL.md) skill describes the whole hand-over for an agent.

### Checking the packaging

`npm run desktop:build` remains the everyday local build, which is signed ad hoc. To check the release packaging without Apple credentials:

```sh
npm run sidecar:build
./frontend/node_modules/.bin/tauri build --ci --bundles app --config src-tauri/tauri.conf.json
APPLE_SIGNING_IDENTITY=- TAURI_SIGNING_PRIVATE_KEY_PATH=~/.tauri/wavefunction-updater.key node scripts/release-desktop.mjs
```

This signs ad hoc under the same hardened runtime and writes the release files to `dist/release`, without notarization. To include the notices as a release does, run `backend/.venv/bin/python scripts/release-notices.py` before the last command. The Python engine needs the entitlements in `src-tauri/backend.entitlements` under the hardened runtime.
