import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ask } from "@tauri-apps/plugin-dialog";
import { toast } from "sonner";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { codingApi, type CodingProject, type CodingRun, type CodingThread } from "./api";
import { Sidebar } from "./sidebar";
import { useCoding } from "./store";
import type { Schemas } from "@/lib/types";
import { makeProject, makeRun, makeThread } from "@/test/fixtures";

type SidebarThread = Schemas["SidebarThread"];

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    codingApi: {
      ...original.codingApi,
      removeProject: vi.fn(),
      fork: vi.fn(),
      orderProjects: vi.fn(),
    },
  };
});
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ ask: vi.fn() }));

const project: CodingProject = makeProject({
  id: "project-repo",
  name: "Repository",
  folder: "/synthetic/repository",
});
const firstProject: CodingProject = {
  ...project,
  id: "project-library",
  name: "Library",
  folder: "/synthetic/library",
};
function thread(id: string, projectId: string | null = null, archived = false): SidebarThread {
  return makeThread({
    id,
    title: id + " chat",
    project_id: projectId,
    folder: "/synthetic/" + id,
    worktree_available: true,
    archived,
  });
}

function run(owner: CodingThread, status: CodingRun["status"]): CodingRun {
  return makeRun({ id: "run-" + owner.id, status, input: { text: "Synthetic task" } });
}

const originalState = useCoding.getState();
const newThread = vi.fn().mockResolvedValue(undefined);
const select = vi.fn().mockResolvedValue(undefined);
const patch = vi.fn().mockResolvedValue(undefined);
const refresh = vi.fn().mockResolvedValue(undefined);
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(codingApi.removeProject).mockResolvedValue(undefined);
  vi.mocked(codingApi.fork).mockResolvedValue({ ...thread("forked", project.id), draft: null });
  useCoding.setState({
    workspace: {
      agent_id: "one-agent",
      projects: [firstProject, project],
      threads: [
        thread("unassigned"),
        thread("repository", project.id),
        thread("orphan", "removed-project"),
        thread("archived-unassigned", null, true),
        thread("archived-repository", project.id, true),
      ],
      access: "sandboxed",
      default_mode: "auto",
    },
    selected: "repository",
    details: {},
    newThread,
    select,
    patch,
    refresh,
  });
});
afterEach(() => {
  useCoding.setState(originalState, true);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function open() {
  const callbacks = {
    onSettings: vi.fn(),
    onChat: vi.fn(),
    onProject: vi.fn(),
    onThread: vi.fn(),
    onToggleSidebar: vi.fn(),
  };
  const view = render(<Sidebar {...callbacks} />);
  return { ...view, ...callbacks };
}

it("starts a chat from its project plus and expands a collapsed project", () => {
  const { onChat } = open();
  fireEvent.click(screen.getByRole("button", { name: "Repository" }));
  expect(screen.queryByRole("button", { name: "repository chat" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "New chat in Repository" }));
  expect(newThread).toHaveBeenCalledWith(project.id);
  expect(onChat).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "repository chat" })).toBeInTheDocument();
});

it("starts a New session in the open chat's project, and opens New project from the Projects title", () => {
  const { onChat, onProject } = open();
  // The open chat is in the second project, so the session doesn't just take the first.
  fireEvent.click(screen.getByRole("button", { name: "New session" }));
  expect(newThread).toHaveBeenCalledExactlyOnceWith(project.id);
  expect(onChat).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  expect(onProject).toHaveBeenCalledExactlyOnceWith();
  expect(newThread).toHaveBeenCalledOnce();
  expect(screen.getAllByRole("button", { name: "New project" })).toHaveLength(1);
});

