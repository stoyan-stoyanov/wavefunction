---
name: package-wavefunction-install-kit
description: Build and verify a shareable Wavefunction macOS app ZIP and checksum using its bundled Python runtime. Use when the user asks for an install kit, test kit, or app archive for another Mac.
---

# Package a Wavefunction install kit

Work from the repository root. The recipient needs a Mac matching the build architecture; running this app does not require Python, Rust, uv, or repository access. Docker is optional for computer use, and model/connector accounts remain the recipient's own.

## Prepare the candidate

- Check the requested branch, commit, and working tree. Preserve unrelated edits. Packaging does not authorize release publication, uploads, a workspace reset, PR merge, or replacing a local app.
- This kit is ad-hoc signed, so another Mac's Gatekeeper warns before opening it. When the user wants a DMG, or a build that opens without that warning, use [release-wavefunction-dmg](../release-wavefunction-dmg/SKILL.md) instead: it is Developer ID-signed and notarized.
- Reuse a successful bundle from this task when its application inputs are unchanged. Otherwise run `npm run desktop:build`. For a combined rebuild/reopen request, follow [rebuild-wavefunction](../rebuild-wavefunction/SKILL.md), then reuse that build.
- Use `scripts/package-desktop.mjs`.
- Keep user data, databases, credentials, test fixtures containing live accounts, and unrelated local files out of archives. A normal app ZIP needs no source snapshot or private account state. If source delivery is specifically requested, select the intended repository files separately and retain the original license and third-party notices.

## Package and verify

If the bundle is already built, run:

```sh
node scripts/package-desktop.mjs
```

The script seals the assembled app with an ad-hoc signature, verifies it, and writes the app ZIP and SHA-256 file to ignored `dist/desktop`. It stops if signing, verification, or archiving fails. Keep the archive and checksum from the same run; repackaging changes the checksum.

For the architecture produced by the current build, run:

```sh
codesign --verify --deep --strict src-tauri/target/release/bundle/macos/Wavefunction.app
unzip -tq dist/desktop/Wavefunction-0.1.0-macos-<arch>.zip
(cd dist/desktop && shasum -a 256 -c Wavefunction-0.1.0-macos-<arch>.zip.sha256)
```

Replace `<arch>` with the actual suffix, normally `arm64` on Apple Silicon. Confirm the app's `CFBundleIdentifier` is `app.harness.coding` and its version matches the candidate. Do not describe a working tree with local changes as an unchanged commit.

When testing extraction, use a task-owned temporary directory, verify the extracted bundle's signature, and remove that directory afterward. Native launch verification uses the isolated workflow in the rebuild skill; never replace the recipient/user app or data as an archive test. ZIP/signature checks are not a fresh-Mac installation test.

## Hand off

- Return absolute links to the ZIP and checksum. The recipient verifies the checksum, extracts the ZIP, moves `Wavefunction.app` to their chosen Applications directory if desired, then opens it and connects their model in Settings → Models.
- The app is ad-hoc signed unless this task explicitly configured another identity. Report notarization, clean-Mac testing, and other architectures as unverified when they were not exercised. Do not bypass operating-system security warnings for the recipient.
- Publish or send the archive or source only when requested, and only to the intended destination.
- Remove task-owned extraction folders, synthetic state, test processes, and disposable caches. Keep the archive, checksum, requested source changes, and useful verification evidence. Leave a requested user launch running.
