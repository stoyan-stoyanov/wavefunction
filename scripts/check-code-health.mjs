// Code health, as a ratchet on product code a branch changes (backend/src, frontend/src):
// files stay within FILE_LINES and functions within FUNCTION_LINES. A file or function already
// over a limit may shrink or stay, never grow. Untouched code, and changes that only reformat, are
// left as they are. Sizes are measured on formatted code, the old version reformatted the same way.
//
//   npm run check:health                       against origin/main
//   npm run check:health -- --base HEAD^1      as CI runs it
//   npm run check:health -- --lang py|ts       one language only
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { changedFiles, formattedAtBase, mergeBase, root } from "./changed-code.mjs";

const FILE_LINES = 1500;
const FUNCTION_LINES = 150;

const args = process.argv.slice(2);
const option = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] : fallback);
const base = option("--base", "origin/main");
const lang = option("--lang", "all");
const since = mergeBase(base);

const patterns = [];
if (lang !== "ts") patterns.push(/^backend\/src\/.+\.py$/);
if (lang !== "py") patterns.push(/^frontend\/src\/.+\.tsx?$/);
const files = changedFiles(since, { test: (file) => patterns.some((pattern) => pattern.test(file)) }).filter(
  (file) => !/\.test\.tsx?$|\.gen\.ts$|\.d\.ts$/.test(file),
);

const PYTHON_SPANS = `
import ast, json, sys
spans = {}
def walk(node, prefix):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = prefix + child.name
            if not isinstance(child, ast.ClassDef):
                spans[name] = max(spans.get(name, 0), child.end_lineno - child.lineno + 1)
            walk(child, name + ".")
        else:
            walk(child, prefix)
walk(ast.parse(sys.stdin.read()), "")
print(json.dumps(spans))
`;

function pythonSpans(text) {
  const result = spawnSync("uv", ["run", "--no-sync", "--project", "backend", "python", "-c", PYTHON_SPANS], {
    cwd: root,
    input: text,
    encoding: "utf8",
  });
  return result.status === 0 ? JSON.parse(result.stdout) : {};
}

let ts;
function typescriptSpans(file, text) {
  ts ??= createRequire(path.join(root, "frontend/package.json"))("typescript");
  const source = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true);
  const lines = (node) =>
    source.getLineAndCharacterOfPosition(node.getEnd()).line -
    source.getLineAndCharacterOfPosition(node.getStart(source)).line +
    1;
  const spans = {};
  const record = (name, node) => (spans[name] = Math.max(spans[name] ?? 0, lines(node)));
  const visit = (node, prefix) => {
    let name;
    if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node) || ts.isClassDeclaration(node)) && node.name) {
      name = node.name.getText(source);
    } else if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      // const X = () => …, function …, or a wrapped one such as memo(function X …).
      const value = ts.isCallExpression(node.initializer) ? node.initializer.arguments[0] : node.initializer;
      if (value && (ts.isArrowFunction(value) || ts.isFunctionExpression(value))) name = node.name.text;
    }
    if (name && !ts.isClassDeclaration(node)) record(prefix + name, node);
    ts.forEachChild(node, (child) => visit(child, name ? `${prefix}${name}.` : prefix));
  };
  visit(source, "");
  return spans;
}

const spansOf = (file, text) => (file.endsWith(".py") ? pythonSpans(text) : typescriptSpans(file, text));
const count = (text) => text.split("\n").length - (text.endsWith("\n") ? 1 : 0);

const problems = [];
for (const file of files) {
  const now = readFileSync(path.join(root, file), "utf8");
  const before = formattedAtBase(file, since);
  if (before === now) continue;
  const [lines, earlier] = [count(now), before === null ? null : count(before)];
  if (lines > FILE_LINES && (earlier === null || lines > earlier)) {
    problems.push(
      `${file} is ${lines} lines${earlier === null ? "" : `, was ${earlier}`} (limit ${FILE_LINES}). ` +
        "Move a group of related functions into a new module rather than growing this one.",
    );
  }
  const [current, previous] = [spansOf(file, now), before === null ? {} : spansOf(file, before)];
  for (const [name, size] of Object.entries(current)) {
    if (size <= FUNCTION_LINES || (previous[name] !== undefined && size <= previous[name])) continue;
    problems.push(
      `${file}: \`${name}\` is ${size} lines${previous[name] === undefined ? "" : `, was ${previous[name]}`} ` +
        `(limit ${FUNCTION_LINES}). Split it: move a self-contained step into its own function.`,
    );
  }
}

if (problems.length) {
  for (const problem of problems) console.error(problem);
  process.exit(1);
}
console.log(`Code health: ${files.length} changed file(s) within the limits (files ${FILE_LINES}, functions ${FUNCTION_LINES} lines).`);
