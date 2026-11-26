<p align="center">
  <img src="assets/branding/wavefunction-mark.svg" width="72" alt="">
</p>

<h1 align="center">Wavefunction</h1>

<p align="center">
  <strong>Minimal by design. Open source down to the prompt.</strong><br>
  A coding agent for macOS and Linux. Keep chatting while it works on several tasks at once.
</p>

<p align="center">
  <a href="https://github.com/stoyan-stoyanov/wavefunction/releases/latest">Download for macOS</a> ·
  <a href="#linux-experimental">Linux (experimental)</a> ·
  <a href="https://www.wavefunction.si">Website</a> ·
  <a href="#build-from-source">Build from source</a> ·
  <a href="CONTRIBUTING.md">Contribute</a>
</p>

<p align="center">Free and MIT licensed. Use local models, your own API keys or a ChatGPT sign-in.</p>

![Wavefunction on a Mac: projects and their chats in the sidebar, and a new session ready to start](docs/assets/wavefunction-new-chat-dark.png)

## Why Wavefunction

- **Less is more.** Coding agents are turning into super apps, adding more with every release. Wavefunction goes the other way: simpler is better, and building comes first.
- **Free to choose.** Pick any model and switch whenever you like. Run models locally or require zero data retention. You're never tied to one provider's plans or limits.
- **Open source, on the desktop.** Most open-source agents shine in the terminal. Wavefunction is built to be a great desktop app: minimal, considered and fast, and open source down to the prompt.

## Agents and sessions

Wavefunction has one built-in coding agent. Each chat is a separate conversation with it, with its own model, folder and history, and chats run side by side. A project is a folder, usually a Git repository, and groups the chats that work in it.

- **Worktrees.** In a Git repository a new chat starts on its own branch in its own checkout, so chats in one project don't step on each other. Untick **New worktree** before the first message to work in the project's folder instead.
- **Talk to it while it works.** A message sent during a task steers that task. **Queue follow-up** runs it after the task instead. When the agent needs a decision, it asks with a question card.
- **Helpers.** For bigger tasks the agent starts helpers: child chats that work in parallel and report back. They share the parent's checkout by default, or get an isolated worktree whose changes the parent reviews and brings in. Open one to read its transcript, redirect it or stop it.
- **Going back.** Fork a chat from any message, or rewind to put the code and the conversation back to how they were at that point.
- **Long conversations.** The ring in the message box shows how full the context is. Near the limit the conversation is summarised, keeping your requests and the confirmed state of the work. **Compact now** does it on demand.
- **Routines.** A chat can set up a routine: a chat that gets the same instruction on a schedule, such as every 15 minutes or weekdays at 9, while the app is open.
- **Projects from a chat.** Ask a chat to create a project (and clone a repository into it), archive old chats or remove a project.
- **Modes.** Plan mode reads and explains without changing anything. Full access runs commands outside the sandbox without asking each time; commands that can't be undone still ask.

## Computer use that leaves your computer alone

Other agents take over your mouse, keyboard and screen to get things done. Wavefunction gets a desktop of its own, so you keep working while it clicks. Watch it, or take control at any time.

- **Your computer stays yours.** It never moves your pointer, types on your keyboard or records your screen. Its desktop runs in a local Docker container that can reach the web, but not your files or keychain.
- **Secure sign-ins.** When a site needs your login, you sign in privately. The agent pauses and never sees what you type, and the chat stays signed in for next time.
- **Files in and out.** Downloads come to your computer in the folder you choose, and files you add are ready to upload.

Computer use needs Docker Desktop, or Docker Engine on Linux, and a model that can read images.

## Connectors and skills

70+ ready-made connectors, plus any MCP server you add. Wavefunction follows your `AGENTS.md` and loads `SKILL.md` skills from the `.agents/skills`, `.claude/skills` and `.opencode/skills` folders in your projects and home folder, and from `~/.codex/skills`, so it works with the setup you already use.

![The connector catalog: Gmail, Outlook, Google, Microsoft 365, Airtable, Asana, Atlassian and more](docs/assets/wavefunction-connectors-dark.png)

## Designs

A design changes how the whole app looks, with CSS, images and fonts; it never runs code. Pick one in **Settings › General › Design**: the built-in Nostalgia, with a glass title bar and a classic navigation pane, or Retro, a dark terminal room with pixel type and art. Or ask the agent for one. It writes the design with its design skill, checks that it loads, and saves it to your themes folder for you to pick.

<p>
  <img src="docs/assets/wavefunction-design-retro.jpg" width="49%" alt="The Retro design: pixel type, pixel icons and pixel art of a desk on the new-session screen">
  <img src="docs/assets/wavefunction-design-night-light.jpg" width="49%" alt="Night Light, a design the agent made in a chat: warm charcoal colours and a sleeping moon on the new-session screen">
</p>

Your own designs go in the `themes` folder Settings shows; [frontend/src/themes](frontend/src/themes/README.md) explains what a design can change.

## Usage tracking

**Settings › Analytics** totals what every run used, from the numbers your providers report:

- input tokens, split into cached and uncached, and output tokens;
- runs, failed runs and working time;
- cost in US dollars, where the provider reports one;
- the last 14 days day by day, and totals per model.

When only some runs reported a figure, it says how many ("Reported for 12 of 15 runs") instead of counting the rest as zero.

![Settings, Analytics: token totals, runs, cost and usage over the last 14 days](docs/assets/wavefunction-settings-analytics-dark.png)

## Secure and private

Use local models or require zero data retention. With **Require zero data retention** on in Settings → Models, OpenRouter and Vercel AI Gateway send chats only to providers that keep no prompts or responses. Other providers set it on your account with them.