it("lists a project's routines first and changes or stops one from its menu", async () => {
  const workspace = useCoding.getState().workspace!;
  const routine = {
    instruction: "Check the logs.",
    timing: { kind: "interval" as const, every_seconds: 3600 },
    summary: "every hour",
    enabled: true,
    report_to: null,
    last_run_at: null,
    next_run_at: "2099-01-01T09:00:00Z",
    last_error: null,
    finished: false,
  };
  useCoding.setState({
    workspace: {
      ...workspace,
      threads: [...workspace.threads, { ...thread("watcher", project.id), routine }],
    },
  });
  const update = vi.spyOn(codingApi, "updateRoutine").mockResolvedValue({ ...routine, enabled: false });
  const stop = vi.spyOn(codingApi, "stopRoutine").mockResolvedValue(undefined);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  open();
  const chats = within(screen.getByRole("region", { name: "Repository project" })).getAllByText(/ chat$/);
  expect(chats.map((title) => title.textContent)).toEqual(["watcher chat", "repository chat"]);
  expect(screen.getByRole("img", { name: /^Routine, every hour\. Next / })).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  await screen.findByRole("menuitem", { name: "Chat settings" });
  expect(screen.queryByRole("menuitem", { name: "Routine settings" })).toBeNull();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });

  const settings = async () => {
    fireEvent.keyDown(screen.getByRole("button", { name: "Options for watcher chat" }), { key: "ArrowDown" });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Routine settings" }));
    return within(await screen.findByRole("dialog", { name: "Routine settings" }));
  };
  let dialog = await settings();
  fireEvent.change(dialog.getByRole("textbox", { name: "Routine instruction" }), {
    target: { value: "Check the logs for errors." },
  });
  fireEvent.change(dialog.getByRole("combobox", { name: "Report each run to" }), {
    target: { value: "repository" },
  });
  fireEvent.click(dialog.getByRole("checkbox", { name: "Running" }));
  fireEvent.click(dialog.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(update).toHaveBeenCalledExactlyOnceWith("watcher", {
      instruction: "Check the logs for errors.",
      timing: routine.timing,
      enabled: false,
      report_to: "repository",
    }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(refresh).toHaveBeenCalled();
  dialog = await settings();
  fireEvent.click(dialog.getByRole("button", { name: "Stop routine" }));
  await waitFor(() => expect(stop).toHaveBeenCalledExactlyOnceWith("watcher"));
});

it("opens New project without creating a projectless chat when there are no projects", () => {
  const workspace = useCoding.getState().workspace!;
  useCoding.setState({ workspace: { ...workspace, projects: [] } });
  const { onChat, onProject } = open();
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  expect(onProject).toHaveBeenCalledOnce();
  expect(onProject).toHaveBeenCalledWith();
  expect(onChat).not.toHaveBeenCalled();
  expect(newThread).not.toHaveBeenCalled();
  expect(useCoding.getState().workspace?.threads).toBe(workspace.threads);
});

it("routes project chat creation and selection to their owning project and chat", () => {
  const { onChat, onProject } = open();
  fireEvent.click(screen.getByRole("button", { name: "Start a chat in Library" }));
  fireEvent.click(screen.getByRole("button", { name: "New chat in Repository" }));
  expect(newThread.mock.calls).toEqual([[firstProject.id], [project.id]]);
  fireEvent.click(screen.getByRole("button", { name: "repository chat" }));
  expect(select).toHaveBeenCalledWith("repository");
  expect(onChat).toHaveBeenCalledTimes(3);
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  expect(onProject).toHaveBeenCalledOnce();
});

it("renames a chat from its menu or by double-click, saving only a changed title", async () => {
  open();
  const row = screen.getByRole("button", { name: "repository chat" });
  fireEvent.click(row, { detail: 1 });
  fireEvent.click(row, { detail: 2 });
  fireEvent.doubleClick(row, { detail: 2 });
  expect(select).toHaveBeenCalledOnce();
  const field = screen.getByRole<HTMLInputElement>("textbox", {
    name: "Rename repository chat",
  });
  expect(field).toHaveFocus();
  expect([field.selectionStart, field.selectionEnd]).toEqual([0, field.value.length]);
  fireEvent.change(field, { target: { value: "  Parser fix  " } });
  fireEvent.keyDown(field, { key: "Enter" });
  expect(patch).toHaveBeenCalledExactlyOnceWith("repository", { title: "Parser fix" });
  expect(screen.queryByRole("textbox", { name: "Rename repository chat" })).toBeNull();

  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }));
  const cancelled = await screen.findByRole("textbox", { name: "Rename repository chat" });
  await waitFor(() => expect(cancelled).toHaveFocus());
  fireEvent.change(cancelled, { target: { value: "Discarded" } });
  fireEvent.keyDown(cancelled, { key: "Escape" });
  fireEvent.doubleClick(screen.getByRole("button", { name: "repository chat" }));
  const emptied = screen.getByRole("textbox", { name: "Rename repository chat" });
  fireEvent.change(emptied, { target: { value: "   " } });
  fireEvent.blur(emptied);
  expect(screen.getByRole("button", { name: "repository chat" })).toBeVisible();
  expect(patch).toHaveBeenCalledOnce();
});

