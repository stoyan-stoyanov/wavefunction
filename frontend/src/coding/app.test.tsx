import { useSettings } from "@/store/settings";
import { api } from "@/lib/api";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { codingApi } from "./api";
import { makeOpenChat, makeProject, makeRun, type OpenChat as ThreadDetail } from "@/test/fixtures";
import { CodingApp } from "./app";
import { useCoding } from "./store";
import type { Connections, RunEvent } from "@/lib/types";
import * as transcripts from "./run-transcript";

const startDragging = vi.hoisted(() => vi.fn(() => Promise.resolve()));
const toggleMaximize = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => ({ startDragging, toggleMaximize }),
}));
vi.mock("@/views/connectors-view", () => ({
  ConnectorsView: () => <section aria-label="Connector management">Connector catalog</section>,
}));
vi.mock("./workbench", () => ({
  Workbench: ({ tab, onTab }: { tab?: string; onTab?: (tab: string) => void }) => (
    <aside className="coding-workbench" aria-label="Repository workbench">
      <span>Tool: {tab}</span>
      {[
        ["Files", "files"],
        ["Terminal", "terminal"],
        ["Changes", "changes"],
      ].map(([label, value]) => (
        <button key={value} role="tab" onClick={() => onTab?.(value)}>
          {label}
        </button>
      ))}
    </aside>
  ),
}));
vi.mock("@/components/model-picker", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/model-picker")>()),
  useModelOptions: () => ({ options: [], loading: false, specFor: () => null }),
}));

const thread: ThreadDetail = makeOpenChat({
  id: "thread-a",
  conversation_id: "conversation-a",
  title: "Fix the parser",
  created_at: "",
  updated_at: "",
});
const project = makeProject({
  id: "project-a",
  name: "Parser",
  folder: "/work/parser",
  created_at: "",
});
const initialCodingState = useCoding.getState();
const initialSettingsState = useSettings.getState();
beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  useSettings.setState(initialSettingsState, true);
  useCoding.setState({
    ...initialCodingState,
    workspace: {
      agent_id: "one-agent",
      threads: [thread],
      projects: [project],
      access: "sandboxed",
      default_mode: "auto",
    },
    selected: thread.id,
    details: { [thread.id]: thread },
    loading: false,
    error: null,
    sending: {},
    restoring: {},
  });
  vi.spyOn(useCoding.getState(), "initialize").mockResolvedValue(undefined);
  vi.spyOn(useSettings.getState(), "loadConnections").mockResolvedValue(undefined);
  vi.spyOn(codingApi, "worktreeCleanup").mockResolvedValue({ retention_days: 14 });
  vi.spyOn(codingApi, "touchThread").mockResolvedValue(undefined);
});

