import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TerminalView } from "./terminal";
import { codingApi, type CodingThread, type TerminalSession } from "./api";

const emulator = vi.hoisted(() => ({
  inputs: [] as Array<(text: string) => void>,
  disposed: vi.fn(),
}));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    options: Record<string, unknown> = {};
    textarea = document.createElement("textarea");
    host?: HTMLElement;
    loadAddon() {}
    open(host: HTMLElement) {
      this.host = host;
      host.appendChild(this.textarea);
    }
    onData(callback: (text: string) => void) {
      emulator.inputs.push(callback);
      return { dispose: vi.fn() };
    }
    onResize() {
      return { dispose: vi.fn() };
    }
    attachCustomKeyEventHandler() {}
    focus() {}
    reset() {}
    writeln(text: string) {
      this.host?.append(text);
    }
    write(text: string, done: () => void) {
      this.host?.append(text);
      done();
    }
    dispose() {
      emulator.disposed();
    }
  },
}));
vi.mock("@xterm/addon-fit", () => ({
  FitAddon: class {
    fit() {}
  },
}));
vi.mock("@xterm/addon-search", () => ({
  SearchAddon: class {
    findNext() {}
    findPrevious() {}
  },
}));
vi.mock("./api", () => ({
  codingApi: {
    terminals: vi.fn(),
    createTerminal: vi.fn(),
    terminalOutput: vi.fn(),
    terminalInput: vi.fn(),
    terminalSize: vi.fn(),
    renameTerminal: vi.fn(),
    closeTerminal: vi.fn(),
  },
}));
const thread = {
  id: "one",
  folder: "/repo",
  archived: false,
  mode: "auto",
} as CodingThread;
const session: TerminalSession = {
  id: "shell-one",
  thread_id: "one",
  title: "Terminal 1",
  cwd: "/repo",
  command: null,
  owner: "user",
  run_id: null,
  cols: 80,
  rows: 24,
  created_at: "",
  status: "running",
  exit_code: null,
};
beforeEach(() => {
  vi.resetAllMocks();
  emulator.inputs.length = 0;
  localStorage.clear();
  vi.mocked(codingApi.terminals).mockResolvedValue([session]);
  vi.mocked(codingApi.terminalOutput).mockImplementation(
    (_id, _terminal, _cursor, signal) =>
      new Promise((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), {
          once: true,
        });
      }),
  );
});
it("streams a real session, orders interactive input, and reconnects without creating a shell", async () => {
  vi.mocked(codingApi.terminalOutput).mockResolvedValueOnce({
    ...session,
    text: "reconnected output",
    cursor: 18,
    reset: false,
  });
  const first = render(<TerminalView thread={thread} />);
  expect(await screen.findByText(/reconnected output/)).toBeInTheDocument();
  emulator.inputs[0]("hello\n");
  await waitFor(() =>
    expect(codingApi.terminalInput).toHaveBeenCalledWith(
      "one",
      "shell-one",
      "hello\n",
      expect.any(AbortSignal),
    ),
  );
  first.unmount();
  vi.mocked(codingApi.terminalOutput).mockClear();
  render(<TerminalView thread={thread} />);
  await screen.findByRole("tab", { name: "Terminal 1" });
  expect(codingApi.createTerminal).not.toHaveBeenCalled();
  expect(emulator.disposed).toHaveBeenCalled();
  await waitFor(() =>
    expect(codingApi.terminalOutput).toHaveBeenCalledExactlyOnceWith(
      "one",
      "shell-one",
      0,
      expect.any(AbortSignal),
    ),
  );
});
it("opens independent tabs, renames, interrupts and closes the selected terminal", async () => {
  vi.mocked(codingApi.createTerminal).mockResolvedValue({
    ...session,
    id: "shell-two",
    title: "Terminal 2",
  });
  vi.mocked(codingApi.renameTerminal).mockResolvedValue({
    ...session,
    id: "shell-two",
    title: "Tests",
  });
  render(<TerminalView thread={thread} />);
  await screen.findByRole("tab", { name: "Terminal 1" });
  const newTerminal = screen.getByRole("button", { name: "New Terminal" });
  expect(newTerminal).toHaveTextContent("New Terminal");
  fireEvent.click(newTerminal);
  expect(await screen.findByRole("tab", { name: "Terminal 2" })).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("button", { name: "Rename terminal" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Terminal name" }), {
    target: { value: "Tests" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save name" }));
  await screen.findByRole("tab", { name: "Tests" });
  // The input's label can follow the tab by a render on slower machines.
  expect(await screen.findByRole("textbox", { name: "Terminal input: Tests" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Interrupt terminal" }));
  await waitFor(() => expect(codingApi.terminalInput).toHaveBeenCalledWith("one", "shell-two", "\u0003"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Close terminal Tests" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Close terminal Tests" }));
  await waitFor(() => expect(codingApi.closeTerminal).toHaveBeenCalledWith("one", "shell-two"));
  expect(screen.getByRole("tab", { name: "Terminal 1" })).toHaveAttribute("aria-selected", "true");
});
it("aborts old output and ignores a late terminal creation after switching conversations", async () => {
  let finish!: (session: TerminalSession) => void;
  vi.mocked(codingApi.createTerminal).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = render(<TerminalView thread={thread} />);
  await screen.findByRole("tab", { name: "Terminal 1" });
  await waitFor(() => expect(codingApi.terminalOutput).toHaveBeenCalled());
  const signal = vi.mocked(codingApi.terminalOutput).mock.calls[0][3]!;
  fireEvent.click(screen.getByRole("button", { name: "New Terminal" }));
  vi.mocked(codingApi.terminals).mockResolvedValue([]);
  view.rerender(<TerminalView thread={{ ...thread, id: "two" }} />);
  await screen.findByText(/Open a terminal to run commands/);
  expect(screen.getByRole("heading", { name: "No terminals open" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Terminal sessions" })).toHaveTextContent(
    "Sessions stay with this conversation until closed or the app quits.",
  );
  finish({ ...session, id: "late-shell", title: "Old conversation" });
  await waitFor(() =>
    expect(screen.queryByRole("tab", { name: "Old conversation" })).not.toBeInTheDocument(),
  );
  expect(signal.aborted).toBe(true);
});
it("withholds new terminals in Plan mode", async () => {
  vi.mocked(codingApi.terminals).mockResolvedValue([]);
  render(<TerminalView thread={{ ...thread, mode: "plan" }} />);
  expect(await screen.findByText(/Switch out of Plan mode/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "New Terminal" })).toBeDisabled();
});
