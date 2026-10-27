---
name: coding-workflow
description: "Complete a coding task in an existing repository. Use for implementing features or making a substantial code change."
---

Read the repository's AGENTS.md, dependency manifests, relevant implementation, callers, and tests. Use find_files and search_files before opening large files. Read nested instructions for the directories you change.

For substantial tasks, keep a short checklist. Make focused edits with edit_file and preserve existing work. Prefer the project's established stack and conventions. Avoid new dependencies unless the requirement needs them.

Run the relevant tests and build or type checks. Inspect the actual diff, including untracked files, before declaring completion. A successful tool call is evidence of an action, not proof of the feature's correctness.

For headless browser checks such as screenshots, use a standalone test browser, never the installed /Applications/Google Chrome.app: started from here it tries to update itself, which macOS blocks and reports to the user. Use the project's own Playwright browser, or install chrome-headless-shell once into the writable cache, where later tasks reuse it: `npx @puppeteer/browsers install chrome-headless-shell@stable --path "${XDG_CACHE_HOME:-$HOME/.cache}/browsers"` prints the binary to launch.

Keep task-owned process IDs, ports, temporary files, and test data scoped to this work. Clean up after testing, including failures. Do not stop someone else's processes or alter unrelated data. Report what changed, what passed, and any remaining limitation.
