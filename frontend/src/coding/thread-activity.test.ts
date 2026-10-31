import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { codingApi, type CodingSnapshot } from "./api";
import { makeProject, makeThread } from "@/test/fixtures";
import { useThreadActivity } from "./thread-activity";
import { useCoding } from "./store";
const snapshot = (finished: string | null): CodingSnapshot => ({
  agent_id: "agent",
  projects: [],
  access: "sandboxed",
  default_mode: "auto",
  threads: [makeThread({ id: "chat", last_finished_run_id: finished })],
});
beforeEach(() => {
  localStorage.clear();
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.spyOn(useCoding.getState(), "refresh").mockResolvedValue(undefined);
  vi.spyOn(useCoding.getState(), "sync").mockResolvedValue(undefined);
});
afterEach(() => vi.restoreAllMocks());
it("persists an unseen completion across remounts and clears it when that chat is opened", async () => {
  const first = renderHook(({ workspace, viewing }) => useThreadActivity(workspace, viewing), {
    initialProps: {
      workspace: snapshot(null),
      viewing: null as string | null,
    },
  });
  first.rerender({ workspace: snapshot("run-one"), viewing: null });
  expect(first.result.current.has("chat")).toBe(true);
  first.unmount();
  const second = renderHook(({ viewing }) => useThreadActivity(snapshot("run-one"), viewing), {
    initialProps: { viewing: null as string | null },
  });
  expect(second.result.current.has("chat")).toBe(true);
  second.rerender({ viewing: "chat" });
  await waitFor(() => expect(second.result.current.has("chat")).toBe(false));
});
it("leaves a completed result unread, and stops polling, until the selected chat's window regains focus", async () => {
  useCoding.setState({ workspace: snapshot(null), selected: "chat", loading: false });
  const timers = vi.spyOn(window, "setInterval");
  const hook = renderHook(({ workspace }) => useThreadActivity(workspace, "chat"), {
    initialProps: { workspace: snapshot(null) },
  });
  const poll = timers.mock.calls.find(([, delay]) => delay === 5000)![0] as () => void;
  const sync = vi.mocked(useCoding.getState().sync);
  vi.mocked(document.hasFocus).mockReturnValue(false);
  act(() => window.dispatchEvent(new Event("blur")));
  // A visible window in the background does not poll.
  poll();
  expect(sync).not.toHaveBeenCalled();
  hook.rerender({ workspace: snapshot("run-two") });
  expect(hook.result.current.has("chat")).toBe(true);
  vi.mocked(document.hasFocus).mockReturnValue(true);
  act(() => window.dispatchEvent(new Event("focus")));
  expect(sync).toHaveBeenCalledOnce();
  await waitFor(() => expect(hook.result.current.has("chat")).toBe(false));
  poll();
  expect(sync).toHaveBeenCalledTimes(2);
});

it("opens the remembered chat after slow startup before polling or focus can replace its first load", async () => {
  vi.mocked(useCoding.getState().refresh).mockRestore();
  vi.mocked(useCoding.getState().sync).mockRestore();
  vi.useFakeTimers();
  useCoding.setState({ workspace: null, selected: null, details: {}, loading: true, error: null });
  localStorage.setItem("harness.thread", "chat");
  const workspace = snapshot(null);
  let finishFirst!: (value: CodingSnapshot) => void;
  let finishLater!: (value: CodingSnapshot) => void;
  const first = new Promise<CodingSnapshot>((resolve) => {
    finishFirst = resolve;
  });
  const later = new Promise<CodingSnapshot>((resolve) => {
    finishLater = resolve;
  });
  const fetchWorkspace = vi.spyOn(codingApi, "workspace").mockReturnValueOnce(first).mockReturnValue(later);
  vi.spyOn(codingApi, "thread").mockResolvedValue({
    ...workspace.threads[0],
    runs: [],
    older_cursor: null,
    attachments: [],
  });
  const starting = useCoding.getState().initialize();
  const hook = renderHook(() =>
    useThreadActivity(
      useCoding((state) => state.workspace),
      useCoding((state) => state.selected),
    ),
  );
  try {
    act(() => {
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
      vi.advanceTimersByTime(10_000);
    });
    await act(async () => {
      finishFirst(workspace);
      await starting;
    });
    expect(useCoding.getState().error).toBeNull();
    expect(useCoding.getState().loading).toBe(false);
    expect(useCoding.getState().selected).toBe("chat");
    expect(useCoding.getState().details.chat.runs).toEqual([]);
    expect(fetchWorkspace).toHaveBeenCalledOnce();
    await act(async () => {
      finishLater(workspace);
      window.dispatchEvent(new Event("focus"));
    });
    expect(fetchWorkspace).toHaveBeenCalledTimes(2);
  } finally {
    finishLater(workspace);
    hook.unmount();
    vi.useRealTimers();
  }
});

it("leaves startup alone while the chat list from the last launch is showing", async () => {
  vi.resetModules();
  const { codingApi: api } = await import("./api");
  const { useCoding: store } = await import("./store");
  const { useThreadActivity: activity } = await import("./thread-activity");
  localStorage.setItem("harness.thread", "chat");
  localStorage.setItem(
    "wavefunction.workspace",
    JSON.stringify({ ...snapshot(null), projects: [makeProject()] }),
  );
  let finish!: (value: CodingSnapshot) => void;
  const answer = new Promise<CodingSnapshot>((resolve) => {
    finish = resolve;
  });
  const fetchWorkspace = vi.spyOn(api, "workspace").mockReturnValue(answer);
  vi.spyOn(api, "touchThread").mockResolvedValue(undefined);
  vi.spyOn(api, "thread").mockResolvedValue({
    ...snapshot(null).threads[0],
    runs: [],
    older_cursor: null,
    attachments: [],
  });
  vi.useFakeTimers();
  try {
    const starting = store.getState().initialize();
    expect(store.getState().selected).toBe("chat");
    renderHook(() =>
      activity(
        store((state) => state.workspace),
        store((state) => state.selected),
      ),
    );
    act(() => {
      window.dispatchEvent(new Event("focus"));
      vi.advanceTimersByTime(5000);
    });
    expect(fetchWorkspace).toHaveBeenCalledOnce();
    finish({ ...snapshot(null), projects: [makeProject()] });
    await act(async () => starting);
  } finally {
    vi.useRealTimers();
  }
});