it("replaces the project sidebar with settings and restores the chat draft", async () => {
  render(<CodingApp />);
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Keep this draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(await screen.findByRole("heading", { name: "General" })).toBeVisible();
  expect(screen.queryByRole("complementary", { name: "Projects sidebar" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(screen.getByRole("complementary", { name: "Projects sidebar" })).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toHaveValue("Keep this draft");
  expect(useCoding.getState().selected).toBe(thread.id);
});

it("attaches files dropped on the chat window and refuses drops elsewhere", async () => {
  const upload = vi.spyOn(codingApi, "upload").mockResolvedValue(undefined as never);
  vi.spyOn(useCoding.getState(), "load").mockResolvedValue(undefined);
  render(<CodingApp />);
  const file = new File(["png"], "screen.png", { type: "image/png" });
  const dataTransfer = { types: ["Files"], files: [file], dropEffect: "none" };
  // The projects sidebar is not part of the chat window: no overlay, and the drop is refused.
  const sidebar = screen.getByRole("complementary", { name: "Projects sidebar" });
  fireEvent.dragEnter(sidebar, { dataTransfer });
  expect(screen.queryByText("Drop files to attach them to your message")).toBeNull();
  expect(fireEvent.drop(sidebar, { dataTransfer })).toBe(false);
  expect(upload).not.toHaveBeenCalled();
  // The toolbar is outside the conversation but still part of the chat window.
  const toolbar = screen.getByRole("banner", { name: "Workspace toolbar" });
  fireEvent.dragEnter(toolbar, { dataTransfer });
  expect(screen.getByRole("status")).toHaveTextContent("Drop files to attach them to your message");
  fireEvent.drop(toolbar, { dataTransfer });
  expect(screen.queryByText("Drop files to attach them to your message")).toBeNull();
  await waitFor(() => expect(upload).toHaveBeenCalledWith(thread.id, file));
  // Without a composer the drop is refused rather than navigating the window to the file.
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(await screen.findByRole("heading", { name: "General" })).toBeVisible();
  expect(fireEvent.drop(screen.getByRole("heading", { name: "General" }), { dataTransfer })).toBe(false);
  expect(upload).toHaveBeenCalledTimes(1);
});

it("opens web links natively and previews linked files beside a still-usable composer", async () => {
  vi.stubGlobal("open", vi.fn());
  vi.spyOn(codingApi, "filePreview").mockResolvedValue({
    path: "/repo/out/totals.csv",
    name: "totals.csv",
    kind: "table",
    size: 40,
    previewable: true,
    openable: true,
  });
  vi.spyOn(codingApi, "filePreviewText").mockResolvedValue('region,total\nNorth,"1,200"\n');
  const openFile = vi.spyOn(codingApi, "openFile").mockResolvedValue(undefined);
  const reply = "Read [the docs](https://example.com/docs?q=wave) and [totals.csv](out/totals.csv).";
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "run-files",
            status: "completed",
            input: { text: "Make a CSV" },
            output: { text: reply },
            events: [],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  fireEvent.click(await screen.findByRole("link", { name: "the docs" }));
  expect(window.open).toHaveBeenCalledWith("https://example.com/docs?q=wave", "_blank", "noopener");
  fireEvent.click(screen.getByRole("link", { name: "totals.csv" }));
  const pane = await screen.findByRole("complementary", { name: "Preview of totals.csv" });
  expect(codingApi.filePreview).toHaveBeenCalledWith(thread.id, "out/totals.csv");
  expect(await within(pane).findByRole("cell", { name: "1,200" })).toBeVisible();
  // The composer stays usable next to the preview.
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toBeEnabled();
  fireEvent.keyDown(within(pane).getByRole("button", { name: /Open in/ }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Default app" }));
  await waitFor(() => expect(openFile).toHaveBeenCalledWith(thread.id, "/repo/out/totals.csv", "app"));
  fireEvent.click(within(pane).getByRole("button", { name: "Close preview" }));
  expect(screen.queryByRole("complementary", { name: "Preview of totals.csv" })).toBeNull();
});

it("preserves a collapsed sidebar when returning from settings", async () => {
  render(<CodingApp />);
  fireEvent.click(screen.getByRole("button", { name: "Hide sidebar" }));
  fireEvent.keyDown(window, { key: ",", metaKey: true });
  expect(await screen.findByRole("heading", { name: "General" })).toBeVisible();
  expect(screen.getByRole("complementary", { name: "Settings sidebar" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(screen.queryByRole("complementary", { name: "Projects sidebar" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Show sidebar" })).toBeVisible();
});

it("drags and zooms the window around the chat title, which renames the chat on a click", () => {
  document.documentElement.classList.add("tauri");
  try {
    render(<CodingApp />);
    const toolbar = screen.getByLabelText("Workspace toolbar");
    const title = within(toolbar).getByRole("button", { name: "Rename Fix the parser" });
    const around = title.parentElement!;
    fireEvent.mouseDown(toolbar, { button: 0, detail: 1 });
    fireEvent.mouseDown(around, { button: 0, detail: 1 });
    expect(startDragging).toHaveBeenCalledTimes(2);
    // Shown through the drag: whatever the page hears while the window moves, until it has rested
    // and the pointer then moves on the page with the button up.
    expect(toolbar).toHaveAttribute("data-held");
    fireEvent.mouseMove(document.body, { buttons: 0 });
    expect(toolbar).toHaveAttribute("data-held");
    const rested = performance.now() + 1000;
    const now = vi.spyOn(performance, "now").mockReturnValue(rested);
    try {
      fireEvent.mouseMove(document.body, { buttons: 1 });
      fireEvent.mouseMove(toolbar, { buttons: 0 });
      expect(toolbar).toHaveAttribute("data-held");
      fireEvent.mouseMove(document.body, { buttons: 0 });
      expect(toolbar).not.toHaveAttribute("data-held");
    } finally {
      now.mockRestore();
    }
    // Around the title, the app zooms the window; on the toolbar itself, a drag region, Tauri does.
    fireEvent.mouseDown(around, { button: 0, detail: 2 });
    fireEvent.mouseDown(toolbar, { button: 0, detail: 2 });
    expect(toggleMaximize).toHaveBeenCalledOnce();
    // The title itself neither drags nor zooms: a click turns it into the rename field.
    fireEvent.mouseDown(title, { button: 0, detail: 1 });
    fireEvent.mouseDown(title, { button: 0, detail: 2 });
    expect(startDragging).toHaveBeenCalledTimes(2);
    expect(toggleMaximize).toHaveBeenCalledOnce();
    fireEvent.click(title);
    expect(within(toolbar).getByRole("textbox", { name: "Rename Fix the parser" })).toHaveFocus();
  } finally {
    document.documentElement.classList.remove("tauri");
  }
});

it("creates keyboard chats in the current project", () => {
  const project = makeProject({
    id: "project-a",
    name: "Parser",
    folder: "/repo",
    created_at: "",
  });
  useCoding.setState({
    workspace: {
      ...useCoding.getState().workspace!,
      projects: [project],
      threads: [{ ...thread, project_id: project.id }],
    },
  });
  const create = vi.spyOn(useCoding.getState(), "newThread").mockResolvedValue(undefined);
  render(<CodingApp />);
  fireEvent.keyDown(window, { key: "n", metaKey: true });
  expect(create).toHaveBeenCalledWith(project.id);
});

it("warms hidden tools after workspace readiness and preserves drafts through collapse and tool switching", async () => {
  const callbacks: IdleRequestCallback[] = [];
  const idle = vi.fn((callback: IdleRequestCallback) => callbacks.push(callback));
  const cancelIdle = vi.fn();
  vi.stubGlobal("requestIdleCallback", idle);
  vi.stubGlobal("cancelIdleCallback", cancelIdle);
  const workspace = useCoding.getState().workspace;
  useCoding.setState({ workspace: null, loading: true });
  const view = render(<CodingApp />);
  try {
    await act(() => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
    expect(idle).not.toHaveBeenCalled();
    act(() => useCoding.setState({ workspace, loading: false }));
    const toolbar = screen.getByRole("banner", { name: "Workspace toolbar" });
    const input = screen.getByRole("textbox", { name: "Message Wavefunction" });
    fireEvent.change(input, { target: { value: "Preserve this draft" } });
    await waitFor(() => expect(idle).toHaveBeenCalledOnce());
    await act(async () => callbacks[0]({ didTimeout: false, timeRemaining: () => 10 }));
    await waitFor(() => expect(idle).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("complementary", { name: "Repository workbench" })).toBeNull();
    expect(input).toHaveValue("Preserve this draft");
    fireEvent.click(screen.getByRole("button", { name: "Show workbench" }));
    // A warmed screen must render immediately instead of entering Suspense again.
    expect(screen.getByRole("complementary", { name: "Repository workbench" })).toBeVisible();
    for (const [label, tab] of [
      ["Files", "files"],
      ["Terminal", "terminal"],
      ["Changes", "changes"],
    ]) {
      fireEvent.click(screen.getByRole("tab", { name: label }));
      expect(screen.getByText(`Tool: ${tab}`)).toBeVisible();
    }
    fireEvent.click(screen.getByRole("button", { name: "Hide sidebar" }));
    expect(screen.queryByRole("complementary", { name: "Projects sidebar" })).toBeNull();
    expect(screen.queryByRole("separator", { name: "Resize sidebar" })).toBeNull();
    expect(toolbar).toContainElement(screen.getByRole("button", { name: "Show sidebar" }));
    fireEvent.click(screen.getByRole("button", { name: "Show sidebar" }));
    expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toHaveValue("Preserve this draft");
  } finally {
    view.unmount();
    vi.unstubAllGlobals();
  }
  expect(cancelIdle).toHaveBeenLastCalledWith(2);
});

it("walks a first launch through a provider, its models and a project into the project's first chat", async () => {
  const connections: Connections = {
    model: { provider: "demo", model: "demo-reliable", base_url: null, temperature: 0.1, max_tokens: null },
    enabled_models: ["demo::demo-reliable"],
    secrets: {
      openai_api_key: false,
      openrouter_api_key: false,
      vercel_api_key: false,
      anthropic_api_key: false,
      gemini_api_key: false,
      tavily_api_key: false,
    },
    codex: { connected: false, account_id: "", email: "", expires_at: 0, signing_in: false, error: "" },
    codex_models: [
      { id: "gpt-a", name: "GPT A", description: "" },
      { id: "gpt-b", name: "GPT B", description: "" },
    ],
    coding_models: [],
    ollama: null,
    require_zdr: false,
  };
  const saveEnabledModels = vi.fn(async (models: string[]) =>
    useSettings.setState({ connections: { ...useSettings.getState().connections!, enabled_models: models } }),
  );
  const setDefaultModel = vi.fn().mockResolvedValue(undefined);
  useSettings.setState({ connections, saveEnabledModels, setDefaultModel, error: null });
  const created = makeProject({
    id: "first-project",
    name: "parser",
    folder: "/work/parser",
    created_at: "",
  });
  const save = vi.spyOn(codingApi, "project").mockResolvedValue(created);
  const newThread = vi.spyOn(useCoding.getState(), "newThread").mockImplementation(async () => {
    useCoding.setState({
      workspace: {
        ...useCoding.getState().workspace!,
        projects: [created],
        threads: [{ ...thread, project_id: created.id }],
      },
    });
  });
  useCoding.setState({
    workspace: { ...useCoding.getState().workspace!, projects: [], threads: [] },
    selected: null,
    details: {},
  });
  render(<CodingApp />);
  // The current step is the numbered title above the form.
  const step = () => screen.getByRole("heading", { level: 2 }).textContent;
  expect(step()).toBe("1Connect a provider");
  fireEvent.click(screen.getByRole("button", { name: "ChatGPT" }));
  expect(screen.getByRole("button", { name: "Sign in with ChatGPT" })).toBeInTheDocument();
  // The sign-in lands in the browser; the step moves on when the connection shows it.
  act(() =>
    useSettings.setState({
      connections: { ...connections, codex: { ...connections.codex, connected: true } },
    }),
  );
  expect(step()).toBe("2Choose models");
  expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Enable GPT B"));
  fireEvent.click(screen.getByLabelText("Enable GPT A"));
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await waitFor(() => expect(step()).toBe("3Create a project"));
  expect(saveEnabledModels).toHaveBeenCalledWith(["codex::gpt-b", "codex::gpt-a"]);
  expect(setDefaultModel).toHaveBeenCalledWith(
    expect.objectContaining({ provider: "codex", model: "gpt-b" }),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Repository folder" }), {
    target: { value: "/work/parser/" },
  });
  expect(screen.getByRole("textbox", { name: "Project name" })).toHaveValue("parser");
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));
  await waitFor(() => expect(newThread).toHaveBeenCalledWith(created.id));
  expect(save).toHaveBeenCalledWith(expect.objectContaining({ name: "parser", folder: "/work/parser/" }));
  expect(screen.queryByRole("heading", { name: /Create a project/ })).toBeNull();
  expect(screen.getByRole("banner", { name: "Workspace toolbar" })).toBeInTheDocument();
  // Signing out of the only provider brings setup back, though its models stay enabled.
  act(() =>
    useSettings.setState({
      connections: { ...useSettings.getState().connections!, codex: connections.codex },
    }),
  );
  expect(step()).toBe("1Connect a provider");
});

it("creates and updates project appearance without creating a chat", async () => {
  const create = vi.spyOn(useCoding.getState(), "newThread").mockResolvedValue(undefined);
  const saved = makeProject({
    id: "new-project",
    name: "Heart app",
    folder: "/work/heart-app",
    created_at: "",
    icon: "heart",
    icon_color: "#aa80e5",
  });
  const save = vi.spyOn(codingApi, "project").mockResolvedValue(saved);
  vi.spyOn(useCoding.getState(), "refresh").mockResolvedValue(undefined);
  render(<CodingApp />);
  fireEvent.click(screen.getByRole("button", { name: "New project" }));
  const dialog = within(screen.getByRole("dialog"));
  fireEvent.change(dialog.getByRole("textbox", { name: "Project name" }), {
    target: { value: saved.name },
  });
  expect(dialog.getByRole("textbox", { name: "Repository folder" })).toBeRequired();
  expect(dialog.getByRole("button", { name: "Create Project" })).toBeDisabled();
  fireEvent.change(dialog.getByRole("textbox", { name: "Repository folder" }), {
    target: { value: ` ${saved.folder} ` },
  });
  const icons = within(dialog.getByRole("group", { name: "Project icon" }));
  expect(icons.queryByRole("button", { name: "Heart" })).toBeNull();
  fireEvent.click(icons.getByRole("button", { name: "Show more" }));
  fireEvent.click(icons.getByRole("button", { name: "Heart" }));
  fireEvent.click(icons.getByRole("button", { name: "Show less" }));
  fireEvent.click(dialog.getByRole("button", { name: "Purple" }));
  expect(dialog.getByRole("button", { name: "Heart" })).toHaveAttribute("aria-pressed", "true");
  expect(dialog.getByRole("button", { name: "Purple" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(dialog.getByRole("button", { name: "Create Project" }));
  await vi.waitFor(() =>
    expect(save).toHaveBeenCalledWith(
      {
        name: saved.name,
        folder: saved.folder,
        icon: "heart",
        icon_color: "#aa80e5",
      },
      undefined,
    ),
  );
  await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

  const legacy = {
    ...saved,
    id: "legacy-project",
    name: "Folderless project",
    folder: null,
    icon: "folder" as const,
    icon_color: null,
  };
  act(() =>
    useCoding.setState({
      workspace: { ...useCoding.getState().workspace!, projects: [saved, legacy] },
    }),
  );
  fireEvent.keyDown(screen.getByRole("button", { name: `Settings for ${saved.name}` }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Settings" }));
  const existing = within(screen.getByRole("dialog"));
  expect(existing.getByRole("textbox", { name: "Repository folder" })).toBeRequired();
  fireEvent.change(existing.getByRole("textbox", { name: "Repository folder" }), { target: { value: " " } });
  expect(existing.getByRole("button", { name: "Save changes" })).toBeDisabled();
  fireEvent.click(existing.getByRole("button", { name: "Cancel" }));

  fireEvent.keyDown(screen.getByRole("button", { name: `Settings for ${legacy.name}` }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("menuitem", { name: "Settings" }));
  const editing = within(screen.getByRole("dialog"));
  expect(editing.getByRole("textbox", { name: "Repository folder" })).not.toBeRequired();
  fireEvent.change(editing.getByRole("textbox", { name: "Project name" }), {
    target: { value: "Renamed project" },
  });
  fireEvent.change(editing.getByRole("textbox", { name: "Repository folder" }), { target: { value: " " } });
  fireEvent.click(editing.getByRole("button", { name: "Rocket" }));
  fireEvent.click(editing.getByRole("button", { name: "Purple" }));
  save.mockResolvedValueOnce({ ...legacy, name: "Renamed project", icon: "rocket", icon_color: "#aa80e5" });
  fireEvent.click(editing.getByRole("button", { name: "Save changes" }));
  await vi.waitFor(() =>
    expect(save).toHaveBeenLastCalledWith(
      {
        name: "Renamed project",
        folder: null,
        icon: "rocket",
        icon_color: "#aa80e5",
      },
      legacy.id,
    ),
  );
  await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(save).toHaveBeenCalledTimes(2);
  expect(create).not.toHaveBeenCalled();
});

it("hides the entire workbench and toolbar action when disabled in Settings, then restores them", async () => {
  const view = render(<CodingApp />);
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Keep my draft while hiding the workbench" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Show workbench" }));
  expect(await screen.findByRole("complementary", { name: "Repository workbench" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  const preference = await screen.findByRole("switch", {
    name: "Show workbench",
  });
  expect(preference).toHaveAttribute("aria-checked", "true");
  fireEvent.click(preference);
  expect(localStorage.getItem("harness.workbench.enabled")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(screen.queryByRole("complementary", { name: "Repository workbench" })).toBeNull();
  expect(screen.queryByRole("button", { name: /Show workbench|Hide workbench/ })).toBeNull();
  expect(screen.queryByRole("separator", { name: "Resize workbench" })).toBeNull();
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toHaveValue(
    "Keep my draft while hiding the workbench",
  );
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(await screen.findByRole("switch", { name: "Show workbench" }));
  fireEvent.click(screen.getByRole("button", { name: "Back to chat" }));
  expect(screen.getByRole("complementary", { name: "Repository workbench" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Hide workbench" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(await screen.findByRole("switch", { name: "Show workbench" }));
  view.unmount();
  render(<CodingApp />);
  expect(screen.queryByRole("button", { name: /Show workbench|Hide workbench/ })).toBeNull();
  expect(screen.queryByRole("complementary", { name: "Repository workbench" })).toBeNull();
});

it("opens connectors from project navigation and restores the owning chat draft", async () => {
  useCoding.setState({
    workspace: {
      agent_id: "one-agent",
      threads: [{ ...thread, project_id: "project" }],
      projects: [makeProject({ id: "project", name: "Project", created_at: "" })],
      access: "sandboxed",
      default_mode: "auto",
    },
  });
  vi.spyOn(codingApi, "thread").mockResolvedValue(thread);
  render(<CodingApp />);
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Keep this connector draft" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connectors" }));
  expect(await screen.findByRole("region", { name: "Connector management" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Fix the parser" }));
  expect(await screen.findByRole("textbox", { name: "Message Wavefunction" })).toHaveValue(
    "Keep this connector draft",
  );
});

it("cancels only the selected queued message", () => {
  const stop = vi.spyOn(useCoding.getState(), "stop").mockResolvedValue(undefined);
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          { id: "queued-one", status: "queued", input: { text: "First follow-up" }, events: [] },
          { id: "queued-two", status: "queued", input: { text: "Second follow-up" }, events: [] },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  fireEvent.click(screen.getAllByRole("button", { name: "Cancel queued message" })[1]);
  expect(stop).toHaveBeenCalledExactlyOnceWith("queued-two");
});

it("binds a folder approval to the request currently shown", async () => {
  const approve = vi.spyOn(api, "decideApproval").mockResolvedValue({} as never);
  vi.spyOn(useCoding.getState(), "load").mockResolvedValue(undefined);
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "paused-run",
            status: "waiting_approval",
            input: { text: "Read external requirements" },
            events: [
              {
                id: "approval",
                sequence: 7,
                event_type: "run_waiting_for_approval",
                payload: {
                  requests: [
                    {
                      kind: "folder_access",
                      description: "Allow read-only access to /synthetic/requirements?",
                    },
                  ],
                },
              },
            ],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  expect(screen.getByText("Allow read-only access to /synthetic/requirements?")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Allow folder" }));
  expect(approve).toHaveBeenCalledExactlyOnceWith("paused-run", "approve", "", 7);
});

it("shows a command the agent wants to run outside the sandbox and can allow every one for the rest of the chat", async () => {
  const decide = vi.spyOn(api, "decideApproval").mockResolvedValue({} as never);
  const patch = vi.spyOn(codingApi, "patch").mockResolvedValue({} as never);
  vi.spyOn(useCoding.getState(), "load").mockResolvedValue(undefined);
  vi.spyOn(useCoding.getState(), "refresh").mockResolvedValue(undefined);
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "outside",
            status: "waiting_approval",
            input: { text: "Start the local database" },
            events: [
              {
                id: "asked",
                sequence: 4,
                event_type: "run_waiting_for_approval",
                payload: {
                  requests: [
                    {
                      kind: "outside_sandbox",
                      command: "docker compose up -d",
                      reason: "Start Supabase's containers",
                      cwd: "/synthetic/repo",
                    },
                  ],
                },
              },
            ],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  const card = within(screen.getByRole("group", { name: "Run outside the sandbox" }));
  expect(card.getByText("docker compose up -d")).toBeInTheDocument();
  expect(card.getByText("Start Supabase's containers")).toBeInTheDocument();
  fireEvent.click(card.getByRole("button", { name: "Allow for this chat" }));
  await waitFor(() => expect(decide).toHaveBeenCalledExactlyOnceWith("outside", "approve", "", 4));
  // Full access first, so the command the card showed already runs under it.
  expect(patch).toHaveBeenCalledExactlyOnceWith(thread.id, { mode: "full" });
  expect(patch.mock.invocationCallOrder[0]).toBeLessThan(decide.mock.invocationCallOrder[0]);

  // A helper's card shows in its main chat too. Deciding it there resumes the helper, and full
  // access goes to the main chat, which its helpers follow.
  const helper = { id: "helper-a", title: "Database helper" };
  const waiting = {
    id: "waiting",
    sequence: 7,
    event_type: "helper_waiting",
    payload: {
      agent_id: helper.id,
      title: helper.title,
      run_id: "helper-run",
      status: "waiting_approval",
      pause: {
        event_type: "run_waiting_for_approval",
        sequence: 3,
        payload: {
          requests: [
            {
              kind: "outside_sandbox",
              command: "docker compose up -d",
              reason: "Start the test database",
              cwd: "/synthetic/repo",
            },
          ],
        },
      },
    },
  };
  const main = {
    id: "main",
    status: "running",
    input: { text: "Fix the login and the copy" },
    events: [waiting],
  };
  // Its card can arrive before the sidebar's next refresh lists the new helper.
  act(() =>
    useCoding.setState({
      details: { [thread.id]: { ...thread, runs: [main] as unknown as ThreadDetail["runs"] } },
    }),
  );
  const mirrored = within(screen.getByRole("group", { name: "Database helper needs you" }));
  expect(mirrored.getByText("Start the test database")).toBeInTheDocument();
  fireEvent.click(mirrored.getByRole("button", { name: "Allow for this chat" }));
  await waitFor(() => expect(decide).toHaveBeenLastCalledWith("helper-run", "approve", "", 3));
  expect(patch).toHaveBeenLastCalledWith(thread.id, { mode: "full" });
  // Once the helper goes on, decided here or in its own chat, the copy closes.
  const resolved = {
    id: "resolved",
    sequence: 8,
    event_type: "helper_resolved",
    payload: { agent_id: helper.id, run_id: "helper-run" },
  };
  act(() =>
    useCoding.setState({
      details: {
        [thread.id]: {
          ...thread,
          runs: [{ ...main, events: [waiting, resolved] }] as unknown as ThreadDetail["runs"],
        },
      },
    }),
  );
  expect(screen.queryByRole("group", { name: "Database helper needs you" })).toBeNull();
});

it("warns that commands can't be undone, even with full access, and shows every one the approval covers", async () => {
  const decide = vi.spyOn(api, "decideApproval").mockResolvedValue({} as never);
  vi.spyOn(useCoding.getState(), "load").mockResolvedValue(undefined);
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        mode: "full",
        runs: [
          {
            id: "reset",
            status: "waiting_approval",
            input: { text: "Apply the migrations" },
            events: [
              {
                id: "asked",
                sequence: 6,
                event_type: "run_waiting_for_approval",
                payload: {
                  requests: [
                    {
                      kind: "irreversible",
                      command: "supabase db reset",
                      danger: "Deletes data in a database.",
                      outside_sandbox: true,
                      reason: "Apply the new migrations",
                      cwd: "/synthetic/repo",
                    },
                    {
                      // Asked in the same pause by a parallel call, so approved together.
                      kind: "irreversible",
                      command: "git reset --hard",
                      danger: "Discards uncommitted changes.",
                      outside_sandbox: false,
                      reason: "",
                      cwd: "/synthetic/repo",
                    },
                    {
                      kind: "outside_sandbox",
                      command: "docker compose up -d",
                      reason: "Start the local database",
                      cwd: "/synthetic/repo",
                    },
                  ],
                },
              },
            ],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  const card = within(screen.getByRole("group", { name: "Can't be undone" }));
  expect(
    card.getByText("Deletes data in a database. It runs outside the sandbox, on this Mac."),
  ).toBeInTheDocument();
  expect(card.getByText("supabase db reset")).toBeInTheDocument();
  expect(card.getByText("Discards uncommitted changes.")).toBeInTheDocument();
  expect(card.getByText("git reset --hard")).toBeInTheDocument();
  expect(card.getByText("docker compose up -d")).toBeInTheDocument();
  expect(card.getByText("It runs outside the sandbox, on this Mac.")).toBeInTheDocument();
  expect(card.queryByRole("button", { name: "Allow for this chat" })).toBeNull();
  fireEvent.click(card.getByRole("button", { name: "Run it" }));
  await waitFor(() => expect(decide).toHaveBeenCalledExactlyOnceWith("reset", "approve", "", 6));
});

it("answers with a chosen option or written text without filling one from the other, keeps older drafts, and Cancel closes the questions", async () => {
  const answer = vi.spyOn(codingApi, "answerQuestions").mockResolvedValue({} as never);
  const dismiss = vi.spyOn(codingApi, "dismissQuestions").mockResolvedValue({} as never);
  vi.spyOn(useCoding.getState(), "load").mockResolvedValue(undefined);
  vi.spyOn(useCoding.getState(), "refresh").mockResolvedValue(undefined);
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "asking",
            status: "waiting_approval",
            input: { text: "Set up the app" },
            events: [
              {
                id: "asked",
                sequence: 3,
                event_type: "run_questions",
                payload: {
                  groups: [
                    {
                      id: "g",
                      questions: [
                        {
                          id: "target",
                          title: "Which project?",
                          options: [
                            { label: "Staging", description: "Synthetic data" },
                            { label: "Production", description: "" },
                          ],
                        },
                        { id: "notes", title: "Anything else?", options: [] },
                      ],
                    },
                  ],
                },
              },
            ],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  // A draft an older build saved, with one answer per question.
  localStorage.setItem(
    `harness.answers.${thread.id}.asking.3`,
    JSON.stringify({ "g:target": "Production", "g:notes": "Keep the old keys" }),
  );
  render(<CodingApp />);
  const card = within(screen.getByRole("group", { name: "Questions from the agent" }));
  const submit = card.getByRole("button", { name: "Continue with answers" });
  const notes = card.getByRole("textbox", { name: "Answer: Anything else?" });
  expect(card.getByRole("button", { name: /Production/ })).toHaveAttribute("aria-pressed", "true");
  expect(notes).toHaveValue("Keep the old keys");
  fireEvent.change(notes, { target: { value: "" } });
  fireEvent.click(card.getByRole("button", { name: /Staging/ }));
  expect(card.getByRole("button", { name: /Staging/ })).toHaveAttribute("aria-pressed", "true");
  expect(card.getByRole("textbox", { name: "Answer: Which project?" })).toHaveValue("");
  expect(submit).toBeDisabled();
  fireEvent.change(notes, { target: { value: "Keep the old keys" } });
  fireEvent.click(submit);
  await waitFor(() =>
    expect(answer).toHaveBeenCalledExactlyOnceWith(thread.id, "asking", 3, {
      "g:target": "Staging",
      "g:notes": "Keep the old keys",
    }),
  );
  // Writing an answer replaces the chosen option.
  fireEvent.change(card.getByRole("textbox", { name: "Answer: Which project?" }), {
    target: { value: "A new project" },
  });
  expect(card.getByRole("button", { name: /Staging/ })).toHaveAttribute("aria-pressed", "false");
  fireEvent.click(card.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(dismiss).toHaveBeenCalledExactlyOnceWith(thread.id, "asking", 3));
});

it("shows each answer where it was given, including a reply typed in the composer", () => {
  const asked = (sequence: number, title: string): RunEvent =>
    ({
      id: `asked-${sequence}`,
      sequence,
      event_type: "run_questions",
      payload: { groups: [{ id: `g${sequence}`, questions: [{ id: "q", title, options: [] }] }] },
    }) as unknown as RunEvent;
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "answered",
            status: "completed",
            input: { text: "Set up the app" },
            events: [
              {
                id: "first",
                event_type: "assistant_message",
                payload: { text: "I need a project.", message_id: "first" },
              },
              asked(3, "Which project?"),
              {
                id: "reply",
                event_type: "steering_requested",
                payload: { text: "Use staging", reply_to: 3 },
              },
              { id: "replied", event_type: "questions_answered", payload: { sequence: 3, replied: true } },
              {
                id: "second",
                event_type: "assistant_message",
                payload: { text: "Using staging. Which region?", message_id: "second" },
              },
              asked(7, "Which region?"),
              {
                id: "chosen",
                event_type: "questions_answered",
                payload: { sequence: 7, answers: { "g7:q": "Europe" } },
              },
              asked(9, "Rotate the keys?"),
              { id: "closed", event_type: "questions_answered", payload: { sequence: 9, dismissed: true } },
              {
                id: "last",
                event_type: "assistant_message",
                payload: { text: "Done without rotating.", message_id: "last" },
              },
            ],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  const order = screen
    .getAllByText(
      /^(I need a project\.|Use staging|Using staging\. Which region\?|Europe|Questions closed without an answer|Done without rotating\.)$/,
    )
    .map((element) => element.textContent);
  expect(order).toEqual([
    "I need a project.",
    "Use staging",
    "Using staging. Which region?",
    "Europe",
    "Questions closed without an answer",
    "Done without rotating.",
  ]);
  const [reply, chosen] = screen.getAllByRole("group", { name: "Your answer" }).map((group) => within(group));
  expect(reply.getByText("Answer · Which project?")).toBeVisible();
  expect(chosen.getByText("Answer · Which region?")).toBeVisible();
});

it("shows a routine's report as the routine's, with its answer rendered and a way to its chat", () => {
  const select = vi.fn().mockResolvedValue(undefined);
  const table = "| # | Story | Points |\n|---|---|---:|\n| 1 | [Askdown](https://askdown.dev) | 1 |";
  useCoding.setState({
    select,
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "reported",
            status: "completed",
            output: { text: "Noted." },
            events: [],
            input: {
              text: `Routine “HN newest” finished:\n\n${table}`,
              display_text: table,
              routine_report: { thread_id: "routine-chat", title: "HN newest", status: "completed" },
            },
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  const report = within(screen.getByRole("group", { name: "Report from routine HN newest" }));
  expect(report.getByText(/· finished/)).toBeVisible();
  expect(report.getByRole("cell", { name: "Askdown" })).toBeVisible();
  expect(screen.queryByRole("group", { name: "Your message" })).toBeNull();
  fireEvent.click(report.getByRole("button", { name: "HN newest" }));
  expect(select).toHaveBeenCalledWith("routine-chat");
});

it("counts distinct tool calls including failures instead of raw activity events", async () => {
  const phases: Array<[string, RunEvent["payload"]]> = [
    ["run_started", {}],
    ["tool_started", { id: "read", tool: "read_file", args: { path: "app.py" } }],
    ["execution_tool_started", { call_id: "read", tool: "read_file" }],
    ["execution_tool_finished", { call_id: "read" }],
    ["tool_completed", { id: "read", tool: "read_file", result: "file contents" }],
    ["tool_started", { id: "command", tool: "run_command", args: { command: "false" } }],
    ["execution_tool_started", { call_id: "command", tool: "run_command" }],
    ["tool_output", { id: "command", stdout: "", stderr: "command error" }],
    ["execution_tool_finished", { call_id: "command" }],
    ["tool_failed", { id: "command", tool: "run_command", result: "command failed" }],
    ["tool_started", { id: "blocked", tool: "write_file", args: { path: "outside.py" } }],
    ["tool_failed", { id: "blocked", tool: "write_file", result: "folder access denied" }],
    ["run_completed", {}],
  ];
  const events = phases.map(([event_type, payload], sequence): RunEvent => ({
    node_id: null,
    id: `event-${sequence}`,
    run_id: "activity-run",
    sequence,
    event_type,
    payload,
    created_at: "2026-10-03T10:00:00Z",
  }));
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "activity-run",
            status: "completed",
            input: { text: "Read and test" },
            events,
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  fireEvent.click(screen.getByRole("button", { name: "View activity 3 tools" }));
  expect(screen.getAllByRole("group", { name: /tool call$/ })).toHaveLength(3);
  expect(screen.getAllByText("Failed")).toHaveLength(2);
  fireEvent.click(screen.getByText("Raw trace · 13 events"));
  const raw = await screen.findByText(/"event_type": "execution_tool_started"/);
  expect(JSON.parse(raw.textContent!)).toEqual(events);
});

it("separates recorded usage, cached and reasoning subsets, and the last agent request", () => {
  const phases: Array<[string, RunEvent["payload"]]> = [
    [
      "model_call_finished",
      {
        call_id: "agent",
        usage: [
          {
            input_tokens: 2000,
            output_tokens: 400,
            input_token_details: { cache_read: 1000 },
            output_token_details: { reasoning: 300 },
          },
        ],
      },
    ],
    [
      "summarization_finished",
      { usage: { input_tokens: 3000, output_tokens: 100, input_token_details: { cache_read: 2000 } } },
    ],
    ["model_call_started", { call_id: "retry" }],
    ["model_call_failed", { call_id: "retry" }],
  ];
  const events = phases.map(([event_type, payload], sequence): RunEvent => ({
    node_id: null,
    id: `usage-${sequence}`,
    run_id: "usage",
    sequence,
    event_type,
    payload,
    created_at: "",
  }));
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          makeRun({
            id: "usage",
            status: "failed",
            input: { text: "Inspect the implementation" },
            output: null,
            error: "Provider unavailable",
            started_at: "",
            events,
            usage: {
              input_tokens: 5000,
              output_tokens: 500,
              total_tokens: 5500,
              input_token_details: { cache_read: 3000 },
              output_token_details: { reasoning: 300 },
              cost: 0.01234,
            },
            // Its helpers ran in chats of their own; what they spent is part of this task's total.
            helper_usage: { input_tokens: 1800, output_tokens: 200, total_tokens: 2000, cost: 0.005 },
          }),
        ],
      },
    },
  });
  render(<CodingApp />);
  const trigger = screen.getByRole("button", { name: "Recorded run usage" });
  expect(trigger).toHaveTextContent("7,500 tokens");
  fireEvent.click(trigger);
  const details = within(screen.getByRole("dialog", { name: "Recorded run usage" }));
  expect(details.getByText(/Helpers spent 2,000 tokens, \$0\.005/)).toBeInTheDocument();
  const value = (label: string) => details.getByText(label, { selector: "dt" }).nextElementSibling;
  expect(value("Input")).toHaveTextContent("5,000");
  expect(value("Cached input (reported)")).toHaveTextContent("3,000");
  expect(value("Uncached input")).toHaveTextContent("2,000");
  expect(value("Output")).toHaveTextContent("500");
  expect(value("Reasoning output (reported)")).toHaveTextContent("300");
  expect(value("Reported cost (USD)")).toHaveTextContent("$0.01234");
  expect(value("Last agent request · input")).toHaveTextContent("2,000");
  // A helper's price shows even when its provider reported no token counts.
  act(() =>
    useCoding.setState({
      details: {
        [thread.id]: {
          ...useCoding.getState().details[thread.id],
          runs: useCoding.getState().details[thread.id].runs.map((run) => ({
            ...run,
            helper_usage: { input_tokens: 0, output_tokens: 0, total_tokens: 0, cost: 0.003 },
          })),
        },
      },
    }),
  );
  expect(trigger).toHaveTextContent("5,500 tokens");
  expect(details.getByText(/Helpers spent \$0\.003\./)).toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("keeps missing usage unknown through incomplete reports and saved history", () => {
  let run: ThreadDetail["runs"][number] = makeRun({
    id: "partial-usage",
    status: "completed",
    input: { text: "Inspect the implementation" },
    output: { text: "Inspection complete" },
    error: null,
    started_at: "",
    usage: {},
    events: [],
  });
  const publish = () => useCoding.setState({ details: { [thread.id]: { ...thread, runs: [run] } } });
  publish();
  render(<CodingApp />);
  expect(screen.queryByRole("button", { name: "Recorded run usage" })).not.toBeInTheDocument();
  // Its helpers' tokens alone are not this task's total: with its own unknown, the total stays unknown.
  run = { ...run, helper_usage: { input_tokens: 1800, output_tokens: 200, total_tokens: 2000 } };
  act(publish);
  expect(screen.getByRole("button", { name: "Recorded run usage" })).toHaveTextContent(/^Usage$/);
  run = { ...run, helper_usage: undefined };
  const finished: RunEvent = {
    node_id: null,
    id: "finished",
    run_id: run.id,
    sequence: 1,
    event_type: "model_call_finished",
    payload: { usage: [{ input_tokens: 2000, output_tokens: 100 }] },
    created_at: "",
  };
  run = {
    ...run,
    usage: {
      input_tokens: 5000,
      output_tokens: 200,
      total_tokens: 5200,
      input_token_details: { cache_read: 3000 },
    },
    events: [
      finished,
      {
        ...finished,
        id: "summary",
        sequence: 2,
        event_type: "summarization_finished",
        payload: {
          usage: { input_tokens: 3000, output_tokens: 100, input_token_details: { cache_read: 3000 } },
        },
      },
    ],
  };
  act(publish);
  fireEvent.click(screen.getByRole("button", { name: "Recorded run usage" }));
  const value = (label: string) =>
    within(screen.getByRole("dialog", { name: "Recorded run usage" })).getByText(label, { selector: "dt" })
      .nextElementSibling;
  expect(value("Cached input (reported)")).toHaveTextContent("3,000");
  expect(value("Uncached input")).toHaveTextContent("Unknown");
  expect(value("Reported cost (USD)")).toHaveTextContent("Unknown");
  expect(value("Reasoning output (reported)")).toHaveTextContent("Unknown");

  // An explicit zero is known. Missing event coverage (such as a nested tool request) is not.
  run = {
    ...run,
    usage: { ...run.usage, input_tokens: 6000, total_tokens: 6200 },
    events: [
      {
        ...finished,
        payload: {
          usage: [{ input_tokens: 2000, output_tokens: 100, input_token_details: { cache_read: 0 } }],
        },
      },
      run.events[1],
    ],
  };
  act(publish);
  expect(value("Uncached input")).toHaveTextContent("Unknown");
  run = { ...run, usage: { ...run.usage, input_tokens: 5000, total_tokens: 5200 } };
  act(publish);
  expect(value("Uncached input")).toHaveTextContent("2,000");

  // A newer completed request without usage must not reuse the preceding request's input.
  run = {
    ...run,
    events: [...run.events, { ...finished, id: "unreported", sequence: 3, payload: { usage: [null] } }],
  };
  act(publish);
  expect(value("Last agent request · input")).toHaveTextContent("Unknown");
  expect(value("Uncached input")).toHaveTextContent("Unknown");
  // Legacy totals remain inspectable even when no detailed trace was saved.
  run = { ...run, events: [] };
  act(publish);
  expect(value("Input")).toHaveTextContent("5,000");
  expect(value("Last agent request · input")).toHaveTextContent("Unknown");
  expect(value("Uncached input")).toHaveTextContent("Unknown");
  // A lost final usage frame is unknown, rather than a zero-cost request. Recovered
  // provider accounting restores coverage without counting an attempt twice.
  const interrupted: RunEvent = {
    ...finished,
    id: "interrupted",
    sequence: 4,
    event_type: "model_call_failed",
    payload: { usage: [null], usage_status: "unknown" },
  };
  run = { ...run, events: [interrupted] };
  act(publish);
  expect(
    screen.getByText("Some interrupted requests have unknown usage. These totals are incomplete."),
  ).toBeVisible();
  expect(value("Uncached input")).toHaveTextContent("Unknown");
  run = {
    ...run,
    usage: {
      input_tokens: 1000,
      output_tokens: 200,
      total_tokens: 1200,
      cost: 0.002,
      input_token_details: { cache_read: 0 },
    },
    events: [
      {
        ...interrupted,
        payload: {
          usage_status: "recovered",
          usage: [
            { input_tokens: 1000, output_tokens: 200, input_token_details: { cache_read: 0 }, cost: 0.002 },
          ],
        },
      },
    ],
  };
  act(publish);
  expect(
    screen.queryByText("Some interrupted requests have unknown usage. These totals are incomplete."),
  ).toBeNull();
  expect(value("Uncached input")).toHaveTextContent("1,000");
  expect(value("Reported cost (USD)")).toHaveTextContent("$0.002");
});

it("forks and rewinds the chosen message boundary after checkout cleanup and confirmation", async () => {
  const owner = { ...thread, worktree_available: false, worktree_status: "deleted" as const };
  const fork = vi.spyOn(useCoding.getState(), "fork").mockResolvedValue(undefined);
  const rewind = vi.spyOn(useCoding.getState(), "rewind").mockResolvedValue(undefined);
  useCoding.setState({
    workspace: { ...useCoding.getState().workspace!, threads: [owner] },
    details: {
      [thread.id]: {
        ...owner,
        runs: [
          {
            id: "finished",
            status: "completed",
            input: { text: "Fix the parser" },
            output: { text: "Fixed it" },
            events: [],
            checkpoints: [
              { id: "before", run_id: "finished", kind: "before_user", available: true },
              { id: "after", run_id: "finished", kind: "after_assistant", available: true },
            ],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  const user = within(screen.getByRole("group", { name: "Your message" }));
  const assistant = within(screen.getByRole("group", { name: "Assistant response" }));
  fireEvent.click(user.getByRole("button", { name: "Fork from this message" }));
  expect(screen.getByRole("checkbox", { name: "New worktree" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "New worktree" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Fork chat" }));
  await waitFor(() =>
    expect(fork).toHaveBeenCalledWith(thread.id, { checkpoint_id: "before", worktree: true }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  // The newest response is where the chat already is, so only earlier boundaries rewind.
  expect(assistant.getByRole("button", { name: "Fork from this message" })).toBeInTheDocument();
  expect(assistant.queryByRole("button", { name: "Rewind to this message" })).toBeNull();
  fireEvent.click(user.getByRole("button", { name: "Rewind to this message" }));
  expect(screen.getByRole("alertdialog", { name: "Rewind conversation?" })).toHaveTextContent(
    "before this message",
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(rewind).not.toHaveBeenCalled();
  fireEvent.click(user.getByRole("button", { name: "Rewind to this message" }));
  fireEvent.click(screen.getByRole("button", { name: "Rewind" }));
  expect(rewind).toHaveBeenCalledWith(thread.id, "before");
});

it("moves a sent message into the chat at once, locks the box until the agent has it, and gives back a refused one", async () => {
  let accept!: () => void;
  const send = vi.spyOn(codingApi, "send").mockReturnValueOnce(
    new Promise((resolve) => {
      accept = () => resolve({ id: "sent" } as never);
    }),
  );
  const started = {
    ...thread,
    runs: [
      {
        id: "sent",
        status: "completed",
        input: { text: "Fix the tokenizer" },
        output: { text: "Fixed." },
        events: [],
        started_at: "",
      },
    ] as unknown as ThreadDetail["runs"],
  };
  vi.spyOn(codingApi, "thread").mockResolvedValue(started);
  vi.spyOn(codingApi, "workspace").mockResolvedValue(useCoding.getState().workspace!);
  const file = { id: "spec", name: "spec.pdf", media_type: "application/pdf", size_bytes: 2048 };
  useCoding.setState({ details: { [thread.id]: { ...thread, attachments: [file] } } });
  render(<CodingApp />);
  const box = screen.getByRole("textbox", { name: "Message Wavefunction" });
  expect(screen.getByRole("button", { name: "Remove spec.pdf" })).toBeVisible();
  fireEvent.change(box, { target: { value: "Fix the tokenizer" } });
  box.focus();
  fireEvent.keyDown(box, { key: "Enter" });
  expect(send).toHaveBeenCalledWith(
    thread.id,
    "Fix the tokenizer",
    { files: [], skills: [], command: null },
    undefined,
  );
  const sent = within(screen.getByRole("group", { name: "Your message" }));
  expect(sent.getByText("Fix the tokenizer")).toBeVisible();
  // Its files go with it: the draft no longer offers to remove what is being sent.
  expect(sent.getByText("spec.pdf")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Remove spec.pdf" })).toBeNull();
  expect(box).toHaveValue("");
  expect(box).toBeDisabled();
  box.blur(); // As a browser does with a focused field it disables.
  act(() => accept());
  await waitFor(() => expect(box).toBeEnabled());
  expect(box).toHaveFocus();
  // The run that has it replaces the placeholder: one message, not two.
  expect(screen.getAllByRole("group", { name: "Your message" })).toHaveLength(1);
  expect(screen.getByText("Fixed.")).toBeVisible();

  send.mockRejectedValueOnce(new Error("The model is not connected"));
  fireEvent.change(box, { target: { value: "Try again" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(box).toHaveValue("Try again"));
  expect(box).toBeEnabled();
  expect(screen.getAllByRole("group", { name: "Your message" })).toHaveLength(1);
});

it("shows steering messages as pending or applied with the matching checkpoint and context", () => {
  const rewind = vi.spyOn(useCoding.getState(), "rewind").mockResolvedValue(undefined);
  const select = vi.fn().mockResolvedValue(undefined);
  useCoding.setState({
    select,
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "working",
            status: "running",
            input: { text: "Implement the parser" },
            events: [
              {
                id: "steer-a",
                event_type: "steering_requested",
                payload: {
                  text: "Expanded instruction",
                  display_text: "Focus on Unicode",
                  coding_context: {
                    files: [{ path: "unicode.ts", start_line: 1, end_line: 4, version: "captured" }],
                    skills: ["review"],
                    command: null,
                  },
                },
              },
              { id: "applied-a", event_type: "steering_applied", payload: { event_id: "steer-a" } },
              { id: "steer-b", event_type: "steering_requested", payload: { text: "Add tests too" } },
              {
                id: "spawn",
                event_type: "delegation_started",
                payload: {
                  agent_id: "helper-a",
                  agent_name: "Docs helper",
                  run_id: "docs-run",
                  task: "Write the parser docs in docs/parser.md, covering Unicode input.",
                },
              },
              {
                id: "again",
                event_type: "delegation_started",
                payload: {
                  agent_id: "helper-a",
                  agent_name: "Docs helper",
                  run_id: "docs-2",
                  followup: true,
                },
              },
              { id: "compact", event_type: "summarization_started", payload: {} },
              { id: "compacted", event_type: "summarization_finished", payload: {} },
              { id: "model", event_type: "model_call_started", payload: { call_id: "m1" } },
              {
                id: "edit",
                event_type: "tool_started",
                payload: { id: "e1", tool: "edit_file", args: { path: "src/home.ts" } },
              },
            ],
            checkpoints: [
              {
                id: "before-steer-a",
                run_id: "working",
                kind: "before_steering",
                event_id: "steer-a",
                available: true,
              },
            ],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  // While it runs, the chat says what the task is doing, and where it compacted the conversation.
  expect(screen.getByRole("status", { name: "Task status" })).toHaveTextContent("Editing home.ts");
  expect(screen.getByText("Context compacted")).toBeVisible();
  // A helper it started gets one card, with what it was asked and a way to its chat; a follow-up adds none.
  const card = within(screen.getByRole("group", { name: "Started a helper: Docs helper" }));
  expect(card.getByText("Started a helper · Docs helper")).toBeVisible();
  expect(card.getByText(/Write the parser docs/)).toBeVisible();
  expect(screen.getAllByRole("group", { name: /^Started a helper/ })).toHaveLength(1);
  fireEvent.click(card.getByRole("button", { name: "Open Docs helper" }));
  expect(select).toHaveBeenCalledWith("helper-a");
  const [applied, pending] = screen
    .getAllByRole("group", { name: "Steering message" })
    .map((group) => within(group));
  expect(applied.getByText("Steering · Applied")).toBeVisible();
  expect(applied.getByText("Focus on Unicode")).toBeVisible();
  expect(applied.getByText("unicode.ts:1–4")).toBeVisible();
  expect(applied.getByText("Skill: review")).toBeVisible();
  expect(applied.getByRole("button", { name: "Fork from this message" })).toBeDisabled();
  expect(pending.getByText("Steering · Pending")).toBeVisible();
  expect(pending.queryByRole("button", { name: "Rewind to this message" })).toBeNull();
  // Compacting again: the conversation says so where it happens, and only there.
  const [working] = useCoding.getState().details[thread.id].runs;
  act(() =>
    useCoding.setState({
      details: {
        [thread.id]: {
          ...useCoding.getState().details[thread.id],
          runs: [
            {
              ...working,
              events: [
                ...working.events,
                { id: "compact-2", event_type: "summarization_started", payload: {} },
              ],
            },
          ] as unknown as ThreadDetail["runs"],
        },
      },
    }),
  );
  expect(screen.getByText("Compacting the conversation…")).toBeVisible();
  expect(screen.queryByRole("status", { name: "Task status" })).toBeNull();
  fireEvent.click(applied.getByRole("button", { name: "Rewind to this message" }));
  expect(screen.getByRole("alertdialog", { name: "Rewind conversation?" })).toHaveTextContent(
    "before this message",
  );
  fireEvent.click(screen.getByRole("button", { name: "Rewind" }));
  expect(rewind).toHaveBeenCalledWith(thread.id, "before-steer-a");
});

it("explains why an unavailable checkpoint cannot restore or fork code", () => {
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "legacy",
            status: "completed",
            input: { text: "Old request" },
            events: [],
            checkpoints: [
              {
                id: "missing",
                run_id: "legacy",
                kind: "before_user",
                available: false,
                unavailable_reason: "Choose a Git repository to record code history.",
              },
            ],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  expect(screen.getByRole("button", { name: "Rewind to this message" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Rewind to this message" })).toHaveAttribute(
    "title",
    "Choose a Git repository to record code history.",
  );
  expect(screen.getByRole("button", { name: "Fork from this message" })).toBeDisabled();
});

it("keeps the plan and separate assistant replies in order around steering, including after reload", () => {
  const phases: Array<[string, RunEvent["payload"]]> = [
    ["assistant_delta", { message_id: "first", text: "The first three reviews are starting." }],
    ["assistant_message", { message_id: "first", text: "The first three reviews are starting." }],
    [
      "tool_started",
      {
        id: "plan",
        tool: "write_todos",
        args: { todos: [{ content: "Review the code", status: "in_progress" }] },
      },
    ],
    ["tool_completed", { id: "plan", tool: "write_todos", result: "Updated" }],
    ["steering_requested", { text: "Add a fourth review" }],
    ["steering_applied", { event_id: "timeline-4" }],
    ["assistant_delta", { message_id: "second", text: "The fourth review will follow." }],
  ];
  const events = phases.map(([event_type, payload], sequence): RunEvent => ({
    node_id: null,
    id: `timeline-${sequence}`,
    run_id: "timeline",
    sequence,
    event_type,
    payload,
    created_at: "",
  }));
  const run = {
    id: "timeline",
    status: "running",
    input: { text: "Start three reviews" },
    events,
  } as ThreadDetail["runs"][number];
  useCoding.setState({ details: { [thread.id]: { ...thread, runs: [run] } } });
  render(<CodingApp />);
  const plan = screen.getByLabelText("Task checklist");
  const steer = screen.getByRole("group", { name: "Steering message" });
  expect(plan.compareDocumentPosition(steer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const replies = screen.getAllByRole("group", { name: "Assistant response" });
  expect(replies).toHaveLength(2);
  expect(replies[0]).toHaveTextContent("The first three reviews are starting.");
  expect(replies[0]).not.toHaveTextContent("The fourth review will follow.");
  expect(steer.compareDocumentPosition(replies[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(replies.every((reply) => reply.classList.contains("agent-progress"))).toBe(true);
  expect(screen.queryByRole("button", { name: "Copy response" })).toBeNull();

  // Settled/reloaded runs have durable messages and no token deltas.
  act(() =>
    useCoding.setState({
      details: {
        [thread.id]: {
          ...thread,
          runs: [
            {
              ...run,
              status: "completed",
              output: { text: "The fourth review will follow." },
              events: [
                ...events.filter((event) => event.event_type !== "assistant_delta"),
                {
                  ...events[6],
                  id: "finished",
                  event_type: "assistant_message",
                },
              ],
            } as ThreadDetail["runs"][number],
          ],
        },
      },
    }),
  );
  expect(screen.getAllByRole("group", { name: "Assistant response" })).toHaveLength(2);
  expect(screen.getAllByText("The first three reviews are starting.")).toHaveLength(1);
  expect(screen.getAllByText("The fourth review will follow.")).toHaveLength(1);
  expect(screen.getAllByLabelText("Task checklist")).toHaveLength(1);
  const settled = screen.getAllByRole("group", { name: "Assistant response" });
  expect(settled[0]).toHaveClass("agent-progress");
  expect(settled[1]).not.toHaveClass("agent-progress");
  expect(screen.getAllByRole("button", { name: "Copy response" })).toHaveLength(1);
  expect(within(settled[1]).getByRole("button", { name: "Copy response" })).toBeVisible();
});

it.each(["main chat", "subagent"])(
  "reconciles streamed and saved provider IDs in a %s without replaying the response",
  (owner) => {
    const selected = { ...thread, parent_thread_id: owner === "subagent" ? "parent" : null };
    const events: RunEvent[] = [];
    const run = {
      id: "provider-stream",
      status: "running",
      input: { text: "Check the work" },
      events,
    } as ThreadDetail["runs"][number];
    const publish = () =>
      useCoding.setState({
        details: { [selected.id]: { ...selected, runs: [{ ...run, events: [...events] }] } },
      });
    const append = (type: string, messageId: string, text: string) => {
      events.push({
        node_id: null,
        id: `event-${events.length}`,
        run_id: run.id,
        sequence: events.length + 1,
        event_type: type,
        payload: { message_id: messageId, text },
        created_at: "",
      });
      act(publish);
    };
    publish();
    render(<CodingApp />);
    append("assistant_delta", "lc_run--tool-only", "\n\n");
    append("assistant_delta", "lc_run--first", "\n");
    expect(screen.queryByRole("group", { name: "Assistant response" })).toBeNull();
    append("assistant_delta", "lc_run--first", "The checks");
    append("assistant_delta", "lc_run--first", " ");
    append("assistant_delta", "lc_run--first", "pass.");
    expect(transcripts.runTranscript(run)).toEqual([
      { kind: "assistant", id: "lc_run--first", text: "\nThe checks pass.", complete: false },
    ]);
    const first = screen.getByRole("group", { name: "Assistant response" });
    expect(first).toHaveTextContent("The checks pass.");
    expect(first.querySelector("[data-sd-animate]")).not.toBeNull();
    append("assistant_message", "resp_first", "The checks pass.");
    expect(screen.getAllByRole("group", { name: "Assistant response" })).toHaveLength(1);
    expect(screen.getByRole("group", { name: "Assistant response" })).toBe(first);
    expect(first.querySelector("[data-sd-animate]")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy response" })).toBeNull();

    // A later model call may say exactly the same thing: it is still a new message.
    append("assistant_delta", "lc_run--another-tool", " \n\t");
    append("assistant_delta", "lc_run--second", "The checks pass.");
    append("assistant_message", "resp_second", "The checks pass.");
    expect(screen.getAllByRole("group", { name: "Assistant response" })).toHaveLength(2);
    expect(
      screen
        .getAllByRole("group", { name: "Assistant response" })
        .every((reply) => !reply.querySelector("[data-sd-animate]")),
    ).toBe(true);

    // Completion and reopening keep only durable messages, with the same two replies.
    run.status = "completed";
    run.output = { text: "The checks pass." };
    append("assistant_delta", "lc_run--empty-tail", "\n\n");
    // A trailing tool-only response must not hide the actual last reply or duplicate the output.
    expect(screen.getAllByRole("group", { name: "Assistant response" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "Copy response" })).toHaveLength(1);
    events.splice(0, events.length, ...events.filter((event) => event.event_type === "assistant_message"));
    act(publish);
    expect(screen.getAllByRole("group", { name: "Assistant response" })).toHaveLength(2);

    // Useful partial text remains visible and copyable when a run stops without a saved message.
    run.status = owner === "main chat" ? "cancelled" : "failed";
    run.output = null;
    append("assistant_delta", "lc_run--partial", "Partial findings");
    const anchor = events.at(-1)!;
    events.push({
      node_id: null,
      id: "partial-steering",
      run_id: run.id,
      sequence: events.length + 1,
      event_type: "steering_requested",
      payload: { text: "Change direction" },
      created_at: "",
    });
    act(publish);
    const partial = screen.getAllByRole("group", { name: "Assistant response" }).at(-1)!;
    expect(partial).toHaveTextContent("Partial findings");
    expect(within(partial).getByRole("button", { name: "Copy response" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: "Copy response" })).toHaveLength(1);
    run.output = { text: " \n\t" };
    act(publish);
    expect(screen.getAllByRole("group", { name: "Assistant response" })).toHaveLength(3);
    events.push({
      node_id: null,
      id: "interrupted-response",
      run_id: run.id,
      sequence: events.length + 1,
      event_type: "assistant_message",
      payload: { message_id: "lc_run--partial", text: "Partial findings", partial: true },
      created_at: "",
    });
    // Reopening keeps the interrupted response after transient token rows are compacted.
    anchor.event_type = "assistant_started";
    anchor.payload = { message_id: "lc_run--partial" };
    events.splice(0, events.length, ...events.filter((event) => event.event_type !== "assistant_delta"));
    act(publish);
    expect(screen.getByText("Interrupted response")).toBeVisible();
    expect(screen.getAllByRole("group", { name: "Assistant response" })).toHaveLength(3);
    const savedPartial = screen.getAllByRole("group", { name: "Assistant response" }).at(-1)!;
    const steering = screen.getByRole("group", { name: "Steering message" });
    expect(savedPartial.compareDocumentPosition(steering) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // A retried response stays separate even if the endpoint repeats its response ID.
    append("assistant_message", "lc_run--partial", "Recovered findings");
    expect(screen.getAllByRole("group", { name: "Assistant response" })).toHaveLength(4);
    expect(screen.getByText("Partial findings")).toBeVisible();
    expect(screen.getByText("Recovered findings")).toBeVisible();
  },
);

it("finishes an in-flight reply before steering without duplicating legacy saved messages", () => {
  const phases: Array<[string, RunEvent["payload"]]> = [
    ["assistant_delta", { message_id: "first", text: "Original " }],
    ["steering_requested", { text: "Change direction" }],
    ["assistant_delta", { message_id: "first", text: "reply." }],
    // Older app versions did not save message_id on completed messages.
    ["assistant_message", { text: "Original reply." }],
    ["steering_applied", { event_id: "crossing-1" }],
    ["assistant_delta", { message_id: "next", text: "New reply." }],
    ["assistant_message", { text: "New reply." }],
  ];
  const events = phases.map(([event_type, payload], sequence): RunEvent => ({
    node_id: null,
    id: `crossing-${sequence}`,
    run_id: "crossing",
    sequence,
    event_type,
    payload,
    created_at: "",
  }));
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "crossing",
            status: "running",
            input: { text: "Start work" },
            events,
          },
        ] as ThreadDetail["runs"],
      },
    },
  });
  render(<CodingApp />);
  const replies = screen.getAllByRole("group", { name: "Assistant response" });
  expect(replies).toHaveLength(2);
  expect(replies[0]).toHaveTextContent("Original reply.");
  expect(replies[1]).toHaveTextContent("New reply.");
  const steer = screen.getByRole("group", { name: "Steering message" });
  expect(replies[0].compareDocumentPosition(steer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(steer.compareDocumentPosition(replies[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByText("Original reply.")).toHaveLength(1);
  expect(screen.getAllByText("New reply.")).toHaveLength(1);
});

it("opens and reloads a completed helper with context-free steering from its parent", async () => {
  const project = makeProject({ id: "helpers-project", name: "Helpers", created_at: "" });
  const parent = { ...thread, project_id: project.id };
  const helper: ThreadDetail = {
    ...parent,
    id: "helper",
    conversation_id: "helper-conversation",
    title: "Completed helper",
    parent_thread_id: parent.id,
    runs: [
      {
        id: "helper-run",
        status: "completed",
        input: { text: "Review the implementation" },
        output: { text: "Helper review complete" },
        events: [
          {
            id: "redirect",
            event_type: "steering_requested",
            payload: { text: "Also check the tests", coding_context: {} },
          },
          { id: "applied", event_type: "steering_applied", payload: { event_id: "redirect" } },
          { id: "reply", event_type: "assistant_message", payload: { text: "Helper review complete" } },
        ].map((event, sequence) => ({ ...event, sequence, run_id: "helper-run", created_at: "" })),
      },
    ] as unknown as ThreadDetail["runs"],
  };
  useCoding.setState({
    workspace: { ...useCoding.getState().workspace!, projects: [project], threads: [parent, helper] },
    details: { [parent.id]: parent },
  });
  vi.spyOn(codingApi, "thread").mockResolvedValue(helper);
  const view = render(<CodingApp />);
  fireEvent.click(screen.getByRole("button", { name: `Show helpers for ${parent.title}` }));
  fireEvent.click(screen.getByRole("button", { name: "Completed helper" }));
  expect(await screen.findByText("Helper review complete")).toBeVisible();
  expect(screen.getByRole("group", { name: "Steering message" })).toHaveTextContent("Also check the tests");
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toBeDisabled();
  expect(screen.getByRole("complementary", { name: "Projects sidebar" })).toBeVisible();
  view.unmount();
  await useCoding.getState().load(helper.id);
  render(<CodingApp />);
  expect(screen.getByText("Helper review complete")).toBeVisible();
  expect(screen.getByRole("group", { name: "Steering message" })).toHaveTextContent("Steering · Applied");
});

it("shows loading and retry for an uncached chat while keeping cached chats immediately usable", async () => {
  const project = makeProject({ id: "loading-project", name: "Loading project", created_at: "" });
  const empty = { ...thread, project_id: project.id };
  const saved: ThreadDetail = {
    ...empty,
    id: "saved-chat",
    conversation_id: "saved-conversation",
    title: "Saved conversation",
    runs: [
      {
        id: "saved-run",
        status: "completed",
        input: { text: "Saved request" },
        output: { text: "Saved reply" },
        events: [],
      },
    ] as unknown as ThreadDetail["runs"],
  };
  useCoding.setState({
    workspace: { ...useCoding.getState().workspace!, projects: [project], threads: [empty, saved] },
    details: { [empty.id]: empty },
  });
  let reject!: (error: Error) => void;
  let finish!: (detail: ThreadDetail) => void;
  const first = new Promise<ThreadDetail>((_, fail) => {
    reject = fail;
  });
  const retry = new Promise<ThreadDetail>((resolve) => {
    finish = resolve;
  });
  vi.spyOn(codingApi, "thread")
    .mockReturnValueOnce(first)
    .mockReturnValueOnce(retry)
    .mockImplementation(async (id) => (id === empty.id ? empty : saved));
  render(<CodingApp />);
  expect(screen.getByRole("heading", { name: "What are we building?" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: saved.title }));
  expect(screen.getByRole("status", { name: "Loading conversation" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "What are we building?" })).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toBeVisible();
  expect(screen.getByLabelText("Workspace toolbar")).toHaveTextContent(saved.title);
  await act(async () => reject(new Error("History unavailable")));
  expect(screen.getByRole("alert")).toHaveTextContent("History unavailable");
  expect(screen.queryByRole("status", { name: "Loading conversation" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(screen.getByRole("status", { name: "Loading conversation" })).toBeVisible();
  const box = screen.getByRole("textbox", { name: "Message Wavefunction" });
  await act(async () => finish(saved));
  expect(screen.getByText("Saved reply")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toBe(box);
  expect(box).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: empty.title }));
  expect(screen.getByRole("heading", { name: "What are we building?" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: saved.title }));
  expect(screen.getByText("Saved reply")).toBeVisible();
  expect(screen.queryByRole("status", { name: "Loading conversation" })).not.toBeInTheDocument();
  expect(codingApi.thread).toHaveBeenCalledTimes(2);
});

it("lands at the end of a chat whose history arrives after the chat opens", () => {
  useCoding.setState({ details: {} });
  const view = render(<CodingApp />);
  const viewport = view.container.querySelector<HTMLDivElement>(".conversation-scroll")!;
  Object.defineProperties(viewport, {
    scrollHeight: { configurable: true, value: 6000 },
    clientHeight: { configurable: true, value: 600 },
  });
  const scrollTo = vi.spyOn(viewport, "scrollTo");
  act(() =>
    useCoding.setState({
      details: {
        [thread.id]: {
          ...thread,
          runs: [
            {
              id: "saved",
              status: "completed",
              input: { text: "Request" },
              output: { text: "The newest reply" },
              events: [],
            },
          ] as unknown as ThreadDetail["runs"],
        },
      },
    }),
  );
  expect(screen.getByText("The newest reply")).toBeVisible();
  expect(scrollTo).toHaveBeenLastCalledWith({ top: 5400, behavior: "instant" });
});

it("jumps to the visible end once and follows later layout changes without fighting a reader", () => {
  let resize!: ResizeObserverCallback;
  vi.spyOn(window, "ResizeObserver").mockImplementation(function (callback) {
    resize = callback;
    return { observe() {}, unobserve() {}, disconnect() {} };
  });
  // Only scrolling by hand moments ago stops the follow: the clock tells the reader's scroll apart.
  let now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [
          {
            id: "saved",
            status: "completed",
            input: { text: "Request" },
            output: { text: "The final reply remains visible" },
            events: [],
          },
        ] as unknown as ThreadDetail["runs"],
      },
    },
  });
  const view = render(<CodingApp />);
  const viewport = view.container.querySelector<HTMLDivElement>(".conversation-scroll")!;
  Object.defineProperties(viewport, {
    scrollHeight: { configurable: true, writable: true, value: 6000 },
    clientHeight: { configurable: true, value: 600 },
    scrollTop: { configurable: true, writable: true, value: 1200 },
  });
  // The reader scrolls up by hand.
  fireEvent.wheel(viewport);
  fireEvent.scroll(viewport);
  now += 1000;
  const scrollTo = vi.spyOn(viewport, "scrollTo");
  fireEvent.click(screen.getByRole("button", { name: "Scroll to latest" }));
  expect(scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 5400, behavior: "smooth" });
  viewport.scrollTop = 3000;
  fireEvent.scroll(viewport);
  act(() => resize([], {} as ResizeObserver));
  expect(scrollTo).toHaveBeenCalledOnce();
  viewport.scrollTop = 5400;
  fireEvent.scroll(viewport);
  Object.defineProperty(viewport, "scrollHeight", { value: 6200 });
  act(() => resize([], {} as ResizeObserver));
  expect(scrollTo).toHaveBeenLastCalledWith({ top: 5600, behavior: "instant" });
  // A new message lands below the view, and a scroll event the browser queued earlier arrives
  // before the follow: the view never moved, so it keeps following.
  Object.defineProperty(viewport, "scrollHeight", { value: 6600 });
  fireEvent.scroll(viewport);
  act(() => resize([], {} as ResizeObserver));
  expect(scrollTo).toHaveBeenLastCalledWith({ top: 6000, behavior: "instant" });
  expect(screen.queryByRole("button", { name: "Scroll to latest" })).not.toBeInTheDocument();
  expect(screen.getByText("The final reply remains visible")).toBeVisible();
  // The view moves up without the reader, as WebKit clamps it while a sent message is replaced
  // by the saved one: it goes back to the end.
  viewport.scrollTop = 5832;
  scrollTo.mockClear();
  fireEvent.scroll(viewport);
  expect(scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 6000, behavior: "instant" });
  // Scrolled up by hand, it stays put as the reply grows, until the reader sends a message.
  fireEvent.wheel(viewport);
  viewport.scrollTop = 2000;
  fireEvent.scroll(viewport);
  now += 1000;
  act(() => resize([], {} as ResizeObserver));
  expect(scrollTo).toHaveBeenCalledOnce();
  act(() =>
    useCoding.setState({
      pending: { [thread.id]: { text: "One more thing", files: [], at: "2026-10-18T10:00:00Z" } },
    }),
  );
  expect(scrollTo).toHaveBeenLastCalledWith({ top: 6000, behavior: "instant" });
  act(() => resize([], {} as ResizeObserver));
  expect(scrollTo).toHaveBeenCalledTimes(3);
});

it("leaves saved transcripts and the reader's scroll position alone while the latest reply streams", () => {
  const history = Array.from({ length: 20 }, (_, index) => ({
    id: `history-${index}`,
    status: "completed",
    input: { text: `Request ${index}` },
    output: { text: `Saved reply ${index}` },
    events: [],
  })) as unknown as ThreadDetail["runs"];
  let current = {
    id: "streaming-run",
    status: "running",
    input: { text: "New request" },
    events: [],
  } as unknown as ThreadDetail["runs"][number];
  useCoding.setState({ details: { [thread.id]: { ...thread, runs: [...history, current] } } });
  const transcript = vi.spyOn(transcripts, "runTranscript");
  const view = render(<CodingApp />);
  const viewport = view.container.querySelector<HTMLDivElement>(".conversation-scroll")!;
  Object.defineProperties(viewport, {
    scrollHeight: { configurable: true, value: 6000 },
    clientHeight: { configurable: true, value: 600 },
    scrollTop: { configurable: true, writable: true, value: 1200 },
  });
  fireEvent.wheel(viewport);
  fireEvent.scroll(viewport);
  expect(screen.getByRole("button", { name: "Scroll to latest" })).toBeVisible();
  transcript.mockClear();
  const scrollTo = vi.spyOn(viewport, "scrollTo");
  for (let index = 0; index < 8; index++) {
    current = {
      ...current,
      events: [
        ...current.events,
        {
          node_id: null,
          id: `delta-${index}`,
          run_id: current.id,
          sequence: index + 1,
          event_type: "assistant_delta",
          payload: { message_id: "reply", text: `word${index} ` },
          created_at: "",
        },
      ],
    };
    act(() => useCoding.setState({ details: { [thread.id]: { ...thread, runs: [...history, current] } } }));
  }
  expect(screen.getAllByRole("group", { name: "Assistant response" }).at(-1)).toHaveTextContent("word7");
  expect(transcript.mock.calls.filter(([run]) => run.id.startsWith("history-"))).toHaveLength(0);
  expect(scrollTo).not.toHaveBeenCalled();
  expect(viewport.scrollTop).toBe(1200);
});
