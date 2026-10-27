import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { codingApi, type CatalogSkill } from "@/coding/api";
import { useCoding } from "@/coding/store";
import { api } from "@/lib/api";
import { SkillsView } from "@/views/skills-view";

function skill(overrides: Partial<CatalogSkill>): CatalogSkill {
  return {
    name: "code-review",
    description: "Review a change. Use when asked to review code.",
    body: "Built-in rules",
    source: "builtin",
    scope: "builtin",
    path: null,
    active: true,
    editable: false,
    version: null,
    digest: null,
    created_at: null,
    modified_at: null,
    ...overrides,
  };
}
const builtin = skill({ overridden_by: "global" });
const global = skill({
  source: "library",
  scope: "global",
  editable: true,
  version: 2,
  body: "My rules",
  path: "/data/Skills/code-review/SKILL.md",
  created_at: "2026-10-01T09:00:00Z",
  modified_at: "2026-10-09T09:00:00Z",
});
const project = skill({
  name: "deploy-steps",
  description: "Ship this app. Use when asked to deploy.",
  body: "Run make deploy.",
  source: "repository",
  scope: "project",
  editable: true,
  project_id: "p1",
  project_name: "Shop",
  path: "/repo/.agents/skills/deploy-steps/SKILL.md",
  digest: "abc",
  modified_at: "2026-10-08T09:00:00Z",
});
const email = skill({
  name: "email",
  description: "Mail etiquette.",
  condition: "Offered when a mailbox is connected",
});

beforeEach(() => {
  useCoding.setState({
    workspace: {
      agent_id: "a",
      access: "network",
      default_mode: "auto",
      threads: [],
      projects: [{ id: "p1", name: "Shop", folder: "/repo" }],
    } as never,
  });
  vi.spyOn(codingApi, "skillCatalog").mockResolvedValue({
    skills: [builtin, global, project, email],
    warnings: [],
  });
});
afterEach(() => vi.restoreAllMocks());

