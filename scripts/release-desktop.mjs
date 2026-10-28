// Turn the built app into release files: signed with Developer ID under the hardened runtime,
// notarized, as a DMG and ZIP to download and a signed archive plus latest.json for the updater,
// with third-party notices inside the app and, with their sources, in an archive beside it.
//
// Run after `npm run sidecar:build`, `tauri build --ci --bundles app` and, for a release,
// `scripts/release-notices.py`. Configured by:
//   APPLE_SIGNING_IDENTITY   "Developer ID Application: Name (TEAMID)"; "-" signs ad hoc (local check)
//   APPLE_API_KEY_PATH, APPLE_API_KEY_ID, APPLE_API_ISSUER   notarization; skipped when unset
//   TAURI_SIGNING_PRIVATE_KEY or TAURI_SIGNING_PRIVATE_KEY_PATH (+ _PASSWORD)   updater signature
//   RELEASE_REPOSITORY       owner/repo the release is published to (default: GITHUB_REPOSITORY)
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cpSync, existsSync, lstatSync, mkdirSync, openSync, readFileSync, readSync, closeSync,
  readdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";
import { restoreBackendLinks } from "./backend-links.mjs";
import { releaseNotes } from "./changelog.mjs";

const root = resolve(import.meta.dirname, "..");
const config = JSON.parse(readFileSync(join(root, "src-tauri/tauri.conf.json"), "utf8"));
const { productName: name, version } = config;
const notes = releaseNotes(version);
const app = join(root, `src-tauri/target/release/bundle/macos/${name}.app`);
const out = join(root, "dist/release");
const arch = process.arch === "arm64" ? "arm64" : "x64";
const stem = `${name}-${version}-macos-${arch}`;
const env = process.env;
const identity = env.APPLE_SIGNING_IDENTITY;
const repository = env.RELEASE_REPOSITORY || env.GITHUB_REPOSITORY || "stoyan-stoyanov/wavefunction";

function run(command, args, options = {}) {
  const result = spawnSync(command, args, { stdio: options.capture ? "pipe" : "inherit", encoding: "utf8", ...options });
  if (result.status !== 0) {
    if (options.capture) process.stderr.write(result.stdout + result.stderr);
    throw new Error(`${basename(command)} ${args[0]} failed`);
  }
  return result.stdout;
}

if (!identity) throw new Error("Set APPLE_SIGNING_IDENTITY (\"-\" signs ad hoc for a local check).");
if (!existsSync(join(app, "Contents/Resources/backend/_internal")))
  throw new Error("Build the app with its bundled backend first.");
restoreBackendLinks(root, app);

// The notices go in before signing, so the app's signature covers them.
const legal = join(root, "dist/legal");
if (existsSync(legal)) cpSync(join(legal, "notices"), join(app, "Contents/Resources/legal"), { recursive: true });
else if (identity !== "-") throw new Error("Run scripts/release-notices.py first: a release ships its third-party notices.");

// Every Mach-O file, deepest first, then the app: the outer signature covers the inner ones.
const MACH_O = new Set(["cffaedfe", "cefaedfe", "cafebabe", "feedfacf", "feedface", "bebafeca"]);
function machO(path) {
  const fd = openSync(path, "r");
  const head = Buffer.alloc(4);
  try {
    return readSync(fd, head, 0, 4, 0) === 4 && MACH_O.has(head.toString("hex"));
  } finally {
    closeSync(fd);
  }
}
function walk(folder) {
  return readdirSync(folder).flatMap((entry) => {
    const path = join(folder, entry);
    const stat = lstatSync(path);
    if (stat.isSymbolicLink()) return [];
    return stat.isDirectory() ? walk(path) : stat.isFile() && machO(path) ? [path] : [];
  });
}
const engine = join(app, "Contents/Resources/backend/wavefunction-backend");
const inner = walk(join(app, "Contents")).filter((path) => path !== join(app, "Contents/MacOS/wavefunction"))
  .sort((a, b) => b.split("/").length - a.split("/").length);
const sign = (path, entitlements) => run("/usr/bin/codesign", [
  "--force", "--options", "runtime", ...(identity === "-" ? [] : ["--timestamp"]),
  ...(entitlements ? ["--entitlements", entitlements] : []), "--sign", identity, path,
], { capture: true });
const backendEntitlements = join(root, "src-tauri/backend.entitlements");
for (const path of inner) sign(path, path === engine ? backendEntitlements : undefined);
sign(app);
run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app]);
console.log(`Signed ${inner.length + 1} binaries with ${identity === "-" ? "an ad-hoc signature" : identity}`);

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

