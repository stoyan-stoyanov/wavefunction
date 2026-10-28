// CHANGELOG.md is written by hand: each pull request that changes what users see adds its line
// under "## Unreleased". version.mjs turns that section into the release's own, and its notes become
// the GitHub release notes and latest.json's, which the app shows before it updates.
// `node scripts/changelog.mjs 0.2.0` prints that release's notes.
import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const file = join(resolve(import.meta.dirname, ".."), "CHANGELOG.md");

/** What follows a "## title" or "## title - date" heading up to the next one, or null without one. */
function section(text, title) {
  const lines = text.split("\n");
  const start = lines.findIndex((line) => line === `## ${title}` || line.startsWith(`## ${title} - `));
  if (start < 0) return null;
  const end = lines.findIndex((line, index) => index > start && line.startsWith("## "));
  return lines.slice(start + 1, end < 0 ? undefined : end).join("\n").trim();
}

/** A released version's notes; a release without any is refused. */
export function releaseNotes(version) {
  const notes = section(readFileSync(file, "utf8"), version);
  if (!notes) throw new Error(`CHANGELOG.md has no notes for ${version}: run node scripts/version.mjs ${version} with them under ## Unreleased.`);
  return notes;
}

/** Date the Unreleased section as the version's, under a fresh empty Unreleased. Done once per version. */
export function closeUnreleased(version) {
  const text = readFileSync(file, "utf8");
  if (section(text, version) !== null) return;
  if (!section(text, "Unreleased")) throw new Error("CHANGELOG.md has nothing under ## Unreleased. Add what this release changes for users first.");
  const date = new Date().toISOString().slice(0, 10);
  writeFileSync(file, text.replace("\n## Unreleased\n", `\n## Unreleased\n\n## ${version} - ${date}\n`));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) console.log(releaseNotes(process.argv[2]));
