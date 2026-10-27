import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { ComposerContext } from "./composer-context";
import { emptyContext } from "./context-draft";
import { codingApi, type CodingThread, type TaskContext } from "./api";
vi.mock("./api", () => ({
  codingApi: {
    contextFiles: vi.fn(),
    file: vi.fn(),
    skills: vi.fn(),
    workflow: vi.fn(),
  },
}));
const thread = { id: "one", folder: "/repo", archived: false } as CodingThread;
function Fixture() {
  const [context, setContext] = useState<TaskContext>(emptyContext);
  const [message, setMessage] = useState("");
  return (
    <>
      <ComposerContext
        thread={thread}
        value={context}
        onChange={setContext}
        message={message}
        onMessage={setMessage}
      />
      <input aria-label="Draft" value={message} onChange={(e) => setMessage(e.target.value)} />
      <output>{JSON.stringify(context)}</output>
    </>
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(codingApi.contextFiles).mockResolvedValue({ ok: true, files: ["app.ts"], more: false });
  vi.mocked(codingApi.skills).mockResolvedValue({
    skills: [
      {
        name: "thorough-bug-review",
        description: "Find reproducible bugs",
        active: true,
        source: "library",
      },
    ] as never,
    warnings: [],
  });
  vi.mocked(codingApi.workflow).mockResolvedValue({
    version: "config",
    scope: "project",
    prompts: [{ name: "pr", description: "Prepare PR", body: "Draft $ARGUMENTS" }],
  });
});
it("attaches only the selected file lines with their version", async () => {
  vi.mocked(codingApi.file).mockResolvedValue({
    ok: true,
    text: "first\nsecond\nthird\n",
    version: "captured",
  });
  render(<Fixture />);
  fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "/" } });
  fireEvent.click(await screen.findByRole("button", { name: /^\/files / }));
  fireEvent.click(await screen.findByRole("button", { name: "app.ts" }));
  fireEvent.change(await screen.findByLabelText("Context start line"), {
    target: { value: "2" },
  });
  fireEvent.change(screen.getByLabelText("Context end line"), {
    target: { value: "2" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Attach lines" }));
  expect(screen.getByText("app.ts:2–2")).toBeInTheDocument();
  expect(screen.getByRole("status").textContent).toContain('"version":"captured"');
  expect(screen.getByLabelText("Draft")).toHaveValue("");
  expect(codingApi.contextFiles).toHaveBeenCalledWith("one", "");
});
it("selects an explicit skill and reusable prompt without changing other context", async () => {
  vi.mocked(codingApi.skills).mockResolvedValue({
    skills: [{ name: "code-review", description: "Review carefully", active: true }] as never,
    warnings: [],
  });
  vi.mocked(codingApi.workflow).mockResolvedValue({
    version: "config",
    scope: "project",
    prompts: [{ name: "pr", description: "Prepare PR", body: "Draft $ARGUMENTS" }],
  });
  render(<Fixture />);
  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "/code-review" },
  });
  fireEvent.click(await screen.findByRole("button", { name: /code-review/ }));
  fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "/pr" } });
  fireEvent.click(await screen.findByRole("button", { name: /\/pr/ }));
  expect(screen.getByRole("status").textContent).toContain('"skills":["code-review"]');
  expect(screen.getByRole("status").textContent).toContain('"command":"pr"');
});
it("ignores a file search response from an old conversation", async () => {
  let finish!: (value: { ok: boolean; files: string[]; more: boolean }) => void;
  vi.mocked(codingApi.contextFiles)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValue({ ok: true, files: ["second.ts"], more: false });
  const view = render(
    <ComposerContext
      thread={thread}
      value={emptyContext()}
      onChange={vi.fn()}
      message="@"
      onMessage={vi.fn()}
    />,
  );
  await waitFor(() => expect(codingApi.contextFiles).toHaveBeenCalledWith("one", ""));
  view.rerender(
    <ComposerContext
      thread={{ ...thread, id: "two" }}
      value={emptyContext()}
      onChange={vi.fn()}
      message="@"
      onMessage={vi.fn()}
    />,
  );
  await screen.findByRole("button", { name: "second.ts" });
  finish({ ok: true, files: ["old.ts"], more: false });
  await waitFor(() => expect(screen.queryByRole("button", { name: "old.ts" })).not.toBeInTheDocument());
});

it("opens the slash menu, filters global skills, and explicitly invokes a selection", async () => {
  render(<Fixture />);
  fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "/" } });
  await screen.findByRole("button", { name: /\/thorough-bug-review/ });
  expect(screen.getByRole("button", { name: /\/pr/ })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "/thorough" },
  });
  expect(screen.queryByRole("button", { name: /\/pr/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /\/thorough-bug-review/ }));
  expect(screen.getByLabelText("Draft")).toHaveValue("/thorough-bug-review");
  expect(screen.getByRole("status").textContent).toContain('"skills":["thorough-bug-review"]');
  expect(screen.queryByText("Skills and prompt commands")).not.toBeInTheDocument();
});

it("keeps prompt arguments without duplicating the slash command", async () => {
  render(<Fixture />);
  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "/pr focus on retries" },
  });
  fireEvent.click(await screen.findByRole("button", { name: /\/pr/ }));
  expect(screen.getByLabelText("Draft")).toHaveValue("focus on retries");
  expect(screen.getByRole("status").textContent).toContain('"command":"pr"');
});