it("moves a dragged project with its chats, keeps it through stale snapshots, ignores a cancelled drag and reverts an unsaved order", async () => {
  let finishSave!: () => void;
  vi.mocked(codingApi.orderProjects)
    .mockReturnValueOnce(
      new Promise((resolve) => {
        finishSave = () => resolve([project, firstProject]);
      }),
    )
    .mockRejectedValueOnce(new Error("Projects changed."));
  // The first snapshot predates the move; the one after the rejection never arrives.
  vi.spyOn(codingApi, "workspace")
    .mockResolvedValueOnce(useCoding.getState().workspace!)
    .mockReturnValueOnce(new Promise(() => undefined));
  useCoding.setState({ refresh: originalState.refresh });
  // Projects are stacked 100px apart in their current order.
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    const sections = [...(this.parentElement?.children ?? [])].filter((item) => item.matches("section"));
    const top = Math.max(0, sections.indexOf(this)) * 100;
    return { top, bottom: top + 100, height: 100, left: 0, right: 200, width: 200, x: 0, y: top } as DOMRect;
  });
  open();
  const order = () => screen.getAllByRole("region").map((section) => section.getAttribute("aria-label"));
  const drag = (name: string, finish: () => void) => {
    const handle = screen.getByRole("button", { name });
    fireEvent.pointerDown(handle, { button: 0, isPrimary: true, pointerId: 7, clientX: 20, clientY: 10 });
    fireEvent.pointerMove(window, { pointerId: 7, clientX: 20, clientY: 190 });
    finish();
    fireEvent.pointerUp(window, { pointerId: 7, clientX: 20, clientY: 190 });
    fireEvent.click(handle);
    // Dropping does not also collapse the project.
    expect(handle).toHaveAttribute("aria-expanded", "true");
  };

  drag("Library", () => undefined);
  expect(order()).toEqual(["Repository project", "Library project"]);
  expect(codingApi.orderProjects).toHaveBeenCalledExactlyOnceWith([project.id, firstProject.id]);
  expect(
    within(screen.getByRole("region", { name: "Repository project" })).getByRole("button", {
      name: "repository chat",
    }),
  ).toBeVisible();
  // A snapshot read before the order was saved does not move the project back.
  await act(() => useCoding.getState().refresh());
  expect(order()).toEqual(["Repository project", "Library project"]);
  await act(async () => finishSave());

  drag("Repository", () => fireEvent.keyDown(window, { key: "Escape" }));
  expect(order()).toEqual(["Repository project", "Library project"]);
  expect(codingApi.orderProjects).toHaveBeenCalledOnce();

  drag("Repository", () => undefined);
  expect(order()).toEqual(["Library project", "Repository project"]);
  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("The project order was not saved: Projects changed."),
  );
  expect(order()).toEqual(["Repository project", "Library project"]);
  expect(codingApi.orderProjects).toHaveBeenLastCalledWith([firstProject.id, project.id]);
  expect(codingApi.workspace).toHaveBeenCalledTimes(2);
});

