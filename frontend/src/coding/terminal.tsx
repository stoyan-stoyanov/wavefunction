import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { SearchAddon } from "@xterm/addon-search";
import "@xterm/xterm/css/xterm.css";
import {
  ArrowDown,
  ArrowUp,
  LoaderCircle,
  Pencil,
  Plus,
  Search,
  Square,
  SquareTerminal,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/transport";
import { codingApi, type CodingThread, type TerminalSession } from "./api";

function TerminalScreen({
  threadId,
  session,
  onStatus,
}: {
  threadId: string;
  session: TerminalSession;
  onStatus: (session: TerminalSession) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const search = useRef<SearchAddon | null>(null);
  const status = useRef(onStatus);
  useEffect(() => {
    status.current = onStatus;
  }, [onStatus]);
  const [error, setError] = useState("");
  const [finding, setFinding] = useState(false);
  const [query, setQuery] = useState("");
  useEffect(() => {
    if (!host.current) return;
    const controller = new AbortController();
    let alive = true,
      cursor = 0,
      failures = 0,
      queued = "";
    let inputTimer: ReturnType<typeof setTimeout> | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let resizeTimer: ReturnType<typeof setTimeout> | undefined;
    let inputs = Promise.resolve();
    const terminal = new Terminal({
      cols: session.cols,
      rows: session.rows,
      fontSize: 13,
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      scrollback: 10000,
      screenReaderMode: true,
      allowProposedApi: false,
    });
    const fit = new FitAddon();
    const finder = new SearchAddon();
    terminal.loadAddon(fit);
    terminal.loadAddon(finder);
    search.current = finder;
    terminal.open(host.current);
    const failure = (reason: unknown) => {
      if (alive && !controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "Terminal connection failed.");
    };
    const flush = () => {
      let end = Math.min(queued.length, 64000);
      const last = queued.charCodeAt(end - 1);
      if (end < queued.length && last >= 0xd800 && last <= 0xdbff) end--;
      const text = queued.slice(0, end);
      queued = queued.slice(text.length);
      if (!text || !alive) return;
      inputs = inputs
        .then(() => codingApi.terminalInput(threadId, session.id, text, controller.signal))
        .catch(failure);
      if (queued) inputTimer = setTimeout(flush, 12);
    };
    const input = terminal.onData((text) => {
      queued += text;
      clearTimeout(inputTimer);
      inputTimer = setTimeout(flush, 12);
    });
    terminal.attachCustomKeyEventHandler((event) => {
      if (
        event.type === "keydown" &&
        (event.metaKey || event.ctrlKey) &&
        event.shiftKey &&
        event.key.toLowerCase() === "f"
      ) {
        setFinding((v) => !v);
        return false;
      }
      return true;
    });
    let colors = "";
    const updateTheme = () => {
      if (!host.current) return;
      const style = getComputedStyle(host.current);
      // Assigning a theme repaints every cell, so only do it when the colours change.
      // Root style also changes for zoom and window-control alignment, which only need a fit.
      if (colors !== `${style.backgroundColor} ${style.color}`) {
        colors = `${style.backgroundColor} ${style.color}`;
        terminal.options.theme = {
          background: style.backgroundColor,
          foreground: style.color,
          cursor: style.color,
          selectionBackground: "#77777770",
        };
      }
      fit.fit();
    };
    const resize = terminal.onResize(({ cols, rows }) => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (alive && cols >= 10 && rows >= 2)
          void codingApi.terminalSize(threadId, session.id, cols, rows, controller.signal).catch(failure);
      }, 80);
    });
    // Size changes arrive every frame while the window or a sidebar is resized.
    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(host.current);
    const themeObserver = new MutationObserver(updateTheme);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style"],
    });
    updateTheme();
    terminal.focus();
    const poll = async () => {
      if (!alive) return;
      try {
        const output = await codingApi.terminalOutput(threadId, session.id, cursor, controller.signal);
        if (!alive) return;
        if (output.reset) {
          terminal.reset();
          terminal.writeln("[Earlier terminal output was trimmed]");
        }
        await new Promise<void>((resolve) => terminal.write(output.text, resolve));
        if (!alive) return;
        cursor = output.cursor;
        failures = 0;
        setError("");
        status.current(output);
        terminal.options.disableStdin = output.status !== "running";
        if (output.status === "running" || output.text) void poll();
      } catch (reason) {
        failure(reason);
        if (alive && !controller.signal.aborted && !(reason instanceof ApiError && reason.status === 404)) {
          retryTimer = setTimeout(() => void poll(), Math.min(5000, 500 * 2 ** failures++));
        }
      }
    };
    void poll();
    return () => {
      alive = false;
      controller.abort();
      clearTimeout(inputTimer);
      clearTimeout(retryTimer);
      clearTimeout(resizeTimer);
      observer.disconnect();
      themeObserver.disconnect();
      input.dispose();
      resize.dispose();
      search.current = null;
      terminal.dispose();
    };
    // The shell's identity, rather than live output metadata, owns this display lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, session.id]);
  useEffect(() => {
    host.current?.querySelector("textarea")?.setAttribute("aria-label", `Terminal input: ${session.title}`);
  }, [session.id, session.title]);
  return (
    <div className="terminal-display" onKeyDown={(event) => event.stopPropagation()}>
      <div className="terminal-search">
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Find in terminal"
          onClick={() => setFinding((v) => !v)}
        >
          <Search />
        </Button>
        {finding && (
          <>
            <Input
              autoFocus
              aria-label="Search terminal output"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                search.current?.findNext(e.target.value, { incremental: true });
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") search.current?.findNext(query);
                if (e.key === "Escape") setFinding(false);
              }}
            />
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Previous terminal match"
              onClick={() => search.current?.findPrevious(query)}
            >
              <ArrowUp />
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Next terminal match"
              onClick={() => search.current?.findNext(query)}
            >
              <ArrowDown />
            </Button>
          </>
        )}
        <span>
          {session.owner === "agent" ? "Agent task" : "Interactive shell"} ·{" "}
          {session.status === "running"
            ? "Running"
            : `Exited${session.exit_code !== null ? ` (${session.exit_code})` : ""}`}
        </span>
      </div>
      {error && (
        <div className="workbench-error" role="alert">
          {error} Output reconnects automatically; input is never resent.
        </div>
      )}
      <div ref={host} className="terminal-surface" />
    </div>
  );
}

