import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { codingApi, type CodingRun } from "./api";
import { api, streamRunEvents } from "@/lib/api";
import type { Run, RunEvent, Schemas } from "@/lib/types";
import { makeEvent, makeProject, makeRun, makeThread } from "@/test/fixtures";

vi.mock("./api", () => ({
  isActive: (status: string) =>
    ["running", "queued", "waiting_approval", "waiting_connector", "waiting_computer"].includes(status),
  codingApi: {
    workspace: vi.fn(),
    thread: vi.fn(),
    touchThread: vi.fn(),
    history: vi.fn(),
    createThread: vi.fn(),
    patch: vi.fn(),
    remove: vi.fn(),
    send: vi.fn(),
    fork: vi.fn(),
    rewind: vi.fn(),
  },
}));
vi.mock("@/lib/api", () => ({
  api: { cancelRun: vi.fn() },
  streamRunEvents: vi.fn(),
}));
let store: typeof import("./store");
const streams = new Map<string, (event: RunEvent) => void>();
// The same object serves as a chat's sidebar entry and as its opened history.
type Detail = Schemas["ThreadDetail"] & Schemas["SidebarThread"];
function thread(id: string) {
  return makeThread({ id });
}
function detail(id: string, status: CodingRun["status"] = "completed"): Detail {
  return {
    ...thread(id),
    configured_folder: null,
    older_cursor: null,
    attachments: [],
    runs: [
      makeRun({
        id: `run-${id}`,
        status,
        input: { text: `Input ${id}` },
        output: status === "completed" ? { text: `Reply ${id}` } : null,
      }),
    ],
  };
}
function event(id: string, sequence: number): RunEvent {
  return makeEvent({
    id: `event-${sequence}`,
    run_id: `run-${id}`,
    sequence,
    payload: { text: `${id}:${sequence}` },
  });
}
function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((finish) => {
    resolve = finish;
  });
  return { promise, resolve };
}
beforeEach(async () => {
  vi.resetModules();
  vi.resetAllMocks();
  localStorage.clear();
  streams.clear();
  vi.mocked(codingApi.workspace).mockResolvedValue({
    agent_id: "one-agent",
    threads: [thread("a"), thread("b")],
    projects: [],
    access: "sandboxed",
    default_mode: "auto",
  });
  vi.mocked(codingApi.thread).mockImplementation(async (id) => detail(id));
  vi.mocked(codingApi.touchThread).mockResolvedValue(undefined);
  vi.mocked(streamRunEvents).mockImplementation(async (run, _after, emit, signal) => {
    streams.set(run, emit);
    await new Promise<void>((resolve) => signal?.addEventListener("abort", () => resolve(), { once: true }));
  });
  store = await import("./store");
});
afterEach(() => store?.stopFollowing());

