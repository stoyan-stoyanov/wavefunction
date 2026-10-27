import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { codingApi, type CodingThread } from "./api";
import { Workbench } from "./workbench";

vi.mock("./api", () => ({
  codingApi: { tree: vi.fn(), file: vi.fn(), save: vi.fn() },
}));
vi.mock("./terminal", () => ({
  TerminalView: ({ thread }: { thread: CodingThread }) => <p>Terminal for {thread.id}</p>,
}));
vi.mock("./changes", () => ({
  ChangesView: ({ thread }: { thread: CodingThread }) => <p>Changes for {thread.id}</p>,
}));

const thread = {
  id: "thread-a",
  folder: "/repo",
  archived: false,
} as CodingThread;

beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(codingApi.tree).mockResolvedValue([{ name: "app.py", path: "app.py", directory: false }]);
  vi.mocked(codingApi.file).mockResolvedValue({ ok: true, text: "original", version: "v1" });
});

it("routes controlled tabs through the parent and keeps tool content with its owning thread", () => {
  const onTab = vi.fn();
  const view = render(<Workbench thread={thread} tab="terminal" onTab={onTab} />);
  const tabs = screen.getByRole("tablist", { name: "Repository tools" });
  expect(within(tabs).getAllByRole("tab")).toHaveLength(3);
  expect(within(tabs).getByRole("tab", { name: "Terminal" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tabpanel", { name: "Terminal" })).toHaveTextContent("Terminal for thread-a");
  fireEvent.click(within(tabs).getByRole("tab", { name: "Changes" }));
  expect(onTab).toHaveBeenCalledWith("changes");
  expect(screen.getByRole("tabpanel", { name: "Terminal" })).toBeVisible();
  view.rerender(<Workbench thread={thread} tab="changes" onTab={onTab} />);
  expect(screen.getByRole("tabpanel", { name: "Changes" })).toHaveTextContent("Changes for thread-a");
  expect(within(tabs).getByRole("tab", { name: "Terminal" })).toHaveAttribute("aria-selected", "false");
  for (const tab of ["files", "terminal", "changes"] as const) {
    view.rerender(<Workbench thread={{ ...thread, worktree_status: "deleted" }} tab={tab} onTab={onTab} />);
    expect(screen.getByRole("region", { name: "Deleted worktree" })).toHaveTextContent(
      "Continue this chat to recreate the worktree",
    );
    expect(screen.queryByText("Changes for thread-a")).toBeNull();
    expect(screen.queryByText("Terminal for thread-a")).toBeNull();
  }
  expect(codingApi.tree).not.toHaveBeenCalled();
  expect(codingApi.file).not.toHaveBeenCalled();
  view.rerender(<Workbench thread={{ ...thread, worktree_status: "active" }} tab="terminal" onTab={onTab} />);
  expect(screen.getByRole("tabpanel", { name: "Terminal" })).toHaveTextContent("Terminal for thread-a");
  expect(screen.queryByRole("region", { name: "Deleted worktree" })).toBeNull();
});

it("recovers file drafts when switching tools", async () => {
  render(<Workbench thread={thread} />);
  fireEvent.click(await screen.findByRole("button", { name: "app.py" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Edit app.py" })).toHaveValue("original"));
  fireEvent.change(screen.getByRole("textbox", { name: "Edit app.py" }), {
    target: { value: "Keep this file draft" },
  });
  fireEvent.click(screen.getByRole("tab", { name: "Terminal" }));
  expect(screen.getByRole("tabpanel", { name: "Terminal" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "Edit app.py" })).toHaveValue("Keep this file draft"),
  );
  expect(codingApi.file).toHaveBeenLastCalledWith("thread-a", "app.py", "/repo");
});

it("closes the overlay through its own toolbar", () => {
  const onClose = vi.fn();
  render(<Workbench thread={thread} tab="terminal" onClose={onClose} />);
  fireEvent.click(screen.getByRole("button", { name: "Close workbench" }));
  expect(onClose).toHaveBeenCalledOnce();
});
