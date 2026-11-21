import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CodingMarkdown } from "./markdown";
import { RunCommand } from "./run-command";
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn().mockResolvedValue(undefined),
}));
afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
const table = (rows: number) =>
  "| App | Details |\n| --- | --- |\n" +
  Array.from({ length: rows }, (_, i) => `| App ${i + 1} | Details ${i + 1} |`).join("\n");
it("paginates table rows in tens and preserves revealed rows as the response grows", () => {
  const view = render(<CodingMarkdown>{table(23)}</CodingMarkdown>);
  const body = () => within(screen.getByRole("table")).getAllByRole("row").slice(1);
  expect(body()).toHaveLength(10);
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(body()).toHaveLength(20);
  view.rerender(<CodingMarkdown>{table(24)}</CodingMarkdown>);
  expect(body()).toHaveLength(20);
  fireEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(body()).toHaveLength(24);
  expect(screen.getByRole("cell", { name: "App 24" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Show less" }));
  expect(body()).toHaveLength(10);
});
it("animates live words but keeps saved and completed messages immediately readable", () => {
  const view = render(<CodingMarkdown>Saved response</CodingMarkdown>);
  expect(view.container.querySelector("[data-sd-animate]")).toBeNull();
  view.rerender(<CodingMarkdown streaming>Live response</CodingMarkdown>);
  expect(view.container.querySelector("[data-sd-animate]")).not.toBeNull();
  view.rerender(<CodingMarkdown streaming>Live response with **new words**</CodingMarkdown>);
  expect(view.container).toHaveTextContent("Live response with new words");
  view.rerender(<CodingMarkdown>Live response with **new words**</CodingMarkdown>);
  expect(view.container.querySelector("[data-sd-animate]")).toBeNull();
  expect(screen.getByText("new words")).toBeVisible();
});
it("offers to run finished shell blocks in the chat, running a console session's commands without their output", () => {
  const run = vi.fn().mockResolvedValue(undefined);
  const reply = [
    "```bash\nnpm test\n```",
    "```console\n$ ls\napp.py  README.md\n$ git commit -m 'one' \\\n  --allow-empty\n[main 1a2b3c4] one\n```",
    "```sh\nfor name in one two; do\n  echo $name\ndone\n```",
    "```python\nprint('not a shell')\n```",
  ].join("\n\n");
  const chat = (markdown: string, streaming = false) => (
    <RunCommand.Provider value={{ run, unavailable: null }}>
      <CodingMarkdown streaming={streaming}>{markdown}</CodingMarkdown>
    </RunCommand.Provider>
  );
  const view = render(chat(reply));
  const buttons = screen.getAllByRole("button", { name: "Run in terminal" });
  expect(buttons).toHaveLength(3);
  expect(screen.getAllByRole("button", { name: "Copy Code" })).toHaveLength(4);
  expect(run).not.toHaveBeenCalled();
  for (const button of buttons) fireEvent.click(button);
  expect(run.mock.calls).toEqual([
    ["npm test"],
    ["ls\ngit commit -m 'one' \\\n  --allow-empty"],
    ["for name in one two; do\n  echo $name\ndone"],
  ]);
  // A block still streaming in is not the whole command yet.
  view.rerender(chat("```bash\nnpm run", true));
  expect(screen.queryByRole("button", { name: "Run in terminal" })).toBeNull();
  // Markdown outside a chat, such as release notes, runs nothing.
  view.rerender(<CodingMarkdown>{"```bash\nnpm test\n```"}</CodingMarkdown>);
  expect(screen.queryByRole("button", { name: "Run in terminal" })).toBeNull();
});
