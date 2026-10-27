import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { restoreBackendLinks } from "./backend-links.mjs";

const root = resolve(import.meta.dirname, "..");
const target = process.env.CARGO_TARGET_DIR
  ? resolve(root, "src-tauri", process.env.CARGO_TARGET_DIR)
  : join(root, "src-tauri/target");
const config = JSON.parse(readFileSync(join(root, "src-tauri/tauri.conf.json"), "utf8"));
const app = join(target, `release/bundle/macos/${config.productName}.app`);
if (!existsSync(app))
  throw new Error("Build the desktop app before packaging it.");
const backend = join(app, "Contents/Resources/backend");
if (!existsSync(join(backend, "wavefunction-backend")) || !existsSync(join(backend, "_internal")))
  throw new Error("The desktop app is missing its bundled backend runtime. Rebuild it before packaging.");
restoreBackendLinks(root, app);
// The linker signs a Mach-O executable, not the complete app resource envelope.
// Seal the assembled bundle for local builds without a Developer ID identity.
for (const args of [
  ["--force", "--deep", "--sign", "-", app],
  ["--verify", "--deep", "--strict", app],
]) {
  const signed = spawnSync("/usr/bin/codesign", args, { stdio: "inherit" });
  if (signed.status !== 0) process.exit(signed.status ?? 1);
}
const folder = join(root, "dist/desktop");
mkdirSync(folder, { recursive: true });
const name = `${config.productName}-${config.version}-macos-${process.arch}.zip`;
const archive = join(folder, name);
const result = spawnSync(
  "/usr/bin/ditto",
  ["-c", "-k", "--sequesterRsrc", "--keepParent", app, archive],
  { stdio: "inherit" },
);
if (result.status !== 0) process.exit(result.status ?? 1);
const digest = createHash("sha256").update(readFileSync(archive)).digest("hex");
writeFileSync(join(folder, name + ".sha256"), `${digest}  ${name}\n`);
console.log(`Packaged desktop: ${archive}`);
