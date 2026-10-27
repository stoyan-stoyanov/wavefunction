import type { RunEvent } from "@/lib/types";

const name = (path: unknown) =>
  String(path ?? "")
    .split("/")
    .filter(Boolean)
    .at(-1) ?? "a file";

/** What one tool call is doing, said as the chat's live status line says it. */
function doing(tool: string, args: Record<string, unknown>, count: number): string {
  if (tool === "read_file") return count > 1 ? `Reading ${count} files` : `Reading ${name(args.path)}`;
  if (tool === "edit_file" || tool === "write_file")
    return count > 1 ? `Editing ${count} files` : `Editing ${name(args.path)}`;
  if (tool === "search_files" || tool === "find_files") return "Searching the code";
  if (tool === "run_command") {
    const command = String(args.command ?? "")
      .split("\n")[0]
      .trim();
    return command
      ? `Running ${command.length > 48 ? `${command.slice(0, 47)}…` : command}`
      : "Running a command";
  }
  if (tool === "spawn_agent") return count > 1 ? `Starting ${count} helpers` : "Starting a helper";
  if (tool === "wait_agents") return "Waiting for helpers";
  if (tool === "get_agent" || tool === "send_message" || tool === "stop_agent") return "Checking on helpers";
  if (tool === "write_todos") return "Updating the plan";
  if (tool === "web_search") return "Searching the web";
  if (tool === "fetch_page") return "Reading a web page";
  if (tool.startsWith("computer_")) return "Using the computer";
  if (tool.endsWith("_terminal") || tool === "list_terminals") return "Using a terminal";
  return `Using ${tool}`;
}

/** The running task's current step, from its events: the tool calls still open, else the model.
 *  None while it compacts the conversation, which the conversation shows where it happens. */
export function runActivity(events: RunEvent[]): string | null {
  const finished = new Set(
    events
      .filter((event) => event.event_type === "tool_completed" || event.event_type === "tool_failed")
      .map((event) => String(event.payload.id)),
  );
  let lastModel = -1;
  let lastTool = -1;
  events.forEach((event, index) => {
    if (["model_call_started", "model_retry", "summarization_started"].includes(event.event_type))
      lastModel = index;
    if (event.event_type === "tool_started") lastTool = index;
  });
  const open = events.filter(
    (event, index) =>
      event.event_type === "tool_started" && index > lastModel && !finished.has(String(event.payload.id)),
  );
  if (open.length) {
    const latest = open.at(-1)!;
    const tool = String(latest.payload.tool);
    const same = open.filter((event) => event.payload.tool === tool).length;
    return doing(tool, (latest.payload.args ?? {}) as Record<string, unknown>, same);
  }
  const model = events[lastModel];
  if (model && lastModel > lastTool) {
    if (model.event_type === "model_retry") return "Retrying the model";
    if (model.event_type === "summarization_started") return null;
    return "Thinking";
  }
  return "Working";
}
