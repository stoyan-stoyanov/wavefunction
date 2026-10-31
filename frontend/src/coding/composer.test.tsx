import { api } from "@/lib/api";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { invoke } from "@tauri-apps/api/core";
import { beforeEach, expect, it, vi } from "vitest";
import { codingApi, type CodingRun, type CodingThread, type GitBranches, type TaskContext } from "./api";
import { makeThread } from "@/test/fixtures";
import { Composer } from "./composer";
import { contextDraftKey } from "./context-draft";
import { useCoding } from "./store";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
vi.mock("@/components/model-picker", () => ({
  useModelOptions: () => ({ options: [], loading: false, specFor: () => null }),
}));
vi.mock("@/lib/api", () => ({
  api: { modelReasoningOptions: vi.fn() },
  streamRunEvents: vi.fn(),
}));
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  codingApi: {
    contextFiles: vi.fn(),
    file: vi.fn(),
    skills: vi.fn(),
    workflow: vi.fn(),
    upload: vi.fn(),
    attachmentPreview: vi.fn(),
    removeAttachment: vi.fn(),
    gitBranches: vi.fn(),
    defaultMode: vi.fn(),
  },
}));

const thread: CodingThread = makeThread({
  id: "one",
  conversation_id: "conversation-one",
  title: "Review the parser",
  project_id: "project-one",
  folder: "/repo/parser",
  model: {
    provider: "codex",
    model: "gpt-6.1-sol",
    base_url: null,
    temperature: 0.1,
    max_tokens: null,
    reasoning_effort: "max",
  },
  created_at: "",
  updated_at: "",
});
const captured: TaskContext = {
  files: [{ path: "app.ts", version: "captured-version", start_line: 2, end_line: 4 }],
  skills: ["code-review"],
  command: null,
};
const onSettings = vi.fn();
const onModels = vi.fn();
const send = vi.fn();
const patch = vi.fn();
function composer(owner = thread) {
  return <Composer thread={owner} onSettings={onSettings} onModels={onModels} />;
}
function pending() {
  let resolve!: () => void;
  const promise = new Promise<void>((finish) => {
    resolve = finish;
  });
  return { promise, resolve };
}
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  send.mockResolvedValue(undefined);
  patch.mockResolvedValue(undefined);
  useCoding.setState({
    selected: thread.id,
    details: { [thread.id]: { ...thread, runs: [], attachments: [] } },
    sending: {},
    restoring: {},
    uploading: {},
    pending: {},
    unsent: {},
    send,
    patch,
  });
  vi.mocked(codingApi.skills).mockResolvedValue({ skills: [], warnings: [] });
  vi.mocked(codingApi.gitBranches).mockResolvedValue({
    ok: true,
    current: "refs/heads/main",
    default: "refs/heads/main",
    head: "abcdef0123456789",
    branches: [
      { name: "main", ref: "refs/heads/main" },
      { name: "feature", ref: "refs/heads/feature" },
      { name: "origin/main", ref: "refs/remotes/origin/main" },
    ],
  });
  vi.mocked(codingApi.workflow).mockResolvedValue({
    version: "workflow-version",
    scope: "project",
    prompts: [{ name: "pr", description: "Prepare PR", body: "Draft $ARGUMENTS" }],
  });
  vi.mocked(api.modelReasoningOptions).mockResolvedValue({
    efforts: ["low", "max"],
  });
});