it("archives a chat from its menu, opening another only in place of the open one, and sends chat settings to the chosen chat", async () => {
  const { onThread } = open();
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Chat settings" }));
  expect(onThread).toHaveBeenCalledWith(
    useCoding.getState().workspace?.threads.find((item) => item.id === "repository"),
  );
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  // Archived through the store: the sidebar keeps the open chat listed, so archiving it opens
  // the next listed chat.
  let threads = [...useCoding.getState().workspace!.threads, thread("second", project.id)];
  act(() => useCoding.setState({ workspace: { ...useCoding.getState().workspace!, threads } }));
  vi.spyOn(codingApi, "patch").mockImplementation(async (id, data) => {
    threads = threads.map((item) => (item.id === id ? { ...item, ...data } : item));
    return threads.find((item) => item.id === id)!;
  });
  vi.spyOn(codingApi, "workspace").mockImplementation(async () => ({
    ...useCoding.getState().workspace!,
    threads,
  }));
  vi.spyOn(codingApi, "thread").mockImplementation(async (id) => ({
    ...threads.find((item) => item.id === id)!,
    runs: [],
    older_cursor: null,
    attachments: [],
  }));
  vi.spyOn(codingApi, "touchThread").mockResolvedValue(undefined);
  useCoding.setState({
    patch: originalState.patch,
    refresh: originalState.refresh,
    load: originalState.load,
    select: originalState.select,
  });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Archive" }));
  await waitFor(() => expect(useCoding.getState().selected).toBe("unassigned"));
  expect(codingApi.patch).toHaveBeenCalledWith("repository", { archived: true });
  expect(screen.queryByText("repository chat")).toBeNull();
  // An archived chat being read stays open while another chat is archived.
  await act(() => useCoding.getState().select("archived-repository"));
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for second chat" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Archive" }));
  await waitFor(() => expect(screen.queryByText("second chat")).toBeNull());
  expect(codingApi.patch).toHaveBeenCalledWith("second", { archived: true });
  expect(useCoding.getState().selected).toBe("archived-repository");
});

it("waits for desktop confirmation and warns before deleting a chat's owned worktree", async () => {
  // The dialog plugin replaces window.confirm with an async function, whose Promise is truthy.
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  const browserConfirm = vi
    .spyOn(window, "confirm")
    .mockImplementation((() => Promise.resolve(false)) as never);
  const remove = vi.fn().mockResolvedValue(undefined);
  const workspace = useCoding.getState().workspace!;
  useCoding.setState({
    remove,
    workspace: {
      ...workspace,
      threads: workspace.threads.map((item) =>
        item.id === "repository" ? { ...item, worktree: true, worktree_status: "active" } : item,
      ),
    },
  });
  vi.mocked(ask).mockResolvedValueOnce(false).mockResolvedValueOnce(true).mockResolvedValueOnce(true);
  open();
  const deleteChat = async () => {
    fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
      key: "ArrowDown",
    });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Delete" }));
  };
  await deleteChat();
  await waitFor(() =>
    expect(ask).toHaveBeenCalledWith(
      "Delete this chat, its history, and its managed worktree? Uncommitted, untracked, and ignored files in the worktree will be permanently deleted. Its Git branch will be kept.",
      {
        kind: "warning",
      },
    ),
  );
  expect(remove).not.toHaveBeenCalled();
  await deleteChat();
  await waitFor(() => expect(remove).toHaveBeenCalledExactlyOnceWith("repository", true));
  act(() => useCoding.setState({ workspace }));
  await deleteChat();
  await waitFor(() => expect(remove).toHaveBeenLastCalledWith("repository", false));
  expect(ask).toHaveBeenLastCalledWith("Delete this chat and its history?", { kind: "warning" });
  // Its helpers go with it, and their worktrees need the same confirmation.
  const chat = workspace.threads.find((item) => item.id === "repository")!;
  const helper = {
    ...chat,
    id: "repository-helper",
    title: "Helper",
    parent_thread_id: "repository",
    worktree: true,
  };
  act(() => useCoding.setState({ workspace: { ...workspace, threads: [...workspace.threads, helper] } }));
  vi.mocked(ask).mockResolvedValueOnce(true);
  await deleteChat();
  await waitFor(() => expect(remove).toHaveBeenLastCalledWith("repository", true));
  expect(ask).toHaveBeenLastCalledWith(
    "Delete this chat, its history, and its managed worktrees? Uncommitted, untracked, and ignored files in them will be permanently deleted. Git branches will be kept. Its 1 helper is deleted too.",
    { kind: "warning" },
  );
  expect(browserConfirm).not.toHaveBeenCalled();
});

