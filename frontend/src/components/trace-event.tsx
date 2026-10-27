/** Task activity, grouped tool calls, and the complete underlying trace. */

import { useEffect, useState } from "react";

import { Check, ChevronDown, CircleAlert, Clock3, Hand, Monitor, Wrench } from "lucide-react";

import { fetchBlobUrl } from "@/lib/api";
import type { Run, RunEvent } from "@/lib/types";
import type { ActivityItem, ToolCallActivity } from "@/lib/run-activity";
import { cn } from "@/lib/utils";

// Routine model calls, context budgets and streamed text appear only in the raw trace.
const eventLabels: Record<string, string> = {
  run_started: "Run started",
  model_call_failed: "Model attempt failed",
  model_retry: "Retrying the model",
  context_recovery: "Reducing context",
  loop_feedback: "Repeated action detected",
  plan_reminder: "Reminded to update the plan",
  action_outcome_unknown: "Action needs verification",
  execution_tool_started: "Tool execution",
  execution_tool_finished: "Tool returned",
  run_queued: "Task queued",
  steering_requested: "Task updated",
  tool_started: "Tool started",
  tool_output: "Command output tail",
  tool_completed: "Tool finished",
  tool_failed: "Tool failed",
  approval_decided: "Approval answered",
  run_waiting_for_approval: "Paused for you",
  delegation_started: "Helper started",
  helper_waiting: "Helper waiting for you",
  helper_resolved: "Helper continued",
  run_completed: "Result ready",
  run_failed: "Run failed",
  run_cancelled: "Run cancelled",
  screenshot: "Screenshot taken",
};

/** A screenshot from the sandboxed computer, fetched with the bearer token.
 *
 * The webview's CSP allows only blob and data images, so the file cannot be an `<img>`
 * pointing at the local API; it arrives through fetch and is shown as a blob URL.
 */
function ScreenshotImage({ agentId, file }: { agentId: string; file: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    fetchBlobUrl(`/api/agents/${agentId}/screenshots/${file}`)
      .then((blobUrl) => {
        if (cancelled) {
          URL.revokeObjectURL(blobUrl);
          return;
        }
        revoked = blobUrl;
        setUrl(blobUrl);
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [agentId, file]);

  if (failed) {
    return <p className="mb-2 text-2xs text-muted-foreground">This screenshot is no longer stored.</p>;
  }
  if (!url) {
    return <p className="mb-2 text-2xs text-muted-foreground">Loading screenshot…</p>;
  }
  return (
    <img
      src={url}
      alt="Screenshot of the sandboxed computer"
      className="mb-2 max-h-72 w-auto rounded border border-hairline"
    />
  );
}

function EventIcon({ event }: { event: RunEvent }) {
  if (event.event_type === "screenshot") return <Monitor className="size-3" />;
  if (event.event_type.includes("tool")) return <Wrench className="size-3" />;
  if (event.event_type.includes("approval") || event.event_type.includes("waiting"))
    return <Hand className="size-3" />;
  if (event.event_type.includes("failed")) return <CircleAlert className="size-3" />;
  if (event.event_type === "run_completed") return <Check className="size-3" />;
  return <Clock3 className="size-3" />;
}

function TraceEvent({ event }: { event: RunEvent }) {
  const screenshot =
    event.event_type === "screenshot" &&
    typeof event.payload?.file === "string" &&
    typeof event.payload?.agent_id === "string"
      ? { agentId: event.payload.agent_id, file: event.payload.file }
      : null;
  return (
    <details className="group border-b border-hairline last:border-b-0" open={screenshot !== null}>
      <summary className="flex cursor-pointer list-none items-center gap-2 py-2 text-xs">
        <span className="text-muted-foreground">
          <EventIcon event={event} />
        </span>
        <span className="min-w-0 flex-1 truncate">
          {eventLabels[event.event_type] ?? event.event_type.replaceAll("_", " ")}
        </span>
        <span className="font-mono text-2xs text-muted-foreground">
          {new Date(event.created_at).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })}
        </span>
        <ChevronDown className="size-3 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      {screenshot ? (
        <ScreenshotImage agentId={screenshot.agentId} file={screenshot.file} />
      ) : (
        <pre className="mb-2 max-h-44 overflow-auto border-l border-hairline pl-3 font-mono text-2xs leading-4 text-muted-foreground">
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      )}
    </details>
  );
}

function ToolCall({ call, status }: { call: ToolCallActivity; status: Run["status"] }) {
  const result = call.result;
  const state = result
    ? { tool_completed: "Finished", tool_failed: "Failed" }[result.event_type]
    : status.startsWith("waiting")
      ? "Waiting"
      : status === "running"
        ? "Running"
        : status === "queued"
          ? "Queued"
          : status === "cancelled"
            ? "Stopped"
            : "Unfinished";
  const output = result
    ? (result.payload.result ?? result.payload)
    : call.output && { stdout: call.output.payload.stdout, stderr: call.output.payload.stderr };
  return (
    <details className="group border-b border-hairline last:border-b-0" aria-label={`${call.tool} tool call`}>
      <summary className="flex cursor-pointer list-none items-center gap-2 py-2 text-xs">
        <Wrench className="size-3 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate">{call.tool}</span>
        <span className={cn("text-2xs", state === "Failed" ? "text-destructive" : "text-muted-foreground")}>
          {state}
        </span>
        <span className="font-mono text-2xs text-muted-foreground">
          {new Date(call.startedAt).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })}
        </span>
        <ChevronDown className="size-3 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      {call.args !== undefined ? (
        <>
          <p className="mb-1 text-2xs text-muted-foreground">Arguments</p>
          <pre className="mb-2 max-h-44 overflow-auto border-l border-hairline pl-3 font-mono text-2xs leading-4 text-muted-foreground">
            {JSON.stringify(call.args, null, 2)}
          </pre>
        </>
      ) : null}
      {output !== undefined ? (
        <>
          <p className="mb-1 text-2xs text-muted-foreground">{result ? "Result" : "Latest output"}</p>
          <pre className="mb-2 max-h-44 overflow-auto border-l border-hairline pl-3 font-mono text-2xs leading-4 text-muted-foreground">
            {typeof output === "string" ? output : JSON.stringify(output, null, 2)}
          </pre>
        </>
      ) : null}
    </details>
  );
}

export function RunActivity({
  items,
  events,
  status,
}: {
  items: ActivityItem[];
  events: RunEvent[];
  status: Run["status"];
}) {
  const [rawOpen, setRawOpen] = useState(false);
  return (
    <>
      {items.map((item) =>
        item.kind === "tool" ? (
          <ToolCall key={item.id} call={item} status={status} />
        ) : (
          <TraceEvent key={item.event.id} event={item.event} />
        ),
      )}
      <details
        className="group border-t border-hairline"
        onToggle={(event) => setRawOpen(event.currentTarget.open)}
      >
        <summary className="flex cursor-pointer list-none items-center gap-2 py-2 text-xs text-muted-foreground">
          <span className="flex-1">Raw trace · {events.length} events</span>
          <ChevronDown className="size-3 transition-transform group-open:rotate-180" />
        </summary>
        {rawOpen ? (
          <pre className="max-h-96 overflow-auto font-mono text-2xs leading-4 text-muted-foreground">
            {JSON.stringify(events, null, 2)}
          </pre>
        ) : null}
      </details>
    </>
  );
}