it("previews attached images and shows other files by type and size", async () => {
  vi.mocked(codingApi.attachmentPreview).mockResolvedValue("blob:shot");
  vi.mocked(codingApi.removeAttachment).mockResolvedValue(undefined);
  useCoding.setState({
    details: {
      [thread.id]: {
        ...thread,
        runs: [],
        attachments: [
          { id: "img", name: "shot.png", media_type: "image/png", size_bytes: 900 },
          { id: "doc", name: "spec.pdf", media_type: "application/pdf", size_bytes: 2048 },
        ],
      },
    },
  });
  render(composer());
  expect(await screen.findByRole("img", { name: "shot.png" })).toHaveAttribute("src", "blob:shot");
  expect(codingApi.attachmentPreview).toHaveBeenCalledWith(thread.id, "img");
  expect(codingApi.attachmentPreview).toHaveBeenCalledTimes(1);
  expect(screen.getByText("PDF · 2 KB")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Remove spec.pdf" }));
  await waitFor(() => expect(codingApi.removeAttachment).toHaveBeenCalledWith(thread.id, "doc"));
  // A file still uploading keeps the message from going without it, also after the user left
  // the chat and came back.
  const uploaded = pending();
  vi.mocked(codingApi.upload).mockReturnValueOnce(uploaded.promise as never);
  const file = new File(["log"], "build.log", { type: "text/plain" });
  const box = screen.getByRole("textbox", { name: "Message Wavefunction" });
  fireEvent.paste(box, { clipboardData: { items: [{ kind: "file", getAsFile: () => file }] } });
  await waitFor(() => expect(codingApi.upload).toHaveBeenCalled());
  cleanup();
  render(composer());
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Summarise it" },
  });
  expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  await act(async () => uploaded.resolve());
  await waitFor(() => expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled());
});