it("preserves removed-project chat history and routes new chats to the remaining real project", async () => {
  const confirmation = vi.spyOn(window, "confirm").mockReturnValue(true);
  const originalThreads = useCoding.getState().workspace!.threads;
  refresh.mockImplementationOnce(async () => {
    const workspace = useCoding.getState().workspace!;
    useCoding.setState({
      workspace: { ...workspace, projects: [firstProject] },
    });
  });
  open();
  fireEvent.keyDown(screen.getByRole("button", { name: "Settings for Repository" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Remove" }));
  await waitFor(() => expect(codingApi.removeProject).toHaveBeenCalledWith(project.id));
  await waitFor(() =>
    expect(screen.queryByRole("region", { name: "Repository project" })).not.toBeInTheDocument(),
  );
  expect(confirmation).toHaveBeenCalledWith("Remove this project? Its chat history will remain searchable.");
  expect(screen.queryByRole("button", { name: "repository chat" })).not.toBeInTheDocument();
  expect(useCoding.getState().workspace?.threads).toBe(originalThreads);
  expect(useCoding.getState().workspace?.threads.find((item) => item.id === "repository")).toMatchObject({
    project_id: project.id,
    folder: "/synthetic/repository",
    conversation_id: "conversation-repository",
  });
  act(() => useCoding.setState({ selected: "repository" }));
  fireEvent.click(screen.getByRole("button", { name: "New chat in Library" }));
  expect(newThread).toHaveBeenCalledWith(firstProject.id);
});

it("forks the chosen row after its checkout was deleted, then refreshes before selecting the fork", async () => {
  const workspace = useCoding.getState().workspace!;
  useCoding.setState({
    workspace: {
      ...workspace,
      threads: [
        ...workspace.threads,
        { ...thread("other", firstProject.id), worktree_available: false, worktree_status: "deleted" },
      ],
    },
  });
  let finishRefresh!: () => void;
  refresh.mockReturnValueOnce(
    new Promise<void>((resolve) => {
      finishRefresh = resolve;
    }),
  );
  open();
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for other chat" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Fork chat" }));
  expect(screen.getByRole("checkbox", { name: "New worktree" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "New worktree" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Fork chat" }));
  await waitFor(() => expect(codingApi.fork).toHaveBeenCalledWith("other", { worktree: true }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
  expect(select).not.toHaveBeenCalled();
  await act(async () => {
    finishRefresh();
  });
  await waitFor(() => expect(select).toHaveBeenCalledWith("forked"));
  expect(codingApi.fork).toHaveBeenCalledOnce();
  expect(toast.error).not.toHaveBeenCalled();
});

it.each([
  { source: "workspace", status: "running" },
  { source: "workspace", status: "waiting_approval" },
  { source: "loaded runs", status: "queued" },
  { source: "loaded runs", status: "waiting_connector" },
] satisfies Array<{
  source: "workspace" | "loaded runs";
  status: CodingRun["status"];
}>)("disables Fork chat when its $source are active ($status)", async ({ source, status }) => {
  const workspace = useCoding.getState().workspace!;
  const owner = workspace.threads.find((item) => item.id === "repository")!;
  if (source === "workspace") {
    useCoding.setState({
      workspace: {
        ...workspace,
        threads: workspace.threads.map((item) => (item.id === owner.id ? { ...item, status } : item)),
      },
    });
  } else {
    // Without a status from the workspace, the sidebar reads the chat's loaded runs.
    const unknown = { ...owner, status: undefined } as unknown as typeof owner;
    useCoding.setState({
      workspace: {
        ...workspace,
        threads: workspace.threads.map((item) => (item.id === owner.id ? unknown : item)),
      },
      details: {
        [owner.id]: {
          ...unknown,
          attachments: [],
          runs: [run(owner, status)],
        },
      },
    });
  }
  open();
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  const fork = await screen.findByRole("menuitem", { name: "Fork chat" });
  expect(fork).toHaveAttribute("aria-disabled", "true");
  fireEvent.click(fork);
  expect(codingApi.fork).not.toHaveBeenCalled();
  expect(select).not.toHaveBeenCalled();
});

it("keeps Fork chat available when only another chat is active", async () => {
  const workspace = useCoding.getState().workspace!;
  const other = thread("other", firstProject.id);
  useCoding.setState({
    selected: other.id,
    workspace: {
      ...workspace,
      threads: [...workspace.threads, { ...other, status: "running" }],
    },
    details: {
      [other.id]: {
        ...other,
        attachments: [],
        runs: [run(other, "running")],
      },
    },
  });
  open();
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  const fork = await screen.findByRole("menuitem", { name: "Fork chat" });
  expect(fork).not.toHaveAttribute("aria-disabled");
  fireEvent.click(fork);
  fireEvent.click(screen.getByRole("button", { name: "Fork chat" }));
  await waitFor(() => expect(codingApi.fork).toHaveBeenCalledWith("repository", { worktree: true }));
  await waitFor(() => expect(select).toHaveBeenCalledWith("forked"));
});

it("replaces a stale background spinner with the unread dot and re-enables Fork", async () => {
  const owner = thread("repository", project.id);
  const workspace = useCoding.getState().workspace!;
  useCoding.setState({
    selected: "unassigned",
    workspace: { ...workspace, threads: [{ ...owner, status: "running" }] },
    details: { [owner.id]: { ...owner, attachments: [], runs: [run(owner, "running")] } },
  });
  render(
    <Sidebar
      onSettings={vi.fn()}
      onChat={vi.fn()}
      onProject={vi.fn()}
      onThread={vi.fn()}
      unreadThreads={new Set([owner.id])}
    />,
  );
  expect(screen.getByLabelText("Chat running")).toBeVisible();
  act(() =>
    useCoding.setState({
      workspace: { ...workspace, threads: [{ ...owner, status: "idle" }] },
    }),
  );
  expect(screen.queryByLabelText("Chat running")).toBeNull();
  expect(screen.getByLabelText("Unread response")).toBeVisible();
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  const fork = await screen.findByRole("menuitem", { name: "Fork chat" });
  expect(fork).not.toHaveAttribute("aria-disabled");
  fireEvent.click(fork);
  fireEvent.click(screen.getByRole("button", { name: "Fork chat" }));
  await waitFor(() => expect(codingApi.fork).toHaveBeenCalledWith(owner.id, { worktree: true }));
});

it("reports a rejected fork and preserves the current selection", async () => {
  vi.mocked(codingApi.fork).mockRejectedValueOnce(new Error("This chat has an active task."));
  open();
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Fork chat" }));
  fireEvent.click(screen.getByRole("button", { name: "Fork chat" }));
  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("This chat has an active task."));
  expect(refresh).not.toHaveBeenCalled();
  expect(select).not.toHaveBeenCalled();
  expect(useCoding.getState().selected).toBe("repository");
});

it("forks a non-Git conversation in its current folder", async () => {
  const workspace = useCoding.getState().workspace!;
  useCoding.setState({
    workspace: {
      ...workspace,
      threads: [{ ...thread("repository", project.id), worktree_available: false }],
    },
  });
  open();
  fireEvent.keyDown(screen.getByRole("button", { name: "Options for repository chat" }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Fork chat" }));
  expect(screen.getByRole("checkbox", { name: "New worktree" })).toBeDisabled();
  expect(
    screen.getByText("Worktrees require a Git repository. The new chat will use the current folder."),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Fork chat" }));
  await waitFor(() => expect(codingApi.fork).toHaveBeenCalledWith("repository", { worktree: false }));
});

it("collapses settled helpers behind their parent's toggle while preserving active helpers, ownership and selection", async () => {
  const workspace = useCoding.getState().workspace!;
  const parent = {
    ...thread("parent", project.id),
    status: "running" as const,
    active_run_id: "current-run",
  };
  const child = {
    ...thread("child", project.id),
    parent_thread_id: parent.id,
    parent_run_id: "current-run",
    status: "idle" as const,
  };
  const previous = {
    ...thread("previous", project.id),
    parent_thread_id: parent.id,
    parent_run_id: "previous-run",
  };
  const pinned = { ...previous, id: "pinned", title: "pinned chat", pinned: true };
  const waiting = { ...previous, id: "waiting", title: "waiting chat", status: "waiting_connector" as const };
  const queued = { ...previous, id: "queued", title: "queued chat", status: "queued" as const };
  const orphan = { ...thread("orphan", project.id), parent_thread_id: "removed" };
  // A set-aside (archived) chat's helpers go with it, as after a rewind; they are not orphans.
  const archived = { ...thread("archived", project.id), archived: true };
  const setAside = { ...previous, id: "set-aside", title: "set-aside chat", parent_thread_id: archived.id };
  // A finished helper is archived: out of the list, under its parent in the archived view.
  const done = { ...previous, id: "done", title: "done chat", archived: true };
  const threads = [orphan, previous, child, pinned, waiting, queued, parent, archived, setAside, done];
  useCoding.setState({ workspace: { ...workspace, threads }, selected: child.id });
  open();
  const rows = () =>
    within(screen.getByRole("region", { name: "Repository project" }))
      .getAllByRole("button")
      .filter((button) => button.classList.contains("thread-select"))
      .map((button) => button.textContent);
  expect(rows()).toEqual([
    "orphan chat",
    "parent chat",
    "child chat",
    "pinned chat",
    "waiting chat",
    "queued chat",
  ]);
  // A helper paused for the user is told apart from running work, on its row and its parent's.
  const row = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}`) });
  expect(within(row("waiting chat")).getByLabelText("Needs your input")).toBeVisible();
  expect(within(row("waiting chat")).queryByLabelText("Chat running")).toBeNull();
  expect(within(row("parent chat")).getByLabelText("A helper needs your input")).toBeVisible();
  expect(within(row("queued chat")).getByLabelText("Chat running")).toBeVisible();
  // A helper's approval card is the user's to decide too.
  act(() =>
    useCoding.setState({
      workspace: {
        ...workspace,
        threads: threads.map((item) =>
          item.id === waiting.id ? { ...waiting, status: "waiting_approval", asks_questions: false } : item,
        ),
      },
    }),
  );
  expect(within(row("parent chat")).getByLabelText("A helper needs your input")).toBeVisible();
  // A helper's question is answered by its parent, so it shows as running work instead.
  act(() =>
    useCoding.setState({
      workspace: {
        ...workspace,
        threads: threads.map((item) =>
          item.id === waiting.id ? { ...waiting, status: "waiting_approval", asks_questions: true } : item,
        ),
      },
    }),
  );
  expect(within(row("waiting chat")).getByLabelText("Chat running")).toBeVisible();
  expect(within(row("parent chat")).queryByLabelText("A helper needs your input")).toBeNull();
  act(() => useCoding.setState({ workspace: { ...workspace, threads } }));
  const show = screen.getByRole("button", { name: "Show helpers for parent chat" });
  expect(show).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(show);
  expect(rows()).toEqual([
    "orphan chat",
    "parent chat",
    "previous chat",
    "child chat",
    "pinned chat",
    "waiting chat",
    "queued chat",
  ]);
  fireEvent.click(screen.getByRole("button", { name: "previous chat" }));
  expect(select).toHaveBeenCalledWith(previous.id);
  fireEvent.click(screen.getByRole("button", { name: "Hide helpers for parent chat" }));

  // Finishing the owning main run settles its child, which stays listed while it is the chat in view.
  act(() =>
    useCoding.setState({
      workspace: {
        ...workspace,
        threads: threads.map((item) =>
          item.id === parent.id ? { ...parent, status: "idle", active_run_id: null } : item,
        ),
      },
    }),
  );
  expect(rows()).toEqual([
    "orphan chat",
    "parent chat",
    "child chat",
    "pinned chat",
    "waiting chat",
    "queued chat",
  ]);
  expect(useCoding.getState().selected).toBe(child.id);
  expect(patch).not.toHaveBeenCalled();
  expect(select).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Show helpers for parent chat" }));
  expect(screen.getByRole("button", { name: "child chat" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("button", { name: "Hide helpers for parent chat" })).toHaveAttribute(
    "aria-expanded",
    "true",
  );

  // The project's menu lists its archived chats instead, each helper still under its parent, and back.
  const view = async (item: string) => {
    fireEvent.keyDown(screen.getByRole("button", { name: "Settings for Repository" }), { key: "ArrowDown" });
    fireEvent.click(await screen.findByRole("menuitem", { name: item }));
  };
  await view("Show archived");
  expect(rows()).toEqual(["parent chat", "done chat", "archived chat", "set-aside chat"]);
  expect(screen.getByText("Archived")).toBeVisible();
  await view("Show active");
  // As it was, its helpers still expanded.
  expect(rows()).toEqual([
    "orphan chat",
    "parent chat",
    "previous chat",
    "child chat",
    "pinned chat",
    "waiting chat",
    "queued chat",
  ]);
  // Opened from its card in the main chat, an archived helper is listed and highlighted under its parent.
  act(() => useCoding.setState({ selected: done.id }));
  expect(rows()).toEqual([
    "orphan chat",
    "parent chat",
    "previous chat",
    "child chat",
    "pinned chat",
    "waiting chat",
    "queued chat",
    "done chat",
  ]);
  expect(screen.getByRole("button", { name: "done chat" })).toHaveAttribute("aria-current", "page");
});
