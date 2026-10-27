import type { RunConnector, RunEvent } from "@/lib/types";

const TERMINAL_EVENTS = new Set(["run_completed", "run_failed", "run_cancelled"]);

/** The setup rows a run is still waiting for: none once it has started again or finished. */
export function pendingRunConnectors(events: RunEvent[]): RunConnector[] | undefined {
  const event = [...events]
    .reverse()
    .find((event) =>
      ["run_connector_setup_required", "run_started", "run_queued", ...TERMINAL_EVENTS].includes(
        event.event_type,
      ),
    );
  return event?.event_type === "run_connector_setup_required"
    ? (event.payload.connectors as RunConnector[])
    : undefined;
}