it("keeps reasoning effort accessible in the model menu", async () => {
  render(composer());
  fireEvent.click(screen.getByRole("button", { name: "Chat model: gpt-6.1-sol" }));
  const reasoning = await screen.findByRole("combobox", {
    name: "Reasoning effort",
  });
  await waitFor(() => expect(reasoning).toBeEnabled());
  fireEvent.keyDown(reasoning, { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("option", { name: "Low" }));
  await waitFor(() =>
    expect(patch).toHaveBeenCalledWith(thread.id, {
      model: { ...thread.model, reasoning_effort: "low" },
    }),
  );
});

it("shows the chat's mode, context and dictation in the toolbar, and acts on them", async () => {
  vi.mocked(codingApi.defaultMode).mockResolvedValue({ mode: "auto" });
  render(composer({ ...thread, mode: "full" }));
  // Dictation is macOS's, so a browser tab has no button for it.
  expect(screen.queryByRole("button", { name: "Dictate" })).not.toBeInTheDocument();
  // The mode shows from a new chat's first message; a choice is also where the next chat starts.
  fireEvent.keyDown(screen.getByRole("button", { name: "Agent mode: Full access" }), { key: "ArrowDown" });
  fireEvent.click(await screen.findByRole("menuitemradio", { name: /^Code/ }));
  await waitFor(() => expect(patch).toHaveBeenCalledExactlyOnceWith(thread.id, { mode: "auto" }));
  expect(codingApi.defaultMode).toHaveBeenCalledExactlyOnceWith("auto");

  // The context as the last request reported it, toward where the conversation is compacted.
  const compact = vi.fn().mockResolvedValue(undefined);
  const event = (event_type: string, payload: Record<string, unknown>) => ({
    id: event_type,
    run_id: "run",
    sequence: 1,
    event_type,
    payload,
    created_at: "",
  });
  const run = (id: string, status: CodingRun["status"], events: ReturnType<typeof event>[], kind = "user") =>
    ({
      id,
      kind,
      status,
      input: { text: "" },
      output: null,
      error: null,
      started_at: "",
      usage: {},
      events,
    }) as CodingRun;
  const asked = run("asked", "completed", [
    event("context_budget", {
      estimated_tokens: 70_000,
      context_window: 200_000,
      compact_at: 140_000,
      breakdown: { system_prompt: 10_000, tools: 20_000, documents: 5_000, conversation: 35_000 },
    }),
  ]);
  act(() =>
    useCoding.setState({ compact, details: { [thread.id]: { ...thread, runs: [asked], attachments: [] } } }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Context 50% full" }));
  const popup = await screen.findByRole("dialog", { name: "Context" });
  expect(popup).toHaveTextContent("70K of 140K tokens before it is compacted");
  expect(popup).toHaveTextContent("Attached documents5K");
  fireEvent.click(screen.getByRole("button", { name: "Compact now" }));
  expect(compact).toHaveBeenCalledExactlyOnceWith(thread.id);
  // While it runs it can't be asked again; afterwards the summary is all that is left of the conversation.
  const compacting = run("compacting", "running", [], "compact");
  act(() =>
    useCoding.setState({
      details: { [thread.id]: { ...thread, runs: [asked, compacting], attachments: [] } },
    }),
  );
  expect(screen.getByRole("button", { name: "Compacting…" })).toBeDisabled();
  const compacted = run(
    "compacting",
    "completed",
    [event("summarization_finished", { summary_tokens: 2_000 })],
    "compact",
  );
  act(() =>
    useCoding.setState({
      details: { [thread.id]: { ...thread, runs: [asked, compacted], attachments: [] } },
    }),
  );
  expect(screen.getByRole("button", { name: "Context 23% full" })).toBeInTheDocument();
  expect(popup).toHaveTextContent("Attached documents0");

  // In the desktop app it starts macOS dictation with the message focused, where the words go.
  cleanup();
  const desktop = window as unknown as Record<string, unknown>;
  desktop.__TAURI_INTERNALS__ = {};
  vi.mocked(invoke).mockResolvedValue(undefined);
  try {
    render(composer());
    const dictate = screen.getByRole("button", { name: "Dictate" });
    expect(fireEvent.mouseDown(dictate)).toBe(false);
    fireEvent.click(dictate);
    const box = screen.getByRole("textbox", { name: "Message Wavefunction" });
    expect(box).toHaveFocus();
    expect(invoke).toHaveBeenCalledExactlyOnceWith("start_dictation");
    // macOS's cue is a small mic by the caret, so the mic shows it is listening until Esc.
    await waitFor(() => expect(dictate).toHaveAttribute("aria-pressed", "true"));
    expect(box).toHaveAttribute("placeholder", "Listening… speak now");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(dictate).toHaveAttribute("aria-pressed", "false");
  } finally {
    delete desktop.__TAURI_INTERNALS__;
  }
});

it.each([
  ["another chat", { ...thread, id: "two", folder: "/repo/next" }],
  ["another repository", { ...thread, folder: "/repo/next" }],
] as const)(
  "preserves the new draft and context after navigating to %s during send",
  async (_label, nextThread) => {
    const deferred = pending();
    send.mockReturnValueOnce(deferred.promise);
    localStorage.setItem(`harness.draft.${thread.id}`, "Submitted draft");
    localStorage.setItem(contextDraftKey(thread), JSON.stringify(captured));
    const nextContext: TaskContext = {
      files: [
        {
          path: "next.ts",
          version: "next-version",
          start_line: 1,
          end_line: 1,
        },
      ],
      skills: [],
      command: null,
    };
    localStorage.setItem(contextDraftKey(nextThread), JSON.stringify(nextContext));
    const view = render(composer());
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(send).toHaveBeenCalledWith(thread.id, "Submitted draft", captured);
    view.rerender(composer(nextThread));
    fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
      target: { value: "New draft" },
    });
    await act(async () => {
      deferred.resolve();
      await deferred.promise;
    });
    expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toHaveValue("New draft");
    expect(screen.getByText("next.ts:1–1")).toBeInTheDocument();
    expect(localStorage.getItem(`harness.draft.${nextThread.id}`)).toBe("New draft");
    expect(JSON.parse(localStorage.getItem(contextDraftKey(nextThread))!)).toEqual(nextContext);
  },
);

it("steers a running task and answers a paused one by default, queueing only when asked", async () => {
  const stop = vi.fn();
  const current = { id: "running", status: "running", events: [] } as unknown as CodingRun;
  const queued = { id: "queued", status: "queued", events: [] } as unknown as CodingRun;
  localStorage.setItem(contextDraftKey(thread), JSON.stringify(captured));
  useCoding.setState({
    details: { [thread.id]: { ...thread, runs: [current, queued], attachments: [] } },
    stop,
  });
  render(composer());
  expect(screen.getByRole("checkbox", { name: "Queue follow-up" })).not.toBeChecked();
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Focus on parsing" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Steer agent" }));
  await waitFor(() => expect(send).toHaveBeenCalledWith(thread.id, "Focus on parsing", captured));
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  expect(stop).toHaveBeenCalledWith("running");
  // Queued behind the task it answers, a message waited for as long as the task kept asking.
  const asking = {
    ...current,
    status: "waiting_approval",
    events: [{ id: "asked", sequence: 4, event_type: "run_questions", payload: { groups: [] } }],
  };
  act(() =>
    useCoding.setState({
      details: {
        [thread.id]: { ...thread, runs: [asking as unknown as CodingRun, queued], attachments: [] },
      },
    }),
  );
  const box = screen.getByRole("textbox", { name: "Message Wavefunction" });
  expect(box).toHaveAttribute("placeholder", "Answer the agent's question…");
  fireEvent.change(box, { target: { value: "Use the staging project" } });
  fireEvent.click(screen.getByRole("button", { name: "Answer agent" }));
  await waitFor(() =>
    expect(send).toHaveBeenLastCalledWith(thread.id, "Use the staging project", {
      files: [],
      skills: [],
      command: null,
    }),
  );
  // An approval card holds the task: the message is sent now and read once the card is decided.
  const approving = {
    ...current,
    status: "waiting_approval",
    events: [{ id: "card", sequence: 5, event_type: "run_waiting_for_approval", payload: { requests: [] } }],
  };
  act(() =>
    useCoding.setState({
      details: {
        [thread.id]: { ...thread, runs: [approving as unknown as CodingRun, queued], attachments: [] },
      },
    }),
  );
  expect(box).toHaveAttribute("placeholder", "The agent reads this once you decide the card above…");
  fireEvent.change(box, { target: { value: "Also fix the copy" } });
  fireEvent.click(screen.getByRole("button", { name: "Send after the card" }));
  await waitFor(() =>
    expect(send).toHaveBeenLastCalledWith(thread.id, "Also fix the copy", {
      files: [],
      skills: [],
      command: null,
    }),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "Queue follow-up" }));
  fireEvent.change(box, { target: { value: "Run the tests afterward" } });
  fireEvent.click(screen.getByRole("button", { name: "Queue follow-up" }));
  await waitFor(() =>
    expect(send).toHaveBeenLastCalledWith(
      thread.id,
      "Run the tests afterward",
      { files: [], skills: [], command: null },
      { delivery: "queue" },
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  expect(stop).toHaveBeenLastCalledWith("running");
});

