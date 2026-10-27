import {
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type KeyboardEvent,
  type MouseEvent,
  type Ref,
} from "react";
import { LoaderCircle, Monitor, ShieldCheck } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { computerControl, type ControlStatus } from "@/lib/computer-control";
import { openSocket } from "@/lib/transport";
import type RFB from "@novnc/novnc";

const keys: Record<string, string> = {
  Enter: "Return",
  Backspace: "BackSpace",
  Escape: "Escape",
  Tab: "Tab",
  Delete: "Delete",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  ArrowUp: "Up",
  ArrowDown: "Down",
  Home: "Home",
  End: "End",
  PageUp: "Prior",
  PageDown: "Next",
  " ": "space",
};

type Input = {
  generation: number;
  action: string;
  payload: Record<string, unknown>;
};

/** One request's worth of input from two, or null when they must stay apart.

    The desktop takes one request at a time, and a busy page can make each take a second. Sent
    one by one, a burst of typing, a held key or a single trackpad flick queued tens of seconds
    of input that went on replaying long after the person stopped. Input that arrives while a
    request is out joins the one waiting behind it instead, within the desktop's own limits. */
function merged(last: Input, next: Input): Record<string, unknown> | null {
  if (last.generation !== next.generation || last.action !== next.action) return null;
  const [was, now] = [last.payload, next.payload];
  if (next.action === "type" && String(was.text).length + String(now.text).length <= 5000)
    return { text: `${was.text}${now.text}` };
  if (next.action === "key" && `${was.keys} ${now.keys}`.split(" ").length <= 32)
    return { keys: `${was.keys} ${now.keys}` };
  if (next.action === "scroll" && was.direction === now.direction)
    return {
      ...now,
      amount: Math.min(20, Number(was.amount) + Number(now.amount)),
    };
  return null;
}

/** Where on the 1280x800 desktop a pointer event over its picture lands. */
function point(event: MouseEvent<HTMLElement>) {
  const rect = event.currentTarget.getBoundingClientRect();
  return {
    x: Math.floor(((event.clientX - rect.left) / rect.width) * 1280),
    y: Math.floor(((event.clientY - rect.top) / rect.height) * 800),
  };
}

