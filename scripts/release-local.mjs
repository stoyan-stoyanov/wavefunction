// The release build on this Mac instead of GitHub Actions: the steps of
// .github/workflows/release.yml, signed with the Developer ID in the login keychain and notarized
// with the App Store Connect key named in ~/.config/wavefunction/release.env. The files land in
// dist/release; nothing is tagged, pushed or uploaded.
//
// release.env holds paths and ids, not keys (environment variables of the same names win):
//   APPLE_API_KEY_PATH=~/path/to/AuthKey_XXXXXXXXXX.p8
//   APPLE_API_ISSUER=<Issuer ID from App Store Connect › Users and Access › Integrations>
//   APPLE_API_KEY_ID=XXXXXXXXXX                                  (default: from the .p8 file name)
//   TAURI_SIGNING_PRIVATE_KEY_PATH=~/.tauri/wavefunction-updater.key   (the default)
//   APPLE_SIGNING_IDENTITY="Developer ID Application: …"          (default: the only one in the keychains)
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const settings = join(homedir(), ".config/wavefunction/release.env");
const expand = (value) => value.replace(/^~(?=\/|$)/, homedir());

// release-desktop.mjs signs the app in src-tauri/target, so the build must put it there.
const { CARGO_TARGET_DIR: _elsewhere, ...inherited } = process.env;
function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: options.capture ? "pipe" : "inherit", encoding: "utf8",
    env: { ...inherited, PATH: `${join(homedir(), ".cargo/bin")}:${process.env.PATH}`, ...options.env } });
  if (result.status !== 0) {
    if (options.capture) process.stderr.write(result.stdout + result.stderr);
    throw new Error(`${basename(command)} ${args.join(" ")} failed`);
  }
  return result.stdout;
}

const saved = {};
if (existsSync(settings)) {
  for (const line of readFileSync(settings, "utf8").split("\n")) {
    const match = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
    if (match) saved[match[1]] = expand(match[2].replace(/^(["'])(.*)\1$/, "$2"));
  }
}
const value = (name, fallback) => process.env[name] || saved[name] || fallback;

// One Developer ID in the keychains is the one to use; with more, which team signs is a choice.
function signingIdentity() {
  const found = [...new Set([...run("/usr/bin/security", ["find-identity", "-v", "-p", "codesigning"], { capture: true })
    .matchAll(/"(Developer ID Application: [^"]+)"/g)].map((match) => match[1]))];
  if (found.length > 1)
    throw new Error(`More than one Developer ID in the keychains (${found.join("; ")}). Set APPLE_SIGNING_IDENTITY to one.`);
  return found[0];
}
const keyPath = value("APPLE_API_KEY_PATH");
const release = {
  APPLE_SIGNING_IDENTITY: value("APPLE_SIGNING_IDENTITY") ?? signingIdentity(),
  APPLE_API_KEY_PATH: keyPath,
  APPLE_API_KEY_ID: value("APPLE_API_KEY_ID", keyPath && basename(keyPath).match(/^AuthKey_(\w+)\.p8$/)?.[1]),
  APPLE_API_ISSUER: value("APPLE_API_ISSUER"),
  TAURI_SIGNING_PRIVATE_KEY_PATH: value("TAURI_SIGNING_PRIVATE_KEY_PATH", join(homedir(), ".tauri/wavefunction-updater.key")),
};
const missing = Object.entries(release).filter(([, setting]) => !setting).map(([name]) => name);
if (missing.length)
  throw new Error(`Set ${missing.join(", ")} in ${settings} (see the top of scripts/release-local.mjs).`);
for (const path of [release.APPLE_API_KEY_PATH, release.TAURI_SIGNING_PRIVATE_KEY_PATH])
  if (!existsSync(path)) throw new Error(`No file at ${path}.`);

const { version } = JSON.parse(readFileSync(join(root, "src-tauri/tauri.conf.json"), "utf8"));
const commit = run("git", ["rev-parse", "--short", "HEAD"], { capture: true }).trim();
// Untracked files count too: one in a folder the app copies whole would ship without being in Git.
const dirty = run("git", ["status", "--porcelain"], { capture: true }).trim();
console.log(`Wavefunction ${version} from ${commit}${dirty ? " with uncommitted changes" : ""}, signed by ${release.APPLE_SIGNING_IDENTITY}`);

// From the lockfiles alone, as the workflow's clean checkout installs them: a package left over in
// either environment would ship in the app with no notice for it.
run("uv", ["sync", "--locked", "--project", "backend", "--python", "3.12", "--extra", "dev"]);
run("npm", ["--prefix", "frontend", "ci", "--no-audit", "--no-fund"]);
run("npm", ["run", "sidecar:build"]);
run("./frontend/node_modules/.bin/tauri", ["build", "--ci", "--bundles", "app", "--config", "src-tauri/tauri.conf.json"]);
run("cargo", ["fetch", "--locked", "--manifest-path", "src-tauri/Cargo.toml", "--target", "aarch64-apple-darwin"]);
// Collected afresh: notices or sources from an earlier run's dependencies must not ship again.
rmSync(join(root, "dist/legal"), { recursive: true, force: true });
run("backend/.venv/bin/python", ["scripts/release-notices.py"]);
// The first signing can ask, in a macOS dialog, to use the Developer ID key: Always Allow.
run("node", ["scripts/release-desktop.mjs"], { env: release });

const dmg = join(root, `dist/release/Wavefunction-${version}-macos-${process.arch === "arm64" ? "arm64" : "x64"}.dmg`);
run("/usr/sbin/spctl", ["-a", "-t", "open", "--context", "context:primary-signature", dmg]);
run("/usr/bin/xcrun", ["stapler", "validate", dmg], { capture: true });
console.log(`Notarized, stapled and accepted by Gatekeeper: ${dmg}`);