it("lets a running chat queue the next message explicitly without changing another chat", async () => {
  const current = { id: "running", status: "running", events: [] } as unknown as CodingRun;
  useCoding.setState({ details: { [thread.id]: { ...thread, runs: [current], attachments: [] } } });
  const view = render(composer());
  fireEvent.click(screen.getByRole("checkbox", { name: "Queue follow-up" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Run the tests afterward" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Queue follow-up" }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      thread.id,
      "Run the tests afterward",
      { files: [], skills: [], command: null },
      { delivery: "queue" },
    ),
  );
  const other = { ...thread, id: "other", conversation_id: "other-conversation" };
  act(() =>
    useCoding.setState({
      details: { [other.id]: { ...other, runs: [{ ...current, id: "other-run" }], attachments: [] } },
    }),
  );
  view.rerender(composer(other));
  expect(screen.getByRole("checkbox", { name: "Queue follow-up" })).not.toBeChecked();
});

it("starts empty Git chats from the chosen branch unless unchecked and disables worktrees outside Git", async () => {
  vi.mocked(codingApi.gitBranches).mockResolvedValue({
    ok: true,
    current: "refs/heads/main",
    default: "refs/heads/main",
    head: "abcdef0123456789",
    branches: [
      { name: "origin/develop", ref: "refs/remotes/origin/develop" },
      { name: "origin/main", ref: "refs/remotes/origin/main" },
      { name: "origin/review-target", ref: "refs/remotes/origin/review-target" },
      ...["feature/main", "trunk", "dev", "development", "master", "develop", "main"].map((name) => ({
        name,
        ref: `refs/heads/${name}`,
      })),
      ...Array.from({ length: 110 }, (_, index) => {
        const name = `feature/task-${String(index).padStart(3, "0")}`;
        return { name, ref: `refs/heads/${name}` };
      }),
    ],
  });
  const gitThread = { ...thread, worktree_available: true };
  const view = render(composer(gitThread));
  const base = screen.getByRole("combobox", { name: "Base branch" });
  await waitFor(() => expect(base).toHaveTextContent("main"));
  expect(codingApi.gitBranches).toHaveBeenCalledExactlyOnceWith(thread.id);
  fireEvent.keyDown(base, { key: "ArrowDown" });
  const search = await screen.findByRole("combobox", { name: "Search branches" });
  const choices = screen.getAllByRole("option");
  expect(choices).toHaveLength(100);
  [
    "main Local",
    "develop Local",
    "master Local",
    "development Local",
    "dev Local",
    "trunk Local",
    "origin/main Remote",
    "origin/develop Remote",
  ].forEach((name, index) => {
    expect(choices[index]).toHaveAccessibleName(name);
  });
  expect(screen.getByText("Showing 100 of 120 branches. Refine your search.")).toBeVisible();
  fireEvent.change(search, { target: { value: "no-such-branch" } });
  expect(await screen.findByText("No branches match your search.")).toBeVisible();
  fireEvent.change(search, { target: { value: "REFS/REMOTES/ORIGIN/REVIEW-TARGET" } });
  await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(1));
  expect(base).toHaveTextContent("main");
  fireEvent.keyDown(search, { key: "ArrowDown" });
  await waitFor(() =>
    expect(screen.getByRole("option", { name: "origin/review-target Remote" })).toHaveAttribute(
      "aria-selected",
      "true",
    ),
  );
  fireEvent.keyDown(search, { key: "Enter" });
  await waitFor(() => expect(base).toHaveTextContent("origin/review-target"));
  expect(codingApi.gitBranches).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("checkbox", { name: "New worktree" }));
  expect(screen.queryByRole("combobox", { name: "Base branch" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "New worktree" }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Base branch" })).toHaveTextContent("origin/review-target"),
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Build it here" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledWith(
      thread.id,
      "Build it here",
      { files: [], skills: [], command: null },
      { worktree: true, worktree_base: "refs/remotes/origin/review-target" },
    ),
  );
  const other = { ...gitThread, id: "other", conversation_id: "other-conversation" };
  act(() => useCoding.setState({ details: { [other.id]: { ...other, runs: [], attachments: [] } } }));
  view.rerender(composer(other));
  const option = screen.getByRole("checkbox", { name: "New worktree" });
  expect(option).toBeChecked();
  fireEvent.click(option);
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Stay in place" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(send).toHaveBeenLastCalledWith(other.id, "Stay in place", {
      files: [],
      skills: [],
      command: null,
    }),
  );
  const plainThread = { ...thread, folder: "/notes", worktree_available: false };
  act(() => useCoding.setState({ details: { [thread.id]: { ...plainThread, runs: [], attachments: [] } } }));
  view.rerender(composer(plainThread));
  expect(screen.getByRole("checkbox", { name: "New worktree" })).toBeDisabled();
  expect(screen.getByRole("checkbox", { name: "New worktree" })).not.toBeChecked();
  expect(codingApi.gitBranches).toHaveBeenCalledTimes(3);
});

