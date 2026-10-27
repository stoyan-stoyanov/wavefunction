import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ComputerTakeover } from "@/components/computer-takeover";
import { Button } from "@/components/ui/button";
import { fetchBlobUrl } from "@/lib/api";
import type { RunEvent } from "@/lib/types";
import { isActive, type CodingRun } from "./api";
import "./live-screen.css";

/** The newest picture of the desktop in a run: what the agent last saw. */
function latestScreenshot(events: RunEvent[]) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event_type !== "screenshot") continue;
    const { file, agent_id: agentId } = event.payload ?? {};
    if (typeof file === "string" && typeof agentId === "string") return { agentId, file };
  }
  return null;
}

/** The newest screenshot as a blob URL. The previous one stays up while the next loads. A card
 * the user removed (not `shown`) loads none and lets go of the one it held. */
function useLatestScreen(events: RunEvent[], shown: boolean) {
  const shot = latestScreenshot(events);
  const [url, setUrl] = useState<string | null>(null);
  const held = useRef<string | null>(null);
  const agentId = shot?.agentId;
  const file = shot?.file;
  useEffect(() => {
    if (!shown || !agentId || !file) return;
    let cancelled = false;
    fetchBlobUrl(`/api/agents/${agentId}/screenshots/${file}`)
      .then((blobUrl) => {
        if (cancelled) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        if (held.current) URL.revokeObjectURL(held.current);
        held.current = blobUrl;
        setUrl(blobUrl);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [agentId, file, shown]);
  useEffect(() => {
    if (shown || !held.current) return;
    URL.revokeObjectURL(held.current);
    held.current = null;
  }, [shown]);
  useEffect(
    () => () => {
      if (held.current) URL.revokeObjectURL(held.current);
    },
    [],
  );
  return shown ? url : null;
}

const doing: Record<string, string> = {
  computer_screenshot: "Looking at the screen",
  computer_click: "Clicking",
  computer_double_click: "Clicking",
  computer_type: "Typing",
  computer_key: "Pressing keys",
  computer_scroll: "Scrolling",
  computer_drag: "Dragging",
  computer_move: "Moving the pointer",
  computer_wait: "Waiting for the page",
};

/** What the agent last did on the desktop. */
function lastAction(events: RunEvent[]) {
  let action = "Using the desktop";
  for (const event of events) {
    const tool = event.payload?.tool;
    if (event.event_type === "tool_started" && typeof tool === "string" && doing[tool]) action = doing[tool];
  }
  return action;
}

// The runs whose desktop card the user removed, newest last, so it stays removed after a restart.
const dismissedKey = "wavefunction.dismissed-desktops";

function dismissedDesktops(): string[] {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(dismissedKey) ?? "[]");
    return Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

function dismissDesktop(runId: string) {
  try {
    const kept = dismissedDesktops().filter((id) => id !== runId);
    localStorage.setItem(dismissedKey, JSON.stringify([...kept, runId].slice(-100)));
  } catch {
    // Removed for this session when storage is unavailable.
  }
}

/**
 * The chat's desktop, attached to the top of the composer instead of the conversation, so the
 * reply grows downward as usual. Live and large enough to follow for as long as the task runs, a
 * small chip once it is done. Kept with its last picture after the desktop closes (quitting the app
 * or twenty idle minutes do that), as the chat's next desktop task opens it again. Not sized by
 * what the agent is doing within the task: it writes between clicks, and the dock would shrink and
 * grow with each note. Clicking the picture takes control, as the button does. Once the task is
 * done, an X removes the card; the chat's next desktop task brings a new one.
 */
export function DesktopDock({ run, onResume }: { run: CodingRun; onResume: () => Promise<void> }) {
  const active = isActive(run.status);
  const requested = run.status === "waiting_computer";
  const action = lastAction(run.events);
  const working = requested || active;
  // Per run, though the card stays mounted for the chat: a newer task taking the desktop over
  // must not close a takeover window that is open.
  const [dismissed, setDismissed] = useState(dismissedDesktops);
  const shown = working || !dismissed.includes(run.id);
  const url = useLatestScreen(run.events, shown);
  // Whether the desktop is still up: unknown until Take control has asked.
  const [open, setOpen] = useState<boolean | null>(null);
  // While the user holds the desktop, removing the card would strand it: it would neither close
  // when idle nor let the next task in until control returns.
  const [held, setHeld] = useState(false);
  const takeover = useRef<{ take: () => void }>(null);
  if (!shown || (!url && !requested)) return null;
  const closed = open === false && !active;
  return (
    // In a composer-wrap so it is exactly as wide as the composer below it.
    <div className="composer-wrap desktop-dock-wrap">
      <div className={`desktop-dock${working ? " working" : ""}`}>
        {url && closed ? (
          <div className="desktop-thumb">
            <img src={url} alt="The agent's desktop when it closed" draggable={false} />
          </div>
        ) : url ? (
          <button
            type="button"
            className="desktop-thumb"
            aria-label="Take control of the desktop"
            onClick={() => takeover.current?.take()}
          >
            <img
              src={url}
              alt={working ? "The agent's desktop, live" : "The agent's desktop"}
              draggable={false}
            />
          </button>
        ) : null}
        <div key={working ? "working" : "idle"} className="desktop-dock-text">
          {working ? (
            <span className="flex items-center gap-1.5 text-xs font-medium">
              <span className="live-dot" aria-hidden="true" />{" "}
              {requested ? "Waiting for you on the desktop" : action}
            </span>
          ) : (
            <span className="text-xs font-medium">Desktop</span>
          )}
          {working || open === null ? null : (
            <span className="text-2xs text-muted-foreground">
              {closed ? "Closed. Opens again when the agent needs it." : "Still open for follow-ups"}
            </span>
          )}
        </div>
        <ComputerTakeover
          ref={takeover}
          id={run.id}
          requested={requested}
          onResume={onResume}
          className="desktop-dock-takeover shrink-0"
          onStatus={(status) => {
            setOpen(status.available);
            setHeld(status.available && status.state !== "agent");
          }}
        />
        {working || held ? null : (
          <Button
            variant="ghost"
            size="icon-xs"
            className="desktop-dock-remove shrink-0"
            aria-label="Remove desktop card"
            title="Remove"
            onClick={() => {
              dismissDesktop(run.id);
              setDismissed((ids) => [...ids, run.id]);
            }}
          >
            <X />
          </Button>
        )}
      </div>
    </div>
  );
}
