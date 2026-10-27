// What a branch really changed: the files that differ from where it left the base branch, and
// whether a file's change only reformats it. Shared by the checks that apply to changed code only
// (typecheck-backend.mjs, check-code-health.mjs), so a formatting-only change never makes a file
// subject to them.
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

export const root = path.resolve(import.meta.dirname, "..");

const git = (...args) => execFileSync("git", args, { cwd: root, encoding: "utf8" });

/** The commit this branch left `base` at, or exits with how to fix it. */
export function mergeBase(base) {
  try {
    return git("merge-base", base, "HEAD").trim();
  } catch {
    console.error(`Cannot find where this branch left ${base}. Fetch it, or pass --base <ref>.`);
    process.exit(2);
  }
}

/** Files added or modified since `since`, committed or not, that match `pattern` and still exist. */
export function changedFiles(since, pattern) {
  const files = [
    ...git("diff", "--name-only", "--diff-filter=d", since).split("\n"),
    ...git("ls-files", "--others", "--exclude-standard").split("\n"),
  ];
  return [...new Set(files)].filter((file) => pattern.test(file) && existsSync(path.join(root, file)));
}

/** The file as it was at `since`, put through today's formatter; null when the file is new. */
export function formattedAtBase(file, since) {
  const shown = spawnSync("git", ["show", `${since}:${file}`], { cwd: root, encoding: "utf8" });
  if (shown.status !== 0) return null;
  const [command, args] = file.endsWith(".py")
    ? ["uv", ["run", "--no-sync", "--project", "backend", "ruff", "format", "--stdin-filename", file, "-"]]
    : [path.join(root, "frontend/node_modules/.bin/prettier"), ["--stdin-filepath", file]];
  const formatted = spawnSync(command, args, { cwd: root, input: shown.stdout, encoding: "utf8" });
  // A base version the formatter can't parse is compared as it was.
  return formatted.status === 0 ? formatted.stdout : shown.stdout;
}

/** Whether the change to `file` since `since` only reformats it. */
export function onlyReformatted(file, since) {
  const before = formattedAtBase(file, since);
  return before !== null && before === readFileSync(path.join(root, file), "utf8");
}