it("starts a worktree from the checked-out branch, or commit when detached, and keeps a pick to its chat", async () => {
  const listing: GitBranches = {
    ok: true,
    current: "refs/heads/feature",
    default: "refs/heads/trunk",
    head: "abcdef0123456789",
    branches: [
      { name: "feature", ref: "refs/heads/feature" },
      { name: "trunk", ref: "refs/heads/trunk" },
    ],
  };
  vi.mocked(codingApi.gitBranches).mockResolvedValue(listing);
  const owner = { ...thread, folder: "/repo/one", worktree_available: true };
  const view = render(composer(owner));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Base branch" })).toHaveTextContent("feature"),
  );
  fireEvent.click(screen.getByRole("combobox", { name: "Base branch" }));
  fireEvent.click(await screen.findByRole("option", { name: "trunk Local" }));
  expect(screen.getByRole("combobox", { name: "Base branch" })).toHaveTextContent("trunk");

  const next = { ...owner, id: "next", conversation_id: "next-conversation" };
  act(() => useCoding.setState({ details: { [next.id]: { ...next, runs: [], attachments: [] } } }));
  view.rerender(composer(next));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Base branch" })).toHaveTextContent("feature"),
  );

  vi.mocked(codingApi.gitBranches).mockResolvedValue({ ...listing, current: null });
  view.rerender(composer({ ...next, folder: "/repo/two" }));
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: "Base branch" })).toHaveTextContent(
      "Current commit (abcdef0)",
    ),
  );
});

