import { requestResponse } from "@/lib/transport";
import type { Schemas } from "@/lib/types";

export type ControlStatus = Schemas["ControlStatus"];

/** Bind the private input channel to the workspace that opened the panel. */
export function computerControl(id: string) {
  const base = `/api/computer/control/run/${encodeURIComponent(id)}`;
  const json = <T>(suffix: string, body?: unknown) =>
    requestResponse<T>(
      base + suffix,
      (response) => response.json(),
      body === undefined
        ? {}
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
    );
  return {
    status: () => json<ControlStatus>(""),
    take: () => json<ControlStatus>("/take", {}),
    resume: (generation: number) => json<ControlStatus>("/resume", { generation }),
    action: (generation: number, action: string, payload: Record<string, unknown>) =>
      json<Schemas["Accepted"]>("/action", { generation, action, payload }),
    screen: (generation: number) =>
      requestResponse(
        base + `/screen?generation=${generation}`,
        async (response) => URL.createObjectURL(await response.blob()),
        { cache: "no-store" },
      ),
  };
}
