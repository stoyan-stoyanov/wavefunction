// What differs between macOS and Linux stays in one place per layer, so a feature is written once
// and Linux work doesn't reach into macOS code:
// - the engine checks the platform only in platforms.py and the sandbox's own modules, which also
//   hold the words "this Mac", "your Mac" and "Finder" in what it says;
// - the interface only in lib/platform.ts, which also holds the words "this Mac", "your Mac",
//   "Finder" and ⌘ that are different on Linux;
// - the shell's per-platform code (`target_os`) stays in its per-platform modules, and lib.rs
//   keeps no more of it than it has now: move code out rather than raising a number.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const files = spawnSync("git", ["ls-files", "--cached", "--others", "--exclude-standard"], { cwd: root, encoding: "utf8" })
  .stdout.split("\n")
  .filter(Boolean);

const ENGINE = "backend/src/wavefunction/";
const ENGINE_ALLOWED = new Set(["platforms.py", "sandbox.py", "sandbox_linux.py"].map((name) => ENGINE + name));
const ENGINE_CHECK = /\bsys\.platform\b|\bplatform\.(system|machine|mac_ver|release|version|uname|platform)\(|\bos\.uname\(/;
const MAC_WORDS = /\bthis Mac\b|\byour Mac\b|\bFinder\b/;
const INTERFACE_ALLOWED = "frontend/src/lib/platform.ts";
const INTERFACE_CHECK = /\bnavigator\.(userAgent|platform|userAgentData)\b|@tauri-apps\/plugin-os/;
const INTERFACE_WORDS = /\bthis Mac\b|\byour Mac\b|\bFinder\b|⌘/;
// The shell's modules that exist to hold one feature's per-platform code, and lib.rs's ceiling.
const SHELL_MODULES = new Set(["notifications.rs", "quit.rs", "window_controls.rs"].map((name) => `src-tauri/src/${name}`));
const SHELL_LIB = "src-tauri/src/lib.rs";
const SHELL_LIB_MOST = 10;

const problems = [];
const read = (file) => readFileSync(path.join(root, file), "utf8");
// Comments may name a platform freely: blank them out, keeping line numbers.
const blank = (text) => text.replace(/\/\*[\s\S]*?\*\//g, (comment) => comment.replace(/[^\n]/g, " ")).replace(/\/\/[^\n]*/g, "");
// So may Python's comments and docstrings, which describe the macOS app to developers. A tool's
// docstring that the model reads takes `{this_computer}`, filled in from platforms.py.
const blankPython = (text) =>
  text.replace(/("""|''')[\s\S]*?\1/g, (string) => string.replace(/[^\n]/g, " ")).replace(/#[^\n]*/g, "");
const lines = (text) => text.split("\n").map((line, index) => [index + 1, line]);

for (const file of files.filter((name) => name.startsWith(ENGINE) && name.endsWith(".py") && !ENGINE_ALLOWED.has(name))) {
  for (const [number, line] of lines(read(file))) {
    if (!line.trimStart().startsWith("#") && ENGINE_CHECK.test(line))
      problems.push(`${file}:${number}: checks the platform. Ask platforms.py instead, adding a function there if none fits.`);
  }
  for (const [number, line] of lines(blankPython(read(file)))) {
    if (MAC_WORDS.test(line))
      problems.push(`${file}:${number}: names the Mac. Use platforms.this_computer(), computer() or file_manager(), which say it right on Linux too.`);
  }
}

const interfaceFiles = files.filter(
  (name) => /^frontend\/src\/.+\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) && name !== INTERFACE_ALLOWED,
);
for (const file of interfaceFiles) {
  for (const [number, line] of lines(blank(read(file)))) {
    if (INTERFACE_CHECK.test(line))
      problems.push(`${file}:${number}: checks the platform. Use \`has\` from lib/platform.ts, adding an entry if none fits.`);
    if (INTERFACE_WORDS.test(line))
      problems.push(`${file}:${number}: names the Mac. Use \`words\` from lib/platform.ts, which says it right on Linux too.`);
  }
}

for (const file of files.filter((name) => /^src-tauri\/src\/.+\.rs$/.test(name) && !SHELL_MODULES.has(name))) {
  const uses = lines(blank(read(file))).filter(([, line]) => /\btarget_os\s*=/.test(line));
  if (file === SHELL_LIB) {
    if (uses.length > SHELL_LIB_MOST)
      problems.push(`${file}: ${uses.length} per-platform blocks, over ${SHELL_LIB_MOST}. Put the new one in its own module, as notifications.rs does.`);
  } else {
    for (const [number] of uses)
      problems.push(`${file}:${number}: per-platform code. Keep it in a module made for it, such as notifications.rs, and add that module to scripts/check-platform.mjs.`);
  }
}

if (problems.length) {
  for (const problem of problems) console.error(problem);
  console.error("\nWhy: docs/ARCHITECTURE.md › macOS and Linux. A feature is written once; what differs on Linux is one function or entry in a platform module.");
  process.exit(1);
}
console.log("Platform differences: only in the platform modules.");