export function ComputerTakeover({
  id,
  requested = false,
  onResume,
  className = "mt-2",
  ref,
  onStatus,
}: {
  id: string;
  requested?: boolean;
  onResume: () => Promise<void>;
  /** Told each status poll, so a host can follow whether the desktop is still open. */
  onStatus?: (status: ControlStatus) => void;
  className?: string;
  /** Lets another control, such as the desktop's thumbnail, take control like the button does. */
  ref?: Ref<{ take: () => void }>;
}) {
  const channel = useMemo(() => computerControl(id), [id]);
  const [status, setStatus] = useState<ControlStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Taking control was refused (the desktop closed, say): the dialog says so instead of waiting.
  const [refused, setRefused] = useState(false);
  const [frame, setFrame] = useState("");
  const currentFrame = useRef("");
  const pending = useRef<Input[]>([]);
  const queue = useRef<Promise<void> | null>(null);
  const mounted = useRef(true);
  const keyboard = useRef<HTMLTextAreaElement>(null);
  // The desktop streamed over VNC while in control; screenshots are the fallback for a desktop
  // without a VNC server (one built before streaming) or a stream that cannot connect.
  const screenTarget = useRef<HTMLDivElement>(null);
  const rfb = useRef<RFB | null>(null);
  const [streaming, setStreaming] = useState(false);
  // The control session whose stream could not connect; a new takeover tries again.
  const [failedGeneration, setFailedGeneration] = useState<number | null>(null);
  const streamFailed = failedGeneration !== null && failedGeneration === status?.generation;

  useEffect(() => {
    const target = screenTarget.current;
    if (!open || status?.state !== "user" || streamFailed || !target) return;
    let stopped = false;
    let connected = false;
    let client: RFB | null = null;
    const generation = status.generation;
    // ⌘ is the Mac's Ctrl: ⌘C, ⌘A and the like reach the desktop as Ctrl, and ⌘V pastes
    // through the text field, the one place the web view delivers a paste.
    const keys = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Meta") {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      // Letters and digits only, read from the key's position so ⌥ does not change them.
      const letter = /^(?:Key|Digit)([A-Z0-9])$/.exec(event.code)?.[1];
      if (!event.metaKey || event.type !== "keydown" || !letter) return;
      if (letter === "V") {
        event.stopPropagation();
        keyboard.current?.focus();
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      const keysym = letter.toLowerCase().codePointAt(0)!;
      client?.sendKey(0xffe3, "ControlLeft", true);
      client?.sendKey(keysym, null, true);
      client?.sendKey(keysym, null, false);
      client?.sendKey(0xffe3, "ControlLeft", false);
    };
    target.addEventListener("keydown", keys, true);
    target.addEventListener("keyup", keys, true);
    void (async () => {
      try {
        // The viewer first: a socket opened before a failed load would keep a stream running.
        const { default: Client } = await import("@novnc/novnc");
        if (stopped) return;
        const socket = await openSocket(
          `/api/computer/control/run/${encodeURIComponent(id)}/stream?generation=${status.generation}`,
          ["binary"],
        );
        if (stopped) {
          socket.close();
          return;
        }
        client = new Client(target, socket, { shared: true });
        client.scaleViewport = true;
        client.focusOnClick = true;
        client.background = "#000";
        client.addEventListener("connect", () => {
          connected = true;
          rfb.current = client;
          setStreaming(true);
          client?.focus();
        });
        client.addEventListener("disconnect", () => {
          rfb.current = null;
          if (stopped) return;
          setStreaming(false);
          if (!connected) setFailedGeneration(generation);
        });
      } catch {
        if (!stopped) setFailedGeneration(generation);
      }
    })();
    return () => {
      stopped = true;
      target.removeEventListener("keydown", keys, true);
      target.removeEventListener("keyup", keys, true);
      client?.disconnect();
      rfb.current = null;
      setStreaming(false);
    };
  }, [id, open, status?.state, status?.generation, streamFailed]);
  const onStatusRef = useRef(onStatus);
  useEffect(() => {
    onStatusRef.current = onStatus;
  });

  useEffect(() => {
    mounted.current = true;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await channel.status();
        if (!stopped) {
          setStatus(value);
          onStatusRef.current?.(value);
        }
      } catch {
        /* The run may no longer exist. */
      }
      if (!stopped) timer = setTimeout(poll, 1000);
    };
    void poll();
    return () => {
      stopped = true;
      mounted.current = false;
      clearTimeout(timer);
    };
  }, [channel]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const clear = () => {
      if (currentFrame.current) URL.revokeObjectURL(currentFrame.current);
      currentFrame.current = "";
      setFrame("");
    };
    if (open && status?.state === "user" && !streaming) {
      const poll = async () => {
        try {
          const url = await channel.screen(status.generation);
          if (stopped) URL.revokeObjectURL(url);
          else {
            if (currentFrame.current) URL.revokeObjectURL(currentFrame.current);
            currentFrame.current = url;
            setFrame(url);
          }
        } catch {
          if (!stopped) {
            clear();
            setError("The desktop is not responding. The agent remains paused.");
          }
        }
        if (!stopped) timer = setTimeout(poll, 300);
      };
      void poll();
    }
    return () => {
      stopped = true;
      clearTimeout(timer);
      clear();
    };
  }, [channel, open, status?.state, status?.generation, streaming]);

  async function take() {
    setOpen(true);
    setBusy(true);
    setError("");
    setRefused(false);
    // Taking control again tries the stream again.
    setFailedGeneration(null);
    try {
      const value = await channel.take();
      if (mounted.current) setStatus(value);
    } catch (e) {
      if (mounted.current) {
        setRefused(true);
        setError(e instanceof Error ? e.message : "Could not take control.");
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  useImperativeHandle(ref, () => ({ take: () => void take() }));

  function send(action: string, payload: Record<string, unknown>) {
    if (status?.state !== "user" || busy) return;
    const next = { generation: status.generation, action, payload };
    const last = pending.current.at(-1);
    const joined = last ? merged(last, next) : null;
    if (last && joined) last.payload = joined;
    else pending.current.push(next);
    if (!queue.current) queue.current = deliver();
  }

  /** Sends what is waiting, one request at a time, until nothing is. */
  async function deliver() {
    try {
      for (let input = pending.current.shift(); input; input = pending.current.shift()) {
        try {
          await channel.action(input.generation, input.action, input.payload);
        } catch {
          if (mounted.current)
            setError("Input could not be delivered. Check the screen before trying again.");
        }
      }
    } finally {
      queue.current = null;
    }
  }

  function key(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Native paste is handled by onPaste; never read the system clipboard implicitly.
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v") return;
    if (["Control", "Shift", "Alt", "Meta"].includes(event.key)) return;
    const name = keys[event.key] ?? (/^[a-zA-Z0-9]$/.test(event.key) ? event.key : undefined);
    event.preventDefault();
    event.stopPropagation();
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey)
      send("type", { text: event.key });
    else if (name)
      send("key", {
        keys: `${event.ctrlKey || event.metaKey ? "ctrl+" : ""}${event.altKey ? "alt+" : ""}${event.shiftKey ? "shift+" : ""}${name}`,
      });
  }

  function paste(event: ClipboardEvent<HTMLTextAreaElement>) {
    event.preventDefault();
    event.stopPropagation();
    // The desktop types at most 5000 characters a request, counted by code point, so a long
    // paste goes in pieces that never split one.
    const characters = Array.from(event.clipboardData.getData("text/plain"));
    for (let at = 0; at < characters.length; at += 5000)
      send("type", { text: characters.slice(at, at + 5000).join("") });
    // Keys typed meanwhile land in the text field and follow the paste through the same queue;
    // the stream gets them back only once the paste has reached the desktop.
    void Promise.resolve(queue.current).then(() => rfb.current?.focus());
  }

  /** Closing the window while in control leaves the agent waiting, so that is said first. */
  function openChange(next: boolean) {
    if (!next && status?.state === "user") setLeaving(true);
    else setOpen(next);
  }

  async function resume() {
    if (!status) return;
    setBusy(true);
    setError("");
    try {
      // Stop the live input first: nothing typed may reach the desktop after the agent has it.
      rfb.current?.disconnect();
      await queue.current;
      const value = await channel.resume(status.generation);
      if (!mounted.current) return;
      setStatus(value);
      setOpen(false);
      setLeaving(false);
      void onResume().catch(() => undefined);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : "Could not resume the agent.");
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  if (!status?.available && !requested) return null;
  return (
    <div className={className}>
      <Button size="sm" variant="secondary" onClick={() => void take()}>
        <Monitor />
        {requested || status?.waiting
          ? "Sign in privately"
          : status?.state === "user"
            ? "Return to private control"
            : "Take control"}
      </Button>
      {status?.state !== "agent" && status?.state ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {status.state === "pausing" ? "Pausing the agent…" : "Waiting for you to return control."}
        </p>
      ) : null}
      <Dialog open={open} onOpenChange={openChange}>
        <DialogContent
          aria-describedby={undefined}
          className="max-h-[95dvh] w-[96vw] overflow-y-auto sm:max-w-[min(1320px,calc((100dvh-240px)*1.6))]"
          onEscapeKeyDown={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <ShieldCheck className="size-5" />
              {refused
                ? "Couldn’t take control"
                : status?.state === "user"
                  ? "You’re in control. The agent is paused."
                  : "Pausing the agent…"}
            </DialogTitle>
          </DialogHeader>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <div
            role="application"
            aria-label="Private desktop"
            className="relative overflow-hidden rounded-lg bg-black focus-within:ring-2 focus-within:ring-ring"
            onClick={(event) => {
              // Streaming, the desktop takes the pointer and keys itself.
              if (streaming) return;
              keyboard.current?.focus();
              if (!frame) return;
              send("click", point(event));
            }}
            onWheel={(event) => {
              if (!streaming && event.deltaY)
                send("scroll", {
                  direction: event.deltaY > 0 ? "down" : "up",
                  amount: 3,
                  ...point(event),
                });
            }}
          >
            {/* The keys go through a text field, not the picture: the Mac's web view pastes only into
              something editable, so ⌘V never reached a password field on the desktop. */}
            <textarea
              ref={keyboard}
              aria-label="Keyboard for the private desktop"
              value=""
              onChange={() => undefined}
              onKeyDown={key}
              onPaste={paste}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              className="absolute left-0 top-0 size-px resize-none opacity-0"
            />
            <div
              ref={screenTarget}
              aria-label="Live desktop"
              className={`absolute inset-0 ${streaming ? "" : "pointer-events-none opacity-0"}`}
            />
            {streaming ? (
              <div className="aspect-[1280/800] w-full" />
            ) : frame ? (
              <img
                src={frame}
                alt="Private browser screen"
                className="block aspect-[1280/800] w-full"
                draggable={false}
              />
            ) : refused ? (
              <div className="flex aspect-[1280/800] items-center justify-center text-sm text-white/70">
                The desktop isn’t available.
              </div>
            ) : (
              <div className="flex aspect-[1280/800] items-center justify-center text-white">
                <LoaderCircle className="mr-2 size-5 animate-spin" />
                Waiting for private control…
              </div>
            )}
          </div>
          {/* Refused, there is nothing to resume: the dialog's own close button ends it. */}
          {refused ? null : (
            <div className="flex justify-end">
              <Button disabled={busy || status?.state !== "user"} onClick={() => void resume()}>
                Resume agent
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog open={leaving} onOpenChange={setLeaving}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>The agent is still paused</AlertDialogTitle>
            <AlertDialogDescription>It waits until you press Resume agent.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setOpen(false)}>Close anyway</AlertDialogCancel>
            <AlertDialogAction disabled={busy} onClick={() => void resume()}>
              Resume agent
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