it("keeps same-name skills, prompts, and the files picker independently selectable", async () => {
  vi.mocked(codingApi.skills).mockResolvedValue({
    skills: [
      { name: "code-review", description: "Review carefully", active: true },
      { name: "files", description: "Review file boundaries", active: true },
    ] as never,
    warnings: [],
  });
  vi.mocked(codingApi.workflow).mockResolvedValue({
    version: "config",
    scope: "project",
    prompts: [
      { name: "code-review", description: "Prepare review", body: "Review $ARGUMENTS" },
      { name: "files", description: "Summarize files", body: "Summarize $ARGUMENTS" },
    ],
  });
  render(<Fixture />);
  fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "/" } });
  await screen.findByRole("button", { name: "/code-review · Skill · Review carefully" });
  expect(screen.getByRole("button", { name: "/code-review · Prompt · Prepare review" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "/files · Files & lines" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "/files · Prompt · Summarize files" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "/files · Skill · Review file boundaries" })).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "/code-review compare the parser" },
  });
  fireEvent.click(screen.getByRole("button", { name: "/code-review · Prompt · Prepare review" }));
  expect(screen.getByLabelText("Draft")).toHaveValue("compare the parser");
  expect(JSON.parse(screen.getByRole("status").textContent!)).toEqual({
    files: [],
    skills: [],
    command: "code-review",
  });

  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "/code-review focus on retries" },
  });
  fireEvent.click(await screen.findByRole("button", { name: "/code-review · Skill · Review carefully" }));
  expect(screen.getByLabelText("Draft")).toHaveValue(
    "Use the code-review skill for this repository. focus on retries",
  );
  expect(JSON.parse(screen.getByRole("status").textContent!)).toEqual({
    files: [],
    skills: ["code-review"],
    command: null,
  });

  fireEvent.change(screen.getByLabelText("Draft"), { target: { value: "/files" } });
  fireEvent.click(await screen.findByRole("button", { name: "/files · Skill · Review file boundaries" }));
  expect(screen.getByLabelText("Draft")).toHaveValue("Use the files skill for this repository.");
  expect(JSON.parse(screen.getByRole("status").textContent!)).toEqual({
    files: [],
    skills: ["code-review", "files"],
    command: null,
  });
});

it("retains skill arguments and keeps the picker closed while adding them", async () => {
  render(<Fixture />);
  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "/thorough focus on retries" },
  });
  fireEvent.click(await screen.findByRole("button", { name: /\/thorough-bug-review/ }));
  expect(screen.getByLabelText("Draft")).toHaveValue("/thorough-bug-review focus on retries");
  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "/thorough-bug-review focus on retries and cancellation" },
  });
  expect(screen.queryByText("Skills and prompt commands")).not.toBeInTheDocument();
  expect(screen.getByRole("status").textContent).toContain('"skills":["thorough-bug-review"]');
});

it("searches files through the slash menu without losing the remaining draft", async () => {
  render(<Fixture />);
  expect(screen.queryByRole("button", { name: "Files & lines" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Skills" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Prompts" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Draft"), {
    target: { value: "/files explain this code" },
  });
  fireEvent.click(await screen.findByRole("button", { name: /^\/files / }));
  expect(screen.getByLabelText("Draft")).toHaveValue("explain this code");
  fireEvent.change(screen.getByLabelText("Search context files"), {
    target: { value: "app" },
  });
  await waitFor(() => expect(codingApi.contextFiles).toHaveBeenCalledWith("one", "app"));
  fireEvent.click(screen.getByRole("button", { name: "Close context picker" }));
  expect(screen.queryByLabelText("Search context files")).not.toBeInTheDocument();
});

it("disables file context without a repository and prevents archived chat pickers", async () => {
  const view = render(
    <ComposerContext
      thread={{ ...thread, folder: null }}
      value={emptyContext()}
      onChange={vi.fn()}
      message="/"
      onMessage={vi.fn()}
    />,
  );
  expect(screen.getByRole("button", { name: /^\/files / })).toBeDisabled();
  view.rerender(
    <ComposerContext
      thread={{ ...thread, archived: true }}
      value={emptyContext()}
      onChange={vi.fn()}
      message="/"
      onMessage={vi.fn()}
    />,
  );
  expect(screen.queryByText("Skills and prompt commands")).not.toBeInTheDocument();
});

it("ignores slash menu responses from a previous conversation", async () => {
  let finish!: (value: Awaited<ReturnType<typeof codingApi.skills>>) => void;
  vi.mocked(codingApi.skills).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(
    <ComposerContext
      thread={thread}
      value={emptyContext()}
      onChange={vi.fn()}
      message="/"
      onMessage={vi.fn()}
    />,
  );
  await waitFor(() => expect(codingApi.skills).toHaveBeenCalledWith("one"));
  view.rerender(
    <ComposerContext
      thread={{ ...thread, id: "two" }}
      value={emptyContext()}
      onChange={vi.fn()}
      message="/"
      onMessage={vi.fn()}
    />,
  );
  await screen.findByRole("button", { name: /\/thorough-bug-review/ });
  finish({
    skills: [{ name: "old-skill", description: "Previous owner", active: true }] as never,
    warnings: [],
  });
  await waitFor(() => expect(screen.queryByRole("button", { name: /\/old-skill/ })).not.toBeInTheDocument());
});