- Your conversations, skills and settings stay on your Mac, in `~/Library/Application Support/app.harness.coding` (the app's identifier from before it was named Wavefunction, kept so existing data stays where it is). Provider and connector keys are local files only your user account can read; they are not encrypted.
- Commands run in the macOS sandbox by default, and folders outside your project need your grant. A command that needs to run outside the sandbox asks first, unless you've given that chat full access.
- A fixed check asks before commands it recognizes as irreversible, such as deletes or force pushes. It catches an overeager agent, not every case; see the [known issues](docs/KNOWN_ISSUES.md) and [SECURITY.md](SECURITY.md).

## Get started

On a Mac, download the DMG from the [latest release](https://github.com/stoyan-stoyanov/wavefunction/releases/latest), open it and drag Wavefunction to Applications. It's signed and notarized by Apple, and it updates itself.

You need a Mac with Apple Silicon (macOS 11 or later). Setup walks you through connecting a provider, choosing models and creating your first project. Model usage is billed by your provider.

On Linux (x86-64), the same release has an AppImage and a `.deb`: see [Linux](#linux-experimental). Linux support is experimental.

<p>
  <img src="docs/assets/wavefunction-onboarding-provider-dark.png" width="49%" alt="Setup, step 1: connect a provider">
  <img src="docs/assets/wavefunction-onboarding-models-dark.png" width="49%" alt="Setup, step 2: choose your models">
</p>

## Build from source

The app is a [Tauri](https://tauri.app) shell in Rust (`src-tauri`) around a React interface (`frontend`) and a Python agent engine (`backend`). The build freezes the engine with PyInstaller and bundles it, with its own Python, into the app.

On macOS you need Node.js 22+, [uv](https://docs.astral.sh/uv/) (it installs Python 3.12), Rust and the Xcode Command Line Tools.

```sh
npm run setup           # frontend packages and the Python environment, from their lockfiles
npm run desktop:build   # the engine, then the app
open src-tauri/target/release/bundle/macos/Wavefunction.app
```

`desktop:build` also writes a ZIP and its checksum to `dist/desktop`. Its signature is ad hoc, so the app runs on the Mac that built it but warns elsewhere. For a signed, notarized DMG you can hand to others, use `npm run release:local`, which needs a Developer ID certificate and an App Store Connect API key ([releasing](docs/RELEASING.md)).

### Linux (experimental)

Linux support is experimental and community-supported. Dictation is macOS-only for now. Commands run in a [Bubblewrap](https://github.com/containers/bubblewrap) sandbox with a seccomp filter, from the same grants as on macOS; without `/usr/bin/bwrap` the app runs no commands at all.

Each [release](https://github.com/stoyan-stoyanov/wavefunction/releases/latest) has x86-64 downloads for Ubuntu 22.04, Debian 12 and later. They aren't signed: compare them with the release's `SHA256SUMS-linux` (`sha256sum -c --ignore-missing SHA256SUMS-linux`).

- **AppImage**: install Bubblewrap (`sudo apt install bubblewrap`), then `chmod +x Wavefunction-<version>-linux-x86_64.AppImage` and run it. It updates itself, as the Mac app does, from **Settings › About**, so keep it in a folder you can write to, such as `~/Applications`.
- **`.deb`**: `sudo apt install ./Wavefunction-<version>-linux-x86_64.deb`, which also installs Bubblewrap. It doesn't update itself: install each new release's `.deb` the same way.

To build it yourself, on arm64 among others, follow these steps for Ubuntu 24.04 or later:

1. Install Node.js 22 or later (from [nodejs.org](https://nodejs.org) or nvm), [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Rust](https://rustup.rs).
2. Install Bubblewrap and the libraries Tauri needs:

   ```sh
   sudo apt install bubblewrap build-essential file git libwebkit2gtk-4.1-dev \
     libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
   ```

3. Get the code and build it:

   ```sh
   git clone https://github.com/stoyan-stoyanov/wavefunction.git
   cd wavefunction
   UV_PYTHON_PREFERENCE=only-managed npm run setup   # uv's own Python 3.12, which the build can bundle
   npm run desktop:build   # the engine, then a .deb and an AppImage in dist/desktop
   ```

4. Install the `.deb` with `sudo apt install ./dist/desktop/*.deb`, or run the AppImage from `dist/desktop` directly.

Other distributions need the same libraries under their own names (Tauri lists them in its [prerequisites](https://v2.tauri.app/start/prerequisites/#linux)). If Settings › Access says the sandbox is unavailable, your distribution may stop `bwrap` from making user namespaces: Ubuntu 23.10 and later do unless an AppArmor profile allows it, which `sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0` lifts until the next restart. The app keeps its data in `~/.local/share/app.harness.coding`. To update a build of your own, pull the latest code and build again.

While working on the interface, `npm run dev:fixture` runs it in your browser against a disposable synthetic workspace, with no account or model provider. `npm run desktop:dev` runs the app itself with the interface reloading as you edit. Before a pull request:

```sh
npm run check
```

[Development](docs/DEVELOPMENT.md) and the [architecture](docs/ARCHITECTURE.md) explain the rest.

## Contributing

Interested in contributing? We welcome new contributors. Start with [CONTRIBUTING.md](CONTRIBUTING.md#your-first-contribution) and the issues labelled [good first issue](https://github.com/stoyan-stoyanov/wavefunction/issues?q=is%3Aissue%20is%3Aopen%20label%3A%22good%20first%20issue%22), and read the [code of conduct](CODE_OF_CONDUCT.md). Questions go in [Discussions](https://github.com/stoyan-stoyanov/wavefunction/discussions), and test reports from Linux or local models are always welcome. Report security issues privately as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE). Dependencies keep their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
