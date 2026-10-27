import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { RunActivity } from "@/components/trace-event";
import * as api from "@/lib/api";
import { groupRunActivity } from "@/lib/run-activity";
import type { RunEvent } from "@/lib/types";

afterEach(() => vi.restoreAllMocks());

const event = (sequence: number, event_type: string, payload: RunEvent["payload"]): RunEvent => ({
  id: `event-${sequence}`,
  run_id: "run",
  node_id: null,
  sequence,
  event_type,
  payload,
  created_at: "2026-10-03T10:00:00Z",
});

it("pairs interleaved calls by identity and replaces each command's output tail", () => {
  const events = [
    event(1, "tool_started", { id: "a", tool: "run_command", args: { command: "first-command" } }),
    event(2, "tool_started", { id: "b", tool: "run_command", args: { command: "second-command" } }),
    event(3, "execution_tool_started", { call_id: "a", tool: "run_command" }),
    event(4, "execution_tool_started", { call_id: "b", tool: "run_command" }),
    event(5, "tool_output", { id: "a", stdout: "old-tail", stderr: "" }),
    event(6, "tool_output", { id: "b", stdout: "second-tail", stderr: "" }),
    event(7, "tool_output", { id: "a", stdout: "latest-tail", stderr: "first-warning" }),
    event(8, "execution_tool_finished", { call_id: "b" }),
    event(9, "tool_completed", { id: "b", tool: "run_command", result: "second-result" }),
  ];
  const activity = groupRunActivity(events);
  expect(activity.toolCount).toBe(2);
  render(<RunActivity items={activity.items} events={events} status="running" />);
  const [first, second] = screen.getAllByRole("group", { name: "run_command tool call" });
  for (const row of [first, second]) fireEvent.click(within(row).getByText("run_command"));
  expect(within(first).getByText(/first-command/)).toBeVisible();
  expect(within(first).getByText(/latest-tail/)).toHaveTextContent("first-warning");
  expect(within(first).queryByText(/old-tail|second-result|second-command/)).toBeNull();
  expect(within(first).getByText("Running")).toBeVisible();
  expect(within(second).getByText(/second-command/)).toBeVisible();
  expect(within(second).getByText("second-result")).toBeVisible();
  expect(within(second).getByText("Finished")).toBeVisible();
});

it("keeps a paused call's open row and identity through resume and failure", () => {
  let events = [
    event(1, "tool_started", { id: "call", tool: "github__create_pull_request", args: { title: "Change" } }),
    event(2, "execution_tool_started", { call_id: "call", tool: "github__create_pull_request" }),
    event(3, "execution_tool_finished", { call_id: "call" }),
  ];
  const view = render(
    <RunActivity items={groupRunActivity(events).items} events={events} status="waiting_connector" />,
  );
  const row = screen.getByRole("group", { name: "github__create_pull_request tool call" });
  fireEvent.click(within(row).getByText("github__create_pull_request"));
  expect(within(row).getByText("Waiting")).toBeVisible();
  events = [
    ...events,
    event(4, "execution_tool_started", { call_id: "call", tool: "github__create_pull_request" }),
  ];
  expect(groupRunActivity(events).toolCount).toBe(1);
  view.rerender(<RunActivity items={groupRunActivity(events).items} events={events} status="running" />);
  expect(screen.getByRole("group", { name: "github__create_pull_request tool call" })).toBe(row);
  expect(row).toHaveAttribute("open");
  expect(within(row).getByText("Running")).toBeVisible();
  events = [
    ...events,
    event(5, "execution_tool_finished", { call_id: "call" }),
    event(6, "tool_failed", {
      id: "call",
      tool: "github__create_pull_request",
      result: "Authentication failed",
    }),
  ];
  view.rerender(<RunActivity items={groupRunActivity(events).items} events={events} status="completed" />);
  expect(screen.getAllByRole("group", { name: "github__create_pull_request tool call" })).toHaveLength(1);
  expect(within(row).getByText("Failed")).toBeVisible();
  expect(within(row).getByText("Authentication failed")).toBeVisible();
});

it("preserves task events and screenshots while keeping the complete trace separately accessible", async () => {
  const fetch = vi.spyOn(api, "fetchBlobUrl").mockResolvedValue("blob:activity-screenshot");
  const revoke = vi.spyOn(URL, "revokeObjectURL");
  const events = [
    event(1, "model_call_started", { model: "synthetic-model" }),
    event(2, "steering_requested", { text: "Run the focused test" }),
    event(3, "run_waiting_for_approval", { requests: [{ kind: "folder_access" }] }),
    event(4, "screenshot", {
      id: "screen",
      tool: "computer_screenshot",
      agent_id: "synthetic-agent",
      file: "capture.png",
    }),
  ];
  const view = render(
    <RunActivity items={groupRunActivity(events).items} events={events} status="waiting_approval" />,
  );
  expect(screen.getByText("Task updated")).toBeVisible();
  expect(screen.getByText("Paused for you")).toBeVisible();
  expect(await screen.findByAltText("Screenshot of the sandboxed computer")).toHaveAttribute(
    "src",
    "blob:activity-screenshot",
  );
  expect(fetch).toHaveBeenCalledExactlyOnceWith("/api/agents/synthetic-agent/screenshots/capture.png");
  expect(screen.queryByText(/synthetic-model/)).toBeNull();
  fireEvent.click(screen.getByText("Raw trace · 4 events"));
  const raw = await screen.findByText(/synthetic-model/);
  expect(JSON.parse(raw.textContent!)).toEqual(events);
  view.unmount();
  expect(revoke).toHaveBeenCalledExactlyOnceWith("blob:activity-screenshot");
});
