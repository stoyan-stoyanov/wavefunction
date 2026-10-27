---
name: skill-creator
description: "Create or update a skill. Use when the user asks to make, save, change or improve a skill, or to turn a workflow, checklist or set of instructions into one."
---

A skill is a SKILL.md: frontmatter with a `name` and a `description`, then markdown instructions. Agents see only the name and description until they decide to read the rest, so the description decides whether the skill is ever used.

Choose the scope. Use what the user asked for; otherwise ask only if it is unclear.
- Project: guidance for one repository, shared with everyone who works in it. Write `.agents/skills/<name>/SKILL.md` in the repository with file tools. To change an existing project skill, edit its file where it is.
- Global: the user's own guidance for every project. Call `save_skill` (search_available_tools for "skills" if it is not loaded). Saving an existing name keeps its earlier versions.

Write it well:
- Name: lowercase words joined by hyphens, at most 64 characters, without "claude" or "anthropic".
- Description: one or two sentences saying what it covers and when to use it, with the words a request would contain ("Use when asked to review a pull request").
- Instructions: the steps, rules and checks someone needs to do the task right, in imperative form. Keep it short; leave out what a capable agent already knows. Point to files in the repository rather than copying them.

Read an existing skill before replacing it, and keep what still applies. Afterwards, tell the user the skill's name, scope and where it was saved. New tasks can use it; running tasks keep the skills they started with.