it("retains the draft through branch errors and ignores a previous folder's response", async () => {
  let finishPrevious!: (value: GitBranches) => void;
  const previousBranches = new Promise<GitBranches>((resolve) => {
    finishPrevious = resolve;
  });
  vi.mocked(codingApi.gitBranches)
    .mockReturnValueOnce(previousBranches)
    .mockRejectedValueOnce(new Error("Branch list unavailable"))
    .mockResolvedValueOnce({ ok: true, current: null, default: null, head: "", branches: [] })
    .mockResolvedValueOnce({
      ok: true,
      current: null,
      default: null,
      head: "1234567890abcdef",
      branches: [{ name: "main", ref: "refs/heads/main" }],
    });
  const gitThread = { ...thread, worktree_available: true };
  const view = render(composer(gitThread));
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Keep this draft" },
  });
  expect(screen.getByRole("combobox", { name: "Base branch" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  fireEvent.keyDown(screen.getByRole("textbox", { name: "Message Wavefunction" }), { key: "Enter" });
  expect(send).not.toHaveBeenCalled();

  view.rerender(composer({ ...gitThread, folder: "/repo/next" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Branch list unavailable");
  expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toHaveValue("Keep this draft");
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Create a commit"));
  expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "New worktree" }));
  expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "New worktree" }));
  const base = screen.getByRole("combobox", { name: "Base branch" });
  // Detached, with no branch to name: the checked-out commit is the base without asking.
  await waitFor(() => expect(base).toHaveTextContent("Current commit (1234567)"));
  await act(async () => {
    finishPrevious({
      ok: true,
      current: "refs/heads/previous",
      default: "refs/heads/previous",
      head: "abcdef0123456789",
      branches: [{ name: "previous", ref: "refs/heads/previous" }],
    });
    await previousBranches;
  });
  expect(base).toHaveTextContent("Current commit (1234567)");
  for (const field of ["sending", "restoring"] as const) {
    act(() => useCoding.setState({ [field]: { [thread.id]: true } }));
    expect(base).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "New worktree" })).toBeDisabled();
    act(() => useCoding.setState({ [field]: {} }));
  }
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledExactlyOnceWith(
      thread.id,
      "Keep this draft",
      { files: [], skills: [], command: null },
      { worktree: true, worktree_base: "1234567890abcdef" },
    ),
  );
});

