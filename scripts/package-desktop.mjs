import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { restoreBackendLinks } from "./backend-links.mjs";

const root = resolve(import.meta.dirname, "..");
const target = process.env.CARGO_TARGET_DIR
  ? resolve(root, "src-tauri", process.env.CARGO_TARGET_DIR)
  : join(root, "src-tauri/target");
const config = JSON.parse(readFileSync(join(root, "src-tauri/tauri.conf.json"), "utf8"));
const folder = join(root, "dist/desktop");

function checksum(path, name) {
  const digest = createHash("sha256").update(readFileSync(path)).digest("hex");
  writeFileSync(join(folder, name + ".sha256"), `${digest}  ${name}\n`);
}

// Linux builds come from source: Tauri's .deb and AppImage, unsigned, with their checksums.
if (process.platform === "linux") {
  const packages = ["deb", "appimage"].flatMap((kind) => {
    const bundle = join(target, "release/bundle", kind);
    return existsSync(bundle)
      ? readdirSync(bundle).filter((name) => /\.(deb|AppImage)$/.test(name)).map((name) => join(bundle, name))
      : [];
  });
  if (!packages.length) throw new Error("Build the desktop app before packaging it.");
  mkdirSync(folder, { recursive: true });
  for (const path of packages) {
    const name = path.split("/").pop();
    copyFileSync(path, join(folder, name));
    checksum(path, name);
    console.log(`Packaged desktop: ${join(folder, name)}`);
  }
  process.exit(0);
}

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
mkdirSync(folder, { recursive: true });
const name = `${config.productName}-${config.version}-macos-${process.arch}.zip`;
const archive = join(folder, name);
const result = spawnSync(
  "/usr/bin/ditto",
  ["-c", "-k", "--sequesterRsrc", "--keepParent", app, archive],
  { stdio: "inherit" },
);
if (result.status !== 0) process.exit(result.status ?? 1);
checksum(archive, name);
console.log(`Packaged desktop: ${archive}`);
