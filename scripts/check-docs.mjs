// Docs that agents rely on stay true and small:
// - every relative link in a tracked Markdown file points at a file that exists;
// - the root AGENTS.md, and the root plus any area AGENTS.md, stay within a size budget. An agent
//   reads them at the start of every task; shrink a file rather than raising a number.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const ROOT_CHARS = 8_000;
const CHAIN_CHARS = 14_000;

const root = path.resolve(import.meta.dirname, "..");
// Test fixtures are recorded output (such as the model's system prompt), not docs.
const tracked = spawnSync("git", ["ls-files", "*.md"], { cwd: root, encoding: "utf8" }).stdout.split("\n")
  .filter((file) => file && !file.includes("/fixtures/"));
const problems = [];

for (const file of tracked) {
  const text = readFileSync(path.join(root, file), "utf8").replace(/```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`/g, "");
  for (const [, target] of text.matchAll(/\]\(([^)\s]+)\)/g)) {
    if (/^([a-z]+:|#)/i.test(target)) continue;
    const resolved = path.resolve(path.dirname(path.join(root, file)), decodeURIComponent(target.split("#")[0]));
    if (!existsSync(resolved)) problems.push(`${file}: the link to ${target} points at nothing.`);
  }
}

const rootSize = readFileSync(path.join(root, "AGENTS.md"), "utf8").length;
if (rootSize > ROOT_CHARS) problems.push(`AGENTS.md is ${rootSize} characters, over the ${ROOT_CHARS} budget. Move detail into an area AGENTS.md or a doc it links to.`);
for (const file of tracked.filter((name) => name.endsWith("/AGENTS.md"))) {
  const chain = rootSize + readFileSync(path.join(root, file), "utf8").length;
  if (chain > CHAIN_CHARS) problems.push(`AGENTS.md plus ${file} is ${chain} characters, over the ${CHAIN_CHARS} budget. Shrink ${file}.`);
}

if (problems.length) {
  for (const problem of problems) console.error(problem);
  process.exit(1);
}
console.log(`Docs: ${tracked.length} files, links resolve; AGENTS.md ${rootSize} of ${ROOT_CHARS} characters.`);
