// What the built macOS app is made of, compared with src-tauri/macos-bundle.txt: its Info.plist, the
// libraries the app binary links, the entitlements of the app and the engine, and the top of its
// layout. A Linux-only dependency, capability or config change that leaks into the Mac build shows
// here, in review, like the prompt surface does for the agent's instructions.
//
//   node scripts/macos-manifest.mjs [app]            print the manifest
//   node scripts/macos-manifest.mjs --check [app]    fail with a diff if it differs from the file
//   node scripts/macos-manifest.mjs --write [app]    rewrite the file after an intended change
//
// The app defaults to Tauri's release bundle. Versions are left out (of the app and of each linked
// library), so a release or an SDK update changes nothing here.
import { spawnSync } from "node:child_process";
import { lstatSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const expected = join(root, "src-tauri/macos-bundle.txt");
const args = process.argv.slice(2);
const mode = args.find((arg) => arg === "--check" || arg === "--write");
const app = resolve(args.find((arg) => !arg.startsWith("--")) ?? join(root, "src-tauri/target/release/bundle/macos/Wavefunction.app"));

function run(command, commandArgs, input) {
  const result = spawnSync(command, commandArgs, { encoding: "utf8", input });
  if (result.status !== 0) throw new Error(`${command} ${commandArgs.join(" ")} failed:\n${result.stderr}`);
  return result.stdout;
}

const plistJson = (xml) => JSON.parse(run("/usr/bin/plutil", ["-convert", "json", "-o", "-", "--", "-"], xml));
// Keys that describe the build, not the app: its version, and the SDK and machine it was built on.
const volatile = (key) => /^(CFBundleVersion|CFBundleShortVersionString|BuildMachineOSBuild)$|^DT/.test(key);

function entitlements(path) {
  const xml = run("/usr/bin/codesign", ["-d", "--entitlements", "-", "--xml", path]);
  if (!xml.trim()) return ["(none)"];
  return Object.entries(plistJson(xml)).map(([key, value]) => `${key} = ${JSON.stringify(value)}`).sort();
}

// `Contents/CodeResources` is the notarization ticket stapling adds, which ad-hoc builds lack.
function layout(folder, prefix = "", depth = 2) {
  return readdirSync(folder).sort().filter((entry) => prefix || entry !== "CodeResources").flatMap((entry) => {
    const path = join(folder, entry);
    const name = prefix + entry;
    if (!lstatSync(path).isDirectory()) return [name];
    return [name + "/", ...(depth > 1 ? layout(path, name + "/", depth - 1) : [])];
  });
}

function manifest() {
  const info = plistJson(readFileSync(join(app, "Contents/Info.plist"), "utf8"));
  const binary = join(app, "Contents/MacOS", info.CFBundleExecutable);
  const linked = run("/usr/bin/otool", ["-L", binary]).split("\n").slice(1)
    .map((line) => line.trim().replace(/\s+\(compatibility version.*\)$/, "")).filter(Boolean).sort();
  return [
    "# The macOS app as built. CI's macos-app job compares a fresh build with this file;",
    "# after an intended change run `node scripts/macos-manifest.mjs --write` on a built app and review the diff.",
    "",
    "[Info.plist]",
    ...Object.entries(info).filter(([key]) => !volatile(key)).map(([key, value]) => `${key} = ${JSON.stringify(value)}`).sort(),
    "",
    `[libraries linked by Contents/MacOS/${info.CFBundleExecutable}]`,
    ...linked,
    "",
    "[entitlements of the app]",
    ...entitlements(app),
    "",
    "[entitlements of the engine, Contents/Resources/backend/wavefunction-backend]",
    ...entitlements(join(app, "Contents/Resources/backend/wavefunction-backend")),
    "",
    "[Contents, two levels deep]",
    ...layout(join(app, "Contents")),
    "",
  ].join("\n");
}

const current = manifest();
if (mode === "--write") {
  writeFileSync(expected, current);
  console.log(`Wrote ${expected}`);
} else if (mode === "--check") {
  if (readFileSync(expected, "utf8") === current) {
    console.log("The macOS app matches src-tauri/macos-bundle.txt.");
  } else {
    const scratch = mkdtempSync(join(tmpdir(), "macos-manifest-"));
    writeFileSync(join(scratch, "built.txt"), current);
    const diff = spawnSync("/usr/bin/diff", ["-u", expected, join(scratch, "built.txt")], { encoding: "utf8" });
    rmSync(scratch, { recursive: true, force: true });
    console.error(diff.stdout);
    console.error("The macOS app differs from src-tauri/macos-bundle.txt (diff above). If the change is intended,");
    console.error("run `node scripts/macos-manifest.mjs --write` on a built app, or paste the new manifest below, and commit it:\n");
    console.error(current);
    process.exit(1);
  }
} else {
  process.stdout.write(current);
}