describe("independent coding conversations", () => {
  it("reuses recent history but refreshes a chat whose workspace summary changed", async () => {
    await store.useCoding.getState().refresh();
    await store.useCoding.getState().select("a");
    await store.useCoding.getState().select("b");
    const cached = store.useCoding.getState().details.a;
    await store.useCoding.getState().select("a");
    expect(store.useCoding.getState().details.a).toBe(cached);
    expect(codingApi.thread).toHaveBeenCalledTimes(2);
    await store.useCoding.getState().select("b");
    const changed = { ...detail("a"), updated_at: "2026-10-04T00:00:00Z" };
    vi.mocked(codingApi.workspace).mockResolvedValue({
      agent_id: "one-agent",
      projects: [],
      access: "sandboxed",
      default_mode: "auto",
      threads: [changed, thread("b")],
    });
    await store.useCoding.getState().refresh();
    expect(codingApi.thread).toHaveBeenCalledTimes(2);
    vi.mocked(codingApi.thread).mockResolvedValueOnce(changed);
    await store.useCoding.getState().select("a");
    expect(store.useCoding.getState().details.a.updated_at).toBe(changed.updated_at);
    expect(codingApi.thread).toHaveBeenCalledTimes(3);
    expect(vi.mocked(codingApi.touchThread).mock.calls).toEqual([["a"], ["b"], ["a"], ["b"], ["a"]]);

    // A shared helper checkout can disappear or return without its metadata changing.
    vi.mocked(codingApi.touchThread).mockClear();
    for (const worktree_status of ["deleted", "active"] as const) {
      const checkout = { ...changed, worktree_status };
      vi.mocked(codingApi.workspace).mockResolvedValue({
        agent_id: "one-agent",
        projects: [],
        access: "sandboxed",
        default_mode: "auto",
        threads: [checkout, thread("b")],
      });
      vi.mocked(codingApi.thread).mockResolvedValueOnce(checkout);
      await store.useCoding.getState().refresh();
      expect(store.useCoding.getState().details.a.worktree_status).toBe(worktree_status);
    }
    expect(codingApi.thread).toHaveBeenCalledTimes(5);
    expect(codingApi.touchThread).not.toHaveBeenCalled();

    // Opening a cached chat must also notice expiry before the next workspace poll.
    await store.useCoding.getState().select("b");
    const expired = { ...changed, worktree_status: "deleted" as const };
    vi.mocked(codingApi.workspace).mockResolvedValue({
      agent_id: "one-agent",
      projects: [],
      access: "sandboxed",
      default_mode: "auto",
      threads: [expired, thread("b")],
    });
    vi.mocked(codingApi.thread).mockResolvedValueOnce(expired);
    expect(store.useCoding.getState().workspace?.threads[0].worktree_status).toBe("active");
    await store.useCoding.getState().select("a");
    expect(store.useCoding.getState().details.a.worktree_status).toBe("deleted");
    expect(codingApi.thread).toHaveBeenCalledTimes(6);

    let rejectActivity!: (error: Error) => void;
    vi.mocked(codingApi.touchThread).mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectActivity = reject;
        }),
    );
    await store.useCoding.getState().select("b");
    expect(store.useCoding.getState().selected).toBe("b");
    store.useCoding.setState({ error: "Unrelated error" });
    rejectActivity(new Error("Activity unavailable"));
    await Promise.resolve();
    expect(store.useCoding.getState().error).toBe("Unrelated error");
  });
  it("bounds idle history by recency while retaining active work and unsent drafts", async () => {
    vi.mocked(codingApi.thread).mockImplementation(async (id) =>
      detail(id, id === "active" ? "running" : "completed"),
    );
    await store.useCoding.getState().select("active");
    localStorage.setItem("harness.draft.a", "Keep my draft");
    for (const id of ["a", "b", "c", "d", "e", "a", "f"]) await store.useCoding.getState().select(id);
    expect(Object.keys(store.useCoding.getState().details).sort()).toEqual([
      "a",
      "active",
      "c",
      "d",
      "e",
      "f",
    ]);
    expect(codingApi.thread).toHaveBeenCalledTimes(7);
    streams.get("run-active")!(event("active", 1));
    expect(store.useCoding.getState().details.active.runs[0].events).toHaveLength(1);
    await store.useCoding.getState().select("b");
    expect(codingApi.thread).toHaveBeenCalledTimes(8);
    expect(Object.keys(store.useCoding.getState().details)).toHaveLength(6);
    expect(localStorage.getItem("harness.draft.a")).toBe("Keep my draft");
  });
  it("creates only one initial conversation when React mounts twice", async () => {
    vi.mocked(codingApi.workspace).mockResolvedValue({
      agent_id: "one-agent",
      threads: [],
      projects: [makeProject({ id: "first", name: "first" })],
      access: "sandboxed",
      default_mode: "auto",
    });
    vi.mocked(codingApi.createThread).mockResolvedValue(thread("a"));
    await Promise.all([store.useCoding.getState().initialize(), store.useCoding.getState().initialize()]);
    expect(codingApi.createThread).toHaveBeenCalledOnce();
    expect(store.useCoding.getState().selected).toBe("a");
  });
  it.each(["selected", "orphan", "empty"])(
    "uses a real project for automatic new chats when selection is %s",
    async (selection) => {
      const projects = ["first", "current"].map((id) => makeProject({ id, name: id }));
      store.useCoding.setState({
        workspace: {
          agent_id: "one-agent",
          threads: [{ ...thread("a"), project_id: selection === "selected" ? "current" : "removed" }],
          projects,
          access: "sandboxed",
          default_mode: "auto",
        },
        selected: selection === "empty" ? null : "a",
      });
      vi.mocked(codingApi.createThread).mockResolvedValue(thread("new"));
      await store.useCoding.getState().newThread();
      expect(codingApi.createThread).toHaveBeenCalledWith({
        project_id: selection === "selected" ? "current" : "first",
      });
    },
  );
  it("opens navigation before startup history and keeps late responses with their original thread", async () => {
    const late = pending<Detail>();
    vi.mocked(codingApi.thread).mockImplementation(async (id) => (id === "a" ? late.promise : detail(id)));
    localStorage.setItem("harness.draft.a", "Keep my startup draft");
    const selectingA = store.useCoding.getState().initialize();
    await vi.waitFor(() => expect(codingApi.thread).toHaveBeenCalledWith("a"));
    expect(store.useCoding.getState().loading).toBe(false);
    expect(store.useCoding.getState().selected).toBe("a");
    expect(store.useCoding.getState().details.a).toBeUndefined();
    await store.useCoding.getState().select("b");
    late.resolve(detail("a", "running"));
    await selectingA;
    streams.get("run-a")!(event("a", 1));
    expect(store.useCoding.getState().selected).toBe("b");
    expect(store.useCoding.getState().details.a.runs[0].events).toHaveLength(1);
    expect(store.useCoding.getState().details.b.runs[0].events).toHaveLength(0);
    expect(localStorage.getItem("harness.draft.a")).toBe("Keep my startup draft");
  });
  it("keeps new events that arrive during an older polling snapshot, deduplicates them, and follows the run's usage", async () => {
    vi.mocked(codingApi.thread).mockResolvedValue(detail("a", "running"));
    await store.useCoding.getState().load("a");
    const late = pending<Detail>();
    vi.mocked(codingApi.thread).mockReturnValueOnce(late.promise);
    const loading = store.useCoding.getState().load("a");
    // A finished model call brings the run's total so far, so its token count stays current,
    // and the older snapshot that lands after it does not roll that total back.
    const finished = {
      ...event("a", 2),
      event_type: "model_call_finished",
      payload: { usage: [{ input_tokens: 90 }], run_usage: { input_tokens: 190, total_tokens: 200 } },
    };
    streams.get("run-a")!(finished);
    streams.get("run-a")!(finished);
    expect(store.useCoding.getState().details.a.runs[0].usage).toEqual({
      input_tokens: 190,
      total_tokens: 200,
    });
    late.resolve({
      ...detail("a", "running"),
      runs: [{ ...detail("a", "running").runs[0], events: [event("a", 1)], usage: { total_tokens: 100 } }],
    });
    await loading;
    const run = store.useCoding.getState().details.a.runs[0];
    expect(run.events.map((e) => e.sequence)).toEqual([1, 2]);
    expect(run.usage).toEqual({ input_tokens: 190, total_tokens: 200 });
    expect(streamRunEvents).toHaveBeenCalledOnce();
  });
  it("ignores stale snapshots that finish after a newer load", async () => {
    const old = pending<Detail>();
    vi.mocked(codingApi.thread).mockReturnValueOnce(old.promise).mockResolvedValueOnce(detail("a"));
    const loading = store.useCoding.getState().load("a");
    await store.useCoding.getState().load("a");
    old.resolve(detail("a", "running"));
    await loading;
    expect(store.useCoding.getState().details.a.runs[0].status).toBe("completed");
    expect(streamRunEvents).not.toHaveBeenCalled();
  });
  it("merges older pages around the active owner without losing live events or changing selection", async () => {
    const owner = { ...detail("a", "running").runs[0], history_order: 1 };
    const saved = (order: number) => ({ ...detail("a").runs[0], id: `saved-${order}`, history_order: order });
    vi.mocked(codingApi.thread).mockResolvedValueOnce({
      ...detail("a"),
      runs: [owner, saved(51), saved(52)],
      older_cursor: "saved-51",
    });
    await store.useCoding.getState().select("a");
    const late = pending<Awaited<ReturnType<typeof codingApi.history>>>();
    vi.mocked(codingApi.history).mockReturnValueOnce(late.promise);
    const loading = store.useCoding.getState().loadOlder("a");
    await store.useCoding.getState().select("b");
    streams.get("run-a")!(event("a", 1));
    late.resolve({ runs: [owner, saved(2)], older_cursor: null });
    await loading;
    expect(codingApi.history).toHaveBeenCalledWith("a", "saved-51");
    expect(store.useCoding.getState().selected).toBe("b");
    expect(store.useCoding.getState().details.a.runs.map((run) => run.id)).toEqual([
      "run-a",
      "saved-2",
      "saved-51",
      "saved-52",
    ]);
    expect(store.useCoding.getState().details.a.runs[0].events).toEqual([event("a", 1)]);
    vi.mocked(codingApi.thread).mockResolvedValueOnce({
      ...detail("a"),
      runs: [owner, saved(51), saved(52), saved(53)],
      older_cursor: "saved-51",
    });
    await store.useCoding.getState().load("a");
    expect(store.useCoding.getState().details.a.runs.map((run) => run.id)).toEqual([
      "run-a",
      "saved-2",
      "saved-51",
      "saved-52",
      "saved-53",
    ]);
    expect(store.useCoding.getState().details.a.older_cursor).toBeNull();
    expect(store.useCoding.getState().details.a.runs[0].events).toHaveLength(1);
    expect(store.useCoding.getState().details.b).toEqual(detail("b"));
  });
  it("polls only metadata and restarts pagination when an unseen gap exceeds the latest page", async () => {
    const saved = (order: number) => ({ ...detail("a").runs[0], id: `saved-${order}`, history_order: order });
    vi.mocked(codingApi.thread).mockResolvedValueOnce({
      ...detail("a"),
      runs: [saved(10), saved(11)],
      older_cursor: "saved-10",
    });
    vi.mocked(codingApi.workspace).mockResolvedValue({
      agent_id: "one-agent",
      projects: [],
      access: "sandboxed",
      default_mode: "auto",
      threads: [{ ...thread("a"), last_run_id: "saved-11" }],
    });
    await store.useCoding.getState().select("a");
    await store.useCoding.getState().sync("a");
    expect(codingApi.thread).toHaveBeenCalledOnce();
    vi.mocked(codingApi.workspace).mockResolvedValue({
      agent_id: "one-agent",
      projects: [],
      access: "sandboxed",
      default_mode: "auto",
      threads: [{ ...thread("a"), last_run_id: "saved-81" }],
    });
    vi.mocked(codingApi.thread).mockResolvedValueOnce({
      ...detail("a"),
      runs: [saved(80), saved(81)],
      older_cursor: "saved-80",
    });
    await store.useCoding.getState().sync("a");
    expect(store.useCoding.getState().details.a.runs.map((run) => run.id)).toEqual(["saved-80", "saved-81"]);
    expect(store.useCoding.getState().details.a.older_cursor).toBe("saved-80");
    await store.useCoding.getState().sync("a");
    expect(codingApi.thread).toHaveBeenCalledTimes(2);
    expect(codingApi.history).not.toHaveBeenCalled();
  });
  it("keeps the older cursor when only an injected active owner overlaps a long queue", async () => {
    const owner = { ...detail("a", "running").runs[0], history_order: 1 };
    const queued = (order: number) => ({
      ...owner,
      id: `queued-${order}`,
      status: "queued" as const,
      history_order: order,
    });
    vi.mocked(codingApi.thread).mockResolvedValueOnce({ ...detail("a"), runs: [owner], older_cursor: null });
    await store.useCoding.getState().select("a");
    vi.mocked(codingApi.thread).mockResolvedValueOnce({
      ...detail("a"),
      runs: [owner, ...Array.from({ length: 50 }, (_, i) => queued(i + 14))],
      older_cursor: "queued-14",
    });
    await store.useCoding.getState().load("a");
    expect(store.useCoding.getState().details.a.older_cursor).toBe("queued-14");
    vi.mocked(codingApi.history).mockResolvedValueOnce({
      runs: [owner, ...Array.from({ length: 12 }, (_, i) => queued(i + 2))],
      older_cursor: null,
    });
    await store.useCoding.getState().loadOlder("a");
    expect(store.useCoding.getState().details.a.runs.map((run) => run.id)).toEqual([
      owner.id,
      ...Array.from({ length: 62 }, (_, i) => `queued-${i + 2}`),
    ]);
    expect(store.useCoding.getState().details.a.older_cursor).toBeNull();
  });
  it("reloads an inherited folder or project change even when run metadata and chat timestamps are unchanged", async () => {
    const original = { ...detail("a"), project_id: "project", folder: "/repo-a" };
    vi.mocked(codingApi.thread).mockResolvedValueOnce(original);
    await store.useCoding.getState().select("a");
    const changed = { ...original, folder: "/repo-b" };
    vi.mocked(codingApi.workspace).mockResolvedValue({
      agent_id: "one-agent",
      projects: [],
      access: "sandboxed",
      default_mode: "auto",
      threads: [changed],
    });
    vi.mocked(codingApi.thread).mockResolvedValueOnce(changed);
    await store.useCoding.getState().refresh();
    expect(store.useCoding.getState().details.a.folder).toBe("/repo-b");
    expect(codingApi.thread).toHaveBeenCalledTimes(2);
    await store.useCoding.getState().refresh();
    expect(codingApi.thread).toHaveBeenCalledTimes(2);
    // A project made again for its folder takes the chat back.
    const returned = { ...changed, project_id: "returned" };
    vi.mocked(codingApi.workspace).mockResolvedValue({
      agent_id: "one-agent",
      projects: [],
      access: "sandboxed",
      default_mode: "auto",
      threads: [returned],
    });
    vi.mocked(codingApi.thread).mockResolvedValueOnce(returned);
    await store.useCoding.getState().refresh();
    expect(store.useCoding.getState().details.a.project_id).toBe("returned");
    // A task paused for the user has no stream, nor news in its queued follow-up's: a helper that
    // moves on reloads it.
    const paused = {
      ...returned,
      runs: [...detail("a", "waiting_approval").runs, { ...detail("a", "queued").runs[0], id: "run-a-next" }],
    };
    const waiting = { ...returned, status: "waiting_approval" as const, active_run_id: "run-a" };
    const helper = (status: Schemas["SidebarThread"]["status"], pause_sequence: number | null = null) => ({
      ...thread("helper"),
      parent_thread_id: "a",
      status,
      pause_sequence,
    });
    for (const moved of [
      helper("running"),
      helper("running"),
      helper("waiting_approval", 3),
      helper("waiting_approval", 7),
      helper("idle"),
    ]) {
      vi.mocked(codingApi.workspace).mockResolvedValue({
        agent_id: "one-agent",
        projects: [],
        access: "sandboxed",
        default_mode: "auto",
        threads: [waiting, moved],
      });
      vi.mocked(codingApi.thread).mockResolvedValue(paused);
      await store.useCoding.getState().refresh();
    }
    expect(codingApi.thread).toHaveBeenCalledTimes(7);
    // The chat itself moves from one pause to another, as when its card is answered elsewhere.
    const next = {
      ...returned,
      status: "waiting_approval" as const,
      active_run_id: "run-a",
      pause_sequence: 9,
    };
    vi.mocked(codingApi.workspace).mockResolvedValue({
      agent_id: "one-agent",
      projects: [],
      access: "sandboxed",
      default_mode: "auto",
      threads: [next, helper("idle")],
    });
    vi.mocked(codingApi.thread).mockResolvedValue({
      ...paused,
      runs: [{ ...paused.runs[0], events: [{ ...event("a", 9), event_type: "run_waiting_for_approval" }] }],
    });
    await store.useCoding.getState().refresh();
    await store.useCoding.getState().refresh();
    expect(codingApi.thread).toHaveBeenCalledTimes(8);
    expect(vi.mocked(streamRunEvents).mock.calls.map(([run]) => run)).toEqual(["run-a-next"]);
  });
  it("backs off a failed stream instead of recursively reopening it", async () => {
    vi.mocked(codingApi.thread).mockResolvedValue(detail("a", "running"));
    vi.mocked(streamRunEvents).mockRejectedValue(new Error("Connection lost"));
    await store.useCoding.getState().load("a");
    await vi.waitFor(() => expect(store.useCoding.getState().error).toBe("Connection lost"));
    await store.useCoding.getState().load("a");
    expect(streamRunEvents).toHaveBeenCalledOnce();
    expect(codingApi.thread).toHaveBeenCalledTimes(2);
  });
  it("takes a deleted chat and its helpers off the list at once, without resurrecting it or hiding a refused deletion", async () => {
    const helper = { ...thread("helper"), parent_thread_id: "a" };
    const snapshot = (threads: Schemas["SidebarThread"][]) => ({
      agent_id: "one-agent",
      projects: [],
      access: "sandboxed" as const,
      default_mode: "auto" as const,
      threads,
    });
    vi.mocked(codingApi.workspace).mockResolvedValue(
      snapshot([thread("a"), helper, thread("b"), thread("c")]),
    );
    await store.useCoding.getState().refresh();
    await store.useCoding.getState().select("helper");
    const listed = () => store.useCoding.getState().workspace!.threads.map((item) => item.id);
    // Deletion can take the backend a while with many helpers: the chat and its helpers leave the
    // list at once, and a refresh meanwhile does not bring them back.
    const removal = pending<void>();
    vi.mocked(codingApi.remove).mockReturnValueOnce(removal.promise);
    const late = pending<Detail>();
    vi.mocked(codingApi.thread).mockReturnValueOnce(late.promise);
    const loading = store.useCoding.getState().load("a");
    const removing = store.useCoding.getState().remove("a", true);
    await vi.waitFor(() => expect(store.useCoding.getState().selected).toBe("b"));
    expect(listed()).toEqual(["b", "c"]);
    await store.useCoding.getState().refresh();
    expect(listed()).toEqual(["b", "c"]);
    vi.mocked(codingApi.workspace).mockResolvedValue(snapshot([thread("b"), thread("c")]));
    removal.resolve();
    await removing;
    expect(codingApi.remove).toHaveBeenCalledExactlyOnceWith("a", true);
    // A response already in flight does not resurrect it.
    late.resolve(detail("a", "running"));
    await loading;
    expect(store.useCoding.getState().details.a).toBeUndefined();
    expect(streamRunEvents).not.toHaveBeenCalled();
    // A refused deletion lists the chat again, with its reason.
    vi.mocked(codingApi.remove).mockRejectedValueOnce(new Error("A task is still using this worktree."));
    await store.useCoding.getState().remove("c");
    expect(listed()).toEqual(["b", "c"]);
    expect(store.useCoding.getState().error).toBe("A task is still using this worktree.");
  });
  it("refreshes the stopped run's owner when selection changes during cancellation", async () => {
    await store.useCoding.getState().select("a");
    await store.useCoding.getState().load("b");
    const stopped = pending<void>();
    vi.mocked(api.cancelRun).mockReturnValue(stopped.promise as never);
    const stopping = store.useCoding.getState().stop("run-a");
    await store.useCoding.getState().select("b");
    stopped.resolve();
    await stopping;
    expect(codingApi.thread).toHaveBeenLastCalledWith("a");
    expect(store.useCoding.getState().selected).toBe("b");
  });
  it("rewinds only its captured chat, fences old history and streams, and preserves displaced drafts", async () => {
    // A chat on its project's folder: the rewind response carries its stored folder, null.
    const original = { ...detail("a", "running"), folder: "/repo", older_cursor: "same-cursor" };
    original.runs[0].events = [event("a", 1), event("a", 2)];
    const restored = {
      ...detail("a"),
      folder: "/repo",
      conversation_id: "restored-a",
      older_cursor: "same-cursor",
    };
    restored.runs[0].events = [event("a", 1)];
    let finished = false;
    vi.mocked(codingApi.thread).mockImplementation(async (id) =>
      id === "a" ? (finished ? restored : original) : detail(id),
    );
    await store.useCoding.getState().select("a");
    const emitOld = streams.get("run-a")!;
    const snapshot = pending<Detail>();
    vi.mocked(codingApi.thread).mockReturnValueOnce(snapshot.promise);
    const loading = store.useCoding.getState().load("a");
    const history = pending<Awaited<ReturnType<typeof codingApi.history>>>();
    vi.mocked(codingApi.history).mockReturnValueOnce(history.promise);
    const loadingOlder = store.useCoding.getState().loadOlder("a");
    const response = pending<Awaited<ReturnType<typeof codingApi.rewind>>>();
    vi.mocked(codingApi.rewind).mockReturnValueOnce(response.promise);
    const context = { files: [], skills: ["review"], command: null };
    localStorage.setItem("harness.draft.a", "Keep this unsent draft");
    localStorage.setItem("harness.context.a./repo", JSON.stringify(context));
    localStorage.setItem("harness.file.a./repo.app.ts", "unsaved file edit");
    const rewinding = store.useCoding.getState().rewind("a", "checkpoint");
    expect(codingApi.rewind).toHaveBeenCalledWith("a", "checkpoint");
    await store.useCoding.getState().select("b");
    finished = true;
    const edited = { files: [], skills: ["edited-message"], command: null };
    response.resolve({
      thread: { ...restored, folder: null },
      draft: { content: "Earlier request", files: [], context: edited },
      archived_thread_id: "archive-a",
    });
    await rewinding;
    emitOld(event("a", 3));
    snapshot.resolve(original);
    history.resolve({ runs: [{ ...original.runs[0], id: "older-run" }], older_cursor: null });
    await Promise.all([loading, loadingOlder]);
    expect(store.useCoding.getState().details.a).toEqual(restored);
    expect(store.useCoding.getState().selected).toBe("b");
    expect(localStorage.getItem("harness.draft.a")).toBe("Earlier request");
    expect(localStorage.getItem("harness.draft.archive-a")).toBe("Keep this unsent draft");
    expect(localStorage.getItem("harness.context.a./repo")).toBe(JSON.stringify(edited));
    expect(localStorage.getItem("harness.context.archive-a./repo")).toBe(JSON.stringify(context));
    expect(localStorage.getItem("harness.file.a./repo.app.ts")).toBe("unsaved file edit");
    expect(store.useCoding.getState().restoring.a).toBe(false);
  });
  it("keeps history and drafts usable after a conflicting rewind", async () => {
    const original = detail("a", "running");
    vi.mocked(codingApi.thread).mockResolvedValue(original);
    await store.useCoding.getState().select("a");
    localStorage.setItem("harness.draft.a", "Unsent edits");
    vi.mocked(codingApi.rewind).mockRejectedValue(new Error("app.ts changed since this task"));
    await expect(store.useCoding.getState().rewind("a", "checkpoint")).rejects.toThrow(
      "app.ts changed since this task",
    );
    expect(store.useCoding.getState().details.a).toEqual(original);
    expect(store.useCoding.getState().error).toBe("app.ts changed since this task");
    expect(store.useCoding.getState().restoring.a).toBe(false);
    expect(localStorage.getItem("harness.draft.a")).toBe("Unsent edits");
    streams.get("run-a")!(event("a", 1));
    expect(store.useCoding.getState().details.a.runs[0].events).toEqual([event("a", 1)]);
    expect(streamRunEvents).toHaveBeenCalledTimes(2);
  });
  it("prefills the fork's captured message and context without changing the source draft", async () => {
    const context = { files: [], skills: ["review"], command: "check" };
    localStorage.setItem("harness.draft.a", "Source draft");
    vi.mocked(codingApi.fork).mockResolvedValue({
      ...thread("forked"),
      draft: { content: "Retry this request", files: [], context },
    });
    await store.useCoding.getState().fork("a", { checkpoint_id: "before-user", worktree: true });
    expect(codingApi.fork).toHaveBeenCalledWith("a", { checkpoint_id: "before-user", worktree: true });
    expect(store.useCoding.getState().selected).toBe("forked");
    expect(localStorage.getItem("harness.draft.forked")).toBe("Retry this request");
    expect(localStorage.getItem("harness.context.forked.null")).toBe(JSON.stringify(context));
    expect(localStorage.getItem("harness.draft.a")).toBe("Source draft");
  });
  it("shows a message in its chat while it is sent, clears its draft, and gives back a refused one", async () => {
    const original = detail("a");
    const context = { files: [], skills: [], command: null };
    const file = { id: "spec", name: "spec.pdf", media_type: "application/pdf", size_bytes: 10 };
    store.useCoding.setState({ details: { a: { ...original, attachments: [file] } } });
    localStorage.setItem("harness.draft.a", "First request");
    localStorage.setItem("harness.context.a.null", JSON.stringify(context));
    vi.mocked(codingApi.thread).mockResolvedValue({ ...original, folder: "/worktree" });
    const accepted = pending<Run>();
    vi.mocked(codingApi.send).mockReturnValueOnce(accepted.promise);
    const options = { worktree: true, worktree_base: "refs/heads/feature" };
    const sending = store.useCoding.getState().send("a", "First request", context, options);
    // In the chat at once, with its files; its saved draft goes, for a composer that mounts meanwhile.
    expect(store.useCoding.getState().pending.a).toMatchObject({
      text: "First request",
      context,
      files: [file],
    });
    expect(localStorage.getItem("harness.draft.a")).toBeNull();
    expect(localStorage.getItem("harness.context.a.null")).toBeNull();
    accepted.resolve({ id: "run-a" } as Run);
    await sending;
    expect(codingApi.send).toHaveBeenLastCalledWith("a", "First request", context, options);
    // The history that has it takes the placeholder's place.
    expect(store.useCoding.getState().pending.a).toBeUndefined();
    expect(store.useCoding.getState().details.a.folder).toBe("/worktree");
    // A command sent alone shows as the command.
    store.useCoding.setState({ details: { a: original } });
    const command = { ...context, command: "review" };
    const reviewing = store.useCoding.getState().send("a", "", command);
    expect(store.useCoding.getState().pending.a).toMatchObject({ text: "/review" });
    await reviewing;
    localStorage.setItem("harness.draft.a", "Later edits");
    await store.useCoding.getState().send("a", "First request", context, { worktree: true });
    expect(localStorage.getItem("harness.draft.a")).toBe("Later edits");

    // Refused, the message goes back to the box it left, and its chips with the chat to its new worktree.
    store.useCoding.setState({ details: { a: original }, selected: "b" });
    localStorage.setItem("harness.draft.a", "First request");
    localStorage.setItem("harness.context.a.null", JSON.stringify(context));
    vi.mocked(codingApi.thread).mockResolvedValue({
      ...original,
      folder: "/feature-worktree",
      worktree: true,
    });
    const failure = new Error("Attached file changed in the selected branch");
    vi.mocked(codingApi.send).mockRejectedValueOnce(failure);
    vi.mocked(codingApi.workspace).mockResolvedValueOnce({
      threads: [{ ...original, folder: "/feature-worktree" }],
    } as never);
    await expect(store.useCoding.getState().send("a", "First request", context, options)).rejects.toBe(
      failure,
    );
    expect(store.useCoding.getState().pending.a).toBeUndefined();
    expect(store.useCoding.getState().unsent.a).toEqual({ text: "First request", context });
    expect(localStorage.getItem("harness.draft.a")).toBe("First request");
    expect(localStorage.getItem("harness.context.a.null")).toBe(JSON.stringify(context));
    expect(localStorage.getItem("harness.context.a./feature-worktree")).toBe(JSON.stringify(context));
    expect(store.useCoding.getState().details.a).toMatchObject({
      folder: "/feature-worktree",
      worktree: true,
    });
    expect(store.useCoding.getState().selected).toBe("b");
    expect(store.useCoding.getState().error).toBe(failure.message);
    expect(store.useCoding.getState().sending.a).toBe(false);
  });
});

