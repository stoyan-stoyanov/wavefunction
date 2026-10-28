// One command for "is my change ready?": the checks CI would fail on, for what this branch changed.
//
//   npm run check            files changed since the base branch, plus uncommitted and new files
//   npm run check -- --all   everything, as CI runs it
//
// Each step prints its own output; the summary at the end lists what failed and how to rerun it.
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const all = process.argv.includes("--all");
const cargoBin = path.join(homedir(), ".cargo/bin");
const env = { ...process.env, PATH: existsSync(cargoBin) ? `${cargoBin}:${process.env.PATH}` : process.env.PATH };

function git(...args) {
  const result = spawnSync("git", args, { cwd: root, encoding: "utf8" });
  return result.status === 0 ? result.stdout.trim() : "";
}

function changedFiles() {
  const base = git("merge-base", "HEAD", "origin/main") || git("merge-base", "HEAD", "main");
  const files = new Set([
    ...(base ? git("diff", "--name-only", base) : "").split("\n"),
    ...git("diff", "--name-only", "HEAD").split("\n"),
    ...git("ls-files", "--others", "--exclude-standard").split("\n"),
  ]);
  files.delete("");
  return [...files];
}

const changed = all ? [] : changedFiles();
const touched = (prefix) => all || changed.some((file) => file.startsWith(prefix));
const frontendSources = changed.filter((file) => /^frontend\/src\/.*\.(ts|tsx)$/.test(file) && existsSync(path.join(root, file)));
const frontendConfig = changed.some((file) => file.startsWith("frontend/") && !file.startsWith("frontend/src/"));
// conftest.py, support.py and fixtures are shared by every test, so they count as sources.
const backendTest = (file) => /^backend\/tests\/test_[^/]+\.py$/.test(file);
const backendSources = changed.some((file) => file.startsWith("backend/") && !backendTest(file));
const backendTests = changed.filter((file) => backendTest(file) && existsSync(path.join(root, file)));

const steps = [];
if (touched("frontend/")) {
  steps.push(["Frontend types", "npm", ["--prefix", "frontend", "run", "typecheck"]]);
  steps.push(["Frontend lint", "npm", ["--prefix", "frontend", "run", "lint"]]);
  steps.push(["Frontend formatting (npm run fix formats)", "npm", ["--prefix", "frontend", "run", "format:check"]]);
  if (all || frontendConfig || !frontendSources.length) {
    steps.push(["Frontend tests", "npm", ["--prefix", "frontend", "run", "test"]]);
  } else {
    const files = frontendSources.map((file) => file.slice("frontend/".length));
    steps.push(["Frontend tests for the changed files", "npm", ["--prefix", "frontend", "exec", "--", "vitest", "related", "--run", ...files]]);
  }
}
if (touched("backend/")) {
  steps.push(["Backend lint", "uv", ["run", "--no-sync", "--project", "backend", "ruff", "check", "backend"]]);
  steps.push(["Backend formatting (npm run fix formats)", "uv", ["run", "--no-sync", "--project", "backend", "ruff", "format", "--check", "backend"]]);
  steps.push(["Backend types (pyright) on the files you changed", "npm", ["run", "typecheck:backend"]]);
  if (all || changed.some((file) => /^backend\/src\//.test(file))) {
    steps.push(["API schema and types are current", "sh", ["-c",
      "npm run --silent generate:api >/dev/null && git diff --exit-code --stat -- backend/openapi.json frontend/src/lib/api.gen.ts || { echo 'Regenerated: commit backend/openapi.json and frontend/src/lib/api.gen.ts.'; exit 1; }"]]);
  }
  if (all || backendSources) {
    // The engine's tests drive its HTTP API, so a source change runs them all; tests marked
    // destructive or live stay excluded (backend/pyproject.toml), as everywhere locally.
    steps.push(["Backend tests", "npm", ["run", "test:backend"]]);
  } else if (backendTests.length) {
    const files = backendTests.map((file) => file.slice("backend/".length));
    steps.push(["Backend tests for the changed files", "uv", ["run", "--no-sync", "pytest", "-q", ...files], "backend"]);
  }
}
if (all || changed.some((file) => /^(backend|frontend)\/src\//.test(file))) {
  steps.push(["Code health: file and function sizes", "node", ["scripts/check-code-health.mjs"]]);
}
if (all || changed.some((file) => /^scripts\/.+\.py$/.test(file))) {
  steps.push(["Script lint and formatting", "sh", ["-c",
    "uv run --no-sync --project backend ruff check --config backend/pyproject.toml scripts && uv run --no-sync --project backend ruff format --check --config backend/pyproject.toml scripts"]]);
}
if (all || changed.some((file) => /^scripts\/.+\.mjs$/.test(file))) {
  steps.push(["Script syntax", "sh", ["-ec", 'for script in scripts/*.mjs; do node --check "$script"; done']]);
}
if (all || changed.some((file) => file.endsWith(".md"))) {
  steps.push(["Docs links and agent file sizes", "node", ["scripts/check-docs.mjs"]]);
}
if (all || changed.some((file) => /^scripts\/(dev|coding-fixture)/.test(file))) {
  steps.push(["Launcher test", "npm", ["run", "test:launcher"]]);
}
if (touched("src-tauri/")) {
  // The shell bundles the frozen engine but never runs it to compile or test: an empty folder stands in.
  mkdirSync(path.join(root, "backend/dist/wavefunction-backend"), { recursive: true });
  steps.push(["Native formatting (npm run fix formats)", "cargo", ["fmt", "--manifest-path", "src-tauri/Cargo.toml", "--check"]]);
  steps.push(["Native lint", "cargo", ["clippy", "--manifest-path", "src-tauri/Cargo.toml", "--all-targets", "--locked", "--", "-D", "warnings"]]);
  steps.push(["Native tests", "cargo", ["test", "--manifest-path", "src-tauri/Cargo.toml", "--lib", "--locked"]]);
}

if (!steps.length) {
  console.log(changed.length ? `Nothing to check for: ${changed.join(", ")}` : "No changes since the base branch. Use --all to check everything.");
  process.exit(0);
}

const results = [];
for (const [name, command, args, cwd = "."] of steps) {
  console.log(`\n▶ ${name}: ${command} ${args.join(" ")}${cwd === "." ? "" : ` (in ${cwd}/)`}`);
  const started = Date.now();
  const { status } = spawnSync(command, args, { cwd: path.join(root, cwd), env, stdio: "inherit" });
  results.push({ name, ok: status === 0, seconds: (Date.now() - started) / 1000, rerun: `${cwd === "." ? "" : `cd ${cwd} && `}${command} ${args.join(" ")}` });
}

console.log("\nSummary");
for (const { name, ok, seconds } of results) console.log(`  ${ok ? "✓" : "✗"} ${name} (${seconds.toFixed(1)} s)`);
const failed = results.filter((result) => !result.ok);
if (failed.length) {
  console.log("\nRerun a failed step:");
  for (const { rerun } of failed) console.log(`  ${rerun}`);
  process.exit(1);
}
