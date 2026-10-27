import type { RunEvent } from "@/lib/types";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunChecklist } from "./task-checklist";

const todos = [
  { content: "Find the Settings save handler", status: "in_progress" },
  { content: "Verify the fix", status: "pending" },
];
function event(type: string, id: string, args?: unknown): RunEvent {
  return {
    id: `${type}-${id}`,
    run_id: "r",
    node_id: null,
    sequence: 0,
    event_type: type,
    created_at: "",
    payload: { id, tool: "write_todos", args },
  };
}

describe("task checklist", () => {
  it("shows only successful updates and keeps unfinished work after interruption", () => {
    const start = event("tool_started", "1", { todos });
    const done = event("tool_completed", "1");
    const { rerender } = render(<RunChecklist events={[start]} active />);
    expect(screen.queryByLabelText("Task checklist")).not.toBeInTheDocument();
    rerender(<RunChecklist events={[start, done]} active />);
    expect(screen.getByLabelText("Task checklist")).toHaveAttribute("open");
    expect(screen.getByText("Find the Settings save handler")).toBeVisible();
    const changed = event("tool_started", "2", {
      todos: [{ content: "Incorrect update", status: "completed" }],
    });
    rerender(<RunChecklist events={[start, done, changed, event("tool_failed", "2")]} active={false} />);
    expect(screen.queryByText("Incorrect update")).not.toBeInTheDocument();
    expect(screen.getByText("Unfinished:")).toBeInTheDocument();
    expect(screen.getByText("Plan · 0/2 complete")).toBeInTheDocument();
    rerender(<RunChecklist events={[start, done, changed, event("tool_completed", "2")]} active={false} />);
    expect(screen.getByText("Plan · 1/1 complete")).toBeInTheDocument();
    expect(screen.queryByText("Find the Settings save handler")).not.toBeInTheDocument();
  });
});