const notarize = env.APPLE_API_KEY_PATH && env.APPLE_API_KEY_ID && env.APPLE_API_ISSUER && identity !== "-";
function notarizeAndStaple(upload, target = upload) {
  const submitted = JSON.parse(run("/usr/bin/xcrun", ["notarytool", "submit", upload, "--key", env.APPLE_API_KEY_PATH,
    "--key-id", env.APPLE_API_KEY_ID, "--issuer", env.APPLE_API_ISSUER, "--wait", "--output-format", "json"], { capture: true }));
  if (submitted.status !== "Accepted")
    throw new Error(`Notarization ${submitted.status}: xcrun notarytool log ${submitted.id} to see why.`);
  run("/usr/bin/xcrun", ["stapler", "staple", target], { capture: true });
}

// The app is notarized and stapled before it is packaged, so every copy, the DMG's included, opens offline.
if (notarize) {
  const upload = join(out, "notarize.zip");
  run("/usr/bin/ditto", ["-c", "-k", "--sequesterRsrc", "--keepParent", app, upload]);
  notarizeAndStaple(upload, app);
  rmSync(upload);
}

// The DMG people download: the app and a link to Applications, signed and notarized itself.
const staging = join(out, "dmg");
mkdirSync(staging);
cpSync(app, join(staging, `${name}.app`), { recursive: true, verbatimSymlinks: true });
symlinkSync("/Applications", join(staging, "Applications"));
const dmg = join(out, `${stem}.dmg`);
run("/usr/bin/hdiutil", ["create", "-volname", name, "-srcfolder", staging, "-ov", "-format", "UDZO", dmg], { capture: true });
rmSync(staging, { recursive: true, force: true });
if (identity !== "-") run("/usr/bin/codesign", ["--force", "--timestamp", "--sign", identity, dmg], { capture: true });
if (notarize) {
  notarizeAndStaple(dmg);
  console.log("Notarized and stapled the app and the DMG");
} else {
  console.warn("Not notarized: set APPLE_API_KEY_PATH, APPLE_API_KEY_ID and APPLE_API_ISSUER with a Developer ID identity.");
}

const zip = join(out, `${stem}.zip`);
run("/usr/bin/ditto", ["-c", "-k", "--sequesterRsrc", "--keepParent", app, zip]);

// What installed apps download to update themselves, signed with the updater key. Without Mac
// metadata: macOS tar otherwise adds a `._` entry for every file with extended attributes, and the
// updater unpacks `._Wavefunction.app` onto its own extraction folder and gives up.
const archive = join(out, `${stem}.app.tar.gz`);
run("/usr/bin/tar", ["--no-mac-metadata", "--no-xattrs", "-czf", archive, "-C", join(app, ".."), `${name}.app`]);
if (env.TAURI_SIGNING_PRIVATE_KEY || env.TAURI_SIGNING_PRIVATE_KEY_PATH) {
  const key = env.TAURI_SIGNING_PRIVATE_KEY ? [] : ["--private-key-path", env.TAURI_SIGNING_PRIVATE_KEY_PATH];
  run(join(root, "frontend/node_modules/.bin/tauri"), ["signer", "sign", ...key,
    "--password", env.TAURI_SIGNING_PRIVATE_KEY_PASSWORD ?? "", archive], { capture: true });
  const platform = arch === "arm64" ? "darwin-aarch64" : "darwin-x86_64";
  writeFileSync(join(out, "latest.json"), JSON.stringify({
    version,
    notes,
    pub_date: new Date().toISOString(),
    platforms: {
      [platform]: {
        signature: readFileSync(`${archive}.sig`, "utf8").trim(),
        url: `https://github.com/${repository}/releases/download/v${version}/${basename(archive)}`,
      },
    },
  }, null, 2) + "\n");
} else {
  console.warn("No updater signature or latest.json: set TAURI_SIGNING_PRIVATE_KEY or TAURI_SIGNING_PRIVATE_KEY_PATH.");
}

if (existsSync(legal)) run("/usr/bin/ditto", ["-c", "-k", "--norsrc", "--noextattr", "--noacl", "--keepParent", legal,
  join(out, `${stem}-notices-and-sources.zip`)]);

const sums = readdirSync(out).filter((file) => !file.endsWith(".sig") && file !== "SHA256SUMS")
  .map((file) => `${createHash("sha256").update(readFileSync(join(out, file))).digest("hex")}  ${file}`);
writeFileSync(join(out, "SHA256SUMS"), sums.join("\n") + "\n");
console.log(`Release files in ${out}:\n  ${readdirSync(out).join("\n  ")}`);