it("lists every skill with its scope, precedence and last edit, filtered by scope and search", async () => {
  vi.mocked(codingApi.skillCatalog).mockRejectedValueOnce(new Error("Backend unavailable"));
  render(<SkillsView />);
  // A failed load says so and retries, rather than loading forever.
  expect(await screen.findByRole("alert")).toHaveTextContent("Backend unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  const list = await screen.findByLabelText("Skills");
  expect(within(list).getAllByRole("button")).toHaveLength(4);
  const overridden = within(list).getByRole("button", { name: "Open skill code-review (Built-in)" });
  expect(overridden).toHaveTextContent("Not used: a Global skill with the same name takes precedence.");
  expect(
    within(list).getByRole("button", { name: "Open skill deploy-steps (Project · Shop)" }),
  ).toHaveTextContent(/Edited/);
  expect(within(list).getByRole("button", { name: "Open skill email (Built-in)" })).toHaveTextContent(
    "Offered when a mailbox is connected",
  );
  fireEvent.change(screen.getByLabelText("Filter by scope"), { target: { value: "project" } });
  expect(within(screen.getByLabelText("Skills")).getAllByRole("button")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Filter by scope"), { target: { value: "all" } });
  fireEvent.change(screen.getByLabelText("Search skills"), { target: { value: "mail" } });
  expect(within(screen.getByLabelText("Skills")).getAllByRole("button")).toHaveLength(1);
});

it("creates a skill in one form, Global or in a project's repository, showing where it is saved", async () => {
  const createGlobal = vi
    .spyOn(api, "createSkill")
    .mockResolvedValue({ name: "release-notes", description: "Write release notes.", body: "", version: 1 });
  const createProject = vi
    .spyOn(codingApi, "createProjectSkill")
    .mockResolvedValue({ name: "lint-rules", path: "/repo/.agents/skills/lint-rules/SKILL.md" });
  render(<SkillsView />);
  fireEvent.click(await screen.findByRole("button", { name: "New skill" }));
  const form = screen.getByRole("form", { name: "New skill" });
  fireEvent.change(within(form).getByLabelText("Skill name"), { target: { value: "Lint Rules" } });
  fireEvent.change(within(form).getByLabelText("Save to"), { target: { value: "p1" } });
  expect(form).toHaveTextContent("/repo/.agents/skills/lint-rules/SKILL.md");
  fireEvent.change(within(form).getByLabelText("Skill description"), {
    target: { value: "Lint conventions. Use when fixing lint." },
  });
  fireEvent.change(within(form).getByLabelText("Skill instructions"), {
    target: { value: "Run npm run lint." },
  });
  fireEvent.click(within(form).getByRole("button", { name: "Create skill" }));
  await waitFor(() =>
    expect(createProject).toHaveBeenCalledWith("p1", {
      name: "Lint Rules",
      description: "Lint conventions. Use when fixing lint.",
      body: "Run npm run lint.",
    }),
  );
  expect(createGlobal).not.toHaveBeenCalled();

  fireEvent.click(await screen.findByRole("button", { name: "New skill" }));
  const second = screen.getByRole("form", { name: "New skill" });
  fireEvent.change(within(second).getByLabelText("Skill name"), { target: { value: "Release notes" } });
  expect(second).toHaveTextContent("Your Wavefunction library · release-notes");
  fireEvent.change(within(second).getByLabelText("Skill description"), {
    target: { value: "Write release notes." },
  });
  fireEvent.click(within(second).getByRole("button", { name: "Create skill" }));
  await waitFor(() =>
    expect(createGlobal).toHaveBeenCalledWith({
      name: "Release notes",
      description: "Write release notes.",
      body: "",
    }),
  );
});

it("edits a project skill against the version it read, reports a conflict, reopens that same skill, and deletes it", async () => {
  // Another project's skill with the same name is listed first; saving must not open it.
  const other = {
    ...project,
    project_id: "p2",
    project_name: "Blog",
    path: "/blog/.agents/skills/deploy-steps/SKILL.md",
    digest: "zzz",
  };
  vi.mocked(codingApi.skillCatalog).mockResolvedValue({ skills: [other, project], warnings: [] });
  const update = vi
    .spyOn(codingApi, "updateProjectSkill")
    .mockRejectedValueOnce(new Error("This skill changed since it was opened. Reload it before saving."))
    .mockResolvedValue({ name: "deploy-steps", path: project.path! });
  const remove = vi.spyOn(codingApi, "deleteProjectSkill").mockResolvedValue(undefined);
  render(<SkillsView />);
  fireEvent.click(await screen.findByRole("button", { name: "Open skill deploy-steps (Project · Shop)" }));
  expect(screen.getByText("/repo/.agents/skills/deploy-steps/SKILL.md")).toBeVisible();
  fireEvent.change(screen.getByLabelText("Skill instructions"), { target: { value: "Run make ship." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("changed since it was opened");
  expect(update).toHaveBeenCalledWith("p1", {
    path: project.path,
    digest: "abc",
    description: project.description,
    body: "Run make ship.",
  });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(update).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(vi.mocked(codingApi.skillCatalog).mock.calls.length).toBeGreaterThan(1));
  expect(await screen.findByText("/repo/.agents/skills/deploy-steps/SKILL.md")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("p1", project.path, "abc"));
});

it("shows built-ins read-only and customises one by copying it to Global, or opens the existing copy", async () => {
  const create = vi.spyOn(api, "createSkill").mockResolvedValue({
    name: "code-review",
    description: global.description,
    body: "My rules",
    version: 2,
  });
  vi.mocked(codingApi.skillCatalog).mockResolvedValueOnce({
    skills: [{ ...builtin, overridden_by: undefined }],
    warnings: [],
  });
  render(<SkillsView />);
  fireEvent.click(await screen.findByRole("button", { name: "Open skill code-review (Built-in)" }));
  expect(screen.queryByLabelText("Skill instructions")).toBeNull();
  expect(screen.getByText("Built-in rules")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Customise" }));
  await waitFor(() =>
    expect(create).toHaveBeenCalledWith({
      name: "code-review",
      description: builtin.description,
      body: "Built-in rules",
    }),
  );
  // The reloaded catalog opens the new Global copy, which is editable.
  expect(await screen.findByLabelText("Skill instructions")).toHaveValue("My rules");
  fireEvent.click(screen.getByRole("button", { name: "All skills" }));
  fireEvent.click(await screen.findByRole("button", { name: "Open skill code-review (Built-in)" }));
  fireEvent.click(screen.getByRole("button", { name: "Open your copy" }));
  expect(await screen.findByLabelText("Skill instructions")).toHaveValue("My rules");
});
