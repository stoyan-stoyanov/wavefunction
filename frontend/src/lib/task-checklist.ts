import type { RunEvent } from "@/lib/types";

export interface Task {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

function parseTasks(value: unknown): Task[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is Task =>
      !!item &&
      typeof item.content === "string" &&
      ["pending", "in_progress", "completed"].includes(item.status),
  );
}

export function runTasks(events: RunEvent[]): Task[] {
  const calls = new Map<string, unknown>();
  let tasks: Task[] = [];
  for (const event of events) {
    const id = String(event.payload.id ?? "");
    if (event.event_type === "tool_started" && event.payload.tool === "write_todos") {
      calls.set(id, (event.payload.args as Record<string, unknown> | undefined)?.todos);
    } else if (event.event_type === "tool_completed" && calls.has(id)) {
      tasks = parseTasks(calls.get(id));
      calls.delete(id);
    } else if (event.event_type === "tool_failed") calls.delete(id);
  }
  return tasks;
}
