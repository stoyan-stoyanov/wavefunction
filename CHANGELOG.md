# Changelog

What changed in each release of Wavefunction, for the people who use it. A pull request that changes what users see or can do adds its line under **Unreleased**. Each release's section becomes its GitHub release notes and the notes the app shows before it updates; [Releasing](docs/RELEASING.md#the-changelog) has the details.

## Unreleased

### Added

- Model lists show when each model was released, where that's known. ([#18](https://github.com/stoyan-stoyanov/wavefunction/pull/18))

### Changed

- The app uses less memory after it starts: it loads only your default model's provider, and connector code only if you have a connector. ([#25](https://github.com/stoyan-stoyanov/wavefunction/pull/25))
- The app opens to your last chat straight away, so you can start typing before the engine has finished starting. A message sent early goes as soon as the chat has loaded. ([#24](https://github.com/stoyan-stoyanov/wavefunction/pull/24))
- Adding a provider in Settings lists all its models with checkboxes, so you can pick several at once, instead of one from a dropdown. ([#18](https://github.com/stoyan-stoyanov/wavefunction/pull/18))

### Fixed

- Messages send faster in repositories too large for code history, because each one no longer scans every file before giving up. ([#23](https://github.com/stoyan-stoyanov/wavefunction/pull/23))
- Signing in to a connector no longer freezes the app for half a minute on a network whose DNS doesn't answer. ([#21](https://github.com/stoyan-stoyanov/wavefunction/pull/21))

## 0.2.0 - 2026-10-29

### Added

- Settings › About shows what a new version changes before you install it. ([#8](https://github.com/stoyan-stoyanov/wavefunction/pull/8))
- Chats on Anthropic, Gemini or Vercel AI Gateway models can search the web with that provider's own search, without a Tavily key. ([#14](https://github.com/stoyan-stoyanov/wavefunction/pull/14))

### Fixed

- Worktree chats start for repositories under folders with accented names, such as `Développement`. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- You get a desktop notification when a chat is waiting for you to answer one of its helpers. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Go, Java installed from a `.pkg`, rbenv, nodenv, pnpm and conda can read their own files in the sandbox, and Poetry keeps its cache there. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Rewind and fork work again after a chat's folder moves behind a symlink. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Changing how often an "every N hours" routine runs no longer runs it straight away. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Enabling a model or choosing the default model in Settings no longer reverts when a settings refresh was already under way. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Reading and searching uploaded documents loads only that task's files, not every upload in the app. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- A chat no longer waits up to an hour on a helper that is itself waiting for the chat's answer. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Live replies no longer stop updating when the connection splits a line ending. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- When the sandbox refuses an edit or command, the agent is told it was refused rather than that it may have run. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Uploaded slides and Word files keep the text in tables, grouped shapes, chart titles and content controls. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- A connector value with `$` before a capital letter, such as the password `Pa$W0rd`, can be written `Pa$$W0rd` to keep it as it is. `$NAME` alone still reads a secret. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Clicks on busy screens land where the agent aimed: large screenshots are no longer halved. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Chats returned to a re-created project follow its folder instead of the old one. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Search excerpts show the match after characters such as `ß`. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Uninstalling a package (`npm rm`, `pnpm remove`) and writing a file or commit message from a heredoc no longer ask as if they couldn't be undone. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- Renaming a file by changing only its case shows a diff in the Changes tab. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))
- A helper stopped while its checkout was being prepared is cleaned up after 24 hours like the others. ([#7](https://github.com/stoyan-stoyanov/wavefunction/pull/7))

## 0.1.1 - 2026-10-28

### Changed

- No changes to the app. This release tested updating from 0.1.0 inside the app. ([#5](https://github.com/stoyan-stoyanov/wavefunction/pull/5))

## 0.1.0 - 2026-10-27

The first public release: a local-first desktop coding agent for Macs with Apple silicon on macOS 11 or later. It is signed with Developer ID, notarized by Apple, and updates itself from this repository's releases.