describe("the saved workspace", () => {
  const workspace = (threads: Schemas["SidebarThread"][]) => ({
    agent_id: "one-agent",
    threads,
    projects: [makeProject()],
    access: "sandboxed" as const,
    default_mode: "auto" as const,
  });

  it("opens the last chat before the engine answers, then takes the engine's list", async () => {
    localStorage.setItem("wavefunction.workspace", JSON.stringify(workspace([thread("a"), thread("b")])));
    localStorage.setItem("harness.thread", "b");
    const answer = pending<ReturnType<typeof workspace>>();
    vi.mocked(codingApi.workspace).mockReturnValue(answer.promise);

    const opening = store.useCoding.getState().initialize();
    expect(store.useCoding.getState()).toMatchObject({ loading: false, selected: "b" });
    expect(store.useCoding.getState().workspace?.threads.map((item) => item.id)).toEqual(["a", "b"]);

    answer.resolve(workspace([thread("c")]));
    await opening;
    expect(store.useCoding.getState().selected).toBe("c");
    expect(store.useCoding.getState().workspace?.threads.map((item) => item.id)).toEqual(["c"]);
  });

  it("gives up the saved view when the engine fails", async () => {
    localStorage.setItem("wavefunction.workspace", JSON.stringify(workspace([thread("a")])));
    localStorage.setItem("harness.thread", "a");
    vi.mocked(codingApi.workspace).mockRejectedValue(new Error("The engine stopped"));

    await store.useCoding.getState().initialize();
    expect(store.useCoding.getState()).toMatchObject({ workspace: null, selected: null, loading: false });
    expect(store.useCoding.getState().error).toContain("The engine stopped");
  });

  it("saves a bounded list without archived chats", async () => {
    const many = Array.from({ length: 700 }, (_, index) => thread(`chat-${index}`));
    localStorage.setItem("harness.thread", "chat-1");
    vi.mocked(codingApi.workspace).mockResolvedValue(
      workspace([{ ...thread("old"), archived: true }, ...many]),
    );

    await store.useCoding.getState().initialize();
    const saved = JSON.parse(localStorage.getItem("wavefunction.workspace")!);
    expect(saved.threads).toHaveLength(500);
    expect(saved.threads.some((item: { id: string }) => item.id === "old")).toBe(false);
  });

  it("keeps the engine's list for next time and ignores a save it can't use", async () => {
    localStorage.setItem("wavefunction.workspace", "{not json");
    localStorage.setItem("harness.thread", "a");
    vi.mocked(codingApi.workspace).mockResolvedValue(workspace([thread("a")]));

    const opening = store.useCoding.getState().initialize();
    expect(store.useCoding.getState().loading).toBe(true);
    await opening;
    const saved = JSON.parse(localStorage.getItem("wavefunction.workspace")!);
    expect(saved.threads.map((item: { id: string }) => item.id)).toEqual(["a"]);
  });
});
