import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ReasoningControl } from "@/components/reasoning-control";
import { api } from "@/lib/api";
import type { ModelSpec } from "@/lib/types";

vi.mock("@/lib/api", () => ({ api: { modelReasoningOptions: vi.fn() } }));
const workspace = vi.hoisted(() => ({ state: {} as Record<string, unknown> }));
vi.mock("@/store/settings", () => ({
  useSettings: (select: (state: object) => unknown) => select(workspace.state),
}));

const model: ModelSpec = {
  provider: "codex",
  model: "test",
  base_url: null,
  temperature: 0.1,
  max_tokens: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  workspace.state = {};
  vi.mocked(api.modelReasoningOptions).mockResolvedValue({
    efforts: ["low", "high", "xhigh"],
  });
});

it("offers provider levels and saves an explicit effort", async () => {
  const onChange = vi.fn();
  render(<ReasoningControl model={model} onChange={onChange} />);
  const trigger = screen.getByRole("combobox", { name: "Reasoning effort" });
  await waitFor(() => expect(trigger).toBeEnabled());
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(screen.queryByRole("option", { name: "Medium" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("option", { name: "Extra high" }));
  await waitFor(() => expect(onChange).toHaveBeenCalledWith("xhigh"));
});

it("does not silently ignore a failed save", async () => {
  const onChange = vi.fn().mockRejectedValue(new Error("This level is no longer available."));
  render(<ReasoningControl model={model} onChange={onChange} />);
  const trigger = screen.getByRole("combobox", { name: "Reasoning effort" });
  await waitFor(() => expect(trigger).toBeEnabled());
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  fireEvent.click(screen.getByRole("option", { name: "High" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("This level is no longer available.");
  expect(trigger).toHaveTextContent("Provider default");
});

it("offers distinct on/off choices for a model with a thinking toggle", async () => {
  vi.mocked(api.modelReasoningOptions).mockResolvedValue({
    efforts: ["none", "enabled"],
  });
  const onChange = vi.fn();
  render(<ReasoningControl model={{ ...model, provider: "ollama" }} onChange={onChange} />);
  const trigger = screen.getByRole("combobox", { name: "Reasoning effort" });
  await waitFor(() => expect(trigger).toBeEnabled());
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  expect(screen.queryByRole("option", { name: "High" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("option", { name: "On" }));
  await waitFor(() => expect(onChange).toHaveBeenCalledWith("enabled"));
});
