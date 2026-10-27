import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { makeProject, makeThread } from "@/test/fixtures";
import { codingApi } from "./api";
import { useCoding } from "./store";
import { Welcome } from "./welcome";

const library = makeProject({ id: "library", name: "Library", icon: "book", icon_color: "#e5604e" });
const parser = makeProject({ id: "parser", name: "Parser", folder: "/synthetic/parser" });
const session = makeThread({ id: "session", project_id: library.id });
const originalState = useCoding.getState();
const patch = vi.fn().mockResolvedValue(undefined);
beforeEach(() => {
  vi.clearAllMocks();
  useCoding.setState({
    workspace: {
      agent_id: "one-agent",
      projects: [library, parser],
      threads: [session],
      access: "sandboxed",
      default_mode: "auto",
    },
    patch,
    refresh: vi.fn().mockResolvedValue(undefined),
  });
});
afterEach(() => {
  useCoding.setState(originalState, true);
  vi.restoreAllMocks();
});

it("moves a new session to another project, or to a project created from its menu", async () => {
  render(<Welcome thread={session} />);
  const menu = () =>
    fireEvent.keyDown(screen.getByRole("button", { name: "Project: Library" }), { key: "ArrowDown" });
  menu();
  expect(await screen.findByRole("menuitemradio", { name: "Library" })).toBeChecked();
  fireEvent.click(screen.getByRole("menuitemradio", { name: "Parser" }));
  await waitFor(() => expect(patch).toHaveBeenCalledExactlyOnceWith(session.id, { project_id: parser.id }));

  const created = makeProject({ id: "created", name: "Created", folder: "/synthetic/created" });
  vi.spyOn(codingApi, "project").mockResolvedValue(created);
  menu();
  fireEvent.click(await screen.findByRole("menuitem", { name: "New project" }));
  const dialog = within(await screen.findByRole("dialog", { name: "New project" }));
  fireEvent.change(dialog.getByRole("textbox", { name: "Project name" }), {
    target: { value: created.name },
  });
  fireEvent.change(dialog.getByRole("textbox", { name: "Repository folder" }), {
    target: { value: created.folder },
  });
  fireEvent.click(dialog.getByRole("button", { name: "Create Project" }));
  await waitFor(() => expect(patch).toHaveBeenLastCalledWith(session.id, { project_id: created.id }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
