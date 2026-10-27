# Inspecting and rebuilding third-party libraries

Wavefunction retains the original MIT license and third-party notices. Dependencies retain their own licenses. The release notice generator records exact versions and collects Python source distributions and Rust crate archives with checksums from the lockfiles.

The desktop bundles a frozen Python sidecar inside the signed app. It has no separate installed Python environment. To replace a Python library, build from the matching Wavefunction source checkout with Node.js, uv, Rust, and Xcode Command Line Tools available:

```sh
npm run setup
uv pip install --python backend/.venv/bin/python --no-deps /path/to/modified/library
npm run desktop:build
```

The sidecar build uses the existing checkout environment without synchronizing it again, so it freezes the modified library. Adjust that library's build dependencies as needed. The result is a newly signed local app under `src-tauri/target/release/bundle/macos/`; the original app and its data remain separate. Quit the old Wavefunction app before launching the rebuilt bundle. Preserve the application data directory and verify the rebuilt tools against synthetic data.

For Rust changes, modify the matching dependency source and configure Cargo to use it, then rebuild through the same desktop command. Keep original notices with redistributed material. The [third-party notices](../THIRD_PARTY_NOTICES.md) describe the source and notice collection required when preparing a published distribution; ordinary local builds do not generate those archives automatically.
