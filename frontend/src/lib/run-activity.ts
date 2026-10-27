import type { RunEvent } from "@/lib/types";

export interface ToolCallActivity {
  kind: "tool";
  id: string;
  tool: string;
  startedAt: string;
  args?: unknown;
  output?: RunEvent;
  result?: RunEvent;
}

export type ActivityItem = ToolCallActivity | { kind: "event"; event: RunEvent };

const toolEvents = new Set([
  "tool_started",
  "execution_tool_started",
  "tool_output",
  "execution_tool_finished",
  "tool_completed",
  "tool_failed",
]);
const resultEvents = new Set(["tool_completed", "tool_failed"]);
const rawOnlyEvents = new Set([
  "assistant_delta",
  "assistant_message",
  "model_call_started",
  "model_call_finished",
  "context_budget",
]);

/** Events arrive in sequence order. A resumed execution keeps its original call identity. */
export function groupRunActivity(events: RunEvent[]) {
  const calls = new Map<string, ToolCallActivity>();
  const items: ActivityItem[] = [];
  for (const event of events) {
    const callId = event.payload.call_id ?? event.payload.id;
    if (toolEvents.has(event.event_type) && typeof callId === "string" && callId) {
      const id = `${event.run_id}:${callId}`;
      let call = calls.get(id);
      if (!call) {
        call = { kind: "tool", id, tool: "Tool", startedAt: event.created_at };
        calls.set(id, call);
        items.push(call);
      }
      if (typeof event.payload.tool === "string" && event.payload.tool) call.tool = event.payload.tool;
      if (event.event_type === "tool_started") call.args = event.payload.args;
      if (event.event_type === "tool_started" || event.event_type === "execution_tool_started") {
        call.result = undefined;
      }
      // Command output events replace a bounded tail; concatenating them duplicates output.
      if (event.event_type === "tool_output") call.output = event;
      if (resultEvents.has(event.event_type)) call.result = event;
    } else if (!rawOnlyEvents.has(event.event_type)) {
      items.push({ kind: "event", event });
    }
  }
  return { items, toolCount: calls.size };
}