export function TerminalView({ thread }: { thread: CodingThread }) {
  return <ThreadTerminals key={thread.id} thread={thread} />;
}

function ThreadTerminals({ thread }: { thread: CodingThread }) {
  const key = `harness.terminal.local.${thread.id}`;
  const [sessions, setSessions] = useState<TerminalSession[]>([]);
  const [selected, setSelected] = useState<string | null>(() => localStorage.getItem(key));
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [title, setTitle] = useState("");
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    const refresh = () =>
      codingApi
        .terminals(thread.id, controller.signal)
        .then((rows) => {
          if (controller.signal.aborted) return;
          setSessions(rows);
          setLoading(false);
          setSelected((id) => (rows.some((row) => row.id === id) ? id : (rows[0]?.id ?? null)));
        })
        .catch((reason: Error) => {
          if (!controller.signal.aborted) {
            setError(reason.message);
            setLoading(false);
          }
        });
    void refresh();
    const interval = setInterval(() => void refresh(), 3000);
    return () => {
      alive.current = false;
      controller.abort();
      clearInterval(interval);
    };
  }, [thread.id]);
  useEffect(() => {
    if (selected) localStorage.setItem(key, selected);
    else localStorage.removeItem(key);
  }, [key, selected]);
  const current = sessions.find((s) => s.id === selected);
  const action = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (reason) {
      if (alive.current) setError(reason instanceof Error ? reason.message : "Terminal action failed");
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  return (
    <div className="terminal-workbench">
      <div className="terminal-tabs" role="tablist" aria-label="Terminals">
        {sessions.map((session) => (
          <div key={session.id} className={selected === session.id ? "terminal-tab active" : "terminal-tab"}>
            <button
              role="tab"
              aria-selected={selected === session.id}
              onClick={() => {
                setSelected(session.id);
                setRenaming(false);
              }}
            >
              {session.status === "running" && <i />}
              {session.title}
            </button>
            <button
              aria-label={`Close terminal ${session.title}`}
              disabled={busy}
              onClick={() =>
                void action(async () => {
                  await codingApi.closeTerminal(thread.id, session.id);
                  if (alive.current) {
                    setSessions((rows) => rows.filter((s) => s.id !== session.id));
                    setSelected((id) =>
                      id === session.id ? (sessions.find((s) => s.id !== session.id)?.id ?? null) : id,
                    );
                  }
                })
              }
            >
              <X size={12} />
            </button>
          </div>
        ))}
        <Button
          size="sm"
          variant="ghost"
          disabled={busy || thread.mode === "plan" || thread.archived}
          onClick={() =>
            void action(async () => {
              const session = await codingApi.createTerminal(thread.id, `Terminal ${sessions.length + 1}`);
              if (alive.current) {
                setSessions((rows) => [...rows.filter((s) => s.id !== session.id), session]);
                setSelected(session.id);
              }
            })
          }
        >
          {busy ? <LoaderCircle className="animate-spin" /> : <Plus />}
          New Terminal
        </Button>
      </div>
      {error && (
        <p className="workbench-error" role="alert">
          {error}
        </p>
      )}
      {current ? (
        <>
          <div className="terminal-actions">
            <code title={current.cwd}>{current.cwd}</code>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Rename terminal"
              onClick={() => {
                setTitle(current.title);
                setRenaming((v) => !v);
              }}
            >
              <Pencil />
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label="Interrupt terminal"
              disabled={current.status !== "running" || busy}
              onClick={() => void action(() => codingApi.terminalInput(thread.id, current.id, "\u0003"))}
            >
              <Square />
            </Button>
          </div>
          {renaming && (
            <form
              className="terminal-rename"
              onSubmit={(event) => {
                event.preventDefault();
                void action(async () => {
                  const updated = await codingApi.renameTerminal(thread.id, current.id, title);
                  if (alive.current) {
                    setSessions((rows) => rows.map((s) => (s.id === updated.id ? updated : s)));
                    setRenaming(false);
                  }
                });
              }}
            >
              <Input
                aria-label="Terminal name"
                value={title}
                maxLength={80}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Button size="sm" disabled={!title.trim() || busy}>
                Save name
              </Button>
            </form>
          )}
          <TerminalScreen
            key={current.id}
            threadId={thread.id}
            session={current}
            onStatus={(updated) =>
              setSessions((rows) => rows.map((s) => (s.id === updated.id ? updated : s)))
            }
          />
        </>
      ) : (
        <section className="terminal-empty-state" aria-label="Terminal sessions">
          <div className="terminal-empty-icon" aria-hidden="true">
            {loading ? <LoaderCircle className="animate-spin" /> : <SquareTerminal />}
          </div>
          <h3>{loading ? "Loading terminals…" : "No terminals open"}</h3>
          {!loading && (
            <p>
              {thread.mode === "plan"
                ? "Switch out of Plan mode to open an interactive terminal."
                : "Open a terminal to run commands, interact with programs, and keep servers running. Sessions stay with this conversation until closed or the app quits."}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
