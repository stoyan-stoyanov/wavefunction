import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Workbench } from "./workbench";
import { codingApi, type CodingThread } from "./api";
import { makeThread } from "@/test/fixtures";

vi.mock("./api", () => ({
  codingApi: {
    tree: vi.fn(),
    file: vi.fn(),
    save: vi.fn(),
  },
}));
const thread: CodingThread = makeThread({
  id: "thread-a",
  conversation_id: "conversation-a",
  title: "Fix",
  folder: "/repo",
  created_at: "",
  updated_at: "",
});
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
  vi.mocked(codingApi.tree).mockResolvedValue([{ name: "app.py", path: "app.py", directory: false }]);
  vi.mocked(codingApi.file).mockResolvedValue({ ok: true, text: "original", version: "v1" });
});
async function open() {
  const view = render(<Workbench thread={thread} />);
  fireEvent.click(await screen.findByRole("button", { name: "app.py" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Edit app.py" })).toHaveValue("original"));
  return view;
}
it("recovers an unsaved draft after changing views and protects an external edit", async () => {
  const first = await open();
  fireEvent.change(screen.getByRole("textbox", { name: "Edit app.py" }), {
    target: { value: "my draft" },
  });
  first.unmount();
  vi.mocked(codingApi.file).mockResolvedValue({ ok: true, text: "external edit", version: "v2" });
  render(<Workbench thread={thread} />);
  fireEvent.click(await screen.findByRole("button", { name: "app.py" }));
  await waitFor(() => expect(screen.getByRole("textbox", { name: "Edit app.py" })).toHaveValue("my draft"));
  expect(screen.getByRole("alert")).toHaveTextContent("changed on disk");
  vi.mocked(codingApi.save).mockRejectedValue(new Error("File changed on disk"));
  fireEvent.click(screen.getByRole("button", { name: "Save file" }));
  await waitFor(() =>
    expect(codingApi.save).toHaveBeenCalledWith("thread-a", "app.py", "my draft", "v1", "/repo"),
  );
  expect(screen.getByRole("textbox", { name: "Edit app.py" })).toHaveValue("my draft");
  fireEvent.click(screen.getByRole("button", { name: "Discard draft and reload file" }));
  await waitFor(() =>
    expect(screen.getByRole("textbox", { name: "Edit app.py" })).toHaveValue("external edit"),
  );
});
it("does not allow an unreadable or binary file to be saved over", async () => {
  vi.mocked(codingApi.file).mockRejectedValue(new Error("The code editor supports text files"));
  render(<Workbench thread={thread} />);
  fireEvent.click(await screen.findByRole("button", { name: "app.py" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("text files");
  expect(screen.getByRole("button", { name: "Save file" })).toBeDisabled();
  expect(screen.getByRole("textbox", { name: "Edit app.py" })).toBeDisabled();
});