it("labels an owned worktree and continues active or deleted checkouts without selecting another base", async () => {
  const folder = "/work/.harness-worktrees/episcope-tcell-6d5479ab-877b-4079-a906-320bb07ea9c1";
  const owner = { ...thread, folder, worktree: true, worktree_available: true };
  const view = render(composer(owner));
  const repository = screen.getByRole("button", { name: "episcope-tcell-6d54" });
  expect(repository).toHaveAttribute("title", `Managed worktree: ${folder}`);
  expect(codingApi.gitBranches).not.toHaveBeenCalled();
  expect(screen.queryByRole("combobox", { name: "Base branch" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Retry here" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(send).toHaveBeenCalledExactlyOnceWith(
      thread.id,
      "Retry here",
      { files: [], skills: [], command: null },
      { worktree: true },
    ),
  );
  view.rerender(composer({ ...owner, worktree_status: "deleted" }));
  expect(screen.getByRole("status")).toHaveTextContent(
    "This worktree was deleted after inactivity. It will be recreated when you continue this chat.",
  );
  expect(screen.queryByRole("combobox", { name: "Base branch" })).toBeNull();
  expect(screen.queryByRole("checkbox", { name: "New worktree" })).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Continue this work" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send message" }));
  await waitFor(() =>
    expect(send).toHaveBeenLastCalledWith(
      thread.id,
      "Continue this work",
      { files: [], skills: [], command: null },
      { worktree: true },
    ),
  );
  view.rerender(composer({ ...owner, worktree_status: "active" }));
  expect(screen.queryByText(/This worktree was deleted/)).toBeNull();
  view.rerender(composer({ ...owner, parent_thread_id: "parent", worktree_status: "deleted" }));
  expect(screen.getByRole("status")).toHaveTextContent(/fork.*helper|helper.*fork/i);
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toBeDisabled();
});

it("loads a restored draft and context when the same chat gets a new continuation", () => {
  const view = render(composer());
  fireEvent.change(screen.getByRole("textbox", { name: "Message Wavefunction" }), {
    target: { value: "Later draft" },
  });
  localStorage.setItem(`harness.draft.${thread.id}`, "Earlier request");
  localStorage.setItem(contextDraftKey(thread), JSON.stringify(captured));
  view.rerender(composer({ ...thread, conversation_id: "restored-continuation" }));
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toHaveValue("Earlier request");
  expect(screen.getByRole("button", { name: "Remove context app.ts" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Remove skill code-review" })).toBeVisible();
});

it("keeps a helper read-only while allowing stop and navigation to its parent", () => {
  const helper = { ...thread, parent_thread_id: "parent" };
  const stop = vi.fn();
  const select = vi.fn();
  useCoding.setState({
    details: {
      [helper.id]: {
        ...helper,
        runs: [{ id: "helper-run", status: "running", events: [] } as unknown as CodingRun],
        attachments: [],
      },
    },
    stop,
    select,
  });
  render(composer(helper));
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Steer agent" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Attach files" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Stop agent" }));
  expect(stop).toHaveBeenCalledWith("helper-run");
  fireEvent.click(screen.getByRole("button", { name: "parent chat" }));
  expect(select).toHaveBeenCalledWith("parent");
  expect(send).not.toHaveBeenCalled();
});

it("sends a message written while the chat's history loads once it arrives, as a new chat", async () => {
  const gitThread = { ...thread, worktree_available: true };
  useCoding.setState({ details: {} });
  render(composer(gitThread));
  const box = screen.getByRole("textbox", { name: "Message Wavefunction" });
  fireEvent.change(box, { target: { value: "First message" } });
  fireEvent.keyDown(box, { key: "Enter" });
  expect(send).not.toHaveBeenCalled();
  expect(box).toHaveValue("First message");

  act(() => useCoding.setState({ details: { [thread.id]: { ...gitThread, runs: [], attachments: [] } } }));
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  expect(send.mock.calls[0][1]).toBe("First message");
  expect(send.mock.calls[0][3]).toMatchObject({ worktree: true });
  expect(screen.getByRole("textbox", { name: "Message Wavefunction" })).toBe(box);
});

it("keeps a message written while history loads if the history never arrives", () => {
  useCoding.setState({ details: {} });
  render(composer());
  const box = screen.getByRole("textbox", { name: "Message Wavefunction" });
  fireEvent.change(box, { target: { value: "Still here" } });
  fireEvent.keyDown(box, { key: "Enter" });
  expect(send).not.toHaveBeenCalled();
  expect(box).toHaveValue("Still here");
});
