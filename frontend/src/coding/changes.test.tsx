import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ChangesView } from "./changes";
import {
  codingApi,
  type CodingThread,
  type GitFile,
  type GitPatch,
  type GitState,
  type ThreadDetail,
} from "./api";
import { useCoding } from "./store";

vi.mock("./api", () => ({ codingApi: { gitState: vi.fn(), gitDiff: vi.fn() } }));
const thread = {
  id: "one",
  conversation_id: "conversation-one",
  folder: "/repo",
  archived: false,
} as CodingThread;
const file = (path: string, values: Partial<GitFile> = {}): GitFile => ({
  path,
  original_path: null,
  index: " ",
  worktree: "M",
  digest: `digest-${path}`,
  staged: false,
  unstaged: true,
  conflict: false,
  ...values,
});
const initial: GitState = {
  ok: true,
  conflicted: false,
  version: "one",
  head: "head",
  branch: "main",
  upstream: "",
  ahead: 0,
  behind: 0,
  remote: null,
  github: null,
  default_base: "main",
  branches: ["main"],
  history: [],
  identity: { name: "", email: "" },
  files: [
    file("app.py", { index: "M", staged: true }),
    file("staged.txt", { index: "A", worktree: " ", staged: true, unstaged: false }),
    file("new.txt", { index: "?", worktree: "?" }),
  ],
  total_files: 3,
};
const patch = (path: string, before = "original", after = "final"): GitPatch => ({
  ok: true,
  diff: `--- a/${path}\n+++ b/${path}\n@@ -1 +1 @@\n-${before}\n+${after}\n`,
  hunks: [],
  warning: null,
});
beforeEach(() => {
  vi.resetAllMocks();
  useCoding.setState({ details: {} });
  vi.mocked(codingApi.gitState).mockImplementation(async () => structuredClone(initial));
  vi.mocked(codingApi.gitDiff).mockImplementation(async (_id, path) => patch(path));
});

it("browses changed repository files with one final unified diff per file", async () => {
  render(<ChangesView thread={thread} />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading changed files");
  const files = await screen.findByRole("list", { name: "Changed files" });
  expect(within(files).getAllByRole("button")).toHaveLength(3);
  expect(codingApi.gitDiff).not.toHaveBeenCalled();
  fireEvent.click(within(files).getByRole("button", { name: "View changes in app.py" }));
  const diff = await screen.findByLabelText("Inline diff");
  expect(within(diff).getByText("original")).toBeVisible();
  expect(within(diff).getByText("final")).toBeVisible();
  expect(within(diff).getByText("+")).toBeVisible();
  expect(within(diff).getByText("−")).toBeVisible();
  expect(codingApi.gitDiff).toHaveBeenLastCalledWith("one", "app.py", false, true);
  expect(screen.queryByRole("list", { name: "Changed files" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Back to changed files" }));

  fireEvent.click(screen.getByRole("button", { name: "View changes in staged.txt" }));
  await screen.findByLabelText("Inline diff");
  expect(codingApi.gitDiff).toHaveBeenLastCalledWith("one", "staged.txt", false, true);
  fireEvent.click(screen.getByRole("button", { name: "Back to changed files" }));
  vi.mocked(codingApi.gitDiff).mockResolvedValueOnce({
    ok: true,
    diff: "--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+brand new\n",
    hunks: [],
    warning: null,
  });
  fireEvent.click(screen.getByRole("button", { name: "View changes in new.txt" }));
  expect(await screen.findByText("brand new")).toBeVisible();
  expect(codingApi.gitDiff).toHaveBeenLastCalledWith("one", "new.txt", false, true);
  expect(codingApi.gitState).toHaveBeenCalledTimes(1);
});

it("isolates late file and folder responses and refreshes the selected diff after task activity", async () => {
  let finishOldState!: (value: GitState) => void;
  let finishOldDiff!: (value: GitPatch) => void;
  vi.mocked(codingApi.gitState)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishOldState = resolve;
        }),
    )
    .mockResolvedValueOnce({ ...initial, files: [file("other.py")] })
    .mockResolvedValueOnce({ ...initial, files: [file("current.py")] })
    .mockResolvedValueOnce({ ...initial, version: "two", files: [file("current.py")] })
    .mockResolvedValueOnce({ ...initial, version: "three", files: [] });
  vi.mocked(codingApi.gitDiff).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishOldDiff = resolve;
      }),
  );
  const view = render(<ChangesView thread={thread} />);
  const other = { ...thread, id: "two", conversation_id: "conversation-two", folder: "/other" };
  view.rerender(<ChangesView thread={other} />);
  await screen.findByRole("button", { name: "View changes in other.py" });
  await act(async () => finishOldState(initial));
  expect(screen.queryByRole("button", { name: "View changes in app.py" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "View changes in other.py" }));
  expect(screen.getByRole("status")).toHaveTextContent("Loading diff");

  view.rerender(<ChangesView thread={{ ...other, folder: "/new-folder" }} />);
  await screen.findByRole("button", { name: "View changes in current.py" });
  await act(async () => finishOldDiff(patch("other.py", "stale before", "stale after")));
  expect(screen.queryByText("stale after")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "View changes in current.py" }));
  expect(await screen.findByText("final")).toBeVisible();
  vi.mocked(codingApi.gitDiff).mockResolvedValueOnce(patch("current.py", "original", "updated by task"));
  const detail = {
    ...other,
    runs: [
      {
        id: "working",
        status: "running",
        events: [
          {
            id: "tool",
            run_id: "working",
            sequence: 1,
            event_type: "tool_completed",
            payload: {},
            created_at: "",
          },
        ],
      },
    ],
  } as ThreadDetail;
  act(() => useCoding.setState({ details: { two: detail } }));
  expect(await screen.findByText("updated by task")).toBeVisible();
  expect(screen.queryByText("final")).toBeNull();
  expect(codingApi.gitState).toHaveBeenCalledTimes(4);
  act(() =>
    useCoding.setState({
      details: {
        two: {
          ...detail,
          runs: [
            {
              ...detail.runs[0],
              events: [
                ...detail.runs[0].events,
                {
                  node_id: null,
                  id: "text",
                  run_id: "working",
                  sequence: 2,
                  event_type: "assistant_delta",
                  payload: { text: "Working" },
                  created_at: "",
                },
              ],
            },
          ],
        },
      },
    }),
  );
  expect(codingApi.gitState).toHaveBeenCalledTimes(4);
  fireEvent.click(screen.getByRole("button", { name: "Refresh changes" }));
  expect(await screen.findByText("No uncommitted changes.")).toBeVisible();
  expect(screen.queryByRole("region", { name: "Changes in current.py" })).toBeNull();
});

