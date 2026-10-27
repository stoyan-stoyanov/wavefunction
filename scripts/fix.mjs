// Apply the lint fixes and formatting CI checks for: Ruff, ESLint, Prettier and rustfmt.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const cargo = path.join(homedir(), ".cargo/bin/cargo");
const ruff = (...args) => ["uv", ["run", "--no-sync", "--project", "backend", "ruff", ...args]];
const steps = [
  ruff("check", "--fix", "backend"),
  ruff("check", "--fix", "--config", "backend/pyproject.toml", "scripts"),
  ruff("format", "backend"),
  ruff("format", "--config", "backend/pyproject.toml", "scripts"),
  // Through the frontend's own script, so ESLint runs in frontend/ and finds its config.
  ["npm", ["--prefix", "frontend", "run", "lint", "--", "--fix"]],
  ["npm", ["--prefix", "frontend", "run", "format"]],
];
// The shell's formatter needs Rust, which only native work requires.
if (existsSync(cargo)) steps.push([cargo, ["fmt", "--manifest-path", "src-tauri/Cargo.toml"]]);

let failed = false;
for (const [command, args] of steps) {
  const { status } = spawnSync(command, args, { cwd: root, stdio: "inherit" });
  failed ||= status !== 0;
}
process.exit(failed ? 1 : 0);
