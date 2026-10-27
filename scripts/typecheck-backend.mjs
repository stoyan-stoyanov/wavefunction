// Type-check backend Python with pyright in basic mode: the files given, or else those added or
// modified since this branch left `--base` (origin/main by default), committed or not.
// A ratchet: a file a change touches must pass; files nobody touched are left as they are. The
// opt-in verification scripts under scripts/ are left out: a one-line fix there shouldn't need
// their old type errors fixed first.
//
//   npm run typecheck:backend
//   npm run typecheck:backend -- --base HEAD^1
//   npm run typecheck:backend -- backend/src/wavefunction/main.py
import { spawnSync } from "node:child_process";
import path from "node:path";
import { changedFiles, mergeBase, onlyReformatted, root } from "./changed-code.mjs";

const args = process.argv.slice(2);
let base = "origin/main";
const given = [];
for (let index = 0; index < args.length; index++) {
  if (args[index] === "--base") base = args[++index];
  else given.push(path.resolve(args[index]));
}

let files = given;
if (!given.length) {
  const since = mergeBase(base);
  // A file whose change only reformats it is left as it is, like a file nobody touched.
  files = changedFiles(since, /^backend\/(src|tests)\/.+\.py$/)
    .filter((file) => !onlyReformatted(file, since))
    .map((file) => path.join(root, file));
}
if (!files.length) {
  console.log(`No backend Python files changed since ${base}.`);
  process.exit(0);
}
console.log(`pyright (basic) on ${files.length} file(s) changed since ${given.length ? "the paths given" : base}:`);
for (const file of files) console.log(`  ${path.relative(root, file)}`);
const result = spawnSync("uv", ["run", "--no-sync", "pyright", ...files], {
  cwd: path.join(root, "backend"),
  stdio: "inherit",
});
process.exit(result.status ?? 1);
