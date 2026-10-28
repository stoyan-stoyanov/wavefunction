// Set the app's version everywhere it is recorded, and date CHANGELOG.md's Unreleased section as
// that version's: `node scripts/version.mjs 0.2.0`.
import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { closeUnreleased } from "./changelog.mjs";

const version = process.argv[2];
if (!/^\d+\.\d+\.\d+$/.test(version ?? "")) {
  console.error("Usage: node scripts/version.mjs <major.minor.patch>");
  process.exit(1);
}
const root = resolve(import.meta.dirname, "..");
closeUnreleased(version);
const files = [
  ["package.json", /("version":\s*")[^"]+(")/],
  ["frontend/package.json", /("version":\s*")[^"]+(")/],
  ["src-tauri/tauri.conf.json", /("version":\s*")[^"]+(")/],
  ["src-tauri/Cargo.toml", /(\[package\][\s\S]*?\nversion = ")[^"]+(")/],
  ["src-tauri/Cargo.lock", /(name = "wavefunction"\nversion = ")[^"]+(")/],
  ["backend/pyproject.toml", /(\[project\][\s\S]*?\nversion = ")[^"]+(")/],
  ["backend/uv.lock", /(name = "wavefunction-backend"\nversion = ")[^"]+(")/],
  ["frontend/package-lock.json", /("name":\s*"wavefunction-frontend",\s*"version":\s*")[^"]+(")/g],
];
for (const [file, pattern] of files) {
  const path = join(root, file);
  const text = readFileSync(path, "utf8");
  const next = text.replace(pattern, `$1${version}$2`);
  if (next === text && !text.includes(version)) throw new Error(`No version found in ${file}`);
  writeFileSync(path, next);
}
console.log(`Version ${version}. Commit, merge, then tag: git tag v${version} && git push origin v${version}`);