it("recovers from loading failures and explains unavailable file previews without stale content", async () => {
  const unavailable = {
    ...initial,
    files: [
      file("image.png"),
      file("large.txt"),
      file("recreated.txt", { index: "D", worktree: " ", staged: true, unstaged: false }),
      file("recreated.txt", { index: "?", worktree: "?" }),
    ],
  };
  vi.mocked(codingApi.gitState)
    .mockRejectedValueOnce(new Error("Repository unavailable"))
    .mockImplementation(async () => structuredClone(unavailable));
  render(<ChangesView thread={thread} />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Repository unavailable");
  expect(screen.queryByText("No uncommitted changes.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Refresh changes" }));
  await screen.findByRole("list", { name: "Changed files" });
  expect(screen.getAllByRole("button", { name: "View changes in recreated.txt" })).toHaveLength(1);
  expect(screen.queryByRole("alert")).toBeNull();
  vi.mocked(codingApi.gitDiff).mockRejectedValueOnce(new Error("File changed while reading"));
  fireEvent.click(screen.getByRole("button", { name: "View changes in image.png" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("File changed while reading");
  vi.mocked(codingApi.gitDiff).mockResolvedValueOnce({
    ok: true,
    diff: "diff --git a/image.png b/image.png\nBinary files a/image.png and b/image.png differ\n",
    hunks: [],
    warning: null,
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh changes" }));
  expect(await screen.findByText("Binary file changed. A text diff is not available.")).toBeVisible();
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Back to changed files" }));
  vi.mocked(codingApi.gitDiff).mockResolvedValueOnce({
    ok: true,
    diff: "",
    hunks: [],
    warning: "Diff exceeds the preview limit.",
  });
  fireEvent.click(screen.getByRole("button", { name: "View changes in large.txt" }));
  expect(await screen.findByText("Diff exceeds the preview limit.")).toBeVisible();
  expect(screen.queryByText("Binary file changed. A text diff is not available.")).toBeNull();
  expect(screen.queryByText("No text changes remain for this file.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Back to changed files" }));
  vi.mocked(codingApi.gitDiff).mockResolvedValueOnce({
    ok: true,
    diff: "",
    hunks: [],
    warning: "The recreated file cannot be previewed safely.",
  });
  fireEvent.click(screen.getByRole("button", { name: "View changes in recreated.txt" }));
  expect(await screen.findByText("The recreated file cannot be previewed safely.")).toBeVisible();
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
});
