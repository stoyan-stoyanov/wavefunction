import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { TerminalView } from "./terminal";
import { codingApi, type CodingThread, type TerminalSession } from "./api";
import { useCommandRunner } from "./run-command";

const emulator = vi.hoisted(() => ({
  inputs: [] as Array<(text: string) => void>,
  disposed: vi.fn(),
  // The columns the screen fits the terminal to, when set.
  width: 0,
  keys: undefined as ((event: KeyboardEvent) => boolean) | undefined,
  selection: "",
}));
type Size = { cols: number; rows: number };
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    options: Record<string, unknown> = {};
    textarea = document.createElement("textarea");
    host?: HTMLElement;
    cols: number;
    rows: number;
    resized?: (size: Size) => void;
    constructor({ cols, rows }: Size) {
      this.cols = cols;
      this.rows = rows;
    }
    loadAddon(addon: { terminal?: unknown }) {
      addon.terminal = this;
    }
    open(host: HTMLElement) {
      this.host = host;
      host.appendChild(this.textarea);
    }
    onData(callback: (text: string) => void) {
      emulator.inputs.push(callback);
      return { dispose: vi.fn() };
    }
    onResize(callback: (size: Size) => void) {
      this.resized = callback;
      return { dispose: vi.fn() };
    }
    attachCustomKeyEventHandler(handler: (event: KeyboardEvent) => boolean) {
      emulator.keys = handler;
    }
    hasSelection() {
      return emulator.selection !== "";
    }
    getSelection() {
      return emulator.selection;
    }
    paste(text: string) {
      emulator.inputs.at(-1)?.(text);
    }
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
    terminal?: Size & { resized?: (size: Size) => void };
    fit() {
      if (!this.terminal || !emulator.width || this.terminal.cols === emulator.width) return;
      this.terminal.cols = emulator.width;
      this.terminal.resized?.({ cols: emulator.width, rows: this.terminal.rows });
    }
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
  emulator.width = 0;
  emulator.keys = undefined;
  emulator.selection = "";
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
it("types a command from the chat into its new terminal once the shell has the screen's size and prompt", async () => {
  emulator.width = 40;
  const opened = { ...session, id: "shell-run", title: "npm test" };
  const prompt = { ...opened, text: "repo % ", cursor: 7, reset: false };
  vi.mocked(codingApi.createTerminal).mockResolvedValue(opened);
  vi.mocked(codingApi.terminals).mockResolvedValue([session, opened]);
  let sized!: () => void;
  vi.mocked(codingApi.terminalSize).mockImplementation(
    () => new Promise<void>((resolve) => (sized = resolve)),
  );
  vi.mocked(codingApi.terminalOutput).mockResolvedValueOnce(prompt);
  const onOpen = vi.fn();
  const runner = renderHook(() => useCommandRunner(thread, onOpen)).result.current!;
  await act(() => runner.run("npm test\nnpm run lint"));
  expect(codingApi.createTerminal).toHaveBeenCalledExactlyOnceWith("one", "npm test");
  expect(onOpen).toHaveBeenCalledWith(opened);
  const view = render(<TerminalView thread={thread} opened="shell-run" />);
  expect(await screen.findByRole("tab", { name: "npm test" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText(/repo %/)).toBeInTheDocument();
  // The prompt is in, but typed before the shell has the screen's width it would echo garbled.
  await waitFor(() =>
    expect(codingApi.terminalSize).toHaveBeenCalledWith("one", "shell-run", 40, 24, expect.any(AbortSignal)),
  );
  expect(codingApi.terminalInput).not.toHaveBeenCalled();
  await act(async () => sized());
  await waitFor(() =>
    expect(codingApi.terminalInput).toHaveBeenCalledExactlyOnceWith(
      "one",
      "shell-run",
      "npm test\rnpm run lint\r",
      expect.any(AbortSignal),
    ),
  );
  // Shown again, the terminal doesn't run it a second time.
  view.unmount();
  vi.mocked(codingApi.terminals).mockResolvedValue([session, { ...opened, cols: 40 }]);
  vi.mocked(codingApi.terminalOutput).mockResolvedValueOnce(prompt);
  const calls = vi.mocked(codingApi.terminalOutput).mock.calls.length;
  render(<TerminalView thread={thread} opened="shell-run" />);
  // The screen polls again only after handling the prompt.
  await waitFor(() => expect(codingApi.terminalOutput).toHaveBeenCalledTimes(calls + 2));
  expect(codingApi.terminalInput).toHaveBeenCalledTimes(1);
});
it("copies and pastes on Ctrl+Shift+C and V only where there is no system menu", async () => {
  const clipboard = { writeText: vi.fn().mockResolvedValue(undefined), readText: vi.fn() };
  Object.defineProperty(navigator, "clipboard", { value: clipboard, configurable: true });
  vi.stubGlobal("__TAURI_INTERNALS__", {});
  render(<TerminalView thread={thread} />);
  await waitFor(() => expect(emulator.keys).toBeDefined());
  emulator.selection = "npm test";
  const key = (key: string) =>
    new KeyboardEvent("keydown", { key, ctrlKey: true, shiftKey: true, cancelable: true });
  // The Mac's Edit menu copies; the keys stay the terminal's.
  expect(emulator.keys!(key("C"))).toBe(true);
  expect(clipboard.writeText).not.toHaveBeenCalled();

  vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 (X11; Linux x86_64)");
  const copy = key("C");
  expect(emulator.keys!(copy)).toBe(false);
  expect(copy.defaultPrevented).toBe(true);
  expect(clipboard.writeText).toHaveBeenCalledExactlyOnceWith("npm test");
  clipboard.readText.mockResolvedValue("git status");
  expect(emulator.keys!(key("V"))).toBe(false);
  await waitFor(() =>
    expect(codingApi.terminalInput).toHaveBeenCalledWith(
      "one",
      "shell-one",
      "git status",
      expect.any(AbortSignal),
    ),
  );
});
